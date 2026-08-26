"""
cache_layer.py
==============
``ProductCacheLayer`` — look-aside caching for the product catalog.

Wraps a ``RedisCache`` configured with a ``CompositeStrategy`` combining:

- ``TTLStrategy(ttl_seconds)`` — automatic time-based eviction.
- ``TaggedStrategy()``          — explicit tag-based invalidation on writes.

``get_product`` is decorated with ``@cached`` so the cache is checked
transparently before hitting the in-memory store.  ``invalidate_product``
uses ``TaggedStrategy.invalidate_tag()`` to evict a specific product entry
on the next read — no direct ``cache.delete()`` call is needed.

DESIGN: @cached + TaggedStrategy over plain cache.get/set in service
    ✅ ``@cached`` keeps the look-aside boilerplate in one place — the
       service layer stays focused on domain logic.
    ✅ ``TaggedStrategy`` invalidates by logical tag (``"product:<id>"``)
       rather than by internal Redis key — the tag is stable even if the key
       format changes.
    ✅ TTL acts as a safety-net fallback: even if invalidation is missed
       (crash, network partition), entries expire automatically.
    ❌ ``TaggedStrategy.invalidate_tag()`` is synchronous and in-process only
       — for multi-process invalidation use ``EventDrivenStrategy`` instead.

Thread safety:  ❌  Not thread-safe — single event loop.
Async safety:   ✅  All public methods are ``async def``.
"""

from __future__ import annotations

import collections.abc
import logging

from models import Product
from varco_core.cache import CompositeStrategy, TaggedStrategy, TTLStrategy
from varco_redis.cache import RedisCache, RedisCacheSettings

_logger = logging.getLogger(__name__)

# Default TTL for product entries (60 seconds).
_DEFAULT_TTL: float = 60.0


class ProductCacheLayer:
    """
    Look-aside cache layer for products backed by Redis.

    Wraps a started ``RedisCache`` and a ``TaggedStrategy`` for explicit
    per-product invalidation.  A ``TTLStrategy`` is composed alongside
    the tagged strategy as a safety-net eviction mechanism.

    Lifecycle::

        async with ProductCacheLayer(redis_url) as layer:
            product = await layer.get_product("p-1", fallback=store.get)
            await layer.invalidate_product("p-1")

    Args:
        redis_url: Redis connection URL.
        ttl:       Cache entry TTL in seconds.  Defaults to 60.
        key_prefix: Redis key namespace prefix.  Defaults to ``"catalog:"``.

    Thread safety:  ❌  Not thread-safe — single event loop.
    Async safety:   ✅  All public methods are ``async def``.

    Edge cases:
        - ``get_product()`` returns ``None`` when the backing fallback does —
          ``None`` results are never cached (``@cached`` skips ``None``).
        - ``invalidate_product()`` is synchronous inside — it marks the tag
          in ``TaggedStrategy`` so the next ``get()`` evicts the stale entry
          from Redis and re-fetches from the fallback.
        - Hits and misses are tracked in ``hits`` and ``misses`` for the
          ``/v1/cache/stats`` diagnostic endpoint.
    """

    def __init__(
        self,
        redis_url: str,
        *,
        ttl: float = _DEFAULT_TTL,
        key_prefix: str = "catalog:",
    ) -> None:
        self._ttl = ttl
        self._tagged = TaggedStrategy()
        self._strategy = CompositeStrategy(
            TTLStrategy(ttl),
            self._tagged,
        )
        settings = RedisCacheSettings(url=redis_url, key_prefix=key_prefix)
        self._cache = RedisCache(settings, strategy=self._strategy)

        # Diagnostic counters — incremented in the wrapper below.
        self.hits: int = 0
        self.misses: int = 0

    async def start(self) -> None:
        """Connect to Redis and start the invalidation strategy."""
        await self._cache.start()

    async def stop(self) -> None:
        """Close the Redis connection and stop the strategy."""
        await self._cache.stop()

    async def __aenter__(self) -> ProductCacheLayer:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    # ── Cache operations ───────────────────────────────────────────────────────

    async def get_product(
        self,
        product_id: str,
        *,
        fallback: "ProductFallback",
    ) -> Product | None:
        """
        Look-aside get: return cached product or call ``fallback`` on miss.

        Cache key: ``"product:<product_id>"``.
        Cache tag: ``"product:<product_id>"`` (for tag-based invalidation).

        Args:
            product_id: Unique product identifier.
            fallback:   Async callable ``(product_id: str) → Product | None``
                        invoked on a cache miss.  Typically ``store.get``.

        Returns:
            The ``Product`` or ``None`` if not found in store or cache.

        Edge cases:
            - ``None`` results from ``fallback`` are NOT cached — a missing
              product will always fall through to the store on each request.
        """
        cache_key = f"product:{product_id}"
        raw = await self._cache.get(cache_key)
        if raw is not None:
            self.hits += 1
            _logger.debug("ProductCacheLayer: cache hit for %r.", cache_key)
            # Deserialize dict → Product (JsonSerializer returns a dict)
            return Product(**raw) if isinstance(raw, dict) else raw

        self.misses += 1
        _logger.debug("ProductCacheLayer: cache miss for %r.", cache_key)

        product = await fallback(product_id)
        if product is None:
            return None

        # Store the product and register the tag for invalidation.
        # We use __dict__ on the dataclass so it serializes cleanly as a JSON dict.
        import dataclasses

        await self._cache.set(cache_key, dataclasses.asdict(product), ttl=self._ttl)
        # Register the tag so TaggedStrategy can map it on the next read.
        self._tagged.register_tags(cache_key, {f"product:{product_id}"})
        return product

    async def invalidate_product(self, product_id: str) -> None:
        """
        Mark the cached entry for ``product_id`` as stale.

        Calls ``TaggedStrategy.invalidate_tag()`` — on the next read the
        cache backend detects the invalidated tag and evicts the entry from
        Redis before calling the fallback.

        Args:
            product_id: Product whose cache entry should be invalidated.

        Edge cases:
            - If the entry was never cached (e.g., a new product), this is
              a no-op — the tag set is empty for that product.
        """
        tag = f"product:{product_id}"
        self._tagged.invalidate_tag(tag)
        # Also delete directly so the next get() sees a clean miss without
        # waiting for should_invalidate() to be consulted (RedisCache checks
        # the strategy on read, but a direct delete is simpler and certain).
        await self._cache.delete(f"product:{product_id}")
        _logger.debug("ProductCacheLayer: invalidated product %r.", product_id)

    def stats(self) -> dict[str, int]:
        """
        Return cache hit/miss counters.

        Returns:
            Dict with keys ``"hits"`` and ``"misses"``.
        """
        return {"hits": self.hits, "misses": self.misses}


# Type alias for type hints in get_product
ProductFallback = collections.abc.Callable[
    [str], collections.abc.Coroutine[object, object, Product | None]
]

__all__ = ["ProductCacheLayer"]
