"""
router.py
=========
FastAPI router and shared app-level state for the payment-gateway example.

Endpoints
---------
POST /v1/charge              — charge a card via the gateway
GET  /v1/balance/{account_id} — read balance via the gateway
POST /v1/control/mode        — switch the stub's failure mode (for tests)
GET  /v1/control/call-count  — return how many times the stub was called

Design decisions
----------------
- A single ``AppState`` dataclass holds the ``FlakeyPaymentStub`` and
  ``PaymentGateway`` instances.  The app stores one ``AppState`` on
  ``app.state.payments`` at startup.
- Route handlers read ``app.state.payments`` via ``request.app.state`` —
  the only acceptable use of ``app.state`` per CLAUDE.md.
- All resilience exceptions (``CallTimeoutError``, ``RetryExhaustedError``,
  ``CircuitOpenError``, ``BulkheadFullError``) are mapped to HTTP 503 by the
  exception handler registered in ``app.py``.

DESIGN: plain FastAPI APIRouter instead of VarcoRouter
    ✅ No DI needed — gateway and stub are plain Python objects wired at
       startup.  VarcoRouter adds value when DI injection is needed.
    ✅ No authentication needed for this demo (focus is on resilience).
    ✅ Request body access requires ``Request`` injection which VarcoRouter
       does not provide directly (F10 from FINDINGS.md).
    ❌ No automatic OpenAPI security schema — acceptable for a demo.

Thread safety:  ⚠️  AppState holds mutable objects; safe within a single
                     asyncio event loop.
Async safety:   ✅  All handlers are ``async def``.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from gateway import PaymentGateway  # noqa: PLC0415
from stub import FailMode, FlakeyPaymentStub  # noqa: PLC0415


# ── AppState ──────────────────────────────────────────────────────────────────


@dataclass
class AppState:
    """
    Mutable application-level state stored on ``app.state.payments``.

    Holds the stub and gateway so every request handler can reach them.
    Creating a new ``AppState`` also creates fresh stub + gateway instances —
    used by test fixtures to isolate state between tests.

    Attributes:
        stub:    Configurable payment stub.
        gateway: Resilience-wrapped gateway backed by the stub.

    Edge cases:
        - ``PaymentGateway`` circuit breakers are CLASS-level singletons;
          creating a new ``AppState`` (and thus a new gateway) does NOT reset
          them.  Call ``PaymentGateway.reset_breakers()`` explicitly in tests.
    """

    stub: FlakeyPaymentStub
    gateway: PaymentGateway

    @classmethod
    def create(cls) -> AppState:
        """
        Factory method — creates a fresh stub and gateway together.

        Returns:
            A new ``AppState`` with a fresh ``FlakeyPaymentStub`` and a
            ``PaymentGateway`` wrapping it.
        """
        stub = FlakeyPaymentStub()
        gateway = PaymentGateway(stub)
        return cls(stub=stub, gateway=gateway)


# ── Router ────────────────────────────────────────────────────────────────────


def build_router() -> APIRouter:
    """
    Build the FastAPI router for the payment-gateway example.

    Returns:
        A configured ``APIRouter`` with all payment and control endpoints.
    """
    router = APIRouter()

    # ── Payment endpoints ─────────────────────────────────────────────────────

    @router.post("/v1/charge")
    async def charge(request: Request) -> JSONResponse:
        """
        Charge a card via the resilience-wrapped gateway.

        Request body (JSON):
            amount:     float — charge amount
            card_token: str   — opaque card token

        Returns:
            200 with ``transaction_id`` and ``amount`` on success.
            503 on resilience failure (timeout, circuit open, retries exhausted).

        Edge cases:
            - Missing body fields raise 422 (FastAPI validation).
            - Resilience errors are caught at the app level by the exception
              handler registered in ``create_app()``.
        """
        state: AppState = request.app.state.payments
        body = await request.json()
        amount: float = body["amount"]
        card_token: str = body["card_token"]

        result = await state.gateway.charge(amount, card_token)
        return JSONResponse(content=result)

    @router.get("/v1/balance/{account_id}")
    async def get_balance(account_id: str, request: Request) -> JSONResponse:
        """
        Read account balance via the resilience-wrapped gateway.

        Args:
            account_id: Account identifier from the path.

        Returns:
            200 with ``account_id`` and ``balance`` on success.
            503 on resilience failure.
        """
        state: AppState = request.app.state.payments
        result = await state.gateway.get_balance(account_id)
        return JSONResponse(content=result)

    # ── Control endpoints (test helpers) ──────────────────────────────────────

    @router.post("/v1/control/mode")
    async def set_mode(request: Request) -> JSONResponse:
        """
        Change the stub's failure mode.

        Request body (JSON):
            mode: str — one of "success", "transient", "slow", "always_fail"

        Returns:
            200 with ``{"mode": <new_mode>}`` on success.
            400 with ``{"error": ...}`` for unknown mode strings.

        Edge cases:
            - Switching to a new mode does NOT reset ``_call_count``.
            - Switching from TRANSIENT back to SUCCESS clears the transient
              window automatically (call_count is already ≥ 3 after a
              successful retry sequence).
        """
        state: AppState = request.app.state.payments
        body = await request.json()
        raw_mode: str = body["mode"]

        try:
            new_mode = FailMode(raw_mode)
        except ValueError:
            valid = [m.value for m in FailMode]
            return JSONResponse(
                status_code=400,
                content={
                    "error": (f"Unknown mode {raw_mode!r}. " f"Valid modes: {valid}"),
                },
            )

        state.stub.mode = new_mode
        return JSONResponse(content={"mode": new_mode.value})

    @router.get("/v1/control/call-count")
    async def get_call_count(request: Request) -> JSONResponse:
        """
        Return the cumulative stub call count.

        Tests use this to verify that retries actually fired (e.g. count ≥ 3
        for a transient failure that succeeds on the third try).

        Returns:
            200 with ``{"count": N}``.
        """
        state: AppState = request.app.state.payments
        return JSONResponse(content={"count": state.stub._call_count})

    return router
