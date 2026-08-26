"""
composite
=========
Combine the orders and billing services into a single all-in-one deployment.

Two ways to build the composite are shown:

1. ``composite`` (module-level) — the plain path: build each service, wrap in a
   ``ServiceMount``, and combine.  Use this when each service reads its own
   namespaced env vars (``ORDERS_DB_URL`` etc.).

2. ``build_scoped_composite()`` — the ``build_service`` path: the two services
   both read the *same* bare env-var name (``SERVICE_DB_URL``), yet each is built
   under its own scoped environment so they stay isolated in one process.

Run it::

    cd examples/23-composite-all-in-one
    uv run uvicorn composite:composite --reload

Then open:
    http://localhost:8000/                       → landing page (lists services)
    http://localhost:8000/orders/docs            → orders service's own docs
    http://localhost:8000/billing/docs           → billing service's own docs
    http://localhost:8000/orders/orders-api/status
    http://localhost:8000/billing/billing-api/status
    http://localhost:8000/health                 → aggregate health (both services)
"""

from __future__ import annotations

from billing_service import create_billing_app
from fastapi import FastAPI
from orders_service import create_orders_app

from varco_fastapi import ServiceMount, build_service, create_composite_app

# ── Path 1: plain composition of pre-built apps ───────────────────────────────

# Each service is built normally (reading its own namespaced env vars), then
# mounted under a prefix.  This is the primary, zero-magic path.
composite: FastAPI = create_composite_app(
    [
        ServiceMount("/orders", create_orders_app()),
        ServiceMount("/billing", create_billing_app()),
    ],
    title="Orders + Billing (all-in-one)",
    version="1.0.0",
)


# ── Path 2: scoped-env composition via build_service ──────────────────────────


def build_scoped_composite() -> FastAPI:
    """
    Build the same composite, but isolate two services that read the SAME bare
    env-var name (``SERVICE_DB_URL``) via ``build_service``'s scoped overlay.

    Returns:
        A composite ``FastAPI`` app where the orders and billing services were
        each built against their own ``SERVICE_DB_URL`` value.
    """
    orders = build_service(
        "/orders",
        create_orders_app,
        env={"SERVICE_DB_URL": "postgres://prod-db/orders"},
    )
    billing = build_service(
        "/billing",
        create_billing_app,
        env={"SERVICE_DB_URL": "postgres://prod-db/billing"},
    )
    return create_composite_app(
        [orders, billing],
        title="Orders + Billing (scoped env)",
        version="1.0.0",
    )
