"""
Red-mode tests for Plan 011 Phase 6 (C5) — ``varco_core.cache.base.BulkCache``
and ``CacheBackend``'s portable get_many/set_many/delete_many defaults.

Encodes D-11: AsyncCache is unchanged (not one line); BulkCache is a new,
additive, runtime_checkable Protocol; CacheBackend gets concrete portable
defaults that loop over get/set/delete; every shipped backend (InMemoryCache
here) therefore satisfies BulkCache immediately.
"""

from __future__ import annotations

import inspect

from varco_core.cache.base import AsyncCache
from varco_core.cache.memory import InMemoryCache


def test_async_cache_protocol_unchanged_no_bulk_methods() -> None:
    # D-11: AsyncCache must NOT grow get_many/set_many/delete_many — adding
    # them would break isinstance() for every out-of-tree AsyncCache impl
    # that only implements the five original methods.
    members = {name for name, _ in inspect.getmembers(AsyncCache)}
    assert "get_many" not in members
    assert "set_many" not in members
    assert "delete_many" not in members


def test_bulk_cache_is_a_new_runtime_checkable_protocol() -> None:
    from varco_core.cache.base import BulkCache

    assert hasattr(BulkCache, "_is_runtime_protocol") or hasattr(BulkCache, "__protocol_attrs__")


async def test_in_memory_cache_satisfies_bulk_cache_via_portable_defaults() -> None:
    from varco_core.cache.base import BulkCache

    cache = InMemoryCache()
    await cache.start()
    try:
        assert isinstance(cache, BulkCache)
    finally:
        await cache.stop()


async def test_get_many_portable_default_loops_over_get() -> None:
    cache = InMemoryCache()
    await cache.start()
    try:
        await cache.set("a", 1)
        await cache.set("b", 2)
        result = await cache.get_many(["a", "b", "missing"])
        assert result == {"a": 1, "b": 2}
        assert "missing" not in result or result["missing"] is None
    finally:
        await cache.stop()


async def test_set_many_portable_default_loops_over_set() -> None:
    cache = InMemoryCache()
    await cache.start()
    try:
        await cache.set_many({"x": 10, "y": 20})
        assert await cache.get("x") == 10
        assert await cache.get("y") == 20
    finally:
        await cache.stop()


async def test_delete_many_portable_default_loops_over_delete() -> None:
    cache = InMemoryCache()
    await cache.start()
    try:
        await cache.set("x", 1)
        await cache.set("y", 2)
        await cache.delete_many(["x", "y", "never-existed"])
        assert await cache.get("x") is None
        assert await cache.get("y") is None
    finally:
        await cache.stop()


async def test_cache_backend_accepts_optional_serializer_kwarg() -> None:
    # D-11: CacheBackend.__init__ takes serializer: Serializer[Any] | None,
    # reusing varco_core.serialization.Serializer rather than a new protocol.
    from varco_core.serialization import NoOpSerializer

    cache = InMemoryCache(serializer=NoOpSerializer())
    await cache.start()
    try:
        await cache.set("k", {"nested": True})
        assert await cache.get("k") == {"nested": True}
    finally:
        await cache.stop()
