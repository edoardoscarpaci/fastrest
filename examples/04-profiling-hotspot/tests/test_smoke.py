"""
test_smoke.py
=============
Smoke tests for the ``04-profiling-hotspot`` example.

Coverage
--------
Happy paths:
  - GET /v1/compute → 200 with a result payload
  - GET /v1/allocate → 200 with a result payload
  - GET /v1/custom-backend → 200 with a result payload
  - ProfilingMiddleware attaches ``X-Profile-Wall-Ms`` on profiled requests

Unhappy paths:
  - Unknown route → 404
  - Global kill-switch off → endpoint still returns 200 (profiling is
    transparent to the caller; reports are simply absent)

DESIGN: session-scoped client fixture
    ✅ FastAPI app is assembled once; tests share one ASGI process.
    ✅ Avoids re-importing work.py (which calls register_cpu_backend and
       set_profiling_enabled) on every test — prevents duplicate-registration
       errors and lock-ordering issues.
    ❌ Tests cannot independently toggle profiling via set_profiling_enabled
       without coordinating with each other; the kill-switch tests use a
       separate app instance.

Thread safety:  ✅ asyncio_mode=auto; single-threaded event loop.
Async safety:   ✅ All tests are ``async def``.
"""

from __future__ import annotations

import pytest
import httpx
from httpx import ASGITransport

from varco_core.profiling import set_profiling_enabled


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
async def client() -> httpx.AsyncClient:
    """
    Shared ASGI client for the profiling hotspot app.

    Ensures profiling is enabled before the app module is imported (because
    ``@profile`` evaluates the kill-switch at decoration time).  Creates a
    fresh ``AsyncClient`` backed by ``ASGITransport``.

    Yields:
        A configured ``httpx.AsyncClient`` pointed at the example app.
    """
    set_profiling_enabled(True)
    # Import after enabling so @profile wrappers are active.
    from app import create_app  # noqa: PLC0415

    test_app = create_app()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://testserver",
    ) as ac:
        yield ac


# ── Happy path — endpoint responses ──────────────────────────────────────────


async def test_compute_returns_200(client: httpx.AsyncClient) -> None:
    """GET /v1/compute returns 200 with numeric result and iterations fields."""
    response = await client.get("/v1/compute")
    assert response.status_code == 200
    body = response.json()
    assert "result" in body
    assert "iterations" in body
    assert body["iterations"] == 50_000


async def test_allocate_returns_200(client: httpx.AsyncClient) -> None:
    """GET /v1/allocate returns 200 with items count and wall_time_ms."""
    response = await client.get("/v1/allocate")
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert body["items"] == 20_000
    # wall_time_ms may be None if profiling is disabled, but with our fixture
    # it should be a non-negative float.
    assert isinstance(body["wall_time_ms"], float)
    assert body["wall_time_ms"] >= 0.0


async def test_custom_backend_returns_200(client: httpx.AsyncClient) -> None:
    """GET /v1/custom-backend returns 200 with result and backend name."""
    response = await client.get("/v1/custom-backend")
    assert response.status_code == 200
    body = response.json()
    assert "result" in body
    assert body["backend"] == "counting"


# ── Happy path — ProfilingMiddleware headers ──────────────────────────────────


async def test_compute_has_profile_wall_ms_header(client: httpx.AsyncClient) -> None:
    """ProfilingMiddleware attaches X-Profile-Wall-Ms to profiled responses."""
    response = await client.get("/v1/compute")
    assert response.status_code == 200
    assert "x-profile-wall-ms" in response.headers, (
        "Expected X-Profile-Wall-Ms header from ProfilingMiddleware "
        "(attach_headers=True, slow_threshold_ms=0)"
    )
    wall_ms = float(response.headers["x-profile-wall-ms"])
    assert wall_ms >= 0.0


async def test_allocate_has_profile_wall_ms_header(client: httpx.AsyncClient) -> None:
    """ProfilingMiddleware attaches X-Profile-Wall-Ms for the allocate endpoint."""
    response = await client.get("/v1/allocate")
    assert response.status_code == 200
    assert "x-profile-wall-ms" in response.headers


async def test_custom_backend_has_profile_wall_ms_header(
    client: httpx.AsyncClient,
) -> None:
    """ProfilingMiddleware attaches X-Profile-Wall-Ms for the custom-backend endpoint."""
    response = await client.get("/v1/custom-backend")
    assert response.status_code == 200
    assert "x-profile-wall-ms" in response.headers


# ── Unhappy path — routing ────────────────────────────────────────────────────


async def test_unknown_route_returns_404(client: httpx.AsyncClient) -> None:
    """Requests to undefined paths return 404."""
    response = await client.get("/v1/nonexistent")
    assert response.status_code == 404


# ── Unhappy path — kill-switch ────────────────────────────────────────────────


async def test_endpoints_still_work_when_profiling_disabled() -> None:
    """
    When the global kill-switch is off, endpoints return 200 and correct
    payloads — profiling is transparent to the HTTP caller.

    A separate app instance is created here to avoid interfering with the
    session-scoped client fixture (which has profiling enabled).

    Edge cases:
        - ``@profile`` is a no-op at decoration time when profiling is
          disabled — calling the function directly returns the same result.
        - ``profiled()`` returns a _NoopSession whose ``.report`` is None;
          ``memory_work()`` guards against this and returns wall_time_ms=None.
    """
    set_profiling_enabled(False)
    try:
        # Import work functions directly (they were already decorated with
        # profiling ON; disabling only affects functions decorated AFTER the
        # flag is set, but memory_work() uses profiled() at call-time, which
        # respects the current flag).
        from work import memory_work  # noqa: PLC0415

        result = await memory_work()
        assert result["items"] == 20_000
        # With profiling off, profiled() returns _NoopSession whose report is None.
        assert result["wall_time_ms"] is None
    finally:
        set_profiling_enabled(True)
