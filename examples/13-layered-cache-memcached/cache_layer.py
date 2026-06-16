"""
cache_layer.py
==============
``ProductCacheLayer`` — two-tier look-aside cache for the product catalog.

Architecture
------------
::

    Request
      ↓
    L1 InMemoryCache (fast, process-local, short TTL)
      ↓ miss
    L2 MemcachedCache (shared, network, longer TTL)
      ↓ miss
    ProductStore (authoritative in-memory store)

Reads walk from L1 to L2.  On an L2 hit the value is promoted back to L1
with ``promote_ttl`` (30 s) to warm the local cache.  Writes propagate to
both layers (write-through).  Invalidation calls ``LayeredCache.delete()``
which removes the entry from all layers simultaneously.

DESIGN: LayeredCache over a hand-rolled two-level cache
    ✅ ``LayeredCache`` handles promote-on-read, write-through, and lifecycle
       orchestration automatically.
    ✅ ``InMemoryCache`` absorbs hot reads at zero network cost.
    ✅ ``MemcachedCache`` shares state across processes (multi-process safe).
    ❌ Memcached has no key enumeration — ``clear()`` is best-effort
       (in-process key registry only; keys set by other processes expire via TTL).

Thread safety:  ❌  Not thread-safe — single event loop.
Async safety:   ✅  All public methods are ``async def``.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import logging

from varco_core.cache import InMemoryCache, LayeredCache, TTLStrategy
from varco_core.cache.base import AsyncCache
from varco_memcached.cache import MemcachedCache, MemcachedCacheSettings

from models import Product

_logger = logging.getLogger(__name__)

# L1 TTL — keep hot entries in process for 30 s.
_L1_TTL: float = 30.0
# L2 TTL — Memcached keeps entries for 5 min.
_L2_TTL: float = 300.0


def make_layered_cache(
    host: str = "localhost",
    port: int = 11211,
    *,
    l1_ttl: float = _L1_TTL,
    l2_ttl: float = _L2_TTL,
    key_prefix: str = "ex13:",
) -> LayeredCache:
    """
    Build the two-tier ``LayeredCache``.

    Args:
        host:       Memcached server hostname.
        port:       Memcached server port.
        l1_ttl:     L1 InMemoryCache TTL in seconds.
        l2_ttl:     L2 MemcachedCache TTL in seconds (Memcached ``exptime``).
        key_prefix: Key namespace for Memcached entries.

    Returns:
        An unstarted ``LayeredCache`` (call ``start()`` before use).

    Edge cases:
        - L1 TTL should be ≤ L2 TTL so the in-process cache always expires
          before the shared Memcached entry.
        - ``key_prefix`` should end with a separator (e.g. ``":"``) to avoid
          namespace collisions on a shared Memcached server.
    """
    l1 = InMemoryCache(strategy=TTLStrategy(l1_ttl))
    l2 = MemcachedCache(
        MemcachedCacheSettings(
            host=host,
            port=port,
            key_prefix=key_prefix,
        )
    )
    return LayeredCache(l1, l2, write_mode="write-through", promote_ttl=l1_ttl)


class ProductCacheLayer:
    """
    Look-aside cache layer for products.

    Wraps an ``AsyncCache`` (typically a ``LayeredCache``, but accepts any
    backend so tests can inject ``NoOpCache`` or ``InMemoryCache`` without
    a real Memcached server).

    Lifecycle::

        async with ProductCacheLayer(cache) as layer:
            product = await layer.get_product("p-1", fallback=store.get)
            await layer.invalidate_product("p-1")

    Args:
        cache:   Any ``AsyncCache`` implementation.  Pass the result of
                 ``make_layered_cache()`` for production; pass ``NoOpCache()``
                 or ``InMemoryCache()`` for tests.
        default_ttl: TTL forwarded to ``cache.set()`` on writes.

    Thread safety:  ❌  Not thread-safe — single event loop.
    Async safety:   ✅  All public methods are ``async def``.

    Edge cases:
        - ``None`` results from the fallback are NOT cached — a missing
          product always falls through to the store.
        - Hit/miss counters are in-process only (not shared across replicas).
    """

    def __init__(self, cache: AsyncCache, *, default_ttl: float = _L2_TTL) -> None:
        self._cache = cache
        self._default_ttl = default_ttl
        self.hits: int = 0
        self.misses: int = 0

    async def start(self) -> None:
        """Start the underlying cache backend."""
        if hasattr(self._cache, "start"):
            await self._cache.start()  # type: ignore[union-attr]

    async def stop(self) -> None:
        """Stop the underlying cache backend."""
        if hasattr(self._cache, "stop"):
            await self._cache.stop()  # type: ignore[union-attr]

    async def __aenter__(self) -> ProductCacheLayer:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    # ── Cache operations ───────────────────────────────────────────────────────

    @staticmethod
    def _cache_key(product_id: str) -> str:
        return f"product:{product_id}"

    async def get_product(
        self,
        product_id: str,
        *,
        fallback: "ProductFallback",
    ) -> Product | None:
        """
        Look-aside get: return cached product or call ``fallback`` on miss.

        On a hit, increments ``self.hits``.  On a miss, calls ``fallback``,
        stores the result (if not ``None``), and increments ``self.misses``.

        Args:
            product_id: Unique product identifier.
            fallback:   Async callable ``(product_id: str) → Product | None``
                        invoked on a cache miss.

        Returns:
            ``Product`` or ``None`` if not found.
        """
        key = self._cache_key(product_id)
        raw = await self._cache.get(key)
        if raw is not None:
            self.hits += 1
            _logger.debug("cache hit: %s", key)
            return Product(**raw) if isinstance(raw, dict) else raw

        self.misses += 1
        _logger.debug("cache miss: %s", key)

        product = await fallback(product_id)
        if product is None:
            return None

        await self._cache.set(key, dataclasses.asdict(product), ttl=self._default_ttl)
        return product

    async def invalidate_product(self, product_id: str) -> None:
        """
        Evict the cached entry for ``product_id`` from all layers.

        Args:
            product_id: Product whose cache entry should be removed.
        """
        await self._cache.delete(self._cache_key(product_id))
        _logger.debug("invalidated: product:%s", product_id)

    def stats(self) -> dict[str, int]:
        """Return cache hit/miss counters."""
        return {"hits": self.hits, "misses": self.misses}


ProductFallback = collections.abc.Callable[
    [str], collections.abc.Coroutine[object, object, Product | None]
]

__all__ = ["make_layered_cache", "ProductCacheLayer"]
