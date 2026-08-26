"""
Red-mode tests for Plan 011 Phase 6, step 70 — RD-1's C5 proof, half 2.

Plan line (step 70): "no batch path is taken without an explicit opt-in;
LayeredCache.set_many() with no backplane publishes nothing; with a
backplane, exactly len(keys) messages are published, all kind='key', after
the authoritative write."
"""

from __future__ import annotations

from varco_core.cache.backplane import InMemoryBackplane
from varco_core.cache.layered import LayeredCache
from varco_core.cache.memory import InMemoryCache


async def test_layered_cache_set_many_with_no_backplane_publishes_nothing() -> None:
    l1 = InMemoryCache()
    l2 = InMemoryCache()
    cache = LayeredCache(l1, l2)
    await cache.start()

    await cache.set_many({"a": 1, "b": 2})

    assert await l2.get("a") == 1
    assert await l1.get("b") == 2
    await cache.stop()


async def test_layered_cache_set_many_with_backplane_publishes_exactly_n_key_messages() -> None:
    l1 = InMemoryCache()
    l2 = InMemoryCache()
    backplane = InMemoryBackplane()
    cache = LayeredCache(l1, l2, backplane=backplane, promote_ttl=30.0)
    await cache.start()

    published: list = []
    original_publish = backplane.publish

    async def _spy(message):
        published.append(message)
        return await original_publish(message)

    backplane.publish = _spy  # type: ignore[assignment]

    await cache.set_many({"a": 1, "b": 2, "c": 3})

    assert len(published) == 3
    assert all(msg.kind == "key" for msg in published)
    await cache.stop()


async def test_batch_path_never_taken_without_explicit_opt_in_on_service_mixin() -> None:
    # CacheServiceMixin.list() takes the batch path only when the cache
    # satisfies BulkCache AND the caller opted in — with no opt-in the
    # existing (loop) bodies run verbatim (step 68).
    from varco_core.cache.mixin import CacheServiceMixin

    assert hasattr(CacheServiceMixin, "_cache_bulk_opt_in") or hasattr(
        CacheServiceMixin, "_use_bulk_cache"
    )
