"""
router.py
=========
FastAPI router for the product catalog with look-aside caching.

Routes
------
- ``POST /v1/products``        — create a product (no cache population)
- ``GET  /v1/products/{id}``   — look-aside read (cache miss → DB → cache)
- ``PUT  /v1/products/{id}``   — update product + invalidate its cache entry
- ``GET  /v1/cache/stats``     — diagnostic hit/miss counters

DESIGN: router holds refs to store and cache_layer vs dependency injection
    ✅ Self-contained example — no DI container wiring complexity.
    ✅ Makes the data flow explicit and easy to follow for readers.
    ❌ Not suitable for production-scale apps — prefer varco_fastapi DI wiring.

Thread safety:  ❌  Not thread-safe — single event loop.
Async safety:   ✅  All route handlers are ``async def``.
"""

from __future__ import annotations

from cache_layer import ProductCacheLayer
from fastapi import APIRouter, HTTPException
from models import Product
from pydantic import BaseModel
from store import ProductStore

# ── Request/Response bodies ─────────────────────────────────────────────────


class CreateProductRequest(BaseModel):
    """Request body for creating a product."""

    id: str
    name: str
    price: float
    description: str = ""


class UpdateProductRequest(BaseModel):
    """Request body for updating a product."""

    name: str | None = None
    price: float | None = None
    description: str | None = None


# ── Router factory ────────────────────────────────────────────────────────────


def build_router(store: ProductStore, cache_layer: ProductCacheLayer) -> APIRouter:
    """
    Build a FastAPI ``APIRouter`` wired to the given store and cache layer.

    Args:
        store:       The in-memory product store (fake DB).
        cache_layer: The Redis-backed look-aside cache layer.

    Returns:
        A fully configured ``APIRouter`` mounted at ``/v1``.
    """
    router = APIRouter(prefix="/v1")

    @router.post("/products", status_code=201)
    async def create_product(body: CreateProductRequest) -> dict:
        """
        Create a new product.

        Does not populate the cache — the entry is lazily cached on first GET.

        Args:
            body: Product fields.

        Returns:
            The created product as a dict.

        Raises:
            HTTP 409: If a product with the same id already exists.
        """
        existing = await store.get(body.id)
        if existing is not None:
            raise HTTPException(status_code=409, detail="Product already exists")
        product = Product(
            id=body.id,
            name=body.name,
            price=body.price,
            description=body.description,
        )
        await store.save(product)
        return {
            "id": product.id,
            "name": product.name,
            "price": product.price,
            "description": product.description,
        }

    @router.get("/products/{product_id}")
    async def get_product(product_id: str) -> dict:
        """
        Return a product by ID using look-aside caching.

        First request → cache miss → store → response (cached).
        Subsequent requests → cache hit → response (no store call).

        Args:
            product_id: Product identifier.

        Returns:
            Product dict.

        Raises:
            HTTP 404: If the product does not exist in the store.
        """
        product = await cache_layer.get_product(product_id, fallback=store.get)
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")
        return {
            "id": product.id,
            "name": product.name,
            "price": product.price,
            "description": product.description,
        }

    @router.put("/products/{product_id}")
    async def update_product(product_id: str, body: UpdateProductRequest) -> dict:
        """
        Update a product and invalidate its cache entry.

        Args:
            product_id: Product to update.
            body:       Fields to update (all optional).

        Returns:
            Updated product dict.

        Raises:
            HTTP 404: If the product does not exist in the store.
        """
        existing = await store.get(product_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Product not found")

        # Build updated product (dataclass is frozen — create a new one)
        updated = Product(
            id=existing.id,
            name=body.name if body.name is not None else existing.name,
            price=body.price if body.price is not None else existing.price,
            description=(
                body.description if body.description is not None else existing.description
            ),
        )
        await store.save(updated)
        # Invalidate the cached entry so the next GET returns fresh data.
        await cache_layer.invalidate_product(product_id)
        return {
            "id": updated.id,
            "name": updated.name,
            "price": updated.price,
            "description": updated.description,
        }

    @router.get("/cache/stats")
    async def cache_stats() -> dict:
        """
        Return cache hit/miss counters.

        Useful for verifying that the cache is working — counters accumulate
        across requests for the lifetime of the process.

        Returns:
            Dict with ``"hits"`` and ``"misses"`` integer counters.
        """
        return cache_layer.stats()

    return router


__all__ = ["build_router", "CreateProductRequest", "UpdateProductRequest"]
