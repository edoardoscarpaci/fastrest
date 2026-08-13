"""
Failing tests for Plan 008 Phase 2, step 7 — RD-16 ``catalog_authority=False``
worker-mode ``TenantControlService``.
"""

from __future__ import annotations

import pytest


class _IfNotExistsProvisioner:
    """Records calls; provision() is idempotent (IF NOT EXISTS semantics)."""

    def __init__(self) -> None:
        self.provision_calls: list[str] = []

    async def provision(self, tenant_id: str, **kwargs) -> None:
        self.provision_calls.append(tenant_id)


def _make_worker_service(catalog=None):
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.event.producer import BusEventProducer
    from varco_core.tenancy.catalog import StaticTenantCatalog
    from varco_core.tenancy.control.service import TenantControlService

    bus = InMemoryEventBus()
    producer = BusEventProducer(bus)
    provisioner = _IfNotExistsProvisioner()
    service = TenantControlService(
        catalog=catalog if catalog is not None else StaticTenantCatalog(),
        provisioner=provisioner,
        producer=producer,
        node_id="worker-1",
        store_id="orders",
        catalog_authority=False,
    )
    return service, bus, provisioner


async def test_worker_provision_never_calls_update_status_or_add() -> None:
    from varco_core.tenancy.catalog import StaticTenantCatalog, TenantDescriptor
    from varco_core.tenancy.settings import TenantStatus

    calls: list[str] = []

    class _SpyCatalog(StaticTenantCatalog):
        async def add(self, descriptor) -> None:
            calls.append("add")
            await super().add(descriptor)

        async def update_status(self, tenant_id, status) -> None:
            calls.append("update_status")
            await super().update_status(tenant_id, status)

    catalog = _SpyCatalog(
        [TenantDescriptor(tenant_id="acme", status=TenantStatus.PENDING)]
    )
    service, _bus, _provisioner = _make_worker_service(catalog)

    await service.provision("acme")

    assert calls == []


async def test_worker_provision_emits_tenant_node_ready_with_configured_store_id() -> (
    None
):
    from varco_core.tenancy.catalog import StaticTenantCatalog, TenantDescriptor
    from varco_core.tenancy.control.events import CHANNEL_TENANCY, TenantNodeReady
    from varco_core.tenancy.settings import TenantStatus

    catalog = StaticTenantCatalog(
        [TenantDescriptor(tenant_id="acme", status=TenantStatus.PENDING)]
    )
    service, bus, _provisioner = _make_worker_service(catalog)

    received: list[TenantNodeReady] = []
    bus.subscribe(
        TenantNodeReady, lambda e: received.append(e), channel=CHANNEL_TENANCY
    )

    await service.provision("acme")
    await bus.drain()

    assert len(received) == 1
    assert received[0].tenant_id == "acme"
    assert received[0].store_id == "orders"


async def test_worker_refuses_deleted_tenant_without_calling_provisioner() -> None:
    from varco_core.tenancy.catalog import StaticTenantCatalog, TenantDescriptor
    from varco_core.tenancy.settings import TenantStatus

    catalog = StaticTenantCatalog(
        [TenantDescriptor(tenant_id="acme", status=TenantStatus.DELETED)]
    )
    service, _bus, provisioner = _make_worker_service(catalog)

    await service.provision("acme")

    assert provisioner.provision_calls == []


async def test_worker_double_provision_is_a_noop_via_provisioner_idempotency_not_status_check() -> (
    None
):
    from varco_core.tenancy.catalog import StaticTenantCatalog, TenantDescriptor
    from varco_core.tenancy.settings import TenantStatus

    catalog = StaticTenantCatalog(
        [TenantDescriptor(tenant_id="acme", status=TenantStatus.PENDING)]
    )
    service, _bus, provisioner = _make_worker_service(catalog)

    await service.provision("acme")
    await service.provision("acme")

    # Worker mode has no status write, so the status check can't be the
    # idempotency mechanism — the provisioner is called every time and MUST
    # itself be idempotent (IF NOT EXISTS). We assert it is invoked twice
    # (not de-duplicated by the service), proving idempotency is not sourced
    # from a status check here.
    assert provisioner.provision_calls == ["acme", "acme"]


async def test_mark_active_on_non_authority_service_raises_value_error() -> None:
    service, _bus, _provisioner = _make_worker_service()

    with pytest.raises(ValueError):
        await service.mark_active("acme")


def test_non_authority_construction_logs_one_warning(caplog) -> None:
    caplog.set_level("WARNING")
    _make_worker_service()

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
