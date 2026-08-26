"""
Failing tests for Plan 008 Phase 2, step 1 — ``TenantControlService.
request_provision()`` / ``request_deprovision()``: broadcast-only, no local
effect (RD-14).
"""

from __future__ import annotations

import pytest


class _CountingProvisioner:
    def __init__(self) -> None:
        self.provision_calls = 0
        self.deprovision_calls = 0

    async def provision(self, tenant_id: str, **kwargs) -> None:
        self.provision_calls += 1

    async def deprovision(self, tenant_id: str, *, confirm_destroy: bool = False) -> None:
        self.deprovision_calls += 1


class _NoOpCatalog:
    def __init__(self) -> None:
        self.add_calls = 0
        self.update_status_calls = 0

    async def get(self, tenant_id: str):
        from varco_core.tenancy.catalog import TenantNotFoundError

        raise TenantNotFoundError(tenant_id)

    async def add(self, descriptor) -> None:
        self.add_calls += 1

    async def update_status(self, tenant_id: str, status) -> None:
        self.update_status_calls += 1

    async def list_tenants(self, status=None):
        return []

    async def remove(self, tenant_id: str) -> None:
        pass


def _make_service(*, with_producer: bool = True):
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.event.producer import BusEventProducer
    from varco_core.tenancy.control.service import TenantControlService

    bus = InMemoryEventBus()
    producer = BusEventProducer(bus) if with_producer else None
    catalog = _NoOpCatalog()
    provisioner = _CountingProvisioner()
    service = TenantControlService(
        catalog=catalog,
        provisioner=provisioner,
        producer=producer,
        node_id="cp-1",
    )
    return service, bus, catalog, provisioner


async def test_request_provision_emits_exactly_one_command_with_origin_and_no_local_effect() -> (
    None
):
    from varco_core.tenancy.control.events import (
        CHANNEL_TENANCY,
        TenantProvisionRequested,
    )

    service, bus, catalog, provisioner = _make_service()

    received: list[TenantProvisionRequested] = []
    bus.subscribe(TenantProvisionRequested, lambda e: received.append(e), channel=CHANNEL_TENANCY)

    await service.request_provision("acme")
    await bus.drain()

    assert len(received) == 1
    assert received[0].tenant_id == "acme"
    assert received[0].origin == service.node_id  # type: ignore[attr-defined]

    assert catalog.add_calls == 0
    assert catalog.update_status_calls == 0
    assert provisioner.provision_calls == 0


async def test_request_deprovision_without_confirm_raises_and_emits_nothing() -> None:
    from varco_core.tenancy.control.events import (
        CHANNEL_TENANCY,
        TenantDeprovisionRequested,
    )
    from varco_core.tenancy.provisioner import DestructiveOperationRefused

    service, bus, _catalog, _provisioner = _make_service()

    received: list[TenantDeprovisionRequested] = []
    bus.subscribe(
        TenantDeprovisionRequested,
        lambda e: received.append(e),
        channel=CHANNEL_TENANCY,
    )

    with pytest.raises(DestructiveOperationRefused):
        await service.request_deprovision("acme", confirm=False)
    await bus.drain()

    assert received == []


async def test_request_deprovision_with_confirm_emits_with_confirm_and_origin() -> None:
    from varco_core.tenancy.control.events import (
        CHANNEL_TENANCY,
        TenantDeprovisionRequested,
    )

    service, bus, _catalog, _provisioner = _make_service()

    received: list[TenantDeprovisionRequested] = []
    bus.subscribe(
        TenantDeprovisionRequested,
        lambda e: received.append(e),
        channel=CHANNEL_TENANCY,
    )

    await service.request_deprovision("acme", confirm=True)
    await bus.drain()

    assert len(received) == 1
    assert received[0].confirm is True
    assert received[0].origin == service.node_id  # type: ignore[attr-defined]


async def test_service_without_producer_raises_runtime_error_naming_producer() -> None:
    with pytest.raises(RuntimeError, match="producer"):
        _make_service(with_producer=False)
