"""
Failing tests for Plan 008 Phase 2, step 2 — RD-15 origin provenance: a
consumer skips events whose ``origin`` matches its own control service's
``node_id``.
"""

from __future__ import annotations


class _CountingProvisioner:
    def __init__(self) -> None:
        self.provision_calls = 0

    async def provision(self, tenant_id: str, **kwargs) -> None:
        self.provision_calls += 1


def _make_service(node_id: str):
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.event.producer import BusEventProducer
    from varco_core.tenancy.catalog import StaticTenantCatalog
    from varco_core.tenancy.control.service import TenantControlService

    bus = InMemoryEventBus()
    producer = BusEventProducer(bus)
    provisioner = _CountingProvisioner()
    catalog = StaticTenantCatalog()
    service = TenantControlService(
        catalog=catalog, provisioner=provisioner, producer=producer, node_id=node_id
    )
    return service, catalog, provisioner


async def test_consumer_skips_event_with_own_origin() -> None:
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.tenancy.control.consumer import TenantProvisionConsumer
    from varco_core.tenancy.control.events import (
        CHANNEL_TENANCY,
        TenantProvisionRequested,
    )

    service, catalog, provisioner = _make_service(node_id="cp-1")
    bus = InMemoryEventBus()
    consumer = TenantProvisionConsumer(control_service=service)
    consumer.register_to(bus)

    await bus.publish(
        TenantProvisionRequested(tenant_id="acme", origin="cp-1"),
        channel=CHANNEL_TENANCY,
    )
    await bus.drain()

    assert provisioner.provision_calls == 0
    from varco_core.tenancy.catalog import TenantNotFoundError

    import pytest

    with pytest.raises(TenantNotFoundError):
        await catalog.get("acme")


async def test_consumer_handles_event_with_none_origin() -> None:
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.tenancy.control.consumer import TenantProvisionConsumer
    from varco_core.tenancy.control.events import (
        CHANNEL_TENANCY,
        TenantProvisionRequested,
    )

    service, _catalog, provisioner = _make_service(node_id="cp-1")
    bus = InMemoryEventBus()
    consumer = TenantProvisionConsumer(control_service=service)
    consumer.register_to(bus)

    await bus.publish(
        TenantProvisionRequested(tenant_id="acme", origin=None), channel=CHANNEL_TENANCY
    )
    await bus.drain()

    assert provisioner.provision_calls == 1


async def test_consumer_handles_event_with_other_origin() -> None:
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.tenancy.control.consumer import TenantProvisionConsumer
    from varco_core.tenancy.control.events import (
        CHANNEL_TENANCY,
        TenantProvisionRequested,
    )

    service, _catalog, provisioner = _make_service(node_id="cp-1")
    bus = InMemoryEventBus()
    consumer = TenantProvisionConsumer(control_service=service)
    consumer.register_to(bus)

    await bus.publish(
        TenantProvisionRequested(tenant_id="acme", origin="other"),
        channel=CHANNEL_TENANCY,
    )
    await bus.drain()

    assert provisioner.provision_calls == 1


async def test_bundled_round_trip_provision_then_request_provision_is_exactly_one_ddl() -> (
    None
):
    """Full bundled round-trip: operator calls provision() (local DDL) then
    request_provision() (broadcast) on one bus with one consumer — the
    consumer's own broadcast must be skipped via origin, so exactly one DDL
    happens, not two."""
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.event.producer import BusEventProducer
    from varco_core.tenancy.catalog import StaticTenantCatalog
    from varco_core.tenancy.control.consumer import TenantProvisionConsumer
    from varco_core.tenancy.control.service import TenantControlService

    bus = InMemoryEventBus()
    producer = BusEventProducer(bus)
    provisioner = _CountingProvisioner()
    catalog = StaticTenantCatalog()
    service = TenantControlService(
        catalog=catalog, provisioner=provisioner, producer=producer, node_id="cp-1"
    )
    consumer = TenantProvisionConsumer(control_service=service)
    consumer.register_to(bus)

    await service.provision("acme")
    await service.request_provision("acme")
    await bus.drain()

    assert provisioner.provision_calls == 1
