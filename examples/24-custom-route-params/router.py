"""
router.py
=========
``CatalogRouter`` — a service-free ``GenericRouter`` that demonstrates **full
FastAPI parameter injection** on custom ``@route`` handlers.

A custom ``@route`` method may declare any parameter a normal FastAPI endpoint
can, and FastAPI parses / validates / coerces / injects it:

    GET  /catalog/health                 — allow_anonymous, no params
    GET  /catalog/items/{item_id}        — typed path param (int) + Query + ctx
    POST /catalog/items                  — Pydantic Body + ctx → response model
    GET  /catalog/search                 — Query params + Depends + Request
    GET  /catalog/reports/summary        — require_scopes + ctx + Query

No database, no broker, no Docker.  All responses are produced inline so the
focus stays on how the parameters reach the handler.

DESIGN: synthesized handler signature (see varco_fastapi.router.base)
    ✅ Query/Body/Depends/Request + typed path params all work natively.
    ✅ ``ctx``/``auth``/``context`` still receive the router's AuthContext.
    ✅ RouteGuard authorization runs before the handler, unchanged.
    ❌ Handlers declaring ``ctx`` require the router to set ``_auth``.

Thread safety:  ✅ All ClassVars are read-only after class definition.
Async safety:   ✅ No blocking I/O; handlers are async for framework consistency.
"""

from __future__ import annotations

from fastapi import Body, Depends, Query, Request
from pydantic import BaseModel, Field

from varco_core.auth.base import AuthContext

from varco_fastapi.auth.guard import allow_anonymous, require_scopes
from varco_fastapi.router.endpoint import route
from varco_fastapi.router.presets import GenericRouter


# ── DTOs ──────────────────────────────────────────────────────────────────────


class NewItem(BaseModel):
    """Request body for creating a catalog item."""

    name: str = Field(min_length=1, max_length=120)
    price_cents: int = Field(ge=0)
    tags: list[str] = Field(default_factory=list)


class Item(BaseModel):
    """Response model — drives the OpenAPI schema for item endpoints."""

    item_id: int
    name: str
    price_cents: int
    currency: str
    created_by: str | None


# ── A trivial FastAPI dependency (proves Depends injection works) ──────────────


class PricingService:
    """A stand-in service resolved via ``Depends`` — no DI container needed."""

    def quote(self, base_cents: int, *, in_stock: bool) -> int:
        """Return a price: a flat surcharge is added when the item is out of stock."""
        # Out-of-stock items carry a 10% backorder surcharge — arbitrary demo logic.
        return base_cents if in_stock else int(base_cents * 1.10)


def get_pricing_service() -> PricingService:
    """FastAPI dependency provider — a fresh ``PricingService`` per request."""
    return PricingService()


# ── Router ────────────────────────────────────────────────────────────────────


class CatalogRouter(GenericRouter):
    """
    Service-free catalog router.

    ``_auth`` is assigned in ``app.create_app()`` before ``build_router()`` runs,
    so ``ctx`` and the ``require_scopes`` guard have an ``AuthContext`` to work with.
    """

    _prefix = "/catalog"
    _tags = ["catalog"]

    # ── Public: no params, no auth ────────────────────────────────────────────
    @route("GET", "/health", requires=allow_anonymous())
    async def health(self) -> dict:
        """Liveness probe — anonymous, no parameters."""
        return {"status": "ok"}

    # ── Typed path param + Query + ctx ────────────────────────────────────────
    @route("GET", "/items/{item_id}")
    async def get_item(
        self,
        item_id: int,  # path param — FastAPI coerces "42" → 42 (422 on non-int)
        ctx: AuthContext,  # injected from _auth
        currency: str = Query("usd", pattern="^[a-z]{3}$"),  # validated query param
    ) -> Item:  # return annotation → OpenAPI response model
        """Fetch a single item; ``currency`` is a validated query param."""
        return Item(
            item_id=item_id,
            name=f"item-{item_id}",
            price_cents=1000,
            currency=currency,
            created_by=ctx.user_id,
        )

    # ── Pydantic body + ctx → response model ──────────────────────────────────
    @route("POST", "/items", status_code=201)
    async def create_item(
        self,
        payload: NewItem = Body(...),  # request body, validated (422 on bad input)
        ctx: AuthContext = None,  # type: ignore[assignment]  # injected from _auth
    ) -> Item:
        """Create an item from a validated JSON body; echoes it back as an ``Item``."""
        return Item(
            item_id=1,
            name=payload.name,
            price_cents=payload.price_cents,
            currency="usd",
            created_by=ctx.user_id if ctx else None,
        )

    # ── Query params + Depends + Request ──────────────────────────────────────
    @route("GET", "/search")
    async def search(
        self,
        request: Request,  # raw request — injected by annotation
        q: str = Query(..., min_length=1),  # required query param
        limit: int = Query(10, ge=1, le=100),  # coerced + range-validated
        in_stock: bool = Query(True),  # "true"/"false"/"1"/"0" → bool
        pricing: PricingService = Depends(get_pricing_service),  # DI dependency
    ) -> dict:
        """Search demo — combines Query params, a ``Depends`` service, and ``Request``."""
        quoted = pricing.quote(1000, in_stock=in_stock)
        return {
            "q": q,
            "limit": limit,
            "in_stock": in_stock,
            "quote_cents": quoted,
            # Prove the real Request object arrived — read an arbitrary header.
            "client_ua": request.headers.get("user-agent", ""),
        }

    # ── Guarded route — ctx + guard still work with rich params ───────────────
    @route("GET", "/reports/summary", requires=require_scopes("catalog:read"))
    async def report_summary(
        self,
        ctx: AuthContext,
        window: int = Query(30, ge=1, le=365),
    ) -> dict:
        """Aggregate report — requires the ``catalog:read`` scope (403 otherwise)."""
        return {"user": ctx.user_id, "window_days": window, "total_items": 123}
