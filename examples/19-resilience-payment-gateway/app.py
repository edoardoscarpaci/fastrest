"""
app.py
======
Application factory for the ``19-resilience-payment-gateway`` example.

Demonstrates all varco resilience primitives applied to a flaky in-process
payment stub:

1. ``@timeout``        — cancel if upstream is too slow
2. ``@retry``          — retry transient failures with back-off
3. ``CircuitBreaker``  — stop hammering a dead service (shared instance)
4. ``@hedge``          — speculative duplicate for tail-latency reduction

No database, no broker, no Docker required.

Run locally::

    cd examples/19-resilience-payment-gateway
    uvicorn app:create_app --factory --reload

DESIGN: ``create_app()`` factory over module-level ``app = FastAPI(...)``
    ✅ Each call produces a fully isolated app — fresh stub, fresh gateway.
    ✅ Tests create independent apps per test (or per test class) to prevent
       stub state and circuit-breaker trip state from bleeding across tests.
    ❌ ``PaymentGateway`` circuit breakers are class-level singletons — fresh
       apps share them.  Call ``PaymentGateway.reset_breakers()`` in test
       fixtures to isolate circuit-breaker state.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from router import AppState, build_router  # noqa: PLC0415


def create_app() -> FastAPI:
    """
    Build and return a FastAPI application for the payment-gateway example.

    Creates a fresh ``AppState`` (stub + gateway), mounts it on
    ``app.state.payments``, registers all routes, and wires a global
    exception handler that maps resilience errors to HTTP 503.

    Returns:
        A configured ``FastAPI`` instance ready to serve requests.

    Edge cases:
        - Each call to ``create_app()`` creates a new stub and a new
          ``PaymentGateway`` wrapper.  The class-level circuit breakers are
          NOT reset — call ``PaymentGateway.reset_breakers()`` explicitly in
          test fixtures when testing circuit-breaker behaviour.
    """
    app = FastAPI(
        title="Resilience Payment Gateway",
        description=(
            "Demonstrates varco resilience primitives: timeout, retry, circuit breaker, hedge."
        ),
        version="1.0.0",
    )

    # ── App-level state ───────────────────────────────────────────────────────
    # Store stub + gateway on app.state so all route handlers can reach them.
    # This is the one acceptable use of app.state (CLAUDE.md §14).
    app.state.payments = AppState.create()

    # ── Routes ────────────────────────────────────────────────────────────────
    app.include_router(build_router())

    # ── Global resilience exception handler ───────────────────────────────────
    # All resilience failures become HTTP 503 so the caller knows the upstream
    # is unavailable rather than seeing an opaque 500.
    #
    # DESIGN: catch resilience exceptions at the app boundary, not inside
    #   the gateway.
    #   ✅ Keeps the gateway return types clean (no Result[T] wrapper).
    #   ✅ HTTP status mapping is a presentation-layer concern.
    #   ❌ The handler must import all resilience exception types — one import
    #      per new exception type.

    from varco_core.resilience import (  # noqa: PLC0415
        BulkheadFullError,
        CallTimeoutError,
        CircuitOpenError,
        RetryExhaustedError,
    )

    @app.exception_handler(CallTimeoutError)
    async def _on_timeout(request, exc: CallTimeoutError) -> JSONResponse:  # type: ignore[misc]
        return JSONResponse(
            status_code=503,
            content={"error": "timeout", "detail": str(exc)},
        )

    @app.exception_handler(CircuitOpenError)
    async def _on_circuit_open(request, exc: CircuitOpenError) -> JSONResponse:  # type: ignore[misc]
        return JSONResponse(
            status_code=503,
            content={
                "error": "circuit_open",
                "detail": str(exc),
                "retry_after": exc.retry_after,
            },
        )

    @app.exception_handler(RetryExhaustedError)
    async def _on_retry_exhausted(request, exc: RetryExhaustedError) -> JSONResponse:  # type: ignore[misc]
        return JSONResponse(
            status_code=503,
            content={
                "error": "retry_exhausted",
                "detail": str(exc),
                "attempts": exc.attempts,
            },
        )

    @app.exception_handler(BulkheadFullError)
    async def _on_bulkhead_full(request, exc: BulkheadFullError) -> JSONResponse:  # type: ignore[misc]
        return JSONResponse(
            status_code=503,
            content={"error": "bulkhead_full", "detail": str(exc)},
        )

    return app
