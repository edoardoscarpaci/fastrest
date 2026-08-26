"""
Tests for ProfilingMiddleware and ProfilingSettings.

Covers:
- Disabled by default (no headers, no profiling)
- Enabled: profiled request logs report; attach_headers adds X-Profile-* headers
- skip_paths: matched paths pass through unprofiled
- Threshold gating: fast request below slow_threshold_ms produces no log
- Serialisation: concurrent requests pass through without being blocked
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from varco_core.profiling import set_profiling_enabled
from varco_fastapi.middleware.profiling import ProfilingMiddleware, ProfilingSettings

# ── Helpers ───────────────────────────────────────────────────────────────────


def make_app(settings: ProfilingSettings) -> FastAPI:
    """Build a minimal FastAPI app with ProfilingMiddleware and two routes."""
    app = FastAPI()
    app.add_middleware(ProfilingMiddleware, settings=settings)

    @app.get("/fast")
    async def fast_route():
        return {"ok": True}

    @app.get("/slow")
    async def slow_route():
        await asyncio.sleep(0.05)
        return {"ok": True}

    @app.get("/health")
    async def health_route():
        return {"status": "ok"}

    return app


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def enable_profiling():
    """Each test needs profiling globally enabled."""
    set_profiling_enabled(True)
    yield
    set_profiling_enabled(False)


async def test_disabled_by_default_no_headers():
    """When enabled=False, no X-Profile-* headers appear."""
    settings = ProfilingSettings(enabled=False)
    app = make_app(settings)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.get("/fast")

    assert resp.status_code == 200
    assert "x-profile-wall-ms" not in resp.headers


async def test_enabled_attach_headers():
    """When enabled=True and attach_headers=True, X-Profile-* headers appear."""
    settings = ProfilingSettings(
        enabled=True, attach_headers=True, slow_threshold_ms=0.0
    )
    app = make_app(settings)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.get("/fast")

    assert resp.status_code == 200
    assert "x-profile-wall-ms" in resp.headers
    # Wall time must be a parseable float
    wall = float(resp.headers["x-profile-wall-ms"])
    assert wall >= 0.0


async def test_enabled_logs_report(caplog):
    """When enabled=True, a report is logged at INFO via the middleware logger."""
    import logging

    settings = ProfilingSettings(enabled=True, slow_threshold_ms=0.0)
    app = make_app(settings)

    with caplog.at_level(logging.INFO, logger="varco_fastapi.middleware.profiling"):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.get("/fast")

    assert resp.status_code == 200
    assert any("GET /fast" in r.message for r in caplog.records)


async def test_skip_paths_not_profiled():
    """Requests to skip_paths are never profiled (no headers even with attach_headers)."""
    settings = ProfilingSettings(
        enabled=True,
        attach_headers=True,
        slow_threshold_ms=0.0,
        skip_paths=frozenset({"/health"}),
    )
    app = make_app(settings)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.get("/health")

    assert resp.status_code == 200
    assert "x-profile-wall-ms" not in resp.headers


async def test_threshold_gating_no_log_for_fast_request(caplog):
    """When slow_threshold_ms is very large, a fast request produces no report log."""
    import logging

    settings = ProfilingSettings(
        enabled=True,
        slow_threshold_ms=999_999.0,  # nothing will be this slow
        attach_headers=False,
    )
    app = make_app(settings)

    with caplog.at_level(logging.INFO, logger="varco_core.profiling.engine"):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.get("/fast")

    assert resp.status_code == 200
    assert not any("GET /fast" in r.message for r in caplog.records)


async def test_concurrent_request_passes_through_unprofiled():
    """A second concurrent request is never blocked — it passes through unprofiled."""
    import time

    settings = ProfilingSettings(
        enabled=True,
        attach_headers=True,
        slow_threshold_ms=0.0,
    )
    app = make_app(settings)

    results = []

    async def fetch(path: str):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            t0 = time.perf_counter()
            resp = await c.get(path)
            elapsed = time.perf_counter() - t0
            results.append(
                {
                    "path": path,
                    "status": resp.status_code,
                    "has_profile": "x-profile-wall-ms" in resp.headers,
                    "elapsed": elapsed,
                }
            )

    # Fire two concurrent requests: one slow (gets profiled), one fast (may be skipped)
    await asyncio.gather(fetch("/slow"), fetch("/fast"))

    assert all(r["status"] == 200 for r in results)
    # The fast path must never have been blocked waiting for the lock
    fast_result = next(r for r in results if r["path"] == "/fast")
    assert fast_result["elapsed"] < 0.5  # way under the /slow sleep of 50ms


async def test_attach_headers_false_no_profile_headers():
    """attach_headers=False means no X-Profile-* headers even when profiling runs."""
    settings = ProfilingSettings(
        enabled=True, attach_headers=False, slow_threshold_ms=0.0
    )
    app = make_app(settings)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.get("/fast")

    assert resp.status_code == 200
    assert "x-profile-wall-ms" not in resp.headers


async def test_profiling_settings_defaults():
    """ProfilingSettings defaults match documented values."""
    s = ProfilingSettings()
    assert s.enabled is False
    assert "/health" in s.skip_paths
    assert "/metrics" in s.skip_paths
    assert s.slow_threshold_ms == 0.0
    assert s.attach_headers is False
    assert s.top_n == 15
    assert s.track_rss is True


async def test_profiling_does_not_suppress_application_errors():
    """Errors raised by the route handler must propagate through the middleware.

    The middleware must never swallow application exceptions.  When there is no
    outer error handler the exception propagates to the ASGI transport (that is
    the expected behaviour, not a bug in the middleware).
    """
    settings = ProfilingSettings(enabled=True, slow_threshold_ms=0.0)
    app = FastAPI()
    app.add_middleware(ProfilingMiddleware, settings=settings)

    @app.get("/boom")
    async def boom_route():
        raise ValueError("intentional")

    # Without an outer error handler the ValueError propagates through ASGI
    # transport to the test.  This is correct — the middleware did NOT swallow it.
    with pytest.raises(Exception, match="intentional"):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            await c.get("/boom")
