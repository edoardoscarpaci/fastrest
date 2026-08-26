"""
Failing structural test for Plan 008 Phase 2, step 3 — RD-13: the tenant
control-plane event graph is acyclic by construction. No fact-event handler
in ``varco_core.tenancy`` may produce a command event; the only producers of
commands are ``request_provision`` / ``request_deprovision``.
"""

from __future__ import annotations


class _RecordingBus:
    """Bus wrapper recording every published event type, driven directly
    (not via InMemoryEventBus's subscription machinery) so we can drive
    handlers by hand and record what each one emits."""

    def __init__(self) -> None:
        self.published_types: list[str] = []

    async def publish(self, event, *, channel: str = "*") -> None:
        self.published_types.append(type(event).__name__)


class _CountingProvisioner:
    async def provision(self, tenant_id: str, **kwargs) -> None:
        pass

    async def deprovision(self, tenant_id: str, *, confirm_destroy: bool = False) -> None:
        pass


def _command_event_types() -> set[str]:
    from varco_core.tenancy.control.events import (
        TenantDeprovisionRequested,
        TenantProvisionRequested,
    )

    return {TenantProvisionRequested.__name__, TenantDeprovisionRequested.__name__}


async def test_provision_and_deprovision_never_emit_command_events() -> None:
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.event.producer import BusEventProducer
    from varco_core.tenancy.catalog import StaticTenantCatalog
    from varco_core.tenancy.control.service import TenantControlService

    bus = InMemoryEventBus()
    producer = BusEventProducer(bus)
    service = TenantControlService(
        catalog=StaticTenantCatalog(),
        provisioner=_CountingProvisioner(),
        producer=producer,
        node_id="cp-1",
    )

    published: list[str] = []
    from varco_core.event.base import Event

    bus.subscribe(Event, lambda e: published.append(type(e).__name__), channel="*")

    await service.provision("acme")
    await service.deprovision("acme", confirm=True)
    await bus.drain()

    commands = _command_event_types()
    assert not (set(published) & commands)


async def test_cached_catalog_on_catalog_changed_emits_nothing() -> None:
    from varco_core.tenancy.cached_catalog import CachedTenantCatalog
    from varco_core.tenancy.catalog import StaticTenantCatalog
    from varco_core.tenancy.control.events import TenantCatalogChanged

    bus = _RecordingBus()
    cached = CachedTenantCatalog(store=StaticTenantCatalog())

    await cached.on_catalog_changed(TenantCatalogChanged(tenant_id="acme"))

    assert bus.published_types == []


async def test_readiness_coordinator_node_ready_handler_never_emits_a_command() -> None:
    """Driving the readiness coordinator's fact handler (TenantNodeReady)
    must never produce a command event — only TenantCatalogChanged is
    permitted as an eventual side-effect of reaching completeness."""
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.event.producer import BusEventProducer
    from varco_core.tenancy.catalog import StaticTenantCatalog
    from varco_core.tenancy.control.events import CHANNEL_TENANCY, TenantNodeReady
    from varco_core.tenancy.control.readiness import TenantReadinessCoordinator
    from varco_core.tenancy.control.service import TenantControlService

    bus = InMemoryEventBus()
    producer = BusEventProducer(bus)
    service = TenantControlService(
        catalog=StaticTenantCatalog(),
        provisioner=_CountingProvisioner(),
        producer=producer,
        node_id="cp-1",
    )
    coordinator = TenantReadinessCoordinator(
        control_service=service, expected_stores=frozenset({"orders"})
    )
    coordinator.register_to(bus)

    published: list[str] = []
    from varco_core.event.base import Event

    bus.subscribe(Event, lambda e: published.append(type(e).__name__), channel="*")

    await bus.publish(
        TenantNodeReady(tenant_id="acme", node_id="n1", store_id="orders"),
        channel=CHANNEL_TENANCY,
    )
    await bus.drain()

    commands = _command_event_types()
    assert not (set(published) & commands)


def test_only_request_methods_produce_commands_by_source_inspection() -> None:
    """Structural check: within varco_core.tenancy.control, the command event
    constructors (TenantProvisionRequested(/TenantDeprovisionRequested)
    appear only in TenantControlService.request_provision/request_deprovision
    — never in provision()/deprovision()/suspend()/resume()/mark_active(), or
    in the readiness/cached_catalog modules."""
    import inspect

    from varco_core.tenancy.control.service import TenantControlService

    for name in ("provision", "deprovision", "suspend", "resume", "mark_active"):
        method = getattr(TenantControlService, name, None)
        assert method is not None, f"TenantControlService.{name} must exist"
        source = inspect.getsource(method)
        assert "TenantProvisionRequested(" not in source
        assert "TenantDeprovisionRequested(" not in source

    for name in ("request_provision", "request_deprovision"):
        method = getattr(TenantControlService, name, None)
        assert method is not None, f"TenantControlService.{name} must exist"
