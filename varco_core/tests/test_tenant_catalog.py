"""
Failing tests for varco_core.tenancy.catalog (Plan 007, Phase 1, step 3).

TenantDescriptor / AbstractTenantCatalog / StaticTenantCatalog —
no backend deps.
"""

from __future__ import annotations

import pytest


def test_tenant_descriptor_frozen_with_documented_defaults() -> None:
    from varco_core.tenancy.catalog import TenantDescriptor
    from varco_core.tenancy.settings import TenantStatus

    descriptor = TenantDescriptor(tenant_id="acme")

    assert descriptor.schema is None
    assert descriptor.database is None
    assert descriptor.dsn_ref is None
    assert descriptor.status == TenantStatus.PENDING

    with pytest.raises(Exception):  # noqa: B017
        descriptor.status = TenantStatus.ACTIVE  # type: ignore[misc]


async def test_list_tenants_is_sorted_and_deterministic() -> None:
    from varco_core.tenancy.catalog import StaticTenantCatalog, TenantDescriptor
    from varco_core.tenancy.settings import TenantStatus

    catalog = StaticTenantCatalog(
        [
            TenantDescriptor(tenant_id="zeta", status=TenantStatus.ACTIVE),
            TenantDescriptor(tenant_id="acme", status=TenantStatus.ACTIVE),
            TenantDescriptor(tenant_id="mid", status=TenantStatus.ACTIVE),
        ]
    )

    result_1 = await catalog.list_tenants()
    result_2 = await catalog.list_tenants()

    ids_1 = [t.tenant_id for t in result_1]
    ids_2 = [t.tenant_id for t in result_2]
    assert ids_1 == ids_2 == sorted(ids_1)


async def test_list_tenants_filters_to_active_by_default() -> None:
    from varco_core.tenancy.catalog import StaticTenantCatalog, TenantDescriptor
    from varco_core.tenancy.settings import TenantStatus

    catalog = StaticTenantCatalog(
        [
            TenantDescriptor(tenant_id="active-1", status=TenantStatus.ACTIVE),
            TenantDescriptor(tenant_id="pending-1", status=TenantStatus.PENDING),
            TenantDescriptor(tenant_id="suspended-1", status=TenantStatus.SUSPENDED),
        ]
    )

    result = await catalog.list_tenants()

    assert [t.tenant_id for t in result] == ["active-1"]


async def test_list_tenants_accepts_explicit_status_filter() -> None:
    from varco_core.tenancy.catalog import StaticTenantCatalog, TenantDescriptor
    from varco_core.tenancy.settings import TenantStatus

    catalog = StaticTenantCatalog(
        [
            TenantDescriptor(tenant_id="active-1", status=TenantStatus.ACTIVE),
            TenantDescriptor(tenant_id="pending-1", status=TenantStatus.PENDING),
        ]
    )

    result = await catalog.list_tenants(status=TenantStatus.PENDING)

    assert [t.tenant_id for t in result] == ["pending-1"]


async def test_get_unknown_tenant_raises_tenant_not_found_error() -> None:
    from varco_core.tenancy.catalog import StaticTenantCatalog, TenantNotFoundError

    catalog = StaticTenantCatalog([])

    with pytest.raises(TenantNotFoundError):
        await catalog.get("ghost")


async def test_add_then_remove_is_idempotent() -> None:
    from varco_core.tenancy.catalog import StaticTenantCatalog, TenantDescriptor

    catalog = StaticTenantCatalog([])
    descriptor = TenantDescriptor(tenant_id="acme")

    await catalog.add(descriptor)
    await catalog.add(descriptor)  # idempotent — must not raise or duplicate

    result = await catalog.list_tenants(status=None)
    assert [t.tenant_id for t in result] == ["acme"]

    await catalog.remove("acme")
    await catalog.remove("acme")  # idempotent — must not raise

    result_after = await catalog.list_tenants(status=None)
    assert result_after == []
