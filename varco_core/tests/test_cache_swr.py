"""
tests.test_cache_swr
======================
Plan 010 Phase 4, step 36 — stale-while-revalidate activation in
``read_through()``.

RED until the soft-stale branch in ``varco_core/cache/readthrough.py`` is
activated.
"""

from __future__ import annotations

import asyncio

import pytest

from varco_core.cache.memory import InMemoryCache
from varco_core.cache.policy import CachePolicy
from varco_core.cache.readthrough import read_through
from varco_core.cache.singleflight import Singleflight


@pytest.fixture()
async def cache() -> InMemoryCache:
    c = InMemoryCache()
    await c.start()
    yield c
    await c.stop()


async def _seed_soft_expired(cache: InMemoryCache, key: str, value) -> None:
    """Write a value whose soft TTL has already elapsed but hard TTL has not."""
    import time

    from varco_core.cache.envelope import CacheEnvelope, wrap

    now = time.time()
    env = CacheEnvelope(
        value=value,
        stored_at=now - 100,
        soft_expires_at=now - 1,  # already soft-expired
        hard_expires_at=now + 1000,  # still hard-fresh
        is_negative=False,
    )
    await cache.set(key, wrap(env))


class TestStaleWhileRevalidate:
    async def test_soft_expired_entry_returned_immediately_one_refresh(
        self, cache
    ) -> None:
        await _seed_soft_expired(cache, "k", "stale-value")

        sf = Singleflight()
        refresh_started = asyncio.Event()
        refresh_calls = 0

        async def loader():
            nonlocal refresh_calls
            refresh_calls += 1
            refresh_started.set()
            await asyncio.sleep(0.05)
            return "fresh-value"

        policy = CachePolicy(ttl=1000.0, soft_ttl=1.0, singleflight=True)
        result = await read_through(cache, "k", loader, policy, singleflight=sf)

        # Must return immediately with the stale value — not await the loader.
        assert result == "stale-value"

        await refresh_started.wait()
        await asyncio.sleep(0.1)
        assert refresh_calls == 1

    async def test_fifty_concurrent_soft_stale_reads_trigger_one_refresh(
        self, cache
    ) -> None:
        await _seed_soft_expired(cache, "k", "stale-value")

        sf = Singleflight()
        refresh_calls = 0

        async def loader():
            nonlocal refresh_calls
            refresh_calls += 1
            await asyncio.sleep(0.05)
            return "fresh-value"

        policy = CachePolicy(ttl=1000.0, soft_ttl=1.0, singleflight=True)
        results = await asyncio.gather(
            *[
                read_through(cache, "k", loader, policy, singleflight=sf)
                for _ in range(50)
            ]
        )
        assert all(r == "stale-value" for r in results)

        await asyncio.sleep(0.15)
        assert refresh_calls == 1

    async def test_cold_reader_during_in_flight_refresh_becomes_follower(
        self, cache
    ) -> None:
        sf = Singleflight()
        calls = 0

        async def slow_loader():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.08)
            return "computed"

        policy = CachePolicy(ttl=1000.0, singleflight=True)

        # Start a cold-miss recompute in the background.
        first = asyncio.create_task(
            read_through(cache, "k", slow_loader, policy, singleflight=sf)
        )
        await asyncio.sleep(0.01)
        # A second cold reader for the same key must become a follower, not
        # start a second recompute.
        second = asyncio.create_task(
            read_through(cache, "k", slow_loader, policy, singleflight=sf)
        )

        val1, val2 = await asyncio.gather(first, second)
        assert calls == 1
        assert val1 == val2 == "computed"

    async def test_hard_expired_entry_blocks_and_recomputes(self, cache) -> None:
        import time

        from varco_core.cache.envelope import CacheEnvelope, wrap

        now = time.time()
        env = CacheEnvelope(
            value="old",
            stored_at=now - 1000,
            soft_expires_at=None,
            hard_expires_at=now - 1,  # hard-expired
            is_negative=False,
        )
        await cache.set("k", wrap(env))

        async def loader():
            return "new"

        policy = CachePolicy(ttl=60.0)
        result = await read_through(cache, "k", loader, policy)
        assert result == "new"

    async def test_refresh_mode_blocking_awaits_the_refresh(self, cache) -> None:
        await _seed_soft_expired(cache, "k", "stale-value")

        async def loader():
            await asyncio.sleep(0.02)
            return "fresh-value"

        policy = CachePolicy(
            ttl=1000.0, soft_ttl=1.0, singleflight=True, refresh_mode="blocking"
        )
        result = await read_through(
            cache, "k", loader, policy, singleflight=Singleflight()
        )
        # Blocking mode must wait for the refresh and return the fresh value.
        assert result == "fresh-value"

    async def test_refresh_tasks_drained_by_singleflight_aclose(self, cache) -> None:
        await _seed_soft_expired(cache, "k", "stale-value")
        sf = Singleflight()

        async def loader():
            await asyncio.sleep(0.05)
            return "fresh-value"

        policy = CachePolicy(ttl=1000.0, soft_ttl=1.0, singleflight=True)
        await read_through(cache, "k", loader, policy, singleflight=sf)
        await sf.aclose()  # must not raise / must drain outstanding refresh
