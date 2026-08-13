"""
Failing tests for varco_beanie.tenancy.catalog.BeanieTenantCatalog (Plan 007,
Phase 4, step 4) — same contract suite as SATenantCatalog.
"""

from __future__ import annotations

import pytest


async def test_round_trip_every_tenant_descriptor_field() -> None:
    from varco_core.tenancy.catalog import TenantDescriptor
    from varco_core.tenancy.settings import TenantStatus
    from varco_beanie.tenancy.catalog import BeanieTenantCatalog

    catalog = BeanieTenantCatalog()
    descriptor = TenantDescriptor(
        tenant_id="acme",
        database="db_acme",
        dsn_ref="secret://acme-dsn",
        status=TenantStatus.ACTIVE,
    )

    await catalog.add(descriptor)
    fetched = await catalog.get("acme")

    assert fetched == descriptor


async def test_list_tenants_filters_active_by_default() -> None:
    from varco_core.tenancy.catalog import TenantDescriptor
    from varco_core.tenancy.settings import TenantStatus
    from varco_beanie.tenancy.catalog import BeanieTenantCatalog

    catalog = BeanieTenantCatalog()
    await catalog.add(TenantDescriptor(tenant_id="acme", status=TenantStatus.ACTIVE))
    await catalog.add(
        TenantDescriptor(tenant_id="pending-1", status=TenantStatus.PENDING)
    )

    result = await catalog.list_tenants()

    assert [t.tenant_id for t in result] == ["acme"]


async def test_update_status_rejects_illegal_transition() -> None:
    from varco_core.tenancy.catalog import TenantDescriptor
    from varco_core.tenancy.settings import TenantStatus
    from varco_beanie.tenancy.catalog import BeanieTenantCatalog

    catalog = BeanieTenantCatalog()
    await catalog.add(TenantDescriptor(tenant_id="acme", status=TenantStatus.DELETED))

    with pytest.raises(ValueError):
        await catalog.update_status("acme", TenantStatus.ACTIVE)


async def test_add_twice_is_idempotent() -> None:
    from varco_core.tenancy.catalog import TenantDescriptor
    from varco_core.tenancy.settings import TenantStatus
    from varco_beanie.tenancy.catalog import BeanieTenantCatalog

    catalog = BeanieTenantCatalog()
    descriptor = TenantDescriptor(tenant_id="acme", status=TenantStatus.ACTIVE)

    await catalog.add(descriptor)
    await catalog.add(descriptor)

    result = await catalog.list_tenants()
    assert [t.tenant_id for t in result] == ["acme"]
