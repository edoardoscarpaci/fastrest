"""
Red-mode tests for Plan 011 Phase 6, step 67 — Plan 010's tenant landmine
retested for the bulk path, plus RD-6 (no locale/timezone segment in bulk
cache keys).

Plan line (step 67): "the same pks under two tenant_context() blocks
produce two batched loader calls, and every coalescing key carries the
tenant:{id}: segment. Also asserts RD-6: no locale or timezone segment
appears in any key produced by the bulk path."
"""

from __future__ import annotations

from varco_core.cache.memory import InMemoryCache
from varco_core.cache.policy import CachePolicy
from varco_core.cache.readthrough import read_through_many
from varco_core.context.request import request_context
from varco_core.service.tenant import tenant_context
from varco_core.tenancy.cache_key import tenancy_cache_key


class _Entity:
    pass


async def test_same_pks_under_two_tenants_produce_two_batched_loader_calls() -> None:
    cache = InMemoryCache()
    await cache.start()

    calls: list[list[str]] = []

    async def loader(missing_keys: list[str]) -> dict[str, str]:
        calls.append(list(missing_keys))
        return {k: "v" for k in missing_keys}

    with tenant_context("tenant-a"):
        keys_a = [tenancy_cache_key(_Entity, "1"), tenancy_cache_key(_Entity, "2")]
        await read_through_many(cache, keys_a, loader, CachePolicy(ttl=60.0))

    with tenant_context("tenant-b"):
        keys_b = [tenancy_cache_key(_Entity, "1"), tenancy_cache_key(_Entity, "2")]
        await read_through_many(cache, keys_b, loader, CachePolicy(ttl=60.0))

    assert len(calls) == 2
    assert all("tenant-a" in k for k in calls[0])
    assert all("tenant-b" in k for k in calls[1])
    await cache.stop()


async def test_bulk_path_keys_never_carry_a_locale_or_timezone_segment() -> None:
    # RD-6: cache the unlocalized representation; locale/timezone are never
    # implicit cache-key components, including on the new bulk path.
    cache = InMemoryCache()
    await cache.start()

    async def loader(missing_keys: list[str]) -> dict[str, str]:
        return {k: "v" for k in missing_keys}

    with tenant_context("acme"), request_context(locale="fr"):
        key = tenancy_cache_key(_Entity, "1")
        await read_through_many(cache, [key], loader, CachePolicy(ttl=60.0))

    assert "fr" not in key
    await cache.stop()
