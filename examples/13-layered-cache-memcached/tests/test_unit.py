"""
test_unit.py
============
Unit tests for the ``13-layered-cache-memcached`` example.

All tests run without Docker by injecting ``NoOpCache`` (which ignores all
writes and returns ``None`` on every get).  This exercises the routing and
business logic without the cache tier.

Coverage
--------
Happy paths:
  1. Health check — GET /health returns 200.
  2. Create + GET round-trip — POST stores; GET fetches via store fallback.
  3. List products — GET /v1/products returns all created products.
  4. Update product — PUT updates store; GET returns new values.
  5. Delete product — DELETE removes; subsequent GET returns 404.
  6. Cache stats — /v1/cache/stats returns hit/miss counters.

Unhappy paths:
  7. GET unknown product returns 404.
  8. Duplicate create returns 409.
  9. UPDATE unknown product returns 404.
  10. DELETE unknown product returns 404.

DESIGN: NoOpCache for unit tests
    ✅ No Docker required — tests run fast in CI without infrastructure.
    ✅ ``ProductCacheLayer(NoOpCache())`` always misses — all reads fall
       through to ``store.get()``, testing the full service path.
    ✅ Stats counters still increment (misses only) — verifiable behaviour.
    ❌ The L1 → L2 promote path is not tested here — see ``test_smoke.py``
       for the full integration test with real Memcached.

Thread safety:  ✅  asyncio_mode=auto; single-threaded event loop.
Async safety:   ✅  All tests are ``async def``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

# ── sys.path guard ─────────────────────────────────────────────────────────────
_EXAMPLE_ROOT = str(Path(__file__).parent.parent.resolve())
if _EXAMPLE_ROOT not in sys.path:
    sys.path.insert(0, _EXAMPLE_ROOT)

from app import create_app  # noqa: E402
from cache_layer import ProductCacheLayer  # noqa: E402
from store import ProductStore  # noqa: E402
from varco_core.cache import NoOpCache  # noqa: E402

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def app_client():
    """
    Yield a function-scoped ``httpx.AsyncClient`` backed by ``NoOpCache``.

    Each test gets a fresh ``ProductStore`` and zeroed hit/miss counters.

    Yields:
        ``httpx.AsyncClient`` connected to the in-process FastAPI app.

    Edge cases:
        - ``NoOpCache`` ignores all writes and returns ``None`` on every get,
          so every GET falls through to the ``ProductStore``.
    """
    store = ProductStore()
    # NoOpCache never hits — all reads fall through to the store fallback.
    cache = ProductCacheLayer(NoOpCache())

    fastapi_app = create_app(store=store, cache=cache)
    return httpx.AsyncClient(
        transport=ASGITransport(app=fastapi_app),
        base_url="http://test",
    )


# ── Helper ─────────────────────────────────────────────────────────────────────


async def _create(client: httpx.AsyncClient, pid: str, name: str, price: float) -> dict:
    resp = await client.post("/v1/products", json={"id": pid, "name": name, "price": price})
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    return resp.json()


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestHealth:
    async def test_health_ok(self, app_client) -> None:
        async with app_client as client:
            resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestCRUD:
    async def test_create_returns_201(self, app_client) -> None:
        async with app_client as client:
            data = await _create(client, "p-1", "Widget", 9.99)
        assert data["id"] == "p-1"
        assert data["name"] == "Widget"
        assert data["price"] == pytest.approx(9.99)

    async def test_get_by_id_returns_product(self, app_client) -> None:
        async with app_client as client:
            await _create(client, "p-2", "Gadget", 19.99)
            resp = await client.get("/v1/products/p-2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Gadget"

    async def test_list_returns_all_products(self, app_client) -> None:
        async with app_client as client:
            await _create(client, "p-a", "Alpha", 1.0)
            await _create(client, "p-b", "Beta", 2.0)
            resp = await client.get("/v1/products")
        assert resp.status_code == 200
        ids = {p["id"] for p in resp.json()}
        assert {"p-a", "p-b"}.issubset(ids)

    async def test_update_returns_updated_values(self, app_client) -> None:
        async with app_client as client:
            await _create(client, "p-upd", "Old Name", 5.0)
            resp = await client.put(
                "/v1/products/p-upd",
                json={"id": "p-upd", "name": "New Name", "price": 7.5},
            )
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"
        assert resp.json()["price"] == pytest.approx(7.5)

    async def test_delete_then_get_returns_404(self, app_client) -> None:
        async with app_client as client:
            await _create(client, "p-del", "Doomed", 0.01)
            del_resp = await client.delete("/v1/products/p-del")
            get_resp = await client.get("/v1/products/p-del")
        assert del_resp.status_code == 204
        assert get_resp.status_code == 404

    async def test_cache_stats_endpoint(self, app_client) -> None:
        """``/v1/cache/stats`` returns hit/miss counters."""
        async with app_client as client:
            await _create(client, "p-stat", "Stat Prod", 1.0)
            # NoOpCache: get hits are all misses (falls through to store)
            await client.get("/v1/products/p-stat")
            await client.get("/v1/products/p-stat")
            resp = await client.get("/v1/cache/stats")
        assert resp.status_code == 200
        stats = resp.json()
        assert "hits" in stats
        assert "misses" in stats
        # With NoOpCache: every get is a miss.
        assert stats["misses"] >= 2


class TestErrorPaths:
    async def test_get_unknown_returns_404(self, app_client) -> None:
        async with app_client as client:
            resp = await client.get("/v1/products/no-such-product")
        assert resp.status_code == 404

    async def test_duplicate_create_returns_409(self, app_client) -> None:
        async with app_client as client:
            await _create(client, "p-dup", "First", 1.0)
            resp = await client.post(
                "/v1/products", json={"id": "p-dup", "name": "Second", "price": 2.0}
            )
        assert resp.status_code == 409

    async def test_update_unknown_returns_404(self, app_client) -> None:
        async with app_client as client:
            resp = await client.put(
                "/v1/products/ghost",
                json={"id": "ghost", "name": "Ghost", "price": 0.0},
            )
        assert resp.status_code == 404

    async def test_delete_unknown_returns_404(self, app_client) -> None:
        async with app_client as client:
            resp = await client.delete("/v1/products/ghost")
        assert resp.status_code == 404
