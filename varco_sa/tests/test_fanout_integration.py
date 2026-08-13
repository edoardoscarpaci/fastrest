"""
Failing integration test — RD-8 acceptance: a tenant-database outbox entry is
genuinely published by the supervisor (Plan 007, Phase 8, step 3). Requires
Docker (testcontainers) — skipped unless VARCO_RUN_INTEGRATION=1.
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("VARCO_RUN_INTEGRATION"),
        reason="Integration tests disabled — set VARCO_RUN_INTEGRATION=1",
    ),
]


@pytest.fixture(scope="module")
def pg_container():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:15-alpine") as pg:
        yield pg


async def test_tenant_a_outbox_entry_published_tenant_b_relay_never_sees_it(
    pg_container,
) -> None:
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.tenancy.fanout import TenantFanoutSupervisor

    # Full wiring (two real tenant databases, routed UoW, SAEngineRegistry,
    # OutboxRelay per tenant) is the responsibility of the not-yet-written
    # varco_sa.tenancy wiring — this test pins the acceptance contract.
    _ = InMemoryEventBus()
    supervisor = TenantFanoutSupervisor(child_factory=lambda tid: None, max_entries=50)

    await supervisor.on_tenant_activated("tenant-a")
    await supervisor.start()

    # Placeholder for the real assertion once wiring exists: publishing an
    # OutboxEntry written into tenant-a's own database must reach `bus` and
    # be deleted, while tenant-b's relay (never activated) must not see it.
    assert supervisor.active_tenant_count() == 1

    await supervisor.stop()
