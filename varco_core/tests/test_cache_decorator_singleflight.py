"""
tests.test_cache_decorator_singleflight
==========================================
Plan 010 Phase 1, step 13 — ``@cached(policy=..., singleflight=...)`` stampede
protection.

RED until ``varco_core/cache/decorator.py`` gains the ``policy=``/
``singleflight=`` keyword-only params.
"""

from __future__ import annotations

import asyncio

import pytest
from varco_core.cache.decorator import cached
from varco_core.cache.memory import InMemoryCache


@pytest.fixture()
async def cache() -> InMemoryCache:
    c = InMemoryCache()
    await c.start()
    yield c
    await c.stop()


class TestDecoratorSingleflight:
    async def test_singleflight_true_calls_loader_once_under_stampede(
        self, cache
    ) -> None:
        from varco_core.cache.policy import CachePolicy

        calls = 0

        @cached(cache, policy=CachePolicy(ttl=60.0), singleflight=True, namespace="sf")
        async def get_user(user_id: int) -> dict:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            return {"id": user_id}

        results = await asyncio.gather(*[get_user(42) for _ in range(100)])
        assert calls == 1
        assert all(r == {"id": 42} for r in results)

    async def test_default_no_singleflight_reproduces_the_stampede_bug(
        self, cache
    ) -> None:
        calls = 0

        @cached(cache, ttl=60.0, namespace="nosf")
        async def get_user(user_id: int) -> dict:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return {"id": user_id}

        # Documents today's actual (undesirable) behaviour: with no
        # singleflight, every concurrent cold miss re-invokes the loader.
        results = await asyncio.gather(*[get_user(7) for _ in range(100)])
        assert calls == 100
        assert all(r == {"id": 7} for r in results)

    async def test_aclose_is_exposed_on_the_wrapper(self, cache) -> None:
        from varco_core.cache.policy import CachePolicy

        @cached(cache, policy=CachePolicy(ttl=60.0), singleflight=True, namespace="ac")
        async def get_thing(x: int) -> int:
            return x

        await get_thing(1)
        await get_thing.aclose()  # must not raise
