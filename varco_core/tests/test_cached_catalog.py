"""
Failing tests for varco_core.tenancy.cached_catalog.CachedTenantCatalog
(Plan 007, Phase 4, step 6).
"""

from __future__ import annotations

import asyncio

import pytest


class _CountingCatalog:
    def __init__(self, descriptors: dict[str, object]) -> None:
        self._descriptors = descriptors
        self.get_calls = 0

    async def get(self, tenant_id: str):
        from varco_core.tenancy.catalog import TenantNotFoundError

        self.get_calls += 1
        if tenant_id not in self._descriptors:
            raise TenantNotFoundError(tenant_id)
        return self._descriptors[tenant_id]

    async def list_tenants(self, status=None):
        return list(self._descriptors.values())

    async def add(self, descriptor) -> None:
        self._descriptors[descriptor.tenant_id] = descriptor

    async def update_status(self, tenant_id: str, status) -> None:
        pass

    async def remove(self, tenant_id: str) -> None:
        self._descriptors.pop(tenant_id, None)


def _descriptor(tenant_id: str, status="active"):
    from varco_core.tenancy.catalog import TenantDescriptor

    return TenantDescriptor(tenant_id=tenant_id, status=status)


async def test_get_hit_does_not_touch_store() -> None:
    from varco_core.tenancy.cached_catalog import CachedTenantCatalog

    store = _CountingCatalog({"acme": _descriptor("acme")})
    cached = CachedTenantCatalog(store=store, catalog_ttl_s=60.0)

    await cached.get("acme")
    await cached.get("acme")

    assert store.get_calls <= 1


async def test_tenant_catalog_changed_invalidates_immediately() -> None:
    from varco_core.tenancy.cached_catalog import CachedTenantCatalog
    from varco_core.tenancy.control.events import TenantCatalogChanged

    store = _CountingCatalog({"acme": _descriptor("acme", status="active")})
    cached = CachedTenantCatalog(store=store, catalog_ttl_s=9999.0)
    await cached.get("acme")

    store._descriptors["acme"] = _descriptor("acme", status="suspended")
    await cached.on_catalog_changed(TenantCatalogChanged(tenant_id="acme"))

    fetched = await cached.get("acme")
    assert fetched.status == "suspended"


async def test_ttl_expiry_triggers_reread() -> None:
    from varco_core.tenancy.cached_catalog import CachedTenantCatalog

    store = _CountingCatalog({"acme": _descriptor("acme")})
    cached = CachedTenantCatalog(store=store, catalog_ttl_s=0.01)

    await cached.get("acme")
    await asyncio.sleep(0.05)
    await cached.get("acme")

    assert store.get_calls >= 2


async def test_unknown_tenant_reads_through_and_is_rate_limited() -> None:
    from varco_core.tenancy.cached_catalog import CachedTenantCatalog
    from varco_core.tenancy.catalog import TenantNotFoundError

    store = _CountingCatalog({})
    cached = CachedTenantCatalog(store=store, catalog_ttl_s=60.0, negative_cache_window_s=60.0)

    with pytest.raises(TenantNotFoundError):
        await cached.get("ghost")
    with pytest.raises(TenantNotFoundError):
        await cached.get("ghost")

    assert store.get_calls == 1


async def test_suspended_tenant_stops_routing_after_invalidation() -> None:
    from varco_core.tenancy.cached_catalog import CachedTenantCatalog
    from varco_core.tenancy.control.events import TenantCatalogChanged

    store = _CountingCatalog({"acme": _descriptor("acme", status="active")})
    cached = CachedTenantCatalog(store=store, catalog_ttl_s=9999.0)
    first = await cached.get("acme")
    assert first.status == "active"

    store._descriptors["acme"] = _descriptor("acme", status="suspended")
    await cached.on_catalog_changed(TenantCatalogChanged(tenant_id="acme"))

    second = await cached.get("acme")
    assert second.status == "suspended"
