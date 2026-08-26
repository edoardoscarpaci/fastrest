"""
Failing tests for varco_core.tenancy.control.service.TenantControlService
(Plan 007, Phase 5, step 1).
"""

from __future__ import annotations

import pytest


class _CountingProvisioner:
    def __init__(self, fail: bool = False) -> None:
        self.provision_calls = 0
        self.deprovision_calls = 0
        self._fail = fail

    async def provision(self, tenant_id: str, **kwargs) -> None:
        self.provision_calls += 1
        if self._fail:
            raise RuntimeError("provision failed")

    async def deprovision(self, tenant_id: str, *, confirm_destroy: bool = False) -> None:
        if not confirm_destroy:
            from varco_core.tenancy.provisioner import DestructiveOperationRefused

            raise DestructiveOperationRefused(tenant_id)
        self.deprovision_calls += 1


class _StaticCatalog:
    def __init__(self) -> None:
        from varco_core.tenancy.catalog import TenantDescriptor
        from varco_core.tenancy.settings import TenantStatus

        self._by_id: dict[str, object] = {
            "acme": TenantDescriptor(tenant_id="acme", status=TenantStatus.PENDING)
        }
        self.status_history: list[str] = []

    async def get(self, tenant_id: str):
        return self._by_id[tenant_id]

    async def add(self, descriptor) -> None:
        self._by_id[descriptor.tenant_id] = descriptor

    async def update_status(self, tenant_id: str, status) -> None:
        self.status_history.append(status)
        from dataclasses import replace

        self._by_id[tenant_id] = replace(self._by_id[tenant_id], status=status)

    async def list_tenants(self, status=None):
        return list(self._by_id.values())

    async def remove(self, tenant_id: str) -> None:
        self._by_id.pop(tenant_id, None)


def _make_service(provisioner=None, catalog=None):
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.event.producer import BusEventProducer
    from varco_core.tenancy.control.service import TenantControlService

    bus = InMemoryEventBus()
    producer = BusEventProducer(bus)
    return (
        TenantControlService(
            catalog=catalog or _StaticCatalog(),
            provisioner=provisioner or _CountingProvisioner(),
            producer=producer,
        ),
        bus,
    )


async def test_provision_is_idempotent_no_ddl_on_already_active() -> None:
    from varco_core.tenancy.settings import TenantStatus

    provisioner = _CountingProvisioner()
    catalog = _StaticCatalog()
    service, _bus = _make_service(provisioner, catalog)

    await service.provision("acme")
    assert provisioner.provision_calls == 1
    await catalog.update_status("acme", TenantStatus.ACTIVE)

    await service.provision("acme")
    assert provisioner.provision_calls == 1


async def test_provision_drives_pending_to_active_and_emits_catalog_changed() -> None:
    from varco_core.tenancy.settings import TenantStatus

    service, bus = _make_service()

    await service.provision("acme")

    catalog = service._catalog  # type: ignore[attr-defined]
    descriptor = await catalog.get("acme")
    assert descriptor.status == TenantStatus.ACTIVE


async def test_provisioner_failure_leaves_status_pending_and_reraises() -> None:
    provisioner = _CountingProvisioner(fail=True)
    catalog = _StaticCatalog()
    service, _bus = _make_service(provisioner, catalog)

    with pytest.raises(RuntimeError):
        await service.provision("acme")

    descriptor = await catalog.get("acme")
    from varco_core.tenancy.settings import TenantStatus

    assert descriptor.status == TenantStatus.PENDING


async def test_deprovision_without_confirm_refuses() -> None:
    from varco_core.tenancy.provisioner import DestructiveOperationRefused

    service, _bus = _make_service()

    with pytest.raises(DestructiveOperationRefused):
        await service.deprovision("acme", confirm=False)


async def test_deprovision_with_confirm_drives_full_lifecycle() -> None:
    from varco_core.tenancy.settings import TenantStatus

    provisioner = _CountingProvisioner()
    catalog = _StaticCatalog()
    service, _bus = _make_service(provisioner, catalog)
    await service.provision("acme")

    await service.deprovision("acme", confirm=True)

    descriptor = await catalog.get("acme")
    assert descriptor.status == TenantStatus.DELETED
    assert provisioner.deprovision_calls == 1


class _RuntimeErrorCatalog:
    """Simulates a catalog outage on get() — must propagate, not become PENDING."""

    def __init__(self) -> None:
        self.add_calls = 0

    async def get(self, tenant_id: str):
        raise RuntimeError("catalog outage")

    async def add(self, descriptor) -> None:
        self.add_calls += 1

    async def update_status(self, tenant_id: str, status) -> None:
        pass

    async def list_tenants(self, status=None):
        return []

    async def remove(self, tenant_id: str) -> None:
        pass


async def test_catalog_outage_on_get_propagates_and_does_not_add_or_provision() -> None:
    """Plan 008 Phase 1, step 5/6: only TenantNotFoundError should be caught
    on the catalog read — any other error (e.g. an outage) must propagate,
    never be silently treated as "unknown tenant" and turned into add()."""
    catalog = _RuntimeErrorCatalog()
    provisioner = _CountingProvisioner()
    service, _bus = _make_service(provisioner, catalog)

    with pytest.raises(RuntimeError):
        await service.provision("acme")

    assert catalog.add_calls == 0
    assert provisioner.provision_calls == 0
