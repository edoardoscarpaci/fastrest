"""
CacheBackendConformance — shared contract tests for ``CacheBackend``
implementations (Plan 012 / RT6, Step 23).

Subclass and override the ``cache`` fixture to opt a backend in::

    from varco_conformance.cache import CacheBackendConformance

    class TestRedisCacheConformance(CacheBackendConformance):
        @pytest.fixture
        async def cache(self, redis_url):
            async with RedisCache(RedisCacheSettings(url=redis_url)) as cache:
                yield cache

``NoOpCache`` legitimately cannot satisfy set→get (a no-op cache never
stores anything) — give it its own subclass overriding the
storage-semantics tests with the no-op expectation, per Step 26 / the
plan's edge-case table, instead of loosening this shared suite.

Not named ``Test*`` — never collected standalone (see package docstring).
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from varco_core.cache.base import AsyncCache, CacheBackend


class CacheBackendConformance:
    """Shared behavioural contract for ``CacheBackend``."""

    @pytest.fixture
    async def cache(self) -> CacheBackend:
        """Abstract — must be overridden by every subclass."""
        raise NotImplementedError(
            "CacheBackendConformance subclasses must override the `cache` "
            "fixture with a concrete CacheBackend implementation."
        )

    def _key(self, suffix: str = "") -> str:
        return f"conformance:{uuid4().hex[:8]}:{suffix}"

    async def test_get_miss_returns_none(self, cache: CacheBackend) -> None:
        assert await cache.get(self._key("missing")) is None

    async def test_set_get_round_trip(self, cache: CacheBackend) -> None:
        key = self._key("roundtrip")
        await cache.set(key, "value-1")
        assert await cache.get(key) == "value-1"

    async def test_ttl_expiry(self, cache: CacheBackend) -> None:
        key = self._key("ttl")
        await cache.set(key, "expires-soon", ttl=0.05)
        assert await cache.get(key) == "expires-soon"
        await asyncio.sleep(0.3)
        assert await cache.get(key) is None

    async def test_delete(self, cache: CacheBackend) -> None:
        key = self._key("delete")
        await cache.set(key, "to-be-deleted")
        await cache.delete(key)
        assert await cache.get(key) is None

    async def test_delete_is_idempotent(self, cache: CacheBackend) -> None:
        # Deleting an absent key must not raise.
        await cache.delete(self._key("never-existed"))

    async def test_clear(self, cache: CacheBackend) -> None:
        key = self._key("clear")
        await cache.set(key, "value")
        await cache.clear()
        assert await cache.get(key) is None

    async def test_overwrite(self, cache: CacheBackend) -> None:
        key = self._key("overwrite")
        await cache.set(key, "first")
        await cache.set(key, "second")
        assert await cache.get(key) == "second"

    async def test_exists(self, cache: CacheBackend) -> None:
        key = self._key("exists")
        assert await cache.exists(key) is False
        await cache.set(key, "value")
        assert await cache.exists(key) is True

    async def test_get_many_partial_hit(self, cache: CacheBackend) -> None:
        prefix = self._key("many")
        key_hit = f"{prefix}:hit"
        key_miss = f"{prefix}:miss"
        await cache.set(key_hit, "value")

        result = await cache.get_many([key_hit, key_miss])

        assert result.get(key_hit) == "value"
        assert key_miss not in result

    async def test_set_many_round_trip(self, cache: CacheBackend) -> None:
        prefix = self._key("setmany")
        items = {f"{prefix}:a": "1", f"{prefix}:b": "2"}
        await cache.set_many(items)

        assert await cache.get(f"{prefix}:a") == "1"
        assert await cache.get(f"{prefix}:b") == "2"

    async def test_delete_many(self, cache: CacheBackend) -> None:
        prefix = self._key("delmany")
        keys = [f"{prefix}:a", f"{prefix}:b"]
        await cache.set_many({k: "v" for k in keys})
        await cache.delete_many(keys)

        for k in keys:
            assert await cache.get(k) is None

    def test_backend_satisfies_async_cache_protocol(self, cache: CacheBackend) -> None:
        # D-11 invariant (CLAUDE.md pitfall table): every CacheBackend
        # satisfies the runtime_checkable AsyncCache Protocol.
        assert isinstance(cache, AsyncCache)
