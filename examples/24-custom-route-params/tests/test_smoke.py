"""
tests/test_smoke.py
===================
Smoke tests for the ``24-custom-route-params`` example.

Verifies that every FastAPI parameter kind is parsed/injected on custom ``@route``
handlers, and that ``ctx`` injection + ``RouteGuard`` authorization still work.

Uses ``httpx.AsyncClient`` with ``ASGITransport`` — no real socket, no Docker.
All tests are ``async def`` under ``asyncio_mode = "auto"`` (examples/pyproject.toml).

Thread safety:  ✅ Each test builds a fresh app + client.
Async safety:   ✅ All test methods are ``async def``.
"""

from __future__ import annotations

import os
import sys

import httpx
from httpx import ASGITransport

# Add the example root to sys.path so ``from app import ...`` works regardless of
# the directory pytest is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402 (path manipulation must come first)

_READER = {"X-API-Key": "reader-key"}  # holds "catalog:read"
_GUEST = {"X-API-Key": "guest-key"}  # no scopes


def _client() -> httpx.AsyncClient:
    """Build an ASGI-transport client bound to a fresh app instance."""
    app = create_app()
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── Public / no params ────────────────────────────────────────────────────────


async def test_health_is_anonymous():
    """Anonymous liveness probe returns 200 with no token."""
    async with _client() as c:
        resp = await c.get("/catalog/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── Typed path param + Query + ctx ────────────────────────────────────────────


async def test_typed_path_param_is_coerced():
    """An ``int`` path param arrives coerced; the query currency is echoed."""
    async with _client() as c:
        resp = await c.get(
            "/catalog/items/42", params={"currency": "eur"}, headers=_READER
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["item_id"] == 42  # int, not "42"
    assert body["currency"] == "eur"
    assert body["created_by"] == "reader"  # ctx injected from _auth


async def test_path_param_non_int_rejected():
    """A non-integer path segment on an ``int`` param is rejected with 422."""
    async with _client() as c:
        resp = await c.get("/catalog/items/not-a-number", headers=_READER)
    assert resp.status_code == 422


async def test_query_validation_rejects_bad_currency():
    """The ``currency`` pattern constraint rejects invalid values with 422."""
    async with _client() as c:
        resp = await c.get(
            "/catalog/items/1", params={"currency": "US"}, headers=_READER
        )
    assert resp.status_code == 422


# ── Pydantic body ─────────────────────────────────────────────────────────────


async def test_body_model_is_parsed():
    """A valid JSON body is parsed into the Pydantic model and echoed back."""
    async with _client() as c:
        resp = await c.post(
            "/catalog/items",
            json={"name": "widget", "price_cents": 500, "tags": ["a"]},
            headers=_READER,
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "widget"
    assert body["price_cents"] == 500
    assert body["created_by"] == "reader"


async def test_body_validation_rejects_missing_field():
    """A body missing the required ``name`` field is rejected with 422."""
    async with _client() as c:
        resp = await c.post(
            "/catalog/items", json={"price_cents": 500}, headers=_READER
        )
    assert resp.status_code == 422


# ── Query + Depends + Request ─────────────────────────────────────────────────


async def test_search_combines_query_depends_and_request():
    """Query params, a ``Depends`` service and ``Request`` all resolve together."""
    async with _client() as c:
        resp = await c.get(
            "/catalog/search",
            params={"q": "widget", "limit": "5", "in_stock": "false"},
            headers={**_READER, "User-Agent": "varco-smoke"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["q"] == "widget"
    assert body["limit"] == 5  # coerced to int
    assert body["in_stock"] is False  # coerced to bool
    assert body["quote_cents"] == 1100  # Depends service applied surcharge
    assert body["client_ua"] == "varco-smoke"  # real Request injected


async def test_search_requires_q():
    """The required ``q`` query param is enforced (422 when absent)."""
    async with _client() as c:
        resp = await c.get("/catalog/search", headers=_READER)
    assert resp.status_code == 422


# ── Guard still works on a rich handler ───────────────────────────────────────


async def test_guarded_report_allows_scoped_caller():
    """A caller with ``catalog:read`` reaches the guarded report (200)."""
    async with _client() as c:
        resp = await c.get(
            "/catalog/reports/summary", params={"window": "7"}, headers=_READER
        )
    assert resp.status_code == 200
    assert resp.json()["window_days"] == 7


async def test_guarded_report_denies_unscoped_caller():
    """A caller without the scope is denied by the RouteGuard (403)."""
    async with _client() as c:
        resp = await c.get("/catalog/reports/summary", headers=_GUEST)
    assert resp.status_code == 403
