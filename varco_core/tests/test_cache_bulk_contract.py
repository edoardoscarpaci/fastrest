"""
Red-mode tests for Plan 011 Phase 6, step 63 — RD-1's C5 proof, half 1.

Plan line (step 63): "A frozen literal set of AsyncCache's Protocol members
asserts it is UNCHANGED (the D-11 regression guard) ... every shipped
backend satisfies isinstance(backend, BulkCache); the portable defaults are
loops with the same per-key semantics as get/set/delete."
"""

from __future__ import annotations

import inspect

from varco_core.cache.base import AsyncCache
from varco_core.cache.memory import InMemoryCache, NoOpCache

# Frozen literal — the D-11 regression guard. Adding a member here silently
# flips isinstance(x, AsyncCache) to False for every out-of-tree cache.
EXPECTED_ASYNC_CACHE_MEMBERS = {
    "get",
    "set",
    "delete",
    "exists",
    "clear",
    "delete_prefix",
}


def test_async_cache_protocol_member_set_is_frozen() -> None:
    members = {
        name
        for name, value in inspect.getmembers(AsyncCache)
        if not name.startswith("_")
    }
    assert members == EXPECTED_ASYNC_CACHE_MEMBERS


async def test_every_shipped_backend_satisfies_bulk_cache() -> None:
    from varco_core.cache.base import BulkCache

    for backend in (InMemoryCache(), NoOpCache()):
        await backend.start()
        try:
            assert isinstance(backend, BulkCache)
        finally:
            await backend.stop()


async def test_get_many_default_has_same_per_key_semantics_as_get() -> None:
    cache = InMemoryCache()
    await cache.start()
    await cache.set("present", "value")

    single = await cache.get("present")
    bulk = await cache.get_many(["present"])
    assert bulk["present"] == single
    await cache.stop()
