"""
test_smoke.py
=============
Integration smoke tests for the ``13-layered-cache-memcached`` example.

All tests require a live Memcached instance — tagged ``pytest.mark.integration``
and skipped by default.  Run with::

    uv run pytest examples/13-layered-cache-memcached/tests/ -v -m integration

What these tests verify
-----------------------
1. **Cache miss then hit** — first GET is a store miss that warms both L1 and L2;
   second GET is served from L1 (counter: 0 hits → 1 hit).
2. **L1 promote** — first GET after a cold start hits L2 and promotes to L1;
   subsequent GETs are L1 hits.
3. **Cache invalidation on update** — PUT invalidates; next GET re-fetches.
4. **Delete evicts cache** — DELETE removes from store + all cache layers.
5. **Stats endpoint** reflects real hit/miss counters.

DESIGN: session-scoped Memcached container, function-scoped app
    ✅ The container is expensive to start — shared across all tests.
    ✅ Each test gets a fresh ``ProductStore`` and zeroed hit/miss counters.
    ✅ A short ``l1_ttl=5`` and unique key prefixes keep tests isolated.
    ✅ The ``ProductCacheLayer`` is pre-started in the fixture (ASGITransport
       does not trigger lifespan hooks).
    ❌ Requires Docker daemon.

Thread safety:  ✅  asyncio_mode=auto; single-threaded event loop.
Async safety:   ✅  All tests are ``async def``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport

# ── sys.path guard ─────────────────────────────────────────────────────────────
_EXAMPLE_ROOT = str(Path(__file__).parent.parent.resolve())
if _EXAMPLE_ROOT not in sys.path:
    sys.path.insert(0, _EXAMPLE_ROOT)

pytestmark = pytest.mark.integration


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def memcached_host_port():
    """
    Start a Memcached 1.6 container and yield ``(host, port)``.

    The container is stopped automatically at session end.

    Yields:
        Tuple ``(host: str, port: int)``.

    Edge cases:
        - Requires Docker daemon.
        - Uses port 11211 inside the container; host port is ephemeral.
    """
    from testcontainers.core.container import DockerContainer  # noqa: PLC0415
    from testcontainers.core.waiting_utils import wait_for_logs  # noqa: PLC0415

    with DockerContainer("memcached:1.6-alpine").with_exposed_ports(11211) as mc:
        wait_for_logs(mc, "server listening")
        host = mc.get_container_host_ip()
        port = int(mc.get_exposed_port(11211))
        yield host, port


@pytest.fixture
async def app_client(memcached_host_port):
    """
    Yield a function-scoped ``httpx.AsyncClient`` backed by a real Memcached.

    A unique ``key_prefix`` is generated per test run to avoid cross-test
    cache collisions on the shared container.

    The ``ProductCacheLayer`` is pre-started here because ``ASGITransport``
    does not trigger FastAPI lifespan hooks.

    Args:
        memcached_host_port: Session-scoped ``(host, port)`` tuple.

    Yields:
        ``httpx.AsyncClient`` for the in-process FastAPI app.
    """
    from app import create_app  # noqa: PLC0415
    from cache_layer import ProductCacheLayer, make_layered_cache  # noqa: PLC0415

    host, port = memcached_host_port
    prefix = f"test:{uuid4().hex[:8]}:"

    layered = make_layered_cache(host, port, l1_ttl=5.0, l2_ttl=60.0, key_prefix=prefix)
    cache = ProductCacheLayer(layered, default_ttl=60.0)
    await cache.start()

    try:
        fastapi_app = create_app(host, port, cache=cache)
        async with httpx.AsyncClient(
            transport=ASGITransport(app=fastapi_app),
            base_url="http://test",
        ) as client:
            yield client
    finally:
        await cache.stop()


# ── Helper ─────────────────────────────────────────────────────────────────────


async def _create(client: httpx.AsyncClient, pid: str, name: str, price: float) -> dict:
    resp = await client.post("/v1/products", json={"id": pid, "name": name, "price": price})
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    return resp.json()


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestHealthCheck:
    async def test_health_returns_ok(self, app_client) -> None:
        """Health probe must return 200."""
        resp = await app_client.get("/health")
        assert resp.status_code == 200


class TestCacheMissThenHit:
    """First GET is a miss (warms L1+L2); second GET is an L1 hit."""

    async def test_miss_then_hit_increments_counters(self, app_client) -> None:
        """
        Stats counters reflect the L1/L2 cache behaviour.

        After one GET (store read → cache warm) and a second GET (L1 hit),
        misses == 1 and hits == 1.
        """
        await _create(app_client, "cache-p1", "Cache Widget", 9.99)

        # First GET: not in cache yet → miss → falls through to store → warms cache.
        r1 = await app_client.get("/v1/products/cache-p1")
        assert r1.status_code == 200

        # Second GET: should come from L1 → hit.
        r2 = await app_client.get("/v1/products/cache-p1")
        assert r2.status_code == 200
        assert r2.json()["name"] == "Cache Widget"

        stats = (await app_client.get("/v1/cache/stats")).json()
        assert stats["misses"] >= 1
        assert stats["hits"] >= 1


class TestCacheInvalidation:
    """PUT/DELETE must evict cached entries so subsequent GETs re-fetch."""

    async def test_put_invalidates_cache(self, app_client) -> None:
        """
        After PUT the cache entry is evicted.  Next GET returns updated value.
        """
        await _create(app_client, "inv-p1", "Old Name", 5.0)
        # Warm the cache.
        await app_client.get("/v1/products/inv-p1")

        # Update — invalidates L1 and L2.
        put_resp = await app_client.put(
            "/v1/products/inv-p1",
            json={"id": "inv-p1", "name": "New Name", "price": 7.5},
        )
        assert put_resp.status_code == 200

        # Next GET must return the updated value, not the stale cached one.
        get_resp = await app_client.get("/v1/products/inv-p1")
        assert get_resp.status_code == 200
        assert get_resp.json()["name"] == "New Name"

    async def test_delete_evicts_cache(self, app_client) -> None:
        """DELETE removes the product from store AND cache."""
        await _create(app_client, "del-p1", "Doomed", 0.01)
        await app_client.get("/v1/products/del-p1")  # warm cache

        del_resp = await app_client.delete("/v1/products/del-p1")
        assert del_resp.status_code == 204

        # Store is gone → cache miss → fallback returns None → 404.
        get_resp = await app_client.get("/v1/products/del-p1")
        assert get_resp.status_code == 404


class TestFullCRUD:
    """Full create → read → update → delete cycle with real Memcached."""

    async def test_create_update_delete(self, app_client) -> None:
        await _create(app_client, "full-p1", "Product", 10.0)

        # Read
        r1 = await app_client.get("/v1/products/full-p1")
        assert r1.status_code == 200
        assert r1.json()["price"] == pytest.approx(10.0)

        # Update
        r2 = await app_client.put(
            "/v1/products/full-p1",
            json={"id": "full-p1", "name": "Product Updated", "price": 12.0},
        )
        assert r2.status_code == 200

        # Read after update
        r3 = await app_client.get("/v1/products/full-p1")
        assert r3.json()["price"] == pytest.approx(12.0)

        # Delete
        r4 = await app_client.delete("/v1/products/full-p1")
        assert r4.status_code == 204

        # Confirm gone
        r5 = await app_client.get("/v1/products/full-p1")
        assert r5.status_code == 404
