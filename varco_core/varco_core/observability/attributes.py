"""
varco_core.observability.attributes
====================================
Process-wide global attribute registry (Plan 004, deliverable B).

A ``GlobalAttributes`` registry whose entries are stamped on **every span**
and **every metric measurement** (counter / up-down counter / histogram /
observable gauge) produced in this process.  Supports static values,
env-var-sourced values, and callable providers for values not known at
bootstrap (a pod name injected by a sidecar after start, a feature-flag
cohort, a config generation counter).

Import direction is strictly one-way — this module imports **only stdlib +
the OTel API**, never any other ``varco_core.observability`` module.

Resource attributes vs. this registry — which one do I want?
--------------------------------------------------------------
| | OTel Resource (``OtelConfig.extra_resource_attrs``) | This registry |
|---|---|---|
| Cost | free — no per-emission work | dict merge per emission; each key
  becomes a metric **label** ⇒ one series per distinct value per metric |
| Known when? | must be known at provider construction (bootstrap) | can be
  registered/updated at any time; providers evaluated lazily |
| Prometheus pull | ``target_info``, not a per-series label | a real label
  on every series |

*Static process identity* (``k8s.pod.name``, ``deployment.environment``, a
Helm release) → put it in ``OtelConfig.extra_resource_attrs``.  *Values not
known at bootstrap, mutable during the process lifetime, or that the backend
must filter/group by as a label* → this registry.

DESIGN: registry does NOT auto-copy into the Resource, and vice versa
    ✅ No silent double-labelling (the same key as both a resource attr and a
       per-series label doubles storage and confuses ``group by`` queries).
    ✅ The user chooses cardinality cost explicitly.
    ❌ Someone who wants both must list the key in both places — mitigated by
       ``OtelConfig.promote_global_attrs_to_resource=True`` (merges the
       static part of the registry into the Resource at bootstrap).

DESIGN: callable providers instead of "static dict only"
    ✅ Covers values not known at bootstrap and values that change over the
       process lifetime.
    ❌ A provider on the hot path can be slow — mitigated by the
       ``cache_ttl=None`` default (evaluate once, memoise forever) and a loud
       doc warning that providers must be non-blocking and never do I/O.

DESIGN: wrap the instrument in a proxy at creation time (single choke point)
    Instruments are created lazily and cached by ``metrics.py`` / ``metric.py``
    / ``helpers.py``.  Wrapping at the single creation choke point (rather
    than at each ``@counter``/``@histogram``/``Metric.add`` call site) covers
    every path including raw-instrument holders and observable gauges, with
    one merge implementation.
    ✅ Zero overhead for `apply_to_metrics=False` — ``wrap_instrument``
       returns the raw instrument.
    ❌ ``isinstance(create_counter(...), opentelemetry.metrics.Counter)``
       becomes ``False`` — mitigated by ``.unwrap()`` and ``__getattr__``
       delegation for duck-typed use.

Thread safety:  ✅ Mutations (``set``/``add``/``remove``/``register_provider``/
                   ``unregister_provider``/``clear``) take a module-level
                   ``threading.Lock`` and rebuild an immutable
                   ``MappingProxyType`` snapshot.  Readers (``snapshot()``)
                   take the lock only to check/refresh the cache — the
                   returned mapping itself requires no locking to read.
                   ``threading.Lock`` (not ``asyncio.Lock``) is used
                   deliberately — this is sync code called from both plain
                   threads and coroutines with no running event loop
                   guarantee; the repo's "create locks lazily" rule targets
                   ``asyncio.Lock`` specifically, and a module-level
                   ``threading.Lock`` needs no event loop to construct.
Async safety:   ✅ All public functions are synchronous; safe to call from
                   sync or async code without ``await``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

_logger = logging.getLogger(__name__)

# ── Type aliases ─────────────────────────────────────────────────────────────

AttributeValue = str | bool | int | float
AttributeProvider = Callable[[], "Mapping[str, AttributeValue] | None"]


# ── Provider bookkeeping (internal) ─────────────────────────────────────────


@dataclass
class _ProviderEntry:
    provider: AttributeProvider
    cache_ttl: float | None
    last_evaluated: float | None = None
    cached_value: dict[str, AttributeValue] = field(default_factory=dict)
    warned: bool = False


# ── GlobalAttributes ─────────────────────────────────────────────────────────


class GlobalAttributes:
    """
    Process-wide registry of attributes stamped on every span and metric.

    Combines static key/value pairs with named callable *providers* into a
    single merged, cached, read-only snapshot.  See the module docstring for
    the Resource-vs-registry decision and the provider caching semantics.

    Thread safety:  ✅ All mutating methods take an internal
                       ``threading.Lock``.  ``snapshot()`` also takes the
                       lock (to check/refresh the cache) but the returned
                       ``MappingProxyType`` is safe to read from any thread
                       without further locking.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._static: dict[str, AttributeValue] = {}
        self._providers: dict[str, _ProviderEntry] = {}
        self._generation = 0
        self._snapshot_generation = -1
        self._snapshot_cache: Mapping[str, AttributeValue] = MappingProxyType({})

    # ── Mutation ─────────────────────────────────────────────────────────────

    def set(
        self,
        mapping: Mapping[str, AttributeValue] | None = None,
        /,
        **attrs: AttributeValue,
    ) -> None:
        """
        Merge ``mapping`` and/or ``**attrs`` into the static attribute set.

        Existing keys not present in this call are left untouched — this is
        a merge/update, not a full replace.  Use ``clear()`` to reset.
        """
        merged: dict[str, AttributeValue] = dict(mapping or {})
        merged.update(attrs)
        with self._lock:
            self._static.update(merged)
            self._generation += 1

    def add(self, key: str, value: AttributeValue) -> None:
        """Set a single static attribute."""
        with self._lock:
            self._static[key] = value
            self._generation += 1

    def remove(self, key: str) -> None:
        """Remove a single static attribute (no-op if absent)."""
        with self._lock:
            self._static.pop(key, None)
            self._generation += 1

    def clear(self) -> None:
        """Remove all static attributes AND all registered providers."""
        with self._lock:
            self._static = {}
            self._providers = {}
            self._generation += 1

    def register_provider(
        self,
        provider: AttributeProvider,
        *,
        name: str,
        cache_ttl: float | None = None,
    ) -> None:
        """
        Register a callable provider contributing attributes to every snapshot.

        Args:
            provider: Zero-arg callable returning a ``Mapping[str, AttributeValue]``
                or ``None``.  Must be non-blocking — never do I/O.
            name: Unique provider name, used for de-duplication, unregistration,
                and the "raised once" warning log.
            cache_ttl: ``None`` (default) — evaluated once, memoised forever.
                ``0.0`` — evaluated on every ``snapshot()`` rebuild.
                ``N > 0`` — re-evaluated once the cached value is older than
                ``N`` seconds.

        Edge cases:
            - A provider that raises is logged **once** (per provider name,
              at ``WARNING``) and then treated as contributing nothing —
              never breaks the emission path.
            - A provider returning ``None`` values for some keys → those
              keys are dropped (OTel forbids ``None`` attribute values).
            - A provider returning a non-mapping → skipped, logged once.
        """
        with self._lock:
            self._providers[name] = _ProviderEntry(
                provider=provider, cache_ttl=cache_ttl
            )
            self._generation += 1

    def unregister_provider(self, name: str) -> None:
        """Remove a previously registered provider (no-op if absent)."""
        with self._lock:
            self._providers.pop(name, None)
            self._generation += 1

    # ── Read ─────────────────────────────────────────────────────────────────

    def snapshot(self) -> Mapping[str, AttributeValue]:
        """
        Return the merged, cached, read-only attribute snapshot.

        Identity-stable across repeated calls as long as nothing has
        mutated (no ``set``/``add``/``remove``/``register_provider``/
        ``unregister_provider``/``clear`` call) and no TTL'd provider is due
        for re-evaluation — this makes ``snapshot()`` cheap to call on every
        span/metric emission in the steady state (one generation compare
        plus a per-provider timestamp compare).

        Returns:
            An immutable ``MappingProxyType`` — mutating it raises
            ``TypeError``.
        """
        with self._lock:
            now = time.monotonic()
            need_rebuild = self._generation != self._snapshot_generation
            if not need_rebuild:
                for entry in self._providers.values():
                    if self._provider_due(entry, now):
                        need_rebuild = True
                        break

            if not need_rebuild:
                return self._snapshot_cache

            merged: dict[str, AttributeValue] = dict(self._static)
            for name, entry in self._providers.items():
                if self._provider_due(entry, now):
                    entry.cached_value = self._evaluate_provider(name, entry)
                    entry.last_evaluated = now
                merged.update(entry.cached_value)

            self._snapshot_cache = MappingProxyType(merged)
            self._snapshot_generation = self._generation
            return self._snapshot_cache

    @staticmethod
    def _provider_due(entry: _ProviderEntry, now: float) -> bool:
        if entry.last_evaluated is None:
            return True
        if entry.cache_ttl is None:
            return False
        return (now - entry.last_evaluated) >= entry.cache_ttl

    def _evaluate_provider(
        self, name: str, entry: _ProviderEntry
    ) -> dict[str, AttributeValue]:
        try:
            result = entry.provider()
        except Exception:
            if not entry.warned:
                _logger.warning(
                    "varco.observability.attributes: provider %r raised; skipping",
                    name,
                    exc_info=True,
                )
                entry.warned = True
            return {}

        if result is None:
            return {}
        if not isinstance(result, Mapping):
            if not entry.warned:
                _logger.warning(
                    "varco.observability.attributes: provider %r returned non-mapping %r; skipping",
                    name,
                    type(result),
                )
                entry.warned = True
            return {}
        return {k: v for k, v in result.items() if v is not None}


# ── Module singleton + free functions ───────────────────────────────────────

_global_registry = GlobalAttributes()
_apply_to_spans = True
_apply_to_metrics = True
_env_loaded = False
_env_load_lock = threading.Lock()


def global_attributes() -> GlobalAttributes:
    """Return the process-wide ``GlobalAttributes`` singleton."""
    return _global_registry


def set_global_attributes(
    mapping: Mapping[str, AttributeValue] | None = None, /, **attrs: AttributeValue
) -> None:
    """Merge attributes into the process-wide registry. See ``GlobalAttributes.set``."""
    _global_registry.set(mapping, **attrs)


def register_global_attribute_provider(
    provider: AttributeProvider, *, name: str, cache_ttl: float | None = None
) -> None:
    """Register a provider on the process-wide registry. See ``GlobalAttributes.register_provider``."""
    _global_registry.register_provider(provider, name=name, cache_ttl=cache_ttl)


def unregister_global_attribute_provider(name: str) -> None:
    """Unregister a provider on the process-wide registry."""
    _global_registry.unregister_provider(name)


def current_global_attributes() -> Mapping[str, AttributeValue]:
    """
    Return the merged, cached snapshot from the process-wide registry.

    Lazily triggers ``load_global_attributes_from_env()`` on first use
    (idempotent, guarded by a flag) so that no-DI users (decorators used
    without ``OtelConfiguration``) still pick up ``VARCO_OTEL_GLOBAL_ATTRS*``
    env vars with zero code.
    """
    _ensure_env_loaded()
    return _global_registry.snapshot()


def clear_global_attributes() -> None:
    """
    Test helper: reset ALL process-wide global-attribute state — static
    values, providers, the spans/metrics apply toggles, and the lazy
    env-load flag.

    Global mutable state hygiene (Plan 004 "Risks" section) — call this in
    an autouse fixture around every test that touches the registry.
    """
    global _apply_to_spans, _apply_to_metrics, _env_loaded
    _global_registry.clear()
    _apply_to_spans = True
    _apply_to_metrics = True
    _env_loaded = False


def configure_global_attributes(
    *, apply_to_spans: bool | None = None, apply_to_metrics: bool | None = None
) -> None:
    """
    Toggle whether the global attribute registry is applied to spans and/or
    metrics.  Both default to ``True`` (unset arguments leave the current
    value unchanged).
    """
    global _apply_to_spans, _apply_to_metrics
    if apply_to_spans is not None:
        _apply_to_spans = apply_to_spans
    if apply_to_metrics is not None:
        _apply_to_metrics = apply_to_metrics


def apply_to_spans() -> bool:
    """Return whether the global attribute registry is applied to spans."""
    return _apply_to_spans


def apply_to_metrics() -> bool:
    """Return whether the global attribute registry is applied to metric measurements."""
    return _apply_to_metrics


def _ensure_env_loaded() -> None:
    global _env_loaded
    if _env_loaded:
        return
    with _env_load_lock:
        if _env_loaded:
            return
        load_global_attributes_from_env()
        _env_loaded = True


# ── Env-var bootstrap ────────────────────────────────────────────────────────

_TRUE_TOKENS = {"true", "1", "yes", "on"}
_FALSE_TOKENS = {"false", "0", "no", "off"}


def _parse_bool_env(raw: str, default: bool, var_name: str) -> bool:
    token = raw.strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    _logger.warning(
        "varco.observability.attributes: invalid boolean value %r for %s; falling back to default %s",
        raw,
        var_name,
        default,
    )
    return default


def _split_csv(raw: str) -> list[str]:
    return [t for t in (part.strip() for part in raw.split(",")) if t]


def _parse_kv_token(token: str) -> tuple[str, str] | None:
    if "=" not in token:
        return None
    key, _, value = token.partition("=")
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    return key, value


def load_global_attributes_from_env(environ: Mapping[str, str] | None = None) -> None:
    """
    Populate the process-wide registry from ``VARCO_OTEL_GLOBAL_ATTRS*`` env vars.

    Reads:
        - ``VARCO_OTEL_GLOBAL_ATTRS`` — literal ``key=value`` pairs,
          comma-separated (e.g. ``"k8s.pod.name=orders-7d9,service.release=blue"``).
        - ``VARCO_OTEL_GLOBAL_ATTR_ENV`` — ``key=ENV_VAR_NAME`` pairs; the
          value is looked up from another env var *lazily at call time*
          (e.g. ``"k8s.pod.name=POD_NAME"`` reads ``os.environ["POD_NAME"]``).
        - ``VARCO_OTEL_GLOBAL_ATTRS_SPANS`` / ``VARCO_OTEL_GLOBAL_ATTRS_METRICS``
          — per-signal kill switches (default ``true`` both), applied via
          ``configure_global_attributes()``.

    Args:
        environ: Mapping to read from instead of ``os.environ`` (mainly for
            tests).

    Edge cases:
        - A malformed token (no ``=``) is logged at ``WARNING`` and skipped
          — never raises.
        - ``VARCO_OTEL_GLOBAL_ATTR_ENV`` pointing at an unset env var → the
          key is simply absent from the registry (not an error).

    Thread safety:  ✅ Delegates to ``GlobalAttributes.add`` — safe to call
                       concurrently.
    """
    env = environ if environ is not None else os.environ
    registry = global_attributes()

    literal_attrs: dict[str, AttributeValue] = {}

    for token in _split_csv(env.get("VARCO_OTEL_GLOBAL_ATTRS", "")):
        kv = _parse_kv_token(token)
        if kv is None:
            _logger.warning(
                "varco.observability.attributes: malformed VARCO_OTEL_GLOBAL_ATTRS token %r; skipping",
                token,
            )
            continue
        key, value = kv
        literal_attrs[key] = value

    for token in _split_csv(env.get("VARCO_OTEL_GLOBAL_ATTR_ENV", "")):
        kv = _parse_kv_token(token)
        if kv is None:
            _logger.warning(
                "varco.observability.attributes: malformed VARCO_OTEL_GLOBAL_ATTR_ENV token %r; skipping",
                token,
            )
            continue
        key, env_var_name = kv
        value = env.get(env_var_name)
        if value is not None:
            literal_attrs[key] = value

    for key, value in literal_attrs.items():
        registry.add(key, value)

    spans_raw = env.get("VARCO_OTEL_GLOBAL_ATTRS_SPANS")
    metrics_raw = env.get("VARCO_OTEL_GLOBAL_ATTRS_METRICS")
    apply_spans = (
        _parse_bool_env(spans_raw, True, "VARCO_OTEL_GLOBAL_ATTRS_SPANS")
        if spans_raw is not None
        else None
    )
    apply_metrics = (
        _parse_bool_env(metrics_raw, True, "VARCO_OTEL_GLOBAL_ATTRS_METRICS")
        if metrics_raw is not None
        else None
    )
    if apply_spans is not None or apply_metrics is not None:
        configure_global_attributes(
            apply_to_spans=apply_spans, apply_to_metrics=apply_metrics
        )


# ── Metric instrument proxy ──────────────────────────────────────────────────


class GlobalAttrInstrument:
    """
    Transparent proxy that merges global attributes into every measurement.

    Wraps any OTel synchronous instrument (``Counter``, ``UpDownCounter``,
    ``Histogram``) exposing ``.add()``/``.record()``.  See the module
    docstring's "wrap at creation time" DESIGN block for why this is the
    single interception point instead of per-call-site merging.

    Args:
        inner: The raw OTel instrument to wrap.

    Edge cases:
        - Empty registry → the caller's ``attributes`` dict is passed
          through **unchanged by identity** (not copied) — zero overhead
          when no global attributes are registered.
        - Key collision → the **caller's** attribute value wins (the
          instrument call site knows the specific measurement's context
          better than a process-wide default).

    Thread safety:  ✅ Stateless proxy — delegates to the (thread-safe) OTel
                       instrument.
    """

    __slots__ = ("_inner",)

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def add(
        self,
        amount: Any,
        attributes: Mapping[str, Any] | None = None,
        context: Any = None,
    ) -> None:
        g = current_global_attributes()
        if g:
            attributes = {**g, **(attributes or {})}
        self._inner.add(amount, attributes=attributes, context=context)

    def record(
        self,
        value: Any,
        attributes: Mapping[str, Any] | None = None,
        context: Any = None,
    ) -> None:
        g = current_global_attributes()
        if g:
            attributes = {**g, **(attributes or {})}
        self._inner.record(value, attributes=attributes, context=context)

    def unwrap(self) -> Any:
        """Return the raw, unwrapped OTel instrument."""
        return self._inner

    def __getattr__(self, item: str) -> Any:
        # Delegate everything else (e.g. duck-typed access) to the raw
        # instrument — only .add()/.record() are intercepted above.
        return getattr(self._inner, item)


def wrap_instrument(instrument: Any) -> Any:
    """
    Wrap ``instrument`` in a ``GlobalAttrInstrument`` proxy, unless
    ``apply_to_metrics()`` is ``False`` — in which case the raw instrument
    is returned unchanged (literally zero overhead for users who opt out).
    """
    if not apply_to_metrics():
        return instrument
    return GlobalAttrInstrument(instrument)


def wrap_gauge_callback(callback: Callable[..., Any]) -> Callable[..., Any]:
    """
    Wrap an observable-gauge callback so global attributes are merged into
    every yielded ``opentelemetry.metrics.Observation``.

    Args:
        callback: A zero- or one-arg callable (the OTel SDK calls it with an
            ``ObservableCallbackOptions`` positional argument) returning an
            iterable of ``Observation``.

    Returns:
        A wrapped callable with the same call signature.  Returns
        ``callback`` unchanged when ``apply_to_metrics()`` is ``False``.
    """
    if not apply_to_metrics():
        return callback

    def wrapped(options: Any = None) -> list[Any]:
        from opentelemetry.metrics import Observation

        observations = callback(options)
        g = current_global_attributes()
        if not g:
            return list(observations)

        merged_observations = []
        for obs in observations:
            attrs = dict(obs.attributes or {})
            merged_observations.append(
                Observation(obs.value, attributes={**g, **attrs})
            )
        return merged_observations

    return wrapped


__all__ = [
    "AttributeValue",
    "AttributeProvider",
    "GlobalAttributes",
    "global_attributes",
    "set_global_attributes",
    "register_global_attribute_provider",
    "unregister_global_attribute_provider",
    "current_global_attributes",
    "clear_global_attributes",
    "configure_global_attributes",
    "apply_to_spans",
    "apply_to_metrics",
    "load_global_attributes_from_env",
    "GlobalAttrInstrument",
    "wrap_instrument",
    "wrap_gauge_callback",
]
