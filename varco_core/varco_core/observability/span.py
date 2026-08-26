"""
varco_core.observability.span
==============================
``@span`` — OpenTelemetry tracing decorator for sync and async callables.

Wraps any function or method in an OTel span, automatically naming it from
the function's ``__qualname__`` unless overridden via ``SpanConfig``.  The
active correlation ID (from ``varco_core.tracing``) is written as a span
attribute so that traces and structured logs can be joined by correlation ID
in any observability backend.

Usage — bare decorator (span name = function qualname)::

    from varco_core.observability import span

    @span
    async def place_order(order_id: UUID) -> Order:
        ...

Usage — configured::

    from varco_core.observability import span, SpanConfig

    @span(SpanConfig(name="order.place", attributes={"db": "postgresql"}))
    async def place_order(order_id: UUID) -> Order:
        ...

Composition with resilience decorators::

    @span(SpanConfig(name="payment.charge"))   # outermost — spans the full call
    @retry(RetryPolicy(max_attempts=3))
    @circuit_breaker(CircuitBreakerConfig(failure_threshold=5))
    async def charge_card(amount: float) -> TransactionId:
        ...

    # Execution order (outermost first):
    #   span → retry (all attempts) → breaker → actual call
    #
    # Keep @span outermost so the span duration includes retries and backoff.

DESIGN: decorator supports both bare (@span) and configured (@span(config)) forms
    ✅ Single name — no @span vs @span_with_config split.
    ✅ Consistent with Python stdlib patterns (e.g. functools.wraps).
    ✅ Type-checker sees the right return type in both call forms via overloads.
    ❌ Implementation must detect whether the first argument is a callable
       (bare form) or a SpanConfig (configured form) — slightly tricky.

DESIGN: correlation ID → span attribute bridge
    ✅ Joins traces with structured log lines that carry ``correlation_id``.
    ✅ Zero coupling — reads from ContextVar; no explicit parameter threading.
    ❌ If correlation context is not set the attribute is omitted (not an error).
       This is intentional — not every code path runs inside a request scope.

Thread safety:  ✅ SpanConfig is a frozen dataclass — safe to share across threads.
Async safety:   ✅ Async wrapper uses ``async with`` on the span context manager.
                   Each asyncio task inherits its own OTel context via ContextVar.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar, overload

from opentelemetry import trace
from opentelemetry.trace import StatusCode

from varco_core.observability.attributes import (
    apply_to_spans,
    current_global_attributes,
)
from varco_core.observability.params import (
    CapturePlan,
    ParamCaptureConfig,
    build_capture_plan,
    capture_enabled,
    param_capture_defaults,
)
from varco_core.tracing import current_correlation_id

_logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])
_AsyncF = TypeVar("_AsyncF", bound=Callable[..., Awaitable[Any]])


# ── SpanConfig ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SpanConfig:
    """
    Immutable configuration for the ``@span`` decorator.

    Args:
        name:
            Span name shown in the trace UI.  If ``None``, defaults to the
            decorated function's ``__qualname__``
            (e.g. ``"OrderService.create"``).
        tracer_name:
            OTel instrumentation library name passed to
            ``opentelemetry.trace.get_tracer()``.  Use the default (``"varco"``)
            unless you need to distinguish spans by library origin.
        attributes:
            Static key-value pairs attached to the span at creation time.
            Values must be strings (OTel attribute type constraint).
            Dynamic attributes (e.g. ``order_id``) should be set inside the
            function body via ``opentelemetry.trace.get_current_span().set_attribute()``.
        record_exception:
            When ``True`` (default), any exception that propagates out of the
            decorated function is recorded on the span via
            ``span.record_exception(exc)``.  The exception is always
            re-raised — this decorator never swallows exceptions.
        set_status_on_error:
            When ``True`` (default), sets the span status to ``ERROR`` if an
            exception propagates.  Only meaningful when ``record_exception``
            is also ``True``.
        capture_params:
            Quick per-decorator toggle for automatic parameter capture
            (Plan 004).  ``None`` (default) defers to ``param_capture.enabled``
            if set, else the process-wide ``capture_enabled()`` /
            ``VARCO_OTEL_CAPTURE_PARAMS`` default.  ``True``/``False`` here
            always wins — the most specific setting in the precedence chain.
        param_capture:
            Full structural override (``ParamCaptureConfig``) — prefix,
            redaction patterns, value rendering mode, limits, etc.  ``None``
            (default) uses the process-wide ``param_capture_defaults()``.

    Edge cases:
        - ``name=None`` with a lambda → ``__qualname__`` is ``"<lambda>"`` which
          is not very useful; pass an explicit name for lambdas.
        - Static ``attributes`` keys must not clash with OTel semantic
          conventions reserved keys (e.g. ``"http.method"``) unless you
          intend to set them.

    Thread safety:  ✅ Frozen dataclass — immutable, safe to share.
    """

    # None means "use the function's __qualname__" — avoids repeating the name
    # when the function name is already descriptive.
    name: str | None = None

    # "varco" groups all framework-level spans under one instrumentation scope
    # in backends like Tempo / Jaeger.  Override for per-service scope names.
    tracer_name: str = "varco"

    # Static attributes added at span creation time.  Dynamic values (request
    # parameters, entity IDs, etc.) should be set inside the function body.
    attributes: dict[str, str] = field(default_factory=dict)

    # Always record exceptions — silent failures are hard to debug in prod.
    record_exception: bool = True
    set_status_on_error: bool = True

    # Plan 004 (A) — automatic parameter capture precedence, most specific
    # first: capture_params > param_capture.enabled > process default.
    capture_params: bool | None = None
    param_capture: ParamCaptureConfig | None = None


# ── @span decorator ───────────────────────────────────────────────────────────


# Overloads so the type checker knows the return type in both call forms:
#   @span          → F (same type as the decorated function)
#   @span(config)  → Callable[[F], F]
@overload
def span(func: _F) -> _F: ...


@overload
def span(config: SpanConfig) -> Callable[[_F], _F]: ...


def span(  # type: ignore[misc]
    func: _F | SpanConfig | None = None,
    config: SpanConfig | None = None,
) -> _F | Callable[[_F], _F]:
    """
    Wrap a sync or async callable in an OpenTelemetry tracing span.

    Supports two call forms::

        @span                              # bare — name = function.__qualname__
        async def my_fn(): ...

        @span(SpanConfig(name="my.span")) # configured
        async def my_fn(): ...

    The active correlation ID (``varco_core.tracing.current_correlation_id()``)
    is automatically set as the ``correlation_id`` span attribute when present.
    This bridges structured log lines (which carry the correlation ID) with
    distributed traces so operators can pivot between the two in their
    observability backend.

    Args:
        func:   The function to decorate (bare form) or a ``SpanConfig``
                (configured form).  Do not pass this explicitly — it is
                filled in by Python's decorator machinery.
        config: Unused in the public API; reserved for future positional
                config support.

    Returns:
        The decorated function (bare form) or a decorator factory
        (configured form).  The wrapper preserves the original function's
        ``__name__``, ``__qualname__``, ``__doc__``, and ``__annotations__``
        via ``functools.wraps``.

    Raises:
        Exception: Any exception raised by the wrapped function — this decorator
            never swallows exceptions.  When ``SpanConfig.record_exception`` is
            ``True`` the exception is recorded on the span before re-raising.

    Edge cases:
        - No active ``TracerProvider`` (e.g. in unit tests that skip DI) →
          OTel returns a no-op tracer; the function still runs normally.
        - Nested ``@span`` decorators → each creates its own child span,
          forming a parent-child hierarchy automatically via OTel context
          propagation.
        - ``current_correlation_id()`` returns ``None`` → the attribute is
          simply not set on the span (no error).

    Thread safety:  ✅ Wrapper is stateless; ``SpanConfig`` is frozen.
    Async safety:   ✅ Async wrapper uses ``async with`` — span context is
                       propagated correctly across ``await`` points.
    """
    # ── Detect call form ──────────────────────────────────────────────────────

    if callable(func) and not isinstance(func, SpanConfig):
        # Bare form: @span — func is the actual function being decorated.
        return _make_wrapper(func, SpanConfig())

    # Configured form: @span(SpanConfig(...)) — func IS the SpanConfig.
    effective_config: SpanConfig = func if isinstance(func, SpanConfig) else SpanConfig()

    def decorator(fn: _F) -> _F:
        return _make_wrapper(fn, effective_config)

    return decorator


# ── Shared attribute merge helper (Plan 004) ────────────────────────────────


def build_span_attributes(
    static_attributes: Mapping[str, Any] | None = None,
    captured_params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Merge global attributes, captured parameters, static attributes, and the
    correlation ID into a single dict ready to pass as
    ``start_as_current_span(..., attributes=...)``.

    Shared by ``span.py``, ``helpers.create_span``, ``mixin.py``,
    ``repository_mixin.py``, and (cross-package) ``varco_fastapi``'s tracing
    middleware — a single merge implementation for every span-creation call
    site in the codebase.

    Merge order (later wins), per Plan 004:
        ``global_attrs → captured params → static_attributes → correlation_id``

    Rationale: explicit per-decorator config beats process-wide defaults;
    ``correlation_id`` is a framework invariant and must never be shadowed.

    DESIGN: attributes passed at span *creation* rather than set after start
        ✅ Attributes present at span start participate in the sampling
           decision (a sampler can drop/keep on ``param.tenant_id``);
           post-start ``set_attribute`` is invisible to the sampler.
        ✅ One SDK call instead of N.
        ❌ Behaviour change: attributes exist from ``t=0`` instead of shortly
           after.  Tests assert on the *finished* span's attributes, so this
           is unobservable to callers.

    Args:
        static_attributes: ``SpanConfig.attributes`` (or equivalent) — wins
            over global attributes and captured params.
        captured_params: Already-rendered ``param.<name>`` attributes (see
            ``varco_core.observability.params``) — wins over global
            attributes, loses to ``static_attributes``.

    Returns:
        A plain dict, safe to pass directly as ``attributes=``.
    """
    merged: dict[str, Any] = {}
    if apply_to_spans():
        merged.update(current_global_attributes())
    if captured_params:
        merged.update(captured_params)
    if static_attributes:
        merged.update(static_attributes)
    cid = current_correlation_id()
    if cid is not None:
        merged["correlation_id"] = cid
    return merged


def _resolve_capture_enabled(cfg: SpanConfig) -> bool:
    """Precedence: cfg.capture_params > cfg.param_capture.enabled > process default."""
    if cfg.capture_params is not None:
        return cfg.capture_params
    if cfg.param_capture is not None and cfg.param_capture.enabled is not None:
        return cfg.param_capture.enabled
    return capture_enabled()


# ── Internal wrapper builder ───────────────────────────────────────────────────


def _make_wrapper(func: _F, cfg: SpanConfig) -> _F:
    """
    Build the actual wrapper function (sync or async) for ``func``.

    Separated from ``span()`` so the logic is not duplicated across the two
    call forms of the decorator.

    Args:
        func: The original callable to wrap.
        cfg:  The resolved ``SpanConfig`` to apply.

    Returns:
        A wrapped callable that creates an OTel span around each call.
    """
    # Span name: explicit > function qualname
    span_name = cfg.name or func.__qualname__

    # Plan 004 (A) — memoised on first call, not at decoration time: the
    # process-wide param_capture_defaults() is not loaded yet when decorators
    # run at import time.  See params.py module docstring for the full
    # rationale.  A plain mutable list cell (not a nonlocal bool) so both
    # wrapper closures below can share it without a `nonlocal` per-branch.
    _plan_cell: list[CapturePlan | None] = [None]

    def _get_plan() -> CapturePlan:
        plan = _plan_cell[0]
        if plan is None:
            effective_cfg = (
                cfg.param_capture if cfg.param_capture is not None else param_capture_defaults()
            )
            plan = build_capture_plan(func, effective_cfg)
            _plan_cell[0] = plan
        return plan

    def _capture(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any] | None:
        if not _resolve_capture_enabled(cfg):
            return None
        return _get_plan().extract(args, kwargs)

    if asyncio.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = trace.get_tracer(cfg.tracer_name)
            captured_params = _capture(args, kwargs)
            merged_attrs = build_span_attributes(cfg.attributes, captured_params)
            # Use record_exception=False so the SDK does NOT auto-record on
            # exception — we handle this ourselves based on SpanConfig flags.
            with tracer.start_as_current_span(
                span_name,
                record_exception=False,
                attributes=merged_attrs,
            ) as current:
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    if cfg.record_exception:
                        current.record_exception(exc)
                    if cfg.set_status_on_error:
                        current.set_status(StatusCode.ERROR, str(exc))
                    raise

        return async_wrapper  # type: ignore[return-value]

    # Sync wrapper — identical logic, but no await.
    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        tracer = trace.get_tracer(cfg.tracer_name)
        captured_params = _capture(args, kwargs)
        merged_attrs = build_span_attributes(cfg.attributes, captured_params)
        with tracer.start_as_current_span(
            span_name,
            record_exception=False,
            attributes=merged_attrs,
        ) as current:
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                if cfg.record_exception:
                    current.record_exception(exc)
                if cfg.set_status_on_error:
                    current.set_status(StatusCode.ERROR, str(exc))
                raise

    return sync_wrapper  # type: ignore[return-value]


__all__ = ["SpanConfig", "span", "build_span_attributes"]
