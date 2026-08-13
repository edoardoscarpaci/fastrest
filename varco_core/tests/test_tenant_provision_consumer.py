"""
Failing tests for varco_core.tenancy.control.consumer.TenantProvisionConsumer
(Plan 007, Phase 5, step 4; migrated to ``control_service=`` and extended with
the RD-11/RD-12/RD-13 contract by Plan 008, Phase 1, steps 7-8).
"""

from __future__ import annotations

import pytest


class _CountingProvisioner:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self._fail = fail

    async def provision(self, tenant_id: str, **kwargs) -> None:
        self.calls += 1
        if self._fail:
            raise RuntimeError("boom")

    async def deprovision(
        self, tenant_id: str, *, confirm_destroy: bool = False
    ) -> None:
        if not confirm_destroy:
            from varco_core.tenancy.provisioner import DestructiveOperationRefused

            raise DestructiveOperationRefused(tenant_id)


def _make_control_service(provisioner=None):
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.event.producer import BusEventProducer
    from varco_core.tenancy.catalog import StaticTenantCatalog
    from varco_core.tenancy.control.service import TenantControlService

    bus = InMemoryEventBus()
    producer = BusEventProducer(bus)
    service = TenantControlService(
        catalog=StaticTenantCatalog(),
        provisioner=provisioner or _CountingProvisioner(),
        producer=producer,
    )
    return service, bus, producer


async def test_listen_handler_has_retry_policy_and_dlq_by_default() -> None:
    from varco_core.tenancy.control.consumer import TenantProvisionConsumer

    handler = TenantProvisionConsumer.on_provision_requested
    metadata = handler._listen_metadata  # type: ignore[attr-defined]

    assert metadata.retry_policy is not None
    assert metadata.dlq is not None


def test_register_to_is_called_from_postconstruct_not_init() -> None:
    import inspect

    from varco_core.tenancy.control.consumer import TenantProvisionConsumer

    init_source = inspect.getsource(TenantProvisionConsumer.__init__)
    assert "register_to" not in init_source


async def test_redelivered_event_is_a_noop() -> None:
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.tenancy.control.consumer import TenantProvisionConsumer
    from varco_core.tenancy.control.events import TenantProvisionRequested

    provisioner = _CountingProvisioner()
    control_service, _bus, _producer = _make_control_service(provisioner)
    bus = InMemoryEventBus()
    consumer = TenantProvisionConsumer(control_service=control_service)
    consumer.register_to(bus)

    event = TenantProvisionRequested(tenant_id="acme")
    await bus.publish(event, channel="varco.tenancy")
    await bus.publish(event, channel="varco.tenancy")  # redelivery, same event_id
    await bus.drain()

    assert provisioner.calls == 1


async def test_exhausted_retry_lands_in_dlq_with_tenant_id_in_source_ref() -> None:
    from varco_core.event.dlq import DeadLetterSource, InMemoryDeadLetterQueue
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.resilience import RetryPolicy
    from varco_core.tenancy.control.consumer import TenantProvisionConsumer
    from varco_core.tenancy.control.events import TenantProvisionRequested

    provisioner = _CountingProvisioner(fail=True)
    control_service, _bus, _producer = _make_control_service(provisioner)
    dlq = InMemoryDeadLetterQueue()
    bus = InMemoryEventBus()
    consumer = TenantProvisionConsumer(control_service=control_service)
    consumer.register_to(
        bus, retry_policy=RetryPolicy(max_attempts=1, base_delay=0.0), dlq=dlq
    )

    event = TenantProvisionRequested(tenant_id="acme")
    await bus.publish(event, channel="varco.tenancy")
    await bus.drain()

    entries = dlq.entries  # type: ignore[attr-defined]
    assert len(entries) == 1
    assert entries[0].source == DeadLetterSource.CONSUMER
    assert entries[0].source_ref == "acme"


async def test_deprovision_without_confirm_is_rejected_and_dlqd() -> None:
    from varco_core.event.dlq import InMemoryDeadLetterQueue
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.resilience import RetryPolicy
    from varco_core.tenancy.control.consumer import TenantProvisionConsumer
    from varco_core.tenancy.control.events import TenantDeprovisionRequested

    dlq = InMemoryDeadLetterQueue()
    bus = InMemoryEventBus()
    control_service, _bus, _producer = _make_control_service()

    consumer = TenantProvisionConsumer(control_service=control_service)
    consumer.register_to(
        bus, retry_policy=RetryPolicy(max_attempts=1, base_delay=0.0), dlq=dlq
    )

    event = TenantDeprovisionRequested(tenant_id="acme", confirm=False)
    await bus.publish(event, channel="varco.tenancy")
    await bus.drain()

    entries = dlq.entries  # type: ignore[attr-defined]
    assert len(entries) == 1


# ── RD-12 shim tests ────────────────────────────────────────────────────────


def test_provisioner_alone_raises_value_error_naming_control_service() -> None:
    from varco_core.tenancy.control.consumer import TenantProvisionConsumer

    with pytest.raises(ValueError, match="control_service"):
        TenantProvisionConsumer(provisioner=_CountingProvisioner())


def test_neither_control_service_nor_provisioner_raises_value_error() -> None:
    from varco_core.tenancy.control.consumer import TenantProvisionConsumer

    with pytest.raises(ValueError):
        TenantProvisionConsumer()


async def test_provisioner_and_catalog_shim_warns_and_still_updates_catalog() -> None:
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.event.producer import BusEventProducer
    from varco_core.tenancy.catalog import StaticTenantCatalog
    from varco_core.tenancy.control.consumer import TenantProvisionConsumer
    from varco_core.tenancy.control.events import TenantProvisionRequested
    from varco_core.tenancy.settings import TenantStatus

    catalog = StaticTenantCatalog()
    provisioner = _CountingProvisioner()
    bus = InMemoryEventBus()
    producer = BusEventProducer(bus)

    with pytest.warns(DeprecationWarning):
        consumer = TenantProvisionConsumer(
            provisioner=provisioner, catalog=catalog, producer=producer
        )
    consumer.register_to(bus)

    await bus.publish(
        TenantProvisionRequested(tenant_id="acme"), channel="varco.tenancy"
    )
    await bus.drain()

    descriptor = await catalog.get("acme")
    assert descriptor.status == TenantStatus.ACTIVE


async def test_shim_without_producer_logs_exactly_one_warning(caplog) -> None:
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.tenancy.catalog import StaticTenantCatalog
    from varco_core.tenancy.control.consumer import TenantProvisionConsumer
    from varco_core.tenancy.control.events import TenantProvisionRequested

    catalog = StaticTenantCatalog()
    provisioner = _CountingProvisioner()
    bus = InMemoryEventBus()

    with pytest.warns(DeprecationWarning):
        consumer = TenantProvisionConsumer(provisioner=provisioner, catalog=catalog)
    consumer.register_to(bus)

    caplog.set_level("WARNING")
    await bus.publish(
        TenantProvisionRequested(tenant_id="acme"), channel="varco.tenancy"
    )
    await bus.drain()

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "TenantCatalogChanged" in warnings[0].message


# ── RD-13 DAG guard ──────────────────────────────────────────────────────────


async def test_provision_over_bus_does_not_re_emit_provision_requested() -> None:
    """Keeps RD-13's acyclicity property alive: provision() must never
    re-emit the command it handles, or Phase 1 turns into a genuine cycle."""
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.tenancy.control.consumer import TenantProvisionConsumer
    from varco_core.tenancy.control.events import TenantProvisionRequested

    control_service, _bus, _producer = _make_control_service()
    bus = InMemoryEventBus()
    consumer = TenantProvisionConsumer(control_service=control_service)
    consumer.register_to(bus)

    received: list[TenantProvisionRequested] = []
    bus.subscribe(
        TenantProvisionRequested, lambda e: received.append(e), channel="varco.tenancy"
    )

    await bus.publish(
        TenantProvisionRequested(tenant_id="acme"), channel="varco.tenancy"
    )
    await bus.drain()

    # Exactly the one we published ourselves — none re-emitted by provision().
    assert len(received) == 1
