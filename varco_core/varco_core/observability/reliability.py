"""
varco_core.observability.reliability
=======================================
Reliability metrics pack (Plan 009, Phase 1 — R2): counters/gauges for the
DLQ, outbox, audit, and job-lease subsystems, all built on the existing
``Metric``/``register_gauge`` primitives in ``varco_core.observability.metric``.

``install_reliability_metrics()`` is imperative, not a scanned
``@Configuration`` — metrics need the *live* DLQ/outbox instance, which only
the application knows, and a scanned ``@Configuration`` would auto-activate
on ``container.scan()`` (``technical_docs/features/casbin-authorization.md``
pitfall: "policy authorizer silently active" is the same class of mistake
for a different feature).

DESIGN: recording helpers (``record_*``) over decorating call sites
    ✅ The DLQ push path must never raise — a decorator wrapping ``push()``
       would sit *outside* the try/except that already guarantees that.
       Helpers are called *inside* it instead.
    ✅ Call sites stay one line; each helper is independently monkeypatchable
       in tests.
    ❌ Six free functions instead of one decorator — accepted for the
       "push() never raises" invariant.

DESIGN: gauge skips rather than reports a negative depth (RD-3)
    ✅ A gap in a depth graph is honest; Kafka's documented ``count() == -1``
       rendered literally would poison every alert threshold built on this
       metric.
    ❌ A broker-down DLQ silently produces zero data points instead of an
       explicit "unknown" state — mitigated by the DEBUG log on the
       exception path (visible to anyone actually looking, not to an
       alerting rule).

Thread safety:  ✅ Module-level dicts are read/written under the CPython GIL
                   — same reasoning as ``_instrument_cache`` in ``metrics.py``.
Async safety:   ✅ ``record_*`` are synchronous. The observable-gauge
                   callbacks below await the (async) ``count()``/
                   ``count_pending()`` methods via ``_run_sync`` — see its
                   docstring for why a plain ``asyncio.run()`` is unsafe here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from opentelemetry import metrics as otel_metrics
from opentelemetry.metrics import Observation

from varco_core.observability.attributes import wrap_gauge_callback
from varco_core.observability.metric import Metric
from datetime import UTC

if TYPE_CHECKING:
    from varco_core.event.dlq import AbstractDeadLetterQueue
    from varco_core.service.outbox import OutboxRepository

_logger = logging.getLogger(__name__)
_T = TypeVar("_T")


# The event loop that owns the observed DLQ/outbox instances — captured at
# ``install_reliability_metrics()`` time.  See ``_run_sync``.
_owner_loop: asyncio.AbstractEventLoop | None = None

# Budget for one gauge observation.  A collection cycle must never hang the
# exporter thread on an unreachable broker.
_OBSERVATION_TIMEOUT_S = 5.0


def _run_sync(coro: Awaitable[_T]) -> _T:
    """
    Run an awaitable to completion from a synchronous callback.

    OTel observable-gauge callbacks are synchronous, but ``count()`` /
    ``count_pending()`` are ``async def``.

    DESIGN: run the coroutine on the loop that OWNS the client, never a fresh one
        ✅ ``RedisDLQ``/``BeanieAuditRepository``/``KafkaDLQ`` hold an async
           client bound to the application's event loop.  Driving their
           coroutines on a *different* loop — a fresh ``asyncio.run()`` loop,
           or ``run_until_complete()`` on a new loop in a worker thread —
           raises ``got Future attached to a different loop``.  The gauge
           callback swallows that (it must never raise), so
           ``varco.dlq.depth`` silently emitted **zero** data points for every
           real backend: the metric appeared to work and reported nothing.
           This is the production path, not just a test artifact — OTel's
           ``PeriodicExportingMetricReader`` collects from its own thread,
           where there is no running loop at all.
        ✅ ``run_coroutine_threadsafe`` submits to the owner loop, so the
           client is used from the thread/loop it was created on.
        ✅ Bounded by ``_OBSERVATION_TIMEOUT_S`` — an unreachable broker costs
           one timeout, not a wedged exporter thread.
        ❌ Requires knowing the owner loop, which is captured best-effort at
           ``install_reliability_metrics()`` time.  When it is unknown (the
           installer ran outside a loop) or when the caller is already *on*
           the owner loop's own thread (it cannot block itself), the historical
           fresh-loop fallback is kept — correct for loop-agnostic DLQs such as
           ``InMemoryDeadLetterQueue``, and no worse than before for the rest.

    Args:
        coro: The awaitable to run.

    Returns:
        The awaitable's result.

    Raises:
        concurrent.futures.TimeoutError: The owner loop did not produce a
            result within ``_OBSERVATION_TIMEOUT_S`` — caught by the callers,
            which then emit no observation.

    Edge cases:
        - Owner loop closed/stopped since install → fresh-loop fallback.
        - Called from the owner loop's own thread → fresh-loop fallback
          (blocking on the loop from inside it would deadlock).

    Thread safety:  ✅ ``run_coroutine_threadsafe`` is the documented
                       cross-thread hand-off; the fallback spins up at most
                       one short-lived thread.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    owner = _owner_loop
    if owner is not None and owner is not running:
        try:
            alive = not owner.is_closed() and owner.is_running()
        except RuntimeError:  # pragma: no cover - defensive
            alive = False
        if alive:
            future = asyncio.run_coroutine_threadsafe(coro, owner)  # type: ignore[arg-type]
            return future.result(timeout=_OBSERVATION_TIMEOUT_S)

    if running is None:
        return asyncio.run(coro)  # type: ignore[arg-type]

    import concurrent.futures

    def _runner() -> _T:
        return asyncio.new_event_loop().run_until_complete(coro)  # type: ignore[arg-type]

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_runner).result()


# ── ReliabilityMetricsConfig ──────────────────────────────────────────────────


@dataclass(frozen=True)
class ReliabilityMetricsConfig:
    """
    Configuration for ``install_reliability_metrics()``.

    Attributes:
        enabled:         Master kill-switch — ``False`` makes ``install_reliability_metrics``
                          a no-op.
        meter_name:      OTel instrumentation scope. Defaults to ``"varco"``.
        depth_by_channel: Opt-in per-channel DLQ depth (RD-3) — calls
                          ``count_by_channel()`` and emits one series per
                          channel. Off by default (unbounded-ish cardinality
                          the operator must explicitly accept).
        include_tenant:   Reserved for a future per-tenant breakdown. Off by
                          default (RD-3 — unbounded cardinality).
        depth_poll:       Whether to register the DLQ depth gauge at all.
    """

    enabled: bool = True
    meter_name: str = "varco"
    depth_by_channel: bool = False
    include_tenant: bool = False
    depth_poll: bool = True


# ── Push-based instruments (module level — safe before MeterProvider setup) ────


class _PatchableMetric(Metric):
    """
    ``Metric`` subclass with no ``__slots__`` of its own.

    ``Metric`` uses ``__slots__`` to keep per-instance memory small — but that
    also makes ``monkeypatch.setattr("...._dlq_pushed.add", fn)`` fail with
    ``AttributeError: attribute 'add' is read-only`` (no instance ``__dict__``
    to shadow the class method in). Module-level reliability metrics are a
    handful of singletons, not a hot allocation path, so the ``__slots__``
    memory optimization doesn't matter here — and this subclass restores a
    ``__dict__`` (Python's normal behaviour when a subclass declares no
    ``__slots__`` of its own) so tests can patch ``.add`` per-instance.
    """


_dlq_pushed = _PatchableMetric(
    "varco.dlq.pushed", kind="counter", description="Dead letters pushed to a DLQ"
)
_dlq_redriven = _PatchableMetric(
    "varco.dlq.redriven", kind="counter", description="Dead letters redriven from a DLQ"
)
_outbox_published = _PatchableMetric(
    "varco.outbox.published", kind="counter", description="Outbox entries published"
)
_outbox_failures = _PatchableMetric(
    "varco.outbox.failures", kind="counter", description="Outbox relay failures"
)
_outbox_dead_lettered = _PatchableMetric(
    "varco.outbox.dead_lettered",
    kind="counter",
    description="Outbox entries dead-lettered",
)
_audit_writes = _PatchableMetric(
    "varco.audit.writes", kind="counter", description="Audit entries written"
)
_job_lease_reaps = _PatchableMetric(
    "varco.job.lease_reaps", kind="counter", description="Job leases reaped as expired"
)


def _safe(fn: Any, *args: Any, **kwargs: Any) -> None:
    """Call a ``Metric`` recording method, swallowing any exception.

    A metrics instrument failing must never break the call site it
    instruments — the DLQ push path in particular must not raise (see the
    module docstring's DESIGN block).
    """
    try:
        fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - metrics must never propagate
        _logger.debug("Reliability metric recording failed: %s", exc)


def record_dlq_push(*, source: str, channel: str, ok: bool) -> None:
    """Record one DLQ push attempt. ``ok=False`` marks a swallowed push failure."""
    _safe(
        _dlq_pushed.add, source=source, channel=channel, status="ok" if ok else "failed"
    )


def record_dlq_redrive(*, source: str, ok: bool) -> None:
    """Record one DLQ redrive outcome (wired by Phase 4's ``DlqRedriver``)."""
    _safe(_dlq_redriven.add, source=source, status="ok" if ok else "failed")


def record_outbox_published(*, channel: str) -> None:
    """Record one successfully published outbox entry."""
    _safe(_outbox_published.add, channel=channel)


def record_outbox_failure(*, reason: str) -> None:
    """Record one outbox relay failure. ``reason`` is ``"deserialize"`` or ``"publish"``."""
    _safe(_outbox_failures.add, reason=reason)


def record_outbox_dead_lettered() -> None:
    """Record one outbox entry that exhausted retries and was dead-lettered."""
    _safe(_outbox_dead_lettered.add)


def record_audit_write(*, action: str, entity_type: str, ok: bool) -> None:
    """Record one audit-entry write attempt."""
    _safe(
        _audit_writes.add,
        action=action,
        entity_type=entity_type,
        status="ok" if ok else "failed",
    )


def record_job_lease_reap(*, count: int) -> None:
    """Record ``count`` job leases reaped as expired in one poll cycle."""
    if count <= 0:
        return
    _safe(_job_lease_reaps.add, count)


# ── Pull-based DLQ depth gauge ──────────────────────────────────────────────────
#
# One observable gauge, re-registered only when the active OTel meter changes
# (a new MeterProvider — e.g. per test, or a real provider replacing the
# bootstrap no-op one). A dict of named targets means a second
# install_reliability_metrics() call for the same dlq_name simply replaces the
# dict entry rather than creating a second gauge instrument — the mechanism
# behind "install called twice is idempotent".

_depth_targets: dict[str, AbstractDeadLetterQueue] = {}
_depth_registered_meter: Any = None
_depth_by_channel_names: set[str] = set()

_outbox_repo: OutboxRepository | None = None
_outbox_registered_meter: Any = None
_outbox_count_pending_disabled = False
_outbox_oldest_pending_disabled = False


def _dlq_depth_callback(_options: Any) -> list[Observation]:
    observations: list[Observation] = []
    for name, dlq in list(_depth_targets.items()):
        try:
            count = _run_sync(dlq.count())
        except Exception as exc:  # noqa: BLE001 - a callback must never raise
            _logger.debug("DLQ depth gauge: count() failed for %r: %s", name, exc)
            continue
        if count < 0:
            # RD-3: Kafka's documented -1 must never be reported literally.
            continue
        observations.append(Observation(count, attributes={"dlq": name}))

        if name in _depth_by_channel_names:
            try:
                by_channel = _run_sync(dlq.count_by_channel())
            except NotImplementedError:
                pass
            except Exception as exc:  # noqa: BLE001
                _logger.debug("DLQ depth-by-channel gauge failed for %r: %s", name, exc)
            else:
                for channel, channel_count in by_channel.items():
                    if channel_count < 0:
                        continue
                    observations.append(
                        Observation(
                            channel_count, attributes={"dlq": name, "channel": channel}
                        )
                    )
    return observations


def _outbox_pending_callback(_options: Any) -> list[Observation]:
    global _outbox_count_pending_disabled
    if _outbox_repo is None or _outbox_count_pending_disabled:
        return []
    try:
        count = _run_sync(_outbox_repo.count_pending())
    except NotImplementedError:
        _outbox_count_pending_disabled = True
        _logger.info(
            "%s does not implement count_pending() — disabling varco.outbox.pending gauge.",
            type(_outbox_repo).__name__,
        )
        return []
    except Exception as exc:  # noqa: BLE001
        _logger.debug("Outbox pending gauge failed: %s", exc)
        return []
    return [Observation(count)]


def _outbox_lag_callback(_options: Any) -> list[Observation]:
    global _outbox_oldest_pending_disabled
    if _outbox_repo is None or _outbox_oldest_pending_disabled:
        return []
    try:
        oldest = _run_sync(_outbox_repo.oldest_pending_at())
    except NotImplementedError:
        _outbox_oldest_pending_disabled = True
        _logger.info(
            "%s does not implement oldest_pending_at() — disabling varco.outbox.lag_seconds gauge.",
            type(_outbox_repo).__name__,
        )
        return []
    except Exception as exc:  # noqa: BLE001
        _logger.debug("Outbox lag gauge failed: %s", exc)
        return []
    if oldest is None:
        return []
    from datetime import datetime, timezone

    lag = (datetime.now(tz=UTC) - oldest).total_seconds()
    return [Observation(max(lag, 0.0))]


def install_reliability_metrics(
    *,
    dlq: AbstractDeadLetterQueue | None = None,
    dlq_name: str | None = None,
    outbox_repo: OutboxRepository | None = None,
    config: ReliabilityMetricsConfig | None = None,
) -> None:
    """
    Install the reliability metrics pack for a live DLQ and/or outbox repo.

    Idempotent: calling this twice for the same ``dlq_name`` replaces the
    tracked instance rather than registering a second gauge. Calling it a
    second time on a *different* ``MeterProvider`` (e.g. once per test)
    re-registers the gauge on the new provider's meter instead of silently
    reporting to the stale one.

    Despite the verb, this takes **no container**, mutates module-level
    globals, and is deliberately not a scanned ``@Configuration`` — see
    CLAUDE.md's "DI wiring verb taxonomy" for how this differs from
    providify's ``container.install(SomeConfiguration)`` (the same shape
    as ``install_cache_metrics``).

    Args:
        dlq:         A live DLQ instance to observe (``varco.dlq.depth``).
        dlq_name:    Name tag for the ``dlq`` attribute. Defaults to
                     ``type(dlq).__name__``.
        outbox_repo: A live ``OutboxRepository`` to observe
                     (``varco.outbox.pending`` / ``varco.outbox.lag_seconds``).
        config:      ``ReliabilityMetricsConfig``. Defaults to
                     ``ReliabilityMetricsConfig()``.

    Edge cases:
        - ``config.enabled=False`` → no-op.
        - Neither ``dlq`` nor ``outbox_repo`` given → registers nothing.
    """
    global _depth_registered_meter, _outbox_repo, _outbox_registered_meter
    global _outbox_count_pending_disabled, _outbox_oldest_pending_disabled
    global _owner_loop

    cfg = config or ReliabilityMetricsConfig()
    if not cfg.enabled:
        return

    # Capture the loop the observed clients belong to (best effort — the
    # installer may legitimately run before the loop starts, in which case
    # _run_sync keeps its historical fallback). This is what lets the OTel
    # exporter thread drive a loop-bound async client at collection time.
    try:
        _owner_loop = asyncio.get_running_loop()
    except RuntimeError:
        _owner_loop = None

    meter = otel_metrics.get_meter(cfg.meter_name)

    if dlq is not None and cfg.depth_poll:
        meter_changed = meter is not _depth_registered_meter
        if meter_changed:
            # A new MeterProvider (e.g. a fresh test, or the real provider
            # replacing the bootstrap no-op one) means every previously
            # tracked target belonged to a reader that no longer exists —
            # start the target set over rather than reporting stale entries
            # against the new provider.
            _depth_targets.clear()
            _depth_by_channel_names.clear()
        name = dlq_name or type(dlq).__name__
        _depth_targets[name] = dlq
        if cfg.depth_by_channel:
            _depth_by_channel_names.add(name)
        if meter_changed:
            meter.create_observable_gauge(
                name="varco.dlq.depth",
                callbacks=[wrap_gauge_callback(_dlq_depth_callback)],
                description="Current unacknowledged entry count per DLQ instance",
                unit="1",
            )
            _depth_registered_meter = meter

    if outbox_repo is not None:
        # A new repo instance or a new MeterProvider resets the self-disable
        # flags — the operator may be swapping in a repository that DOES
        # implement these, or this is a fresh test/process.
        if outbox_repo is not _outbox_repo or meter is not _outbox_registered_meter:
            _outbox_count_pending_disabled = False
            _outbox_oldest_pending_disabled = False
        _outbox_repo = outbox_repo
        if meter is not _outbox_registered_meter:
            meter.create_observable_gauge(
                name="varco.outbox.pending",
                callbacks=[wrap_gauge_callback(_outbox_pending_callback)],
                description="Pending (unpublished) outbox entries",
                unit="1",
            )
            meter.create_observable_gauge(
                name="varco.outbox.lag_seconds",
                callbacks=[wrap_gauge_callback(_outbox_lag_callback)],
                description="Age of the oldest pending outbox entry, in seconds",
                unit="s",
            )
            _outbox_registered_meter = meter


__all__ = [
    "ReliabilityMetricsConfig",
    "install_reliability_metrics",
    "record_audit_write",
    "record_dlq_push",
    "record_dlq_redrive",
    "record_job_lease_reap",
    "record_outbox_dead_lettered",
    "record_outbox_failure",
    "record_outbox_published",
]
