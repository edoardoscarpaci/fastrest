"""
tests.test_cache_singleflight_tenancy
========================================
Plan 010 Phase 1, step 12 — the tenant landmine test.

Coalescing must happen on the FINAL (already tenant-namespaced) cache key.
Coalescing on a pre-namespaced key would let two tenants share one
recompute -> a cross-tenant leak.

This exercises the concept directly via ``Singleflight`` +
``tenancy_cache_key()`` (the two primitives ``CacheServiceMixin.read()`` is
specified to compose, per Plan 010 step 11) rather than standing up a full
``AsyncService``/repository/assembler fixture — the assertion under test
("two tenants -> two loader calls, coalescing key carries tenant:{id}:") is
independent of that plumbing.

RED until ``varco_core/cache/singleflight.py`` lands.
"""

from __future__ import annotations

import asyncio


from varco_core.model import DomainModel
from varco_core.service.tenant import tenant_context
from varco_core.tenancy.cache_key import tenancy_cache_key


class Widget(DomainModel):
    pk: str = ""

    class Meta:
        table = "widgets"


class TestSingleflightTenantSafety:
    async def test_two_tenants_concurrent_miss_same_pk_yields_two_loader_calls(
        self,
    ) -> None:
        from varco_core.cache.singleflight import Singleflight

        sf = Singleflight()
        calls: list[str] = []

        async def loader_for_tenant(tenant_id: str):
            async def _loader():
                calls.append(tenant_id)
                await asyncio.sleep(0.03)
                return f"widget-for-{tenant_id}"

            return _loader

        async def do_for_tenant(tenant_id: str):
            with tenant_context(tenant_id):
                key = tenancy_cache_key(Widget, "42")
            loader = await loader_for_tenant(tenant_id)
            return await sf.do(key, loader)

        (val_a, _), (val_b, _) = await asyncio.gather(
            do_for_tenant("tenant-a"), do_for_tenant("tenant-b")
        )

        # A regression here (one loader call, shared value) is a
        # cross-tenant data leak.
        assert sorted(calls) == ["tenant-a", "tenant-b"]
        assert val_a == "widget-for-tenant-a"
        assert val_b == "widget-for-tenant-b"

    async def test_coalescing_key_carries_tenant_segment(self) -> None:
        with tenant_context("acme"):
            key = tenancy_cache_key(Widget, "42")
        assert "tenant:acme:" in key
