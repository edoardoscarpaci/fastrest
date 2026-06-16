"""
app.py
======
Application factory for the ``13-layered-cache-memcached`` example.

``create_app(host, port)`` wires:

- ``ProductStore``    — in-memory fake DB.
- ``ProductCacheLayer(LayeredCache(InMemoryCache, MemcachedCache))``
  — two-tier look-aside cache.
- FastAPI routes via ``build_router()``.

Lifecycle
---------
The ``ProductCacheLayer`` is started in the FastAPI ``lifespan`` context so
Memcached connections are opened at startup and closed at shutdown.

An optional ``cache`` argument lets tests inject a pre-started ``NoOpCache``
or ``InMemoryCache`` to avoid needing a real Memcached server (since
``ASGITransport`` does not trigger ``lifespan``).

DESIGN: optional pre-built cache for test isolation
    ✅ Production code passes only ``host``/``port`` — the factory builds the
       two-tier stack automatically.
    ✅ Tests inject a ``NoOpCache`` (or pre-started ``InMemoryCache``) to run
       without Docker, keeping unit tests fast.
    ✅ Integration tests inject a pre-started ``LayeredCache`` pointing at a
       real Memcached container to exercise the full stack.
    ❌ Not DI-wired — intentional; this example focuses on the cache tier.

Thread safety:  ❌  Single event loop.
Async safety:   ✅  Lifespan manages cache start/stop.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from cache_layer import ProductCacheLayer, make_layered_cache
from router import build_router
from store import ProductStore


def create_app(
    host: str = "localhost",
    port: int = 11211,
    *,
    store: ProductStore | None = None,
    cache: ProductCacheLayer | None = None,
) -> FastAPI:
    """
    Build and return the configured FastAPI application.

    Args:
        host:  Memcached server hostname.
        port:  Memcached server port.
        store: Pre-built ``ProductStore``.  Created fresh if ``None``.
        cache: Pre-started ``ProductCacheLayer``.  If provided, the lifespan
               does NOT call ``start()``/``stop()``  — the caller owns the
               lifecycle.  Pass this from test fixtures to avoid needing
               ``ASGITransport`` to trigger lifespan.

    Returns:
        A ``FastAPI`` instance ready to serve requests.

    Edge cases:
        - If Memcached is unreachable and no ``cache`` is provided,
          ``cache.start()`` raises during lifespan startup — FastAPI
          propagates this as a startup error.
        - The store starts empty; products must be created via POST.
    """
    _store = store or ProductStore()
    _cache = cache or ProductCacheLayer(make_layered_cache(host, port))
    _manage = cache is None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        if _manage:
            await _cache.start()
        try:
            yield
        finally:
            if _manage:
                await _cache.stop()

    app = FastAPI(
        title="Product Catalog — Layered Cache (L1 In-Memory + L2 Memcached)",
        description=(
            "Demonstrates varco_core LayeredCache + varco_memcached: "
            "two-tier look-aside cache with promote-on-read and write-through."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_api_route("/health", lambda: {"status": "ok"}, methods=["GET"])
    app.include_router(build_router(_store, _cache))
    return app


__all__ = ["create_app"]
