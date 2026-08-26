"""
tests.test_cache_negative
============================
Plan 010 Phase 4, step 40 — negative caching activation in ``read_through()``.

RED until the negative-caching branch in ``varco_core/cache/readthrough.py``
is activated (D-4: opt-in only).
"""

from __future__ import annotations

import pytest
from varco_core.cache.memory import InMemoryCache
from varco_core.cache.policy import CachePolicy
from varco_core.cache.readthrough import read_through


@pytest.fixture()
async def cache() -> InMemoryCache:
    c = InMemoryCache()
    await c.start()
    yield c
    await c.stop()


class TestNegativeCaching:
    async def test_default_policy_none_result_is_not_cached(self, cache) -> None:
        calls = 0

        async def loader():
            nonlocal calls
            calls += 1
            return None

        await read_through(cache, "k", loader, CachePolicy())
        await read_through(cache, "k", loader, CachePolicy())
        assert calls == 2  # unchanged-behaviour guard (D-4)

    async def test_negative_ttl_set_caches_none_and_skips_loader_on_second_call(
        self, cache
    ) -> None:
        calls = 0

        async def loader():
            nonlocal calls
            calls += 1
            return None

        policy = CachePolicy(negative_ttl=30.0)
        first = await read_through(cache, "k", loader, policy)
        second = await read_through(cache, "k", loader, policy)

        assert first is None
        assert second is None
        assert calls == 1  # second call short-circuits without invoking loader

    async def test_negative_entry_returns_raw_none_not_the_envelope(
        self, cache
    ) -> None:
        async def loader():
            return None

        policy = CachePolicy(negative_ttl=30.0)
        result = await read_through(cache, "k", loader, policy)
        assert result is None  # caller must never see the wrapper dict

    async def test_negative_entry_expires_on_negative_ttl_not_ttl(self, cache) -> None:
        import time

        from varco_core.cache.envelope import CacheEnvelope, wrap

        now = time.time()
        # A negative entry whose hard_expires_at (driven by negative_ttl) has
        # already passed must be treated as absent, triggering a recompute.
        env = CacheEnvelope(
            value=None,
            stored_at=now - 100,
            soft_expires_at=None,
            hard_expires_at=now - 1,
            is_negative=True,
        )
        await cache.set("k", wrap(env))

        calls = 0

        async def loader():
            nonlocal calls
            calls += 1
            return "no-longer-none"

        policy = CachePolicy(negative_ttl=30.0)
        result = await read_through(cache, "k", loader, policy)
        assert calls == 1
        assert result == "no-longer-none"

    async def test_negative_hit_records_kind_negative(self, cache) -> None:
        from unittest import mock

        async def loader():
            return None

        policy = CachePolicy(negative_ttl=30.0)
        await read_through(cache, "k", loader, policy)

        with mock.patch("varco_core.cache.readthrough.record_cache_hit") as mocked_hit:
            await read_through(cache, "k", loader, policy)
            assert any(
                call.kwargs.get("kind") == "negative"
                for call in mocked_hit.call_args_list
            )

    async def test_negative_entry_invalidated_by_delete(self, cache) -> None:
        calls = 0

        async def loader():
            nonlocal calls
            calls += 1
            return None

        policy = CachePolicy(negative_ttl=30.0)
        await read_through(cache, "k", loader, policy)
        await cache.delete("k")

        await read_through(cache, "k", loader, policy)
        assert calls == 2  # deletion clears the negative entry like any other
