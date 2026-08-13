"""
Failing tests for varco_core.tenancy.provisioner (Plan 007, Phase 1, step 9).

AbstractTenantProvisioner + ExternalTenantProvisioner — the destructive-op
gate (confirm_destroy) lives in the ABC so no backend/subclass can forget it.
"""

from __future__ import annotations

import pytest


async def test_external_provisioner_deprovision_without_confirm_raises() -> None:
    from varco_core.tenancy.provisioner import (
        DestructiveOperationRefused,
        ExternalTenantProvisioner,
    )

    provisioner = ExternalTenantProvisioner()

    with pytest.raises(DestructiveOperationRefused):
        await provisioner.deprovision("acme")


async def test_external_provisioner_deprovision_with_confirm_succeeds() -> None:
    from varco_core.tenancy.provisioner import ExternalTenantProvisioner

    provisioner = ExternalTenantProvisioner()

    await provisioner.deprovision("acme", confirm_destroy=True)


async def test_subclass_overriding_deprovision_still_gated_by_abc() -> None:
    """The gate must live in the ABC's deprovision(), not be re-implemented
    per backend — a subclass that overrides and calls super() cannot bypass
    it even if it forgets its own check.
    """
    from varco_core.tenancy.provisioner import (
        AbstractTenantProvisioner,
        DestructiveOperationRefused,
    )

    calls: list[str] = []

    class _NaiveProvisioner(AbstractTenantProvisioner):
        async def provision(self, tenant_id: str, **kwargs: object) -> None:
            calls.append(f"provision:{tenant_id}")

        async def deprovision(
            self, tenant_id: str, *, confirm_destroy: bool = False
        ) -> None:
            calls.append(f"deprovision:{tenant_id}")
            await super().deprovision(tenant_id, confirm_destroy=confirm_destroy)

    provisioner = _NaiveProvisioner()

    with pytest.raises(DestructiveOperationRefused):
        await provisioner.deprovision("acme")
