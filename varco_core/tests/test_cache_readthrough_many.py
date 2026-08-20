"""
Red-mode tests for Plan 011 Phase 6, step 66 —
varco_core.cache.readthrough.read_through_many.

Plan line (step 65): "read_through_many(cache, keys, loader, policy, *,
type_hint=None, singleflight=None) ... per-key leadership election with ONE
batched loader(missing_keys) call for the led keys, followers
asyncio.shield-ed -> wrap + set_many."
Plan line (step 66): "the loader is called ONCE with exactly the missing
keys; a single read_through() for one of those keys becomes a follower of
the SAME slot rather than racing; ... a loader raising fails all led keys
and clears their slots."
"""

from __future__ import annotations

import asyncio

import pytest
from varco_core.cache.memory import InMemoryCache
from varco_core.cache.policy import CachePolicy
from varco_core.cache.readthrough import read_through, read_through_many
from varco_core.cache.singleflight import Singleflight


async def test_loader_called_once_with_exactly_the_missing_keys() -> None:
    cache = InMemoryCache()
    await cache.start()
    await cache.set("k1", "cached-1")

    calls: list[list[str]] = []

    async def loader(missing_keys: list[str]) -> dict[str, str]:
        calls.append(list(missing_keys))
        return {k: f"loaded-{k}" for k in missing_keys}

    result = await read_through_many(
        cache, ["k1", "k2", "k3"], loader, CachePolicy(ttl=60.0)
    )

    assert calls == [["k2", "k3"]]
    assert result == {"k1": "cached-1", "k2": "loaded-k2", "k3": "loaded-k3"}
    await cache.stop()


async def test_single_read_through_for_missing_key_joins_same_singleflight_slot() -> (
    None
):
    cache = InMemoryCache()
    await cache.start()
    sf = Singleflight()

    load_count = {"n": 0}

    async def batch_loader(missing_keys: list[str]) -> dict[str, str]:
        load_count["n"] += 1
        await asyncio.sleep(0.01)
        return {k: "batched" for k in missing_keys}

    async def single_loader() -> str:
        load_count["n"] += 1
        await asyncio.sleep(0.01)
        return "single"

    # DEVIATION: added singleflight=True to CachePolicy — passing a
    # Singleflight INSTANCE alone does not opt a policy into coalescing;
    # `policy.singleflight` is the actual gate (mirrors read_through()'s
    # pre-existing contract and every other singleflight test in this
    # suite, e.g. test_cache_swr.py / test_cache_readthrough.py).
    policy = CachePolicy(ttl=60.0, singleflight=True)
    await asyncio.gather(
        read_through_many(cache, ["shared"], batch_loader, policy, singleflight=sf),
        read_through(cache, "shared", single_loader, policy, singleflight=sf),
    )
    # Only one of the two loaders should have actually executed — the other
    # call became a follower on the same Singleflight slot for "shared".
    assert load_count["n"] == 1
    await cache.stop()


async def test_loader_raising_fails_all_led_keys_and_clears_slots() -> None:
    cache = InMemoryCache()
    await cache.start()
    sf = Singleflight()

    async def failing_loader(missing_keys: list[str]) -> dict[str, str]:
        raise RuntimeError("boom")

    # DEVIATION: singleflight=True added — see the note above.
    policy = CachePolicy(ttl=60.0, singleflight=True)
    with pytest.raises(RuntimeError, match="boom"):
        await read_through_many(
            cache, ["a", "b"], failing_loader, policy, singleflight=sf
        )

    # Slots must be cleared — a subsequent call is a fresh attempt, not a
    # forever-broken future.
    assert sf.in_flight == 0
    await cache.stop()


async def test_key_absent_from_loader_result_resolves_to_none() -> None:
    cache = InMemoryCache()
    await cache.start()

    async def loader(missing_keys: list[str]) -> dict[str, str]:
        return {}  # omits every requested key

    result = await read_through_many(cache, ["missing"], loader, CachePolicy(ttl=60.0))
    assert result["missing"] is None
    await cache.stop()


async def test_absent_key_negative_cached_only_when_negative_ttl_set() -> None:
    cache = InMemoryCache()
    await cache.start()

    calls = {"n": 0}

    async def loader(missing_keys: list[str]) -> dict[str, str]:
        calls["n"] += 1
        return {}

    policy = CachePolicy(ttl=60.0, negative_ttl=30.0)
    await read_through_many(cache, ["k"], loader, policy)
    await read_through_many(cache, ["k"], loader, policy)

    # Second call should be served from the negative cache entry, not the loader again.
    assert calls["n"] == 1
    await cache.stop()


async def test_empty_keys_list_no_round_trip_no_message_returns_empty() -> None:
    cache = InMemoryCache()
    await cache.start()

    called = {"n": 0}

    async def loader(missing_keys: list[str]) -> dict[str, str]:
        called["n"] += 1
        return {}

    result = await read_through_many(cache, [], loader, CachePolicy(ttl=60.0))
    assert result == {}
    assert called["n"] == 0
    await cache.stop()
