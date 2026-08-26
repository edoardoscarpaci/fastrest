"""
Failing tests for varco_sa.tenancy.catalog.SATenantCatalog (Plan 007, Phase 4,
step 1). Uses the in-memory SQLite fixtures already established for
varco_sa (see conftest.py) rather than requiring Postgres for the unit
subset.
"""

from __future__ import annotations

import pytest


async def _make_catalog(session):
    from varco_sa.tenancy.catalog import SATenantCatalog

    return SATenantCatalog(session=session)


async def test_round_trip_every_tenant_descriptor_field(session) -> None:
    from varco_core.tenancy.catalog import TenantDescriptor
    from varco_core.tenancy.settings import TenantStatus

    catalog = await _make_catalog(session)
    descriptor = TenantDescriptor(
        tenant_id="acme",
        schema="t_acme",
        database=None,
        dsn_ref="secret://acme-dsn",
        status=TenantStatus.ACTIVE,
    )

    await catalog.add(descriptor)
    fetched = await catalog.get("acme")

    assert fetched == descriptor


async def test_list_tenants_filters_active_by_default_and_is_ordered(session) -> None:
    from varco_core.tenancy.catalog import TenantDescriptor
    from varco_core.tenancy.settings import TenantStatus

    catalog = await _make_catalog(session)
    await catalog.add(TenantDescriptor(tenant_id="zeta", status=TenantStatus.ACTIVE))
    await catalog.add(TenantDescriptor(tenant_id="acme", status=TenantStatus.ACTIVE))
    await catalog.add(TenantDescriptor(tenant_id="pending-1", status=TenantStatus.PENDING))

    result = await catalog.list_tenants()

    assert [t.tenant_id for t in result] == ["acme", "zeta"]


async def test_update_status_rejects_illegal_transition(session) -> None:
    from varco_core.tenancy.catalog import TenantDescriptor
    from varco_core.tenancy.settings import TenantStatus

    catalog = await _make_catalog(session)
    await catalog.add(TenantDescriptor(tenant_id="acme", status=TenantStatus.DELETED))

    with pytest.raises(ValueError):
        await catalog.update_status("acme", TenantStatus.ACTIVE)


async def test_add_twice_is_idempotent(session) -> None:
    from varco_core.tenancy.catalog import TenantDescriptor
    from varco_core.tenancy.settings import TenantStatus

    catalog = await _make_catalog(session)
    descriptor = TenantDescriptor(tenant_id="acme", status=TenantStatus.ACTIVE)

    await catalog.add(descriptor)
    await catalog.add(descriptor)

    result = await catalog.list_tenants()
    assert [t.tenant_id for t in result] == ["acme"]


async def test_literal_dsn_rejected_unless_allow_literal_dsn(session) -> None:
    from varco_core.tenancy.catalog import TenantDescriptor
    from varco_core.tenancy.settings import TenantStatus

    catalog = await _make_catalog(session)
    literal_dsn_descriptor = TenantDescriptor(
        tenant_id="acme",
        dsn_ref="postgresql://user:password@host/db",
        status=TenantStatus.ACTIVE,
    )

    with pytest.raises(ValueError):
        await catalog.add(literal_dsn_descriptor)

    await catalog.add(literal_dsn_descriptor, allow_literal_dsn=True)
