"""
test_smoke.py
=============
Integration smoke tests for the ``20-distributed-rate-limit`` example.

All tests require a live Redis instance (started via testcontainers).
Run with::

    uv run pytest examples/20-distributed-rate-limit/tests/ -v -m integration

Coverage
--------
Happy paths:
  1. Health check returns 200.
  2. Redis rate limit — 3 calls within the 3-req/s limit → 200 each.
  3. In-memory rate limit — 3 calls within the 3-req/s limit → 200 each.

Unhappy paths:
  4. Redis rate limit exceeded — burst past 3 calls/s → 429.
  5. In-memory rate limit exceeded — burst past 3 calls/s → 429.
  6. 429 response body — ``detail`` field present and ``retry_after`` is a float.
  7. Stats endpoint — reflects exhausted budget after limit is hit.

DESIGN: pre-connected limiter injected into create_app (F17 pattern)
    ``ASGITransport`` does NOT trigger FastAPI lifespan.  We connect the
    ``RedisRateLimiter`` in the fixture and pass it as ``redis_limiter=`` to
    ``create_app``.  The lifespan then skips the redundant connect/disconnect.
    This pattern is documented as F17 in FINDINGS.md.

DESIGN: low rate (1 req/s) for limit-exceeded tests
    Tests that need to hit the limit use a fresh limiter with ``rate=1`` so a
    single extra call triggers the 429 without needing to loop many times.
    Tests that need normal usage use ``rate=100`` to avoid accidentally
    triggering the limit during the happy-path calls.

Thread safety:  ✅  asyncio_mode=auto; single-threaded event loop.
Async safety:   ✅  All tests are ``async def``.
"""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

pytestmark = pytest.mark.integration


# ── Session fixtures ───────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def redis_container():
    """
    Start a Redis 7 container for the entire test session.

    Yields:
        A started ``RedisContainer`` instance.
    """
    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def redis_url(redis_container) -> str:
    """
    Return the Redis connection URL for the session-scoped container.

    Returns:
        Redis URL string (``"redis://<host>:<port>/0"``).
    """
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}/0"


# ── Per-test fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
async def client(redis_url: str):
    """
    HTTP client with a generous rate limit (100 req/s) for happy-path tests.

    Uses pre-connected limiter injected into ``create_app`` to work around
    ``ASGITransport`` not triggering FastAPI lifespan (F17).

    Each fixture call gets a fresh limiter with an empty window so tests
    don't interfere with each other.

    Yields:
        An ``httpx.AsyncClient`` bound to the ASGI app.
    """
    from app import create_app  # noqa: PLC0415
    from limiters import build_in_memory_limiter, build_redis_limiter  # noqa: PLC0415

    # High rate limit — won't be exceeded during happy-path tests.
    async with build_redis_limiter(redis_url, rate=100, period=1.0) as r_limiter:
        im_limiter = build_in_memory_limiter(rate=100, period=1.0)
        app = create_app(
            redis_url,
            redis_limiter=r_limiter,
            in_mem_limiter=im_limiter,
        )
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.fixture
async def tight_client(redis_url: str):
    """
    HTTP client with a tight rate limit (1 req/s) for limit-exceeded tests.

    A single extra call past the first will trigger the 429.

    Each fixture call uses a unique Redis key prefix to avoid cross-test
    interference when Redis persists the sorted set within the 1-second window.

    Yields:
        An ``httpx.AsyncClient`` bound to the ASGI app.
    """
    import uuid  # noqa: PLC0415

    from app import create_app  # noqa: PLC0415
    from limiters import build_in_memory_limiter  # noqa: PLC0415
    from varco_core.resilience.rate_limit import RateLimitConfig  # noqa: PLC0415
    from varco_redis.config import RedisEventBusSettings  # noqa: PLC0415
    from varco_redis.rate_limit import RedisRateLimiter  # noqa: PLC0415

    # Unique prefix per fixture call — prevents cross-test key collisions.
    prefix = f"test:{uuid.uuid4().hex[:8]}:"
    config = RateLimitConfig(rate=1, period=1.0)
    settings = RedisEventBusSettings(url=redis_url, channel_prefix=prefix)

    # Rate=1 — second call within 1 second hits the limit.
    async with RedisRateLimiter(config, settings=settings) as r_limiter:
        im_limiter = build_in_memory_limiter(rate=1, period=1.0)
        app = create_app(
            redis_url,
            redis_limiter=r_limiter,
            in_mem_limiter=im_limiter,
        )
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


# ── Happy path tests ───────────────────────────────────────────────────────────


async def test_health_check(client: httpx.AsyncClient) -> None:
    """Health endpoint returns 200 with status ok."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_redis_rate_limit_normal_usage(client: httpx.AsyncClient) -> None:
    """
    Three calls within the generous rate limit return 200 each.

    The ``client`` fixture uses rate=100/s so normal test traffic never trips
    the limit.
    """
    for _ in range(3):
        resp = await client.get("/v1/weather")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["source"] == "redis-limiter"
        assert "temperature_c" in data


async def test_in_memory_rate_limit_normal_usage(client: httpx.AsyncClient) -> None:
    """
    Three calls within the generous rate limit return 200 each.

    Uses the in-memory limiter endpoint.
    """
    for _ in range(3):
        resp = await client.get("/v1/weather/in-mem")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["source"] == "in-memory-limiter"
        assert "temperature_c" in data


# ── Unhappy path tests ─────────────────────────────────────────────────────────


async def test_redis_rate_limit_exceeded(tight_client: httpx.AsyncClient) -> None:
    """
    The second call within one second exceeds the Redis rate limit → 429.

    Uses ``tight_client`` (rate=1/s) so the very first extra call triggers
    the limit.
    """
    # First call — should succeed.
    first = await tight_client.get("/v1/weather")
    assert first.status_code == 200, first.text

    # Second call immediately — budget is exhausted → 429.
    second = await tight_client.get("/v1/weather")
    assert second.status_code == 429, second.text


async def test_in_memory_rate_limit_exceeded(tight_client: httpx.AsyncClient) -> None:
    """
    The second call within one second exceeds the in-memory rate limit → 429.
    """
    # First call — should succeed.
    first = await tight_client.get("/v1/weather/in-mem")
    assert first.status_code == 200, first.text

    # Second call immediately — budget is exhausted → 429.
    second = await tight_client.get("/v1/weather/in-mem")
    assert second.status_code == 429, second.text


async def test_429_response_body_has_detail(tight_client: httpx.AsyncClient) -> None:
    """
    A 429 response includes a ``detail`` string and a ``retry_after`` float.
    """
    # Exhaust the Redis limiter (rate=1).
    await tight_client.get("/v1/weather")  # allowed
    resp = await tight_client.get("/v1/weather")  # denied → 429
    assert resp.status_code == 429

    body = resp.json()
    assert "detail" in body, f"No 'detail' key in 429 body: {body}"
    assert isinstance(body["detail"], str)
    assert "retry_after" in body, f"No 'retry_after' key in 429 body: {body}"
    assert isinstance(body["retry_after"], (int, float))

    # Retry-After header should be present and a positive integer string.
    assert "retry-after" in resp.headers or "Retry-After" in resp.headers


async def test_stats_endpoint_reflects_exhausted_budget(
    tight_client: httpx.AsyncClient,
) -> None:
    """
    /v1/rate-limit/stats shows budget_exhausted=True after the limit is hit.
    """
    # Exhaust the Redis limiter.
    await tight_client.get("/v1/weather")  # allowed
    await tight_client.get("/v1/weather")  # denied

    resp = await tight_client.get("/v1/rate-limit/stats")
    assert resp.status_code == 200, resp.text

    stats = resp.json()
    assert "redis_limiter" in stats
    assert "in_memory_limiter" in stats

    redis_stats = stats["redis_limiter"]
    assert redis_stats["rate"] == 1
    assert redis_stats["budget_exhausted"] is True
    assert redis_stats["retry_after_seconds"] > 0.0
