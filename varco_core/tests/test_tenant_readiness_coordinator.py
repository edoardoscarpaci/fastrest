"""
Failing tests for Plan 008 Phase 3, step 1 — ``TenantReadinessCoordinator``.
"""

from __future__ import annotations

import asyncio

import pytest


class _CountingProvisioner:
    async def provision(self, tenant_id: str, **kwargs) -> None:
        pass

    async def deprovision(
        self, tenant_id: str, *, confirm_destroy: bool = False
    ) -> None:
        pass


def _make_authority_service(catalog=None):
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.event.producer import BusEventProducer
    from varco_core.tenancy.catalog import StaticTenantCatalog
    from varco_core.tenancy.control.service import TenantControlService

    bus = InMemoryEventBus()
    producer = BusEventProducer(bus)
    service = TenantControlService(
        catalog=catalog if catalog is not None else StaticTenantCatalog(),
        provisioner=_CountingProvisioner(),
        producer=producer,
        node_id="cp-1",
    )
    return service, bus


def _make_worker_service():
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
        node_id="worker-1",
        store_id="orders",
        catalog_authority=False,
    )
    return service, bus


def test_construction_without_expected_stores_raises_value_error() -> None:
    from varco_core.tenancy.control.readiness import TenantReadinessCoordinator

    service, _bus = _make_authority_service()

    with pytest.raises(ValueError, match="expected_stores"):
        TenantReadinessCoordinator(control_service=service, expected_stores=None)  # type: ignore[arg-type]


async def test_subset_of_stores_leaves_tenant_pending_and_no_catalog_write() -> None:
    from varco_core.tenancy.catalog import StaticTenantCatalog, TenantDescriptor
    from varco_core.tenancy.control.events import CHANNEL_TENANCY, TenantNodeReady
    from varco_core.tenancy.control.readiness import TenantReadinessCoordinator
    from varco_core.tenancy.routing import routing_decision_for_status
    from varco_core.tenancy.settings import TenantStatus

    catalog = StaticTenantCatalog(
        [TenantDescriptor(tenant_id="acme", status=TenantStatus.PENDING)]
    )
    service, bus = _make_authority_service(catalog)
    coordinator = TenantReadinessCoordinator(
        control_service=service, expected_stores=frozenset({"orders", "billing"})
    )
    coordinator.register_to(bus)

    await bus.publish(
        TenantNodeReady(tenant_id="acme", node_id="n1", store_id="orders"),
        channel=CHANNEL_TENANCY,
    )
    await bus.drain()

    descriptor = await catalog.get("acme")
    assert descriptor.status == TenantStatus.PENDING
    decision = routing_decision_for_status(descriptor.status.value)
    assert decision.routable is False
    assert decision.http_status == 503


async def test_last_expected_store_flips_active_exactly_once() -> None:
    from varco_core.tenancy.catalog import StaticTenantCatalog, TenantDescriptor
    from varco_core.tenancy.control.events import (
        CHANNEL_TENANCY,
        TenantCatalogChanged,
        TenantNodeReady,
    )
    from varco_core.tenancy.control.readiness import TenantReadinessCoordinator
    from varco_core.tenancy.settings import TenantStatus

    catalog = StaticTenantCatalog(
        [TenantDescriptor(tenant_id="acme", status=TenantStatus.PENDING)]
    )
    service, bus = _make_authority_service(catalog)
    coordinator = TenantReadinessCoordinator(
        control_service=service, expected_stores=frozenset({"orders", "billing"})
    )
    coordinator.register_to(bus)

    received: list[TenantCatalogChanged] = []
    bus.subscribe(
        TenantCatalogChanged, lambda e: received.append(e), channel=CHANNEL_TENANCY
    )

    await bus.publish(
        TenantNodeReady(tenant_id="acme", node_id="n1", store_id="orders"),
        channel=CHANNEL_TENANCY,
    )
    await bus.publish(
        TenantNodeReady(tenant_id="acme", node_id="n2", store_id="billing"),
        channel=CHANNEL_TENANCY,
    )
    await bus.drain()

    descriptor = await catalog.get("acme")
    assert descriptor.status == TenantStatus.ACTIVE
    assert len(received) == 1


async def test_duplicate_node_ready_from_already_seen_store_is_a_noop() -> None:
    from varco_core.tenancy.catalog import StaticTenantCatalog, TenantDescriptor
    from varco_core.tenancy.control.events import CHANNEL_TENANCY, TenantNodeReady
    from varco_core.tenancy.control.readiness import TenantReadinessCoordinator
    from varco_core.tenancy.settings import TenantStatus

    catalog = StaticTenantCatalog(
        [TenantDescriptor(tenant_id="acme", status=TenantStatus.PENDING)]
    )
    service, bus = _make_authority_service(catalog)
    coordinator = TenantReadinessCoordinator(
        control_service=service, expected_stores=frozenset({"orders", "billing"})
    )
    coordinator.register_to(bus)

    for _ in range(10):
        await bus.publish(
            TenantNodeReady(tenant_id="acme", node_id="pod-x", store_id="orders"),
            channel=CHANNEL_TENANCY,
        )
    await bus.drain()

    snapshot = coordinator.readiness("acme")
    assert snapshot.seen == frozenset({"orders"})
    assert snapshot.complete is False


async def test_unexpected_store_id_is_ignored_with_one_warning_and_never_counts(
    caplog,
) -> None:
    from varco_core.tenancy.catalog import StaticTenantCatalog, TenantDescriptor
    from varco_core.tenancy.control.events import CHANNEL_TENANCY, TenantNodeReady
    from varco_core.tenancy.control.readiness import TenantReadinessCoordinator
    from varco_core.tenancy.settings import TenantStatus

    catalog = StaticTenantCatalog(
        [TenantDescriptor(tenant_id="acme", status=TenantStatus.PENDING)]
    )
    service, bus = _make_authority_service(catalog)
    coordinator = TenantReadinessCoordinator(
        control_service=service, expected_stores=frozenset({"orders"})
    )
    coordinator.register_to(bus)

    caplog.set_level("WARNING")
    await bus.publish(
        TenantNodeReady(tenant_id="acme", node_id="n1", store_id="unexpected-store"),
        channel=CHANNEL_TENANCY,
    )
    await bus.drain()

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1

    snapshot = coordinator.readiness("acme")
    assert snapshot.seen == frozenset()


async def test_timeout_elapsed_with_store_missing_logs_error_and_never_activates(
    caplog,
) -> None:
    from varco_core.tenancy.catalog import StaticTenantCatalog, TenantDescriptor
    from varco_core.tenancy.control.events import (
        CHANNEL_TENANCY,
        TenantCatalogChanged,
        TenantNodeReady,
    )
    from varco_core.tenancy.control.readiness import TenantReadinessCoordinator
    from varco_core.tenancy.settings import TenantStatus

    catalog = StaticTenantCatalog(
        [TenantDescriptor(tenant_id="acme", status=TenantStatus.PENDING)]
    )
    service, bus = _make_authority_service(catalog)
    coordinator = TenantReadinessCoordinator(
        control_service=service,
        expected_stores=frozenset({"orders", "billing"}),
        timeout_s=0.05,
    )
    coordinator.register_to(bus)

    received: list[TenantCatalogChanged] = []
    bus.subscribe(
        TenantCatalogChanged, lambda e: received.append(e), channel=CHANNEL_TENANCY
    )

    caplog.set_level("ERROR")
    await bus.publish(
        TenantNodeReady(tenant_id="acme", node_id="n1", store_id="orders"),
        channel=CHANNEL_TENANCY,
    )
    await bus.drain()
    await asyncio.sleep(0.2)

    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) >= 1
    assert "billing" in errors[0].message

    descriptor = await catalog.get("acme")
    assert descriptor.status == TenantStatus.PENDING
    assert received == []


def test_coordinator_over_non_authority_service_raises_value_error() -> None:
    from varco_core.tenancy.control.readiness import TenantReadinessCoordinator

    service, _bus = _make_worker_service()

    with pytest.raises(ValueError):
        TenantReadinessCoordinator(
            control_service=service, expected_stores=frozenset({"orders"})
        )


async def test_readiness_for_tenant_a_is_independent_of_tenant_b() -> None:
    from varco_core.tenancy.catalog import StaticTenantCatalog, TenantDescriptor
    from varco_core.tenancy.control.events import CHANNEL_TENANCY, TenantNodeReady
    from varco_core.tenancy.control.readiness import TenantReadinessCoordinator
    from varco_core.tenancy.settings import TenantStatus

    catalog = StaticTenantCatalog(
        [
            TenantDescriptor(tenant_id="acme", status=TenantStatus.PENDING),
            TenantDescriptor(tenant_id="widgets", status=TenantStatus.PENDING),
        ]
    )
    service, bus = _make_authority_service(catalog)
    coordinator = TenantReadinessCoordinator(
        control_service=service, expected_stores=frozenset({"orders", "billing"})
    )
    coordinator.register_to(bus)

    await bus.publish(
        TenantNodeReady(tenant_id="acme", node_id="n1", store_id="orders"),
        channel=CHANNEL_TENANCY,
    )
    await bus.publish(
        TenantNodeReady(tenant_id="acme", node_id="n2", store_id="billing"),
        channel=CHANNEL_TENANCY,
    )
    await bus.drain()

    acme_descriptor = await catalog.get("acme")
    widgets_descriptor = await catalog.get("widgets")

    assert acme_descriptor.status == TenantStatus.ACTIVE
    assert widgets_descriptor.status == TenantStatus.PENDING


async def test_readiness_raises_for_a_tenant_never_observed() -> None:
    """``readiness()`` answers only for tenants this coordinator has heard
    about. An unobserved tenant — including one whose in-memory state was
    lost to a restart (RD-18) — raises ``TenantNotFoundError``, which the
    admin route renders as 404 rather than a misleading "0 of N ready"."""
    import pytest

    from varco_core.tenancy.catalog import StaticTenantCatalog, TenantNotFoundError
    from varco_core.tenancy.control.readiness import TenantReadinessCoordinator

    service, _bus = _make_authority_service(StaticTenantCatalog())
    coordinator = TenantReadinessCoordinator(
        control_service=service, expected_stores=frozenset({"orders"})
    )

    with pytest.raises(TenantNotFoundError):
        coordinator.readiness("never-seen")
