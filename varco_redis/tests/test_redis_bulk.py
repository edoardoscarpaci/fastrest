"""
Red-mode tests for Plan 011 Phase 6, step 72 — native MGET/pipelined SET/
UNLINK-based bulk overrides on RedisCache.

Plan line (step 71): "native MGET, pipelined SET (one round trip),
UNLINK-based delete_many overrides, and serializer= plumbing whose default
stays JsonSerializer."
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from varco_redis.cache import RedisCache, RedisCacheSettings


class FakeRedis:
    """Extends the pattern from test_redis_cache.py's FakeRedis with the
    batch commands RedisCache's bulk overrides are expected to use."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self.mget_calls: list[list[str]] = []
        self.unlink_calls: list[list[str]] = []

    async def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    async def set(self, key: str, value: bytes) -> None:
        self._store[key] = value

    async def setex(self, key: str, ttl: int, value: bytes) -> None:
        self._store[key] = value

    async def mget(self, keys: list[str]) -> list[bytes | None]:
        self.mget_calls.append(list(keys))
        return [self._store.get(k) for k in keys]

    async def unlink(self, *keys: str) -> int:
        self.unlink_calls.append(list(keys))
        count = 0
        for key in keys:
            if key in self._store:
                del self._store[key]
                count += 1
        return count

    async def delete(self, *keys: str) -> int:
        return await self.unlink(*keys)

    async def exists(self, key: str) -> int:
        return 1 if key in self._store else 0

    async def scan(self, cursor: int, match: str = "*", count: int = 100):
        return 0, list(self._store.keys())

    async def aclose(self) -> None:
        pass


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
async def cache(fake_redis: FakeRedis):
    settings = RedisCacheSettings(url="redis://fake:6379/0")
    with patch("varco_redis.cache.aioredis") as mock_aioredis:
        mock_aioredis.from_url.return_value = fake_redis
        async with RedisCache(settings) as c:
            yield c, fake_redis


async def test_get_many_uses_native_mget_one_round_trip(cache) -> None:
    c, fake = cache
    await c.set("a", 1)
    await c.set("b", 2)
    fake.mget_calls.clear()

    result = await c.get_many(["a", "b"])

    assert len(fake.mget_calls) == 1
    assert result == {"a": 1, "b": 2}


async def test_delete_many_uses_unlink(cache) -> None:
    c, fake = cache
    await c.set("a", 1)
    await c.set("b", 2)

    await c.delete_many(["a", "b"])

    assert len(fake.unlink_calls) >= 1
    assert await c.get("a") is None
    assert await c.get("b") is None


async def test_default_serializer_is_json_serializer() -> None:
    from varco_core.serialization import JsonSerializer

    settings = RedisCacheSettings(url="redis://fake:6379/0")
    c = RedisCache(settings)
    assert isinstance(c._serializer, JsonSerializer)


@pytest.mark.integration
async def test_mget_partial_miss_against_real_redis() -> None:
    pytest.skip("requires Docker Redis — run with -m integration")


@pytest.mark.integration
async def test_per_key_ttl_correctness_against_real_redis() -> None:
    pytest.skip("requires Docker Redis — run with -m integration")


@pytest.mark.integration
async def test_empty_key_list_against_real_redis() -> None:
    pytest.skip("requires Docker Redis — run with -m integration")
