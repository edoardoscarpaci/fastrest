"""
test_smoke.py
=============
Smoke tests for the ``19-resilience-payment-gateway`` example.

Coverage
--------
Happy paths:
  - POST /v1/charge     → 200 with transaction_id on success
  - GET  /v1/balance    → 200 with balance on success
  - POST /v1/control/mode   → 200, mode updated
  - GET  /v1/control/call-count → 200 with integer count

Unhappy paths:
  - TRANSIENT mode — stub fails 2× then succeeds → 200 AND call_count ≥ 3
    (proves @retry actually fires multiple times)
  - SLOW mode — stub sleeps 10 s → 503 timeout (gateway @timeout fires at 0.5 s)
  - ALWAYS_FAIL mode — enough calls trip the circuit → 503 circuit_open
    (proves CircuitBreaker opens on consecutive failures)
  - Control: unknown mode string → 400

DESIGN: function-scoped ``client`` fixture + explicit breaker reset
    ✅ Each test gets a fresh app and a fresh stub — _call_count starts at 0.
    ✅ ``PaymentGateway.reset_breakers()`` is called at the start of every
       test that touches the circuit breaker, ensuring the breaker starts
       CLOSED regardless of test execution order.
    ✅ Session-scoped anyio_backend satisfies pytest-asyncio.
    ❌ Creating a new ASGI app per test has startup overhead; acceptable for
       these lightweight tests (no DB, no broker).

Thread safety:  ✅ asyncio_mode=auto; single-threaded event loop per test.
Async safety:   ✅ All tests are ``async def``.
"""

from __future__ import annotations

import httpx

# sys.path setup is handled in conftest.py — sibling modules (app, gateway,
# etc.) are importable because conftest.py adds the example root before any
# test module is imported, avoiding E402 ruff violations.
import pytest
from app import create_app  # noqa: PLC0415
from gateway import PaymentGateway  # noqa: PLC0415
from httpx import ASGITransport

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Use asyncio as the anyio backend for all tests in this session."""
    return "asyncio"


@pytest.fixture
async def client() -> httpx.AsyncClient:
    """
    Provide a fresh ``httpx.AsyncClient`` backed by a fresh ASGI app.

    Each test gets its own app instance (fresh stub, fresh _call_count).
    Circuit breakers are also reset before each client is yielded so tests
    that trip the breaker don't affect subsequent tests.

    DESIGN: function scope (not session scope)
        ✅ Fresh stub per test — call_count starts at 0.
        ✅ No shared failure state between tests.
        ❌ Slightly higher overhead vs. session scope — acceptable here.

    Yields:
        An ``httpx.AsyncClient`` pointing at the fresh app.
    """
    # Reset circuit breakers first — they are class-level singletons that
    # survive across app instances.
    PaymentGateway.reset_breakers()

    app = create_app()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _set_mode(client: httpx.AsyncClient, mode: str) -> None:
    """Switch the stub's failure mode via the control endpoint."""
    resp = await client.post("/v1/control/mode", json={"mode": mode})
    assert resp.status_code == 200, f"Failed to set mode: {resp.text}"


async def _get_call_count(client: httpx.AsyncClient) -> int:
    """Read the cumulative stub call count from the control endpoint."""
    resp = await client.get("/v1/control/call-count")
    assert resp.status_code == 200
    return resp.json()["count"]


# ── Happy path tests ──────────────────────────────────────────────────────────


async def test_charge_success(client: httpx.AsyncClient) -> None:
    """
    POST /v1/charge in SUCCESS mode returns 200 with a transaction_id.

    Verifies the basic happy path: stub returns a transaction, gateway passes
    it through, router wraps it in a 200 JSON response.
    """
    resp = await client.post(
        "/v1/charge",
        json={"amount": 49.99, "card_token": "tok_test_123"},
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "transaction_id" in data
    assert data["amount"] == 49.99


async def test_balance_success(client: httpx.AsyncClient) -> None:
    """
    GET /v1/balance/{account_id} in SUCCESS mode returns 200 with balance.

    Also exercises the hedge path — the stub responds immediately (well under
    the 50 ms hedge delay), so the hedge never fires.  The test confirms the
    fast path works correctly when hedging is inactive.
    """
    resp = await client.get("/v1/balance/acc_test_456")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["account_id"] == "acc_test_456"
    assert data["balance"] == 1000.0


# ── Control endpoint tests ────────────────────────────────────────────────────


async def test_control_mode_change(client: httpx.AsyncClient) -> None:
    """POST /v1/control/mode updates the stub mode correctly."""
    resp = await client.post("/v1/control/mode", json={"mode": "transient"})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "transient"

    # Switch back to success — no cleanup side-effects
    resp = await client.post("/v1/control/mode", json={"mode": "success"})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "success"


async def test_control_mode_unknown_returns_400(client: httpx.AsyncClient) -> None:
    """POST /v1/control/mode with an unknown mode string returns 400."""
    resp = await client.post("/v1/control/mode", json={"mode": "oops"})
    assert resp.status_code == 400
    assert "error" in resp.json()


async def test_control_call_count(client: httpx.AsyncClient) -> None:
    """GET /v1/control/call-count returns a non-negative integer."""
    resp = await client.get("/v1/control/call-count")
    assert resp.status_code == 200
    count = resp.json()["count"]
    assert isinstance(count, int)
    assert count >= 0


# ── Retry test ────────────────────────────────────────────────────────────────


async def test_retry_fires_on_transient_failure(client: httpx.AsyncClient) -> None:
    """
    In TRANSIENT mode the stub fails the first 2 calls then succeeds on the 3rd.

    Verifies that:
      - The final response is 200 (retry eventually succeeds).
      - call_count ≥ 3 (proving the retry decorator actually called the stub
        multiple times, not just once).

    DESIGN: verify via call_count, not via timing
        ✅ call_count is deterministic — no sleep or timing assumptions.
        ✅ Proves the retry loop ran, not just that the response was 200.
    """
    await _set_mode(client, "transient")

    resp = await client.post(
        "/v1/charge",
        json={"amount": 10.00, "card_token": "tok_retry_test"},
    )

    # The retry should eventually succeed on the 3rd attempt
    assert (
        resp.status_code == 200
    ), f"Expected 200 after retries, got {resp.status_code}: {resp.text}"

    # call_count must be ≥ 3 — the stub was called at least 3 times
    count = await _get_call_count(client)
    assert count >= 3, f"Expected call_count ≥ 3 (proving retries fired), got {count}"


# ── Timeout test ──────────────────────────────────────────────────────────────


async def test_timeout_fires_on_slow_stub(client: httpx.AsyncClient) -> None:
    """
    In SLOW mode the stub sleeps 10 s, well beyond the 0.5 s gateway timeout.

    Verifies that @timeout cancels the call and the router returns 503.
    The test itself should complete in ~0.5 s (the timeout budget), not 10 s.

    Edge cases:
        - The retry loop never retries on CallTimeoutError (excluded from
          retryable_on), so only one stub call is attempted before timing out.
    """
    await _set_mode(client, "slow")

    resp = await client.post(
        "/v1/charge",
        json={"amount": 5.00, "card_token": "tok_slow_test"},
        # Give httpx enough headroom to wait for the gateway timeout + overhead
        timeout=3.0,
    )

    assert (
        resp.status_code == 503
    ), f"Expected 503 (timeout), got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["error"] == "timeout", f"Expected error=timeout, got: {data}"


async def test_timeout_fires_on_slow_balance(client: httpx.AsyncClient) -> None:
    """
    In SLOW mode the balance endpoint also times out — 503 with error=timeout.

    Verifies that @timeout protects get_balance as well as charge.
    """
    await _set_mode(client, "slow")

    resp = await client.get(
        "/v1/balance/acc_slow",
        timeout=3.0,
    )

    assert resp.status_code == 503, resp.text
    assert resp.json()["error"] == "timeout"


# ── Circuit breaker test ──────────────────────────────────────────────────────


async def test_circuit_breaker_opens_after_repeated_failures(
    client: httpx.AsyncClient,
) -> None:
    """
    In ALWAYS_FAIL mode, repeated failures trip the circuit open.

    The breaker config has failure_threshold=3.  We send 3 requests to exhaust
    the threshold, then verify the 4th request fails fast with
    ``error=circuit_open`` (without reaching the stub again).

    Verifies:
      - First 3 calls reach the stub (counts increment).
      - 4th call is rejected by the open circuit before reaching the stub.
      - Response is 503 with error=circuit_open.

    DESIGN: use charge endpoint for circuit-breaker test
        ✅ charge() has no @retry that would re-use a slot after CircuitOpenError.
           The retry policy excludes CircuitOpenError from retryable_on, so
           CircuitOpenError propagates immediately.
        ✅ clean, deterministic: each POST is one attempt.
    """
    await _set_mode(client, "always_fail")

    # Send failure_threshold (3) requests — each should hit the stub and fail
    for i in range(3):
        resp = await client.post(
            "/v1/charge",
            json={"amount": 1.00, "card_token": f"tok_trip_{i}"},
        )
        # Each attempt returns 503 (retry_exhausted or direct error)
        assert (
            resp.status_code == 503
        ), f"Attempt {i+1}: expected 503, got {resp.status_code}: {resp.text}"

    # Record stub call count after the 3 threshold failures
    count_after_trips = await _get_call_count(client)

    # 4th request — circuit should now be OPEN, stub should NOT be called
    resp = await client.post(
        "/v1/charge",
        json={"amount": 1.00, "card_token": "tok_after_trip"},
    )

    assert resp.status_code == 503, resp.text
    data = resp.json()
    assert (
        data["error"] == "circuit_open"
    ), f"Expected error=circuit_open for 4th request, got: {data}"

    # Stub call count must NOT have increased — circuit is open, stub not reached
    count_after_open = await _get_call_count(client)
    assert count_after_open == count_after_trips, (
        f"Stub was called after circuit opened: "
        f"count_before={count_after_trips}, count_after={count_after_open}"
    )
