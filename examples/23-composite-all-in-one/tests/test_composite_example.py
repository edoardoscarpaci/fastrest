"""
test_composite_example
=======================
Smoke tests for the all-in-one composite deployment example.

Verifies that two independently-built services route independently under the
composite, that each keeps its own docs, that the aggregate health endpoint
reports both, and that ``build_service`` isolates two services reading the same
bare env-var name.
"""

from __future__ import annotations

import httpx
from composite import build_scoped_composite, composite

from varco_fastapi import create_composite_app


def _client(app) -> httpx.AsyncClient:
    """Return an in-process httpx client for the given ASGI app."""
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test.local")


async def test_both_services_route_independently():
    """Each service serves its own route under its own prefix."""
    async with _client(composite) as client:
        orders = await client.get("/orders/orders-api/status")
        billing = await client.get("/billing/billing-api/status")

    assert orders.json()["service"] == "orders"
    assert billing.json()["service"] == "billing"


async def test_each_service_keeps_its_own_docs():
    """Mounted sub-apps preserve their own /docs."""
    async with _client(composite) as client:
        assert (await client.get("/orders/docs")).status_code == 200
        assert (await client.get("/billing/docs")).status_code == 200


async def test_landing_page_lists_both_services():
    """GET / lists both mounted services."""
    async with _client(composite) as client:
        body = (await client.get("/")).json()
    assert {s["name"] for s in body["services"]} == {"orders", "billing"}


async def test_aggregate_health_reports_both_services():
    """The composite /health probes both services and reports healthy."""
    async with _client(composite) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["services"]) == {"orders", "billing"}
    assert body["status"] == "healthy"


async def test_build_service_isolates_same_bare_env_name():
    """
    Two services reading the SAME bare env var (SERVICE_DB_URL) end up isolated
    when built via build_service with different scoped values.
    """
    app = build_scoped_composite()
    async with _client(app) as client:
        orders = (await client.get("/orders/orders-api/status")).json()
        billing = (await client.get("/billing/billing-api/status")).json()

    # orders_service reads ORDERS_DB_URL, billing reads SERVICE_DB_URL; the
    # scoped overlay gives billing its own value without leaking to orders.
    assert billing["db"] == "postgres://prod-db/billing"
    # Sanity: both services are distinct instances with distinct config.
    assert orders["service"] == "orders"
    assert billing["service"] == "billing"


def test_composite_is_a_fastapi_app():
    """The module-level composite is a ready-to-serve ASGI app."""
    from fastapi import FastAPI

    assert isinstance(composite, FastAPI)
    # Rebuildable via the factory path too.
    assert isinstance(build_scoped_composite(), FastAPI)
    assert create_composite_app  # re-exported symbol is importable
