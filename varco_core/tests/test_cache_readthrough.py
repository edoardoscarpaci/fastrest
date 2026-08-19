"""
tests.test_cache_readthrough
==============================
Plan 010 Phase 0, step 8 — ``varco_core.cache.readthrough.read_through``.

RED until ``varco_core/cache/readthrough.py`` lands.
"""

from __future__ import annotations

import pytest

from varco_core.cache.memory import InMemoryCache


@pytest.fixture()
async def cache() -> InMemoryCache:
    c = InMemoryCache()
    await c.start()
    yield c
    await c.stop()


class TestReadThroughByteIdenticalDefault:
    async def test_default_policy_writes_raw_value_no_envelope(self, cache) -> None:
        from varco_core.cache.envelope import MARKER
        from varco_core.cache.policy import CachePolicy
        from varco_core.cache.readthrough import read_through

        async def loader() -> dict:
            return {"a": 1}

        result = await read_through(cache, "k", loader, CachePolicy())
        assert result == {"a": 1}

        stored = await cache.get("k")
        # No envelope marker should ever appear in the store under the
        # identity policy — byte-identical to pre-plan behaviour.
        assert not (isinstance(stored, dict) and MARKER in stored)
        assert stored == {"a": 1}

    async def test_default_policy_does_not_cache_none_result(self, cache) -> None:
        from varco_core.cache.policy import CachePolicy
        from varco_core.cache.readthrough import read_through

        calls = 0

        async def loader():
            nonlocal calls
            calls += 1
            return None

        await read_through(cache, "k", loader, CachePolicy())
        await read_through(cache, "k", loader, CachePolicy())

        # A None result must never be cached under the default policy (D-4) —
        # the loader runs again on the second call.
        assert calls == 2

    async def test_legacy_raw_value_already_in_store_is_served_as_hit(
        self, cache
    ) -> None:
        from varco_core.cache.policy import CachePolicy
        from varco_core.cache.readthrough import read_through

        await cache.set("k", {"legacy": True})
        calls = 0

        async def loader():
            nonlocal calls
            calls += 1
            return {"fresh": True}

        result = await read_through(cache, "k", loader, CachePolicy())
        assert result == {"legacy": True}
        assert calls == 0

    async def test_never_constructs_a_cache_key(self, cache) -> None:
        from varco_core.cache.policy import CachePolicy
        from varco_core.cache.readthrough import read_through

        # Passing an already-final key must be used verbatim — read_through
        # must not namespace/hash it itself (that is the caller's job).
        async def loader():
            return "v"

        await read_through(cache, "already:final:key", loader, CachePolicy())
        assert await cache.get("already:final:key") == "v"


class TestReadThroughSingleflightIntegration:
    async def test_singleflight_passed_through_coalesces_cold_miss(self, cache) -> None:
        import asyncio

        from varco_core.cache.policy import CachePolicy
        from varco_core.cache.readthrough import read_through
        from varco_core.cache.singleflight import Singleflight

        sf = Singleflight()
        calls = 0

        async def loader():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            return "v"

        policy = CachePolicy(ttl=60.0, singleflight=True)
        results = await asyncio.gather(
            *[
                read_through(cache, "k", loader, policy, singleflight=sf)
                for _ in range(20)
            ]
        )
        assert calls == 1
        assert all(r == "v" for r in results)
