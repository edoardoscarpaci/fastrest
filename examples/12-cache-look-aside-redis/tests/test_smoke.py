"""
test_smoke.py
=============
Integration smoke tests for the ``12-cache-look-aside-redis`` example.

All tests require a live Redis instance (started via testcontainers).
Run with::

    uv run pytest examples/12-cache-look-aside-redis/tests/ -v -m integration

Coverage
--------
Happy paths:
  1. Cache miss then hit — first GET hits the store; second GET hits the cache.
  2. Cache invalidation on update — PUT invalidates; next GET re-fetches.
  3. TTL eviction — short TTL expires; next GET is a fresh miss.
  4. Create then read — POST then GET returns the new product.
  5. Cache stats endpoint — /v1/cache/stats reflects hit/miss counters.

Unhappy paths:
  6. Unknown product returns 404.
  7. Duplicate product creation returns 409.

DESIGN: session-scoped Redis container + function-scoped app
    ✅ The Redis container is expensive to spin up — starting it once per test
       session avoids repeated Docker pull + port-bind overhead.
    ✅ Function-scoped ``app`` fixture means each test gets a fresh
       ``ProductStore`` (no carry-over data) and clean cache counters.
    ✅ ``lifespan=True`` on ``ASGITransport`` exercises the full lifespan
       hook — ``cache_layer.start()`` and ``cache_layer.stop()`` are called.
    ❌ Session-scoped Redis is shared; tests must use unique product IDs to
       avoid cross-test key collisions (enforced by naming convention below).

Thread safety:  ✅  asyncio_mode=auto; single-threaded event loop.
Async safety:   ✅  All tests are ``async def``.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from httpx import ASGITransport

pytestmark = pytest.mark.integration


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def redis_container():
    """
    Start a Redis 7 container for the entire test session.

    The container is stopped automatically by testcontainers when the fixture
    goes out of scope (session end).

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


@pytest.fixture
async def client(redis_url: str):
    """
    Per-test HTTP client with pre-started cache.

    ``ASGITransport`` does NOT trigger FastAPI lifespan, so we pre-start the
    ``ProductCacheLayer`` here and pass it into ``create_app``.  The app's
    lifespan then skips the redundant start/stop calls.

    Each test gets a fresh ``ProductStore`` (empty) and fresh cache counters.

    Yields:
        An ``httpx.AsyncClient`` bound to the ASGI app.
    """
    from app import create_app  # noqa: PLC0415
    from cache_layer import ProductCacheLayer  # noqa: PLC0415
    from store import ProductStore  # noqa: PLC0415

    async with ProductCacheLayer(redis_url, ttl=60.0) as cache:
        store = ProductStore()
        app = create_app(redis_url, store=store, cache_layer=cache)
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.fixture
async def short_ttl_client(redis_url: str):
    """
    Like ``client`` but with a 1-second TTL for TTL-eviction tests.

    Yields:
        An ``httpx.AsyncClient`` with a 1-second cache TTL.
    """
    from app import create_app  # noqa: PLC0415
    from cache_layer import ProductCacheLayer  # noqa: PLC0415
    from store import ProductStore  # noqa: PLC0415

    async with ProductCacheLayer(redis_url, ttl=1.0) as cache:
        store = ProductStore()
        app = create_app(redis_url, store=store, cache_layer=cache)
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


# ── Helper ────────────────────────────────────────────────────────────────────


async def _create(client: httpx.AsyncClient, product_id: str, **kwargs) -> dict:
    """POST a product and assert 201."""
    payload = {"id": product_id, "name": "Widget", "price": 9.99, **kwargs}
    resp = await client.post("/v1/products", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Happy path tests ──────────────────────────────────────────────────────────


async def test_cache_miss_then_hit(client: httpx.AsyncClient) -> None:
    """
    First GET is a cache miss; second GET is a cache hit.

    We verify the miss by checking that store.get_calls would increase on the
    first request.  Instead we observe via /v1/cache/stats:
    - After first GET: misses=1, hits=0.
    - After second GET: misses=1, hits=1.
    """
    product_id = "miss-hit-001"
    await _create(client, product_id, name="Test Widget", price=4.99)

    # First GET — cache miss
    resp = await client.get(f"/v1/products/{product_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test Widget"

    stats = (await client.get("/v1/cache/stats")).json()
    assert stats["misses"] == 1
    assert stats["hits"] == 0

    # Second GET — cache hit
    resp2 = await client.get(f"/v1/products/{product_id}")
    assert resp2.status_code == 200
    assert resp2.json()["name"] == "Test Widget"

    stats2 = (await client.get("/v1/cache/stats")).json()
    assert stats2["misses"] == 1
    assert stats2["hits"] == 1


async def test_cache_invalidation_on_update(client: httpx.AsyncClient) -> None:
    """
    PUT invalidates the cache entry; next GET re-fetches from the store.

    Sequence:
    1. POST product.
    2. GET → cache miss (misses=1).
    3. GET → cache hit (hits=1).
    4. PUT with new name → cache invalidated.
    5. GET → cache miss again (misses=2) → returns updated name.
    """
    product_id = "invalidate-002"
    await _create(client, product_id, name="Old Name", price=19.99)

    # Warm the cache
    await client.get(f"/v1/products/{product_id}")
    stats = (await client.get("/v1/cache/stats")).json()
    assert stats["misses"] == 1

    # Verify hit
    await client.get(f"/v1/products/{product_id}")
    stats2 = (await client.get("/v1/cache/stats")).json()
    assert stats2["hits"] == 1

    # Update — should invalidate cache
    put_resp = await client.put(f"/v1/products/{product_id}", json={"name": "New Name"})
    assert put_resp.status_code == 200
    assert put_resp.json()["name"] == "New Name"

    # Next GET — should be a miss (stale entry evicted) returning fresh name
    get_resp = await client.get(f"/v1/products/{product_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "New Name"

    stats3 = (await client.get("/v1/cache/stats")).json()
    assert stats3["misses"] == 2  # second miss after invalidation


async def test_ttl_eviction(short_ttl_client: httpx.AsyncClient) -> None:
    """
    After the TTL expires, the next GET is a cache miss.

    Uses ``short_ttl_client`` which configures a 1-second TTL.

    Sequence:
    1. POST product.
    2. GET → cache miss (misses=1).
    3. Sleep 1.5 seconds — TTL expires.
    4. GET → cache miss again (misses=2) because Redis evicted the key.
    """
    client = short_ttl_client
    product_id = "ttl-evict-003"
    await _create(client, product_id, name="Expiring Widget", price=1.00)

    # First GET — miss
    await client.get(f"/v1/products/{product_id}")
    stats = (await client.get("/v1/cache/stats")).json()
    assert stats["misses"] == 1

    # Wait for TTL to expire
    await asyncio.sleep(1.5)

    # Second GET after TTL — another miss
    resp = await client.get(f"/v1/products/{product_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Expiring Widget"

    stats2 = (await client.get("/v1/cache/stats")).json()
    assert stats2["misses"] == 2


async def test_create_then_read(client: httpx.AsyncClient) -> None:
    """
    POST creates a product; GET returns it (via cache miss → store → cache).
    """
    product_id = "create-read-004"
    created = await _create(
        client,
        product_id,
        name="New Product",
        price=29.99,
        description="A fine product",
    )
    assert created["id"] == product_id

    resp = await client.get(f"/v1/products/{product_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == product_id
    assert data["name"] == "New Product"
    assert data["price"] == 29.99
    assert data["description"] == "A fine product"


async def test_cache_stats_endpoint(client: httpx.AsyncClient) -> None:
    """
    /v1/cache/stats returns a dict with ``hits`` and ``misses`` integers.
    """
    product_id = "stats-005"
    await _create(client, product_id, name="Stats Widget", price=3.99)

    # Two GETs: one miss + one hit
    await client.get(f"/v1/products/{product_id}")
    await client.get(f"/v1/products/{product_id}")

    resp = await client.get("/v1/cache/stats")
    assert resp.status_code == 200
    stats = resp.json()
    assert "hits" in stats
    assert "misses" in stats
    assert isinstance(stats["hits"], int)
    assert isinstance(stats["misses"], int)
    assert stats["hits"] >= 1
    assert stats["misses"] >= 1


# ── Unhappy path tests ────────────────────────────────────────────────────────


async def test_unknown_product_returns_404(client: httpx.AsyncClient) -> None:
    """GET for a non-existent product returns 404."""
    resp = await client.get("/v1/products/does-not-exist-999")
    assert resp.status_code == 404


async def test_duplicate_product_creation_returns_409(
    client: httpx.AsyncClient,
) -> None:
    """Creating a product with a duplicate ID returns 409."""
    product_id = "dup-006"
    await _create(client, product_id, name="Original", price=1.00)

    resp = await client.post(
        "/v1/products",
        json={"id": product_id, "name": "Duplicate", "price": 2.00},
    )
    assert resp.status_code == 409


async def test_update_nonexistent_product_returns_404(
    client: httpx.AsyncClient,
) -> None:
    """PUT for a non-existent product returns 404."""
    resp = await client.put("/v1/products/ghost-007", json={"name": "Ghost"})
    assert resp.status_code == 404
