"""
tests.test_smoke
================
Smoke tests for the minimal CRUD API example.

Tests cover the full happy path (create → read → update → list → delete)
and a 404 unhappy path for a missing product.

No Docker or external services required — the in-memory repository is
used throughout.

Thread safety:  ✅ Each test function receives its own ``test_app`` /
                   ``client`` fixture instance — no shared state.
Async safety:   ✅ All tests are ``async def`` (asyncio auto mode via pytest.ini).
"""

from __future__ import annotations

import os
import sys

import pytest
from httpx import ASGITransport, AsyncClient

# Add the example root to the path so relative imports (``from app import ...``)
# resolve correctly when pytest is invoked from the workspace root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402 (path manipulation must come first)


@pytest.fixture
def test_app():
    """
    Create a fresh FastAPI app for each test.

    Each call to ``create_app()`` returns an independent app with its own
    ``DIContainer`` and in-memory store, so tests do not share state.

    Returns:
        A configured ``FastAPI`` instance.
    """
    return create_app()


@pytest.fixture
async def client(test_app):
    """
    Async HTTP client backed by the test app via ASGI transport.

    No real network connection is used — httpx sends requests directly
    to the ASGI interface.

    Args:
        test_app: ``FastAPI`` instance from the ``test_app`` fixture.

    Yields:
        An ``AsyncClient`` with ``base_url="http://test"``.
    """
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as c:
        yield c


# ── Happy-path: full CRUD lifecycle ───────────────────────────────────────────


async def test_create_product(client: AsyncClient) -> None:
    """POST /v1/products → 201 with the created product."""
    response = await client.post(
        "/v1/products",
        json={"name": "Widget", "description": "A useful widget", "price": 9.99},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Widget"
    assert body["description"] == "A useful widget"
    assert body["price"] == 9.99
    assert body["in_stock"] is True
    # pk, created_at, updated_at must be present after create
    assert "pk" in body
    assert "created_at" in body
    assert "updated_at" in body


async def test_read_product(client: AsyncClient) -> None:
    """POST then GET /v1/products/{id} → 200 with matching fields."""
    create_resp = await client.post(
        "/v1/products",
        json={"name": "Gadget", "price": 19.99},
    )
    assert create_resp.status_code == 201, create_resp.text
    pk = create_resp.json()["pk"]

    read_resp = await client.get(f"/v1/products/{pk}")
    assert read_resp.status_code == 200, read_resp.text
    body = read_resp.json()
    assert body["pk"] == pk
    assert body["name"] == "Gadget"
    assert body["price"] == 19.99


async def test_update_product(client: AsyncClient) -> None:
    """POST then PUT /v1/products/{id} → 200 with updated fields."""
    create_resp = await client.post(
        "/v1/products",
        json={"name": "OldName", "price": 5.0},
    )
    assert create_resp.status_code == 201, create_resp.text
    pk = create_resp.json()["pk"]

    update_resp = await client.put(
        f"/v1/products/{pk}",
        json={"name": "NewName", "price": 7.5},
    )
    assert update_resp.status_code == 200, update_resp.text
    body = update_resp.json()
    assert body["name"] == "NewName"
    assert body["price"] == 7.5
    # Fields not sent → keep existing value
    assert body["in_stock"] is True


async def test_list_products(client: AsyncClient) -> None:
    """POST two products then GET /v1/products → both appear in list."""
    await client.post("/v1/products", json={"name": "Alpha", "price": 1.0})
    await client.post("/v1/products", json={"name": "Beta", "price": 2.0})

    list_resp = await client.get("/v1/products")
    assert list_resp.status_code == 200, list_resp.text
    body = list_resp.json()
    # ListMixin returns a paginated envelope: {"results": [...], "count": N, ...}
    items = body["results"] if isinstance(body, dict) else body
    names = [item["name"] for item in items]
    assert "Alpha" in names
    assert "Beta" in names


async def test_delete_product(client: AsyncClient) -> None:
    """POST then DELETE /v1/products/{id} → 204, subsequent GET → 404."""
    create_resp = await client.post(
        "/v1/products",
        json={"name": "Disposable", "price": 0.01},
    )
    assert create_resp.status_code == 201, create_resp.text
    pk = create_resp.json()["pk"]

    delete_resp = await client.delete(f"/v1/products/{pk}")
    assert delete_resp.status_code == 204, delete_resp.text

    # Confirm the product is gone — GET should now return 404.
    get_resp = await client.get(f"/v1/products/{pk}")
    assert get_resp.status_code == 404, get_resp.text


# ── Unhappy path ──────────────────────────────────────────────────────────────


async def test_read_missing_product_returns_404(client: AsyncClient) -> None:
    """GET /v1/products/{non-existent-id} → 404."""
    import uuid

    non_existent_pk = str(uuid.uuid4())
    response = await client.get(f"/v1/products/{non_existent_pk}")
    assert response.status_code == 404, response.text


async def test_update_missing_product_returns_404(client: AsyncClient) -> None:
    """PUT /v1/products/{non-existent-id} → 404."""
    import uuid

    non_existent_pk = str(uuid.uuid4())
    response = await client.put(
        f"/v1/products/{non_existent_pk}",
        json={"name": "Ghost"},
    )
    assert response.status_code == 404, response.text


async def test_delete_missing_product_returns_404(client: AsyncClient) -> None:
    """DELETE /v1/products/{non-existent-id} → 404."""
    import uuid

    non_existent_pk = str(uuid.uuid4())
    response = await client.delete(f"/v1/products/{non_existent_pk}")
    assert response.status_code == 404, response.text
