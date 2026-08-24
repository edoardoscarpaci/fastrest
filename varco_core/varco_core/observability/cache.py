"""
varco_core.observability.cache
=================================
Cache observability pack (Plan 010, Phase 2 — C3): hit/miss/eviction
counters, a per-operation duration histogram, and the varco-specific
stampede/stale/backplane counters, built on the existing ``Metric``
primitive — the same shape as ``install_reliability_metrics()``
(``varco_core.observability.reliability``), which the scout named as the
pattern to copy.

``install_cache_metrics()`` is a manual install function, deliberately
**not** a scanned ``@Configuration`` — a scanned config auto-activates on
``container.scan()`` (``technical_docs/features/casbin-authorization.md``'s
"policy authorizer silently active" pitfall, same class of mistake for a
different feature).

Instrument names (Decision D-2 — settled by research brief 003; see
Plan 010's "Decisions" section for the full citation trail):

============================================  =========== ====  ============================
Name                                          Kind        Unit  Attributes
============================================  =========== ====  ============================
``varco.cache.hits``                          counter     1     cache, layer, kind
``varco.cache.misses``                        counter     1     cache, layer
``varco.cache.evictions``                     counter     1     cache, layer, reason
``varco.cache.duration``                      histogram   ms    cache, operation
``varco.cache.stampede_suppressed``           counter     1     cache
``varco.cache.stale_served``                  counter     1     cache, reason
``varco.cache.backplane.published``           counter     1     kind
``varco.cache.backplane.received``            counter     1     kind
``varco.cache.backplane.dropped``             counter     1     reason
============================================  =========== ====  ============================

Cardinality (brief 003 §4 — deny-list): attribute values are bounded to
``cache`` (instance name), ``layer`` (``l1``/``l2``/…), ``operation``
(``get``/``set``/``delete``/``clear``), ``kind``
(``positive``/``negative``/``stale`` for hits; ``key``/``prefix``/``clear``
for backplane messages), and ``reason``. **Never** the cache key, a tenant
id, a user id, or a correlation id.

Hit **ratio** is derived at query time (``hits / (hits + misses)``) rather
than emitted as its own series — brief 003 §2 (Uptrace) explicitly
recommends deriving it.

Semconv migration path: no OTel semantic convention for application cache
metrics exists as of v1.44.0 (Aug 2026);
``open-telemetry/semantic-conventions#1747`` proposes span attributes, not
metrics, and remains open. If a convention is approved later, realign via
the module-level ``METRIC_NAMES``/``ATTR_KEYS`` tables below (a one-line
edit per name) rather than renaming series ad hoc.

DESIGN: record_* helpers, gated by an install-time enabled flag
    ✅ Mirrors ``reliability.py``'s ``_safe()`` swallow — an instrument
       failure must never break a cache read.
    ✅ "Off unless installed": the module-level ``_enabled`` flag defaults
       to ``False`` and is only flipped by ``install_cache_metrics()`` — a
       cache read in a process that never called ``install_cache_metrics()``
       pays only the cost of one boolean check, matching the "metrics pack
       never installed → hot path unchanged" edge case.
    ❌ A tiny bit of extra module-level state versus relying purely on the
       OTel SDK's own no-op-meter-when-unconfigured behaviour (which is what
       ``reliability.py`` does) — accepted so ``CacheMetricsConfig(enabled=
       False)`` has an observable effect distinguishable from "no
       MeterProvider configured yet" in tests.

Thread safety:  ✅ Module-level dict/flag reads and writes are atomic under
                   the CPython GIL — same reasoning as ``_instrument_cache``
                   in ``metrics.py``.
Async safety:   ✅ All ``record_*`` helpers are synchronous.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from varco_core.observability.metric import Metric

_logger = logging.getLogger(__name__)


class _PatchableMetric(Metric):
    """
    ``Metric`` subclass with no ``__slots__`` of its own.

    ``Metric`` uses ``__slots__`` to keep per-instance memory small, which
    also makes ``monkeypatch``/``mock.patch("...._CACHE_HITS.add", ...)``
    fail with ``AttributeError: attribute 'add' is read-only``. The module
    level cache instruments are a handful of singletons, not a hot
    allocation path, so restoring a ``__dict__`` here (Python's normal
    behaviour for a subclass declaring no ``__slots__`` of its own) is
    harmless and lets tests patch ``.add``/``.record`` per-instance.
    """


# ── Module-level instruments (safe before MeterProvider setup) ─────────────

_CACHE_HITS = _PatchableMetric(
    "varco.cache.hits", kind="counter", description="Cache read hits"
)
_CACHE_MISSES = _PatchableMetric(
    "varco.cache.misses", kind="counter", description="Cache read misses"
)
_CACHE_EVICTIONS = _PatchableMetric(
    "varco.cache.evictions", kind="counter", description="Cache entries evicted"
)
_CACHE_DURATION = _PatchableMetric(
    "varco.cache.duration",
    kind="histogram",
    unit="ms",
    description="Cache operation latency",
)
_STAMPEDE_SUPPRESSED = _PatchableMetric(
    "varco.cache.stampede_suppressed",
    kind="counter",
    description="Concurrent recomputes coalesced by Singleflight",
)
_STALE_SERVED = _PatchableMetric(
    "varco.cache.stale_served",
    kind="counter",
    description="Stale values served (soft-TTL SWR or stale-if-error)",
)
_BACKPLANE_PUBLISHED = _PatchableMetric(
    "varco.cache.backplane.published",
    kind="counter",
    description="Invalidation messages published to the backplane",
)
_BACKPLANE_RECEIVED = _PatchableMetric(
    "varco.cache.backplane.received",
    kind="counter",
    description="Invalidation messages received from the backplane",
)
_BACKPLANE_DROPPED = _PatchableMetric(
    "varco.cache.backplane.dropped",
    kind="counter",
    description="Backplane messages dropped (publish or decode failure)",
)

#: Name of every OTel instrument this pack creates — the single place a
#: future semconv rename touches (D-2).
METRIC_NAMES: dict[str, str] = {
    "hits": "varco.cache.hits",
    "misses": "varco.cache.misses",
    "evictions": "varco.cache.evictions",
    "duration": "varco.cache.duration",
    "stampede_suppressed": "varco.cache.stampede_suppressed",
    "stale_served": "varco.cache.stale_served",
    "backplane_published": "varco.cache.backplane.published",
    "backplane_received": "varco.cache.backplane.received",
    "backplane_dropped": "varco.cache.backplane.dropped",
}

#: Attribute keys this pack ever emits — the deny-list boundary (brief 003 §4).
ATTR_KEYS: frozenset[str] = frozenset({"cache", "layer", "operation", "kind", "reason"})


def _safe(fn: object, *args: object, **kwargs: object) -> None:
    """Call a ``Metric`` recording method, swallowing any exception.

    A metrics instrument failing must never break the cache operation it
    instruments (same contract as ``reliability.py``'s ``_safe()``).
    """
    try:
        fn(*args, **kwargs)  # type: ignore[operator]
    except Exception as exc:  # noqa: BLE001 - metrics must never propagate
        _logger.debug("Cache metric recording failed: %s", exc)


# ── CacheMetricsConfig ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class CacheMetricsConfig:
    """
    Configuration for ``install_cache_metrics()``.

    Attributes:
        enabled:            Master kill-switch. ``False`` makes
                             ``install_cache_metrics()`` a no-op and leaves
                             every ``record_*`` call a cheap no-op.
        meter_name:         OTel instrumentation scope. Defaults to
                             ``"varco"``.
        duration_histogram: Whether ``record_cache_duration()`` actually
                             records — set ``False`` to skip histogram
                             overhead when only counters are wanted.
        by_layer:           Whether ``layer=`` is included as an attribute
                             on hit/miss/eviction records. ``False`` collapses
                             the ``layer`` dimension (useful to bound
                             cardinality on a deployment with many named
                             ``LayeredCache`` instances).
    """

    enabled: bool = True
    meter_name: str = "varco"
    duration_histogram: bool = True
    by_layer: bool = True


# Module-level "is the pack active" flag — False until install_cache_metrics()
# is called with enabled=True.  See the module DESIGN block.
_enabled: bool = False
_config: CacheMetricsConfig = CacheMetricsConfig()


def install_cache_metrics(*, config: CacheMetricsConfig | None = None) -> None:
    """
    Install the cache metrics pack.

    Idempotent — calling this more than once simply re-applies ``config``
    (or the default). ``config.enabled=False`` disables recording without
    unregistering the already-created OTel instruments (they just stop
    being written to).

    Despite the verb, this takes **no container**, mutates module-level
    globals, and is deliberately not a scanned ``@Configuration`` — see
    CLAUDE.md's "DI wiring verb taxonomy" for how this differs from
    providify's ``container.install(SomeConfiguration)``.

    Args:
        config: ``CacheMetricsConfig``. Defaults to ``CacheMetricsConfig()``
            (enabled, ``meter_name="varco"``).
    """
    global _enabled, _config
    _config = config or CacheMetricsConfig()
    _enabled = _config.enabled


def _layer_attr(layer: str) -> dict[str, str]:
    if not layer or not _config.by_layer:
        return {}
    return {"layer": layer}


def record_cache_hit(
    *, cache: str = "", layer: str = "", kind: str = "positive"
) -> None:
    """Record one cache hit. ``kind`` is ``"positive"``/``"negative"``/``"stale"``."""
    if not _enabled:
        return
    _safe(_CACHE_HITS.add, cache=cache, kind=kind, **_layer_attr(layer))


def record_cache_miss(*, cache: str = "", layer: str = "") -> None:
    """Record one cache miss."""
    if not _enabled:
        return
    _safe(_CACHE_MISSES.add, cache=cache, **_layer_attr(layer))


def record_cache_eviction(*, cache: str = "", layer: str = "", reason: str) -> None:
    """Record one eviction. ``reason`` is ``capacity``/``ttl``/``explicit``/``backplane``."""
    if not _enabled:
        return
    _safe(_CACHE_EVICTIONS.add, cache=cache, reason=reason, **_layer_attr(layer))


def record_cache_duration(*, cache: str = "", operation: str, value_ms: float) -> None:
    """Record the duration (in ms) of one cache operation."""
    if not _enabled or not _config.duration_histogram:
        return
    _safe(_CACHE_DURATION.record, value_ms, cache=cache, operation=operation)


def record_stampede_suppressed(*, cache: str = "") -> None:
    """Record one recompute coalesced away by ``Singleflight`` (a follower)."""
    if not _enabled:
        return
    _safe(_STAMPEDE_SUPPRESSED.add, cache=cache)


def record_cache_stale_served(*, cache: str = "", reason: str) -> None:
    """Record one stale-value serve. ``reason`` is ``soft_ttl``/``error``."""
    if not _enabled:
        return
    _safe(_STALE_SERVED.add, cache=cache, reason=reason)


def record_backplane_published(*, kind: str) -> None:
    """Record one invalidation message published to the backplane."""
    if not _enabled:
        return
    _safe(_BACKPLANE_PUBLISHED.add, kind=kind)


def record_backplane_received(*, kind: str) -> None:
    """Record one invalidation message received from the backplane."""
    if not _enabled:
        return
    _safe(_BACKPLANE_RECEIVED.add, kind=kind)


def record_backplane_dropped(*, reason: str) -> None:
    """Record one dropped backplane message. ``reason`` is ``publish_failed``/``decode_failed``."""
    if not _enabled:
        return
    _safe(_BACKPLANE_DROPPED.add, reason=reason)


__all__ = [
    "ATTR_KEYS",
    "METRIC_NAMES",
    "CacheMetricsConfig",
    "install_cache_metrics",
    "record_backplane_dropped",
    "record_backplane_published",
    "record_backplane_received",
    "record_cache_duration",
    "record_cache_eviction",
    "record_cache_hit",
    "record_cache_miss",
    "record_cache_stale_served",
    "record_stampede_suppressed",
]
