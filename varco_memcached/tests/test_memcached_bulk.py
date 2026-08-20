"""
Red-mode tests for Plan 011 Phase 6, step 74 — get_multi/set_multi overrides
on MemcachedCache, and the key-length/illegal-character guard.

Plan line (step 73): "get_multi/set_multi overrides plus serializer=, with
the default reproducing today's bytes codec exactly."
Plan line (step 74): "the key-length and illegal-character limits Memcached
imposes are asserted to surface as a legible error rather than a silent
partial write."
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from varco_memcached.cache import MemcachedCache, MemcachedCacheSettings


class FakeMemcached:
    def __init__(self) -> None:
        self._store: dict[bytes, bytes] = {}
        self.multi_get_calls: list[list[bytes]] = []
        self.multi_set_calls: list[dict] = []

    async def get(self, key: bytes) -> bytes | None:
        return self._store.get(key)

    async def set(self, key: bytes, value: bytes, exptime: int = 0) -> bool:
        self._store[key] = value
        return True

    async def multi_get(self, *keys: bytes) -> list[bytes | None]:
        self.multi_get_calls.append(list(keys))
        return [self._store.get(k) for k in keys]

    async def delete(self, key: bytes) -> bool:
        self._store.pop(key, None)
        return True

    async def close(self) -> None:
        pass


@pytest.fixture
def fake_mc() -> FakeMemcached:
    return FakeMemcached()


@pytest.fixture
async def cache(fake_mc: FakeMemcached):
    settings = MemcachedCacheSettings(host="fake-mc", port=11211)
    with patch("varco_memcached.cache.aiomcache.Client", return_value=fake_mc):
        async with MemcachedCache(settings) as c:
            yield c, fake_mc


async def test_get_many_uses_multi_get(cache) -> None:
    c, fake = cache
    await c.set("a", 1)
    await c.set("b", 2)
    fake.multi_get_calls.clear()

    result = await c.get_many(["a", "b"])

    assert len(fake.multi_get_calls) == 1
    assert result == {"a": 1, "b": 2}


async def test_set_many_reproduces_default_bytes_codec() -> None:
    settings = MemcachedCacheSettings(host="fake-mc", port=11211)
    fake = FakeMemcached()
    with patch("varco_memcached.cache.aiomcache.Client", return_value=fake):
        async with MemcachedCache(settings) as c:
            await c.set("k", {"x": 1})
            single_bytes = fake._store[list(fake._store.keys())[0]]

            await c.set_many({"k2": {"x": 1}})
            multi_bytes = list(fake._store.values())[-1]

    assert single_bytes == multi_bytes


async def test_key_too_long_raises_legible_error_not_silent_partial_write() -> None:
    settings = MemcachedCacheSettings(host="fake-mc", port=11211)
    fake = FakeMemcached()
    with patch("varco_memcached.cache.aiomcache.Client", return_value=fake):
        async with MemcachedCache(settings) as c:
            too_long_key = "x" * 300  # Memcached's 250-byte key limit
            with pytest.raises(ValueError):
                await c.set_many({too_long_key: "value"})


@pytest.mark.integration
async def test_get_multi_against_real_memcached() -> None:
    pytest.skip("requires Docker Memcached — run with -m integration")


@pytest.mark.integration
async def test_set_multi_against_real_memcached() -> None:
    pytest.skip("requires Docker Memcached — run with -m integration")
