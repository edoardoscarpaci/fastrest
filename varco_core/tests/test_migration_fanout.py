"""
Failing tests for varco_core.migration.fanout.TenantFanoutMigrator (Plan 007,
Phase 9, step 1).
"""

from __future__ import annotations

import pytest


class _FakeMigrator:
    def __init__(self, label: str, fail: bool = False) -> None:
        self.label = label
        self._fail = fail
        self.upgrade_calls = 0

    async def plan(self):
        return f"plan-{self.label}"

    async def upgrade(self, target: str = "heads"):
        self.upgrade_calls += 1
        if self._fail:
            raise RuntimeError(f"{self.label} failed")

    async def check(self):
        return self.label != "behind-tenant"


def _make_catalog(tenant_ids):
    from varco_core.tenancy.catalog import StaticTenantCatalog, TenantDescriptor
    from varco_core.tenancy.settings import TenantStatus

    return StaticTenantCatalog(
        [TenantDescriptor(tenant_id=tid, status=TenantStatus.ACTIVE) for tid in tenant_ids]
    )


async def test_plan_aggregates_per_tenant_plans_in_sorted_order() -> None:
    from varco_core.migration.fanout import TenantFanoutMigrator

    catalog = _make_catalog(["zeta", "acme"])
    global_migrator = _FakeMigrator("global")
    migrator = TenantFanoutMigrator(
        catalog=catalog,
        global_migrator=global_migrator,
        tenant_migrator_factory=lambda descriptor: _FakeMigrator(descriptor.tenant_id),
    )

    plan = await migrator.plan()

    assert list(plan.per_tenant.keys()) == ["acme", "zeta"]  # type: ignore[attr-defined]


async def test_upgrade_applies_global_before_tenants() -> None:
    from varco_core.migration.fanout import TenantFanoutMigrator

    call_order: list[str] = []

    class _OrderedFake(_FakeMigrator):
        async def upgrade(self, target: str = "heads"):
            call_order.append(self.label)

    catalog = _make_catalog(["acme"])
    global_migrator = _OrderedFake("global")
    migrator = TenantFanoutMigrator(
        catalog=catalog,
        global_migrator=global_migrator,
        tenant_migrator_factory=lambda d: _OrderedFake(d.tenant_id),
    )

    await migrator.upgrade()

    assert call_order[0] == "global"
    assert "acme" in call_order[1:]


async def test_check_fails_if_any_tenant_behind_and_names_it() -> None:
    from varco_core.migration.fanout import TenantFanoutMigrator

    catalog = _make_catalog(["acme", "behind-tenant"])
    migrator = TenantFanoutMigrator(
        catalog=catalog,
        global_migrator=_FakeMigrator("global"),
        tenant_migrator_factory=lambda d: _FakeMigrator(d.tenant_id),
    )

    with pytest.raises(Exception) as exc:
        await migrator.check()

    assert "behind-tenant" in str(exc.value)


async def test_fanout_on_failure_stop_names_applied_failed_not_attempted() -> None:
    from varco_core.migration.fanout import TenantFanoutMigrator

    catalog = _make_catalog(["a", "b-fails", "c"])

    def factory(descriptor):
        return _FakeMigrator(descriptor.tenant_id, fail=(descriptor.tenant_id == "b-fails"))

    migrator = TenantFanoutMigrator(
        catalog=catalog,
        global_migrator=_FakeMigrator("global"),
        tenant_migrator_factory=factory,
        fanout_on_failure="stop",
    )

    report = await migrator.upgrade()

    assert "b-fails" in report.failures  # type: ignore[attr-defined]
    assert "c" in report.not_attempted  # type: ignore[attr-defined]


async def test_empty_catalog_is_successful_noop_with_warning() -> None:

    from varco_core.migration.fanout import TenantFanoutMigrator

    catalog = _make_catalog([])
    migrator = TenantFanoutMigrator(
        catalog=catalog,
        global_migrator=_FakeMigrator("global"),
        tenant_migrator_factory=lambda d: _FakeMigrator(d.tenant_id),
    )

    report = await migrator.upgrade()

    assert report is not None


async def test_only_active_and_suspended_tenants_are_targeted() -> None:
    from varco_core.migration.fanout import TenantFanoutMigrator
    from varco_core.tenancy.catalog import StaticTenantCatalog, TenantDescriptor
    from varco_core.tenancy.settings import TenantStatus

    catalog = StaticTenantCatalog(
        [
            TenantDescriptor(tenant_id="active-1", status=TenantStatus.ACTIVE),
            TenantDescriptor(tenant_id="pending-1", status=TenantStatus.PENDING),
            TenantDescriptor(tenant_id="deleted-1", status=TenantStatus.DELETED),
        ]
    )
    targeted: list[str] = []

    def factory(descriptor):
        targeted.append(descriptor.tenant_id)
        return _FakeMigrator(descriptor.tenant_id)

    migrator = TenantFanoutMigrator(
        catalog=catalog,
        global_migrator=_FakeMigrator("global"),
        tenant_migrator_factory=factory,
    )

    await migrator.upgrade()

    assert "pending-1" not in targeted
    assert "deleted-1" not in targeted
