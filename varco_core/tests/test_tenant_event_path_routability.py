"""
Failing tests for Plan 008 Phase 1, step 1 — the event path must converge on
the same catalog transition as the REST path (``TenantControlService``), so a
bus-onboarded tenant is routable.

Today ``TenantProvisionConsumer`` only accepts ``provisioner=`` and calls the
provisioner directly — the catalog is never touched, so a bus-onboarded
tenant is permanently unroutable (404). These tests pin the fixed contract:
the consumer takes ``control_service=`` and drives the full catalog
transition, symmetrically for provision and deprovision.
"""

from __future__ import annotations


class _CountingProvisioner:
    def __init__(self, *, fail: bool = False) -> None:
        self.provision_calls = 0
        self.deprovision_calls = 0
        self._fail = fail

    async def provision(self, tenant_id: str, **kwargs) -> None:
        self.provision_calls += 1
        if self._fail:
            raise RuntimeError("provision failed")

    async def deprovision(
        self, tenant_id: str, *, confirm_destroy: bool = False
    ) -> None:
        if not confirm_destroy:
            from varco_core.tenancy.provisioner import DestructiveOperationRefused

            raise DestructiveOperationRefused(tenant_id)
        self.deprovision_calls += 1


def _make_control_service(
    *, provisioner=None, catalog=None, supervisor=None, pool=None
):
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.event.producer import BusEventProducer
    from varco_core.tenancy.catalog import StaticTenantCatalog
    from varco_core.tenancy.control.service import TenantControlService

    bus = InMemoryEventBus()
    producer = BusEventProducer(bus)
    service = TenantControlService(
        catalog=catalog if catalog is not None else StaticTenantCatalog(),
        provisioner=provisioner or _CountingProvisioner(),
        producer=producer,
        supervisor=supervisor,
        pool=pool,
    )
    return service, bus


async def test_provision_requested_over_bus_makes_tenant_routable() -> None:
    """The regression test: an event-onboarded tenant must be routable (200),
    not stuck 404, because the consumer now drives the catalog too."""
    from varco_core.tenancy.control.consumer import TenantProvisionConsumer
    from varco_core.tenancy.control.events import (
        CHANNEL_TENANCY,
        TenantProvisionRequested,
    )
    from varco_core.tenancy.routing import routing_decision_for_status

    provisioner = _CountingProvisioner()
    control_service, bus = _make_control_service(provisioner=provisioner)

    consumer = TenantProvisionConsumer(control_service=control_service)
    consumer.register_to(bus)

    await bus.publish(
        TenantProvisionRequested(tenant_id="acme"), channel=CHANNEL_TENANCY
    )
    await bus.drain()

    catalog = control_service._catalog  # type: ignore[attr-defined]
    descriptor = await catalog.get("acme")
    decision = routing_decision_for_status(descriptor.status.value)
    assert decision.routable is True
    assert decision.http_status == 200

    assert provisioner.provision_calls == 1


async def test_provision_requested_over_bus_emits_catalog_changed_exactly_once() -> (
    None
):
    from varco_core.tenancy.control.consumer import TenantProvisionConsumer
    from varco_core.tenancy.control.events import (
        CHANNEL_TENANCY,
        TenantCatalogChanged,
        TenantProvisionRequested,
    )

    control_service, bus = _make_control_service()
    consumer = TenantProvisionConsumer(control_service=control_service)
    consumer.register_to(bus)

    received: list[TenantCatalogChanged] = []
    bus.subscribe(
        TenantCatalogChanged, lambda e: received.append(e), channel=CHANNEL_TENANCY
    )

    await bus.publish(
        TenantProvisionRequested(tenant_id="acme"), channel=CHANNEL_TENANCY
    )
    await bus.drain()

    assert len(received) == 1
    assert received[0].tenant_id == "acme"


async def test_deprovision_symmetry_supervisor_and_pool_called_before_destructive_provisioner_call() -> (
    None
):
    """Mirror-image defect: deprovision over the bus must stop the fan-out
    child and evict the pool entry BEFORE the destructive provisioner call —
    ordering is asserted with a shared call-log."""
    from varco_core.tenancy.catalog import StaticTenantCatalog, TenantDescriptor
    from varco_core.tenancy.control.consumer import TenantProvisionConsumer
    from varco_core.tenancy.control.events import (
        CHANNEL_TENANCY,
        TenantDeprovisionRequested,
    )
    from varco_core.tenancy.routing import routing_decision_for_status
    from varco_core.tenancy.settings import TenantStatus

    call_log: list[str] = []

    class _LoggingProvisioner(_CountingProvisioner):
        async def deprovision(
            self, tenant_id: str, *, confirm_destroy: bool = False
        ) -> None:
            await super().deprovision(tenant_id, confirm_destroy=confirm_destroy)
            call_log.append("provisioner.deprovision")

    class _LoggingSupervisor:
        async def on_tenant_deactivated(self, tenant_id: str) -> None:
            call_log.append("supervisor.on_tenant_deactivated")

    class _LoggingPool:
        async def evict(self, tenant_id: str) -> None:
            call_log.append("pool.evict")

    catalog = StaticTenantCatalog(
        [TenantDescriptor(tenant_id="acme", status=TenantStatus.ACTIVE)]
    )
    provisioner = _LoggingProvisioner()
    control_service, bus = _make_control_service(
        provisioner=provisioner,
        catalog=catalog,
        supervisor=_LoggingSupervisor(),
        pool=_LoggingPool(),
    )
    consumer = TenantProvisionConsumer(control_service=control_service)
    consumer.register_to(bus)

    await bus.publish(
        TenantDeprovisionRequested(tenant_id="acme", confirm=True),
        channel=CHANNEL_TENANCY,
    )
    await bus.drain()

    assert call_log == [
        "supervisor.on_tenant_deactivated",
        "pool.evict",
        "provisioner.deprovision",
    ]

    descriptor = await catalog.get("acme")
    decision = routing_decision_for_status(descriptor.status.value)
    assert descriptor.status == TenantStatus.DELETED
    assert decision.routable is False
    assert decision.http_status == 404


async def test_deprovision_without_confirm_over_bus_still_dlqs_and_calls_nothing() -> (
    None
):
    from varco_core.event.dlq import InMemoryDeadLetterQueue
    from varco_core.resilience import RetryPolicy
    from varco_core.tenancy.control.consumer import TenantProvisionConsumer
    from varco_core.tenancy.control.events import (
        CHANNEL_TENANCY,
        TenantDeprovisionRequested,
    )

    provisioner = _CountingProvisioner()
    control_service, bus = _make_control_service(provisioner=provisioner)
    dlq = InMemoryDeadLetterQueue()
    consumer = TenantProvisionConsumer(control_service=control_service)
    consumer.register_to(
        bus, retry_policy=RetryPolicy(max_attempts=1, base_delay=0.0), dlq=dlq
    )

    await bus.publish(
        TenantDeprovisionRequested(tenant_id="acme", confirm=False),
        channel=CHANNEL_TENANCY,
    )
    await bus.drain()

    entries = dlq.entries  # type: ignore[attr-defined]
    assert len(entries) == 1
    assert provisioner.deprovision_calls == 0


async def test_provisioner_failure_over_bus_leaves_pending_and_dlqs_with_source_ref() -> (
    None
):
    from varco_core.event.dlq import InMemoryDeadLetterQueue
    from varco_core.resilience import RetryPolicy
    from varco_core.tenancy.control.consumer import TenantProvisionConsumer
    from varco_core.tenancy.control.events import (
        CHANNEL_TENANCY,
        TenantProvisionRequested,
    )
    from varco_core.tenancy.settings import TenantStatus

    provisioner = _CountingProvisioner(fail=True)
    control_service, bus = _make_control_service(provisioner=provisioner)
    dlq = InMemoryDeadLetterQueue()
    consumer = TenantProvisionConsumer(control_service=control_service)
    consumer.register_to(
        bus, retry_policy=RetryPolicy(max_attempts=1, base_delay=0.0), dlq=dlq
    )

    await bus.publish(
        TenantProvisionRequested(tenant_id="acme"), channel=CHANNEL_TENANCY
    )
    await bus.drain()

    catalog = control_service._catalog  # type: ignore[attr-defined]
    descriptor = await catalog.get("acme")
    assert descriptor.status == TenantStatus.PENDING

    entries = dlq.entries  # type: ignore[attr-defined]
    assert len(entries) == 1
    assert entries[0].source_ref == "acme"


async def test_event_path_and_rest_path_produce_byte_identical_catalog_state() -> None:
    """Acceptance criterion: the event path and the REST path must produce
    byte-identical TenantDescriptor state for the same tenant id."""
    from varco_core.tenancy.control.consumer import TenantProvisionConsumer
    from varco_core.tenancy.control.events import (
        CHANNEL_TENANCY,
        TenantProvisionRequested,
    )

    # REST path: direct provision() call.
    rest_service, _rest_bus = _make_control_service()
    await rest_service.provision("acme")
    rest_catalog = rest_service._catalog  # type: ignore[attr-defined]
    rest_descriptor = await rest_catalog.get("acme")

    # Event path: same call driven over the bus via the consumer.
    event_service, event_bus = _make_control_service()
    consumer = TenantProvisionConsumer(control_service=event_service)
    consumer.register_to(event_bus)
    await event_bus.publish(
        TenantProvisionRequested(tenant_id="acme"), channel=CHANNEL_TENANCY
    )
    await event_bus.drain()
    event_catalog = event_service._catalog  # type: ignore[attr-defined]
    event_descriptor = await event_catalog.get("acme")

    assert rest_descriptor == event_descriptor
