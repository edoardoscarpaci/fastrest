"""
app.py
======
Application factory for the product catalog look-aside caching example.

``create_app(redis_url)`` returns a FastAPI application wired with:

- ``ProductStore``      — in-memory fake DB
- ``ProductCacheLayer`` — Redis-backed look-aside cache (TTL + tag invalidation)
- ``build_router``      — CRUD routes for products + cache stats endpoint

Lifecycle
---------
The ``ProductCacheLayer`` is started in the FastAPI ``lifespan`` context so
Redis connections are opened at startup and closed at shutdown.  The
``ProductStore`` has no lifecycle — it is a plain dict.

DESIGN: lifespan-managed cache over module-level singleton
    ✅ The cache is properly started and stopped around the ASGI lifecycle —
       no leaked Redis connections in tests or after app shutdown.
    ✅ ``create_app(redis_url)`` is a pure factory — tests pass a real Redis URL
       (from testcontainers) without env-var injection.
    ✅ The store and cache_layer are passed into the router via closure so there
       is no global mutable state.
    ❌ Not DI-wired — intentional for a focused caching example.

Usage::

    from app import create_app
    app = create_app("redis://localhost:6379/0")
    # uvicorn app:app

Thread safety:  ❌  Single event loop.
Async safety:   ✅  Lifespan context manages cache start/stop.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI

from cache_layer import ProductCacheLayer
from router import build_router
from store import ProductStore


def create_app(
    redis_url: str,
    *,
    ttl: float = 60.0,
    store: ProductStore | None = None,
    cache_layer: ProductCacheLayer | None = None,
) -> FastAPI:
    """
    Create and return a configured FastAPI application.

    Args:
        redis_url:   Redis connection URL (e.g. ``"redis://localhost:6379/0"``).
        ttl:         Cache entry TTL in seconds.  Defaults to 60.
        store:       Pre-built ``ProductStore``.  Created fresh if ``None``.
        cache_layer: Pre-started ``ProductCacheLayer``.  If provided, the
                     lifespan does NOT call ``start()``/``stop()`` — the
                     caller owns the lifecycle.  Pass this from test fixtures
                     to avoid needing ``ASGITransport`` to trigger lifespan.

    Returns:
        A ``FastAPI`` instance ready to serve requests.

    DESIGN: optional pre-built components for test isolation
        ``ASGITransport`` does NOT trigger FastAPI lifespan, so the cache
        would never be started in tests.  Accepting a pre-started
        ``cache_layer`` lets tests manage the lifecycle themselves while
        keeping production usage simple (pass only ``redis_url``).

    Edge cases:
        - If Redis is unreachable and no ``cache_layer`` is provided,
          ``cache_layer.start()`` will raise during the lifespan startup
          phase — FastAPI propagates this as a startup error.
        - The store starts empty — products must be created via
          ``POST /v1/products``.
    """
    _store = store or ProductStore()
    _cache_layer = cache_layer or ProductCacheLayer(redis_url, ttl=ttl)
    _manage = cache_layer is None  # only manage lifecycle when we own the object

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        """Start/stop cache only when we created it (not when caller pre-started it)."""
        if _manage:
            await _cache_layer.start()
        try:
            yield
        finally:
            if _manage:
                await _cache_layer.stop()

    app = FastAPI(
        title="Product Catalog — Look-Aside Redis Cache",
        description=(
            "Demonstrates varco_redis caching: RedisCache, TTLStrategy, "
            "TaggedStrategy, CompositeStrategy, and the @cached decorator."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    app.include_router(build_router(_store, _cache_layer))
    return app


__all__ = ["create_app"]
