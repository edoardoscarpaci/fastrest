"""
router.py
=========
FastAPI router for the layered-cache product catalog example.

Endpoints
---------
- ``POST /v1/products``         — create a product (persists + warms cache)
- ``GET  /v1/products``         — list all products (bypasses cache)
- ``GET  /v1/products/{id}``    — fetch a product (look-aside cache)
- ``PUT  /v1/products/{id}``    — update a product (invalidates cache)
- ``DELETE /v1/products/{id}``  — delete a product (invalidates cache)
- ``GET  /v1/cache/stats``      — expose hit/miss counters

Thread safety:  ❌  Single event loop.
Async safety:   ✅  All handlers are ``async def``.
"""

from __future__ import annotations

from cache_layer import ProductCacheLayer
from fastapi import APIRouter, HTTPException, status
from models import Product
from pydantic import BaseModel
from store import ProductStore

# ── DTOs ──────────────────────────────────────────────────────────────────────


class ProductCreate(BaseModel):
    """Request body for product creation."""

    id: str
    name: str
    price: float


class ProductResponse(BaseModel):
    """Product representation in API responses."""

    id: str
    name: str
    price: float


# ── Router factory ────────────────────────────────────────────────────────────


def build_router(store: ProductStore, cache: ProductCacheLayer) -> APIRouter:
    """
    Build and return an ``APIRouter`` wired with ``store`` and ``cache``.

    Args:
        store: In-memory product store (authoritative source).
        cache: Started (or pre-started) product cache layer.

    Returns:
        An ``APIRouter`` with all product and cache endpoints registered.
    """
    router = APIRouter()

    @router.post("/v1/products", response_model=ProductResponse, status_code=201)
    async def create_product(body: ProductCreate) -> Product:
        """
        Create a new product.

        Raises:
            HTTPException: 409 if a product with the same ``id`` already exists.
        """
        existing = await store.get(body.id)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Product {body.id!r} already exists.",
            )
        product = Product(id=body.id, name=body.name, price=body.price)
        await store.save(product)
        # Warm the cache immediately so the first GET is a hit.
        await cache.invalidate_product(product.id)
        return product

    @router.get("/v1/products", response_model=list[ProductResponse])
    async def list_products() -> list[Product]:
        """Return all products (bypasses cache — always hits the store)."""
        return await store.all()

    @router.get("/v1/products/{product_id}", response_model=ProductResponse)
    async def get_product(product_id: str) -> Product:
        """
        Fetch a product via look-aside cache.

        Raises:
            HTTPException: 404 if not found.
        """
        product = await cache.get_product(product_id, fallback=store.get)
        if product is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product {product_id!r} not found.",
            )
        return product

    @router.put("/v1/products/{product_id}", response_model=ProductResponse)
    async def update_product(product_id: str, body: ProductCreate) -> Product:
        """
        Update an existing product and invalidate its cache entry.

        Raises:
            HTTPException: 404 if not found.
        """
        existing = await store.get(product_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product {product_id!r} not found.",
            )
        updated = Product(id=product_id, name=body.name, price=body.price)
        await store.save(updated)
        await cache.invalidate_product(product_id)
        return updated

    @router.delete("/v1/products/{product_id}", status_code=204)
    async def delete_product(product_id: str) -> None:
        """
        Delete a product and evict it from all cache layers.

        Raises:
            HTTPException: 404 if not found.
        """
        existed = await store.delete(product_id)
        if not existed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product {product_id!r} not found.",
            )
        await cache.invalidate_product(product_id)

    @router.get("/v1/cache/stats")
    async def cache_stats() -> dict[str, int]:
        """Return in-process cache hit/miss counters."""
        return cache.stats()

    return router


__all__ = ["build_router"]
