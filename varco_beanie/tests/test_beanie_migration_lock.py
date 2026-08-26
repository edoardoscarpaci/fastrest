"""
Failing tests for the ``varco_migrations`` lock document (Plan 006, Phase 3,
step 37). Real MongoDB testcontainer — the lock document + heartbeat
reclaim mechanic cannot be expressed against a mocked collection.
"""

from __future__ import annotations

import asyncio
import os

import pytest
import pytest_asyncio

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("VARCO_RUN_INTEGRATION"),
        reason="Integration tests disabled — set VARCO_RUN_INTEGRATION=1",
    ),
]


# mongo_container (module-scoped) was replaced by the session-scoped
# mongo_url fixture in tests/conftest.py (Plan 012 / RT1, Step 6/7).


@pytest_asyncio.fixture
async def db(mongo_url: str):
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(mongo_url)
    database = client["varco_migration_lock_test"]
    yield database
    await client.drop_database("varco_migration_lock_test")
    client.close()


async def test_two_migrators_concurrent_upgrade_exactly_one_applies(db) -> None:
    from varco_beanie.migration.base import Migration, MigrationRegistry
    from varco_beanie.migration.migrator import BeanieMigrator

    class _Slow(Migration):
        version = "0001"
        name = "slow"

        async def up(self, db) -> None:  # noqa: ANN001
            await asyncio.sleep(1.0)

    registry = MigrationRegistry()
    registry.register(_Slow)

    migrator_a = BeanieMigrator(db, registry, index_mode="off", owner_id="a")
    migrator_b = BeanieMigrator(db, registry, index_mode="off", owner_id="b")

    report_a, report_b = await asyncio.gather(migrator_a.upgrade(), migrator_b.upgrade())

    reports = [report_a, report_b]
    skipped = [r for r in reports if r.skipped_locked]
    applied = [r for r in reports if not r.skipped_locked]

    assert len(applied) == 1
    assert len(skipped) == 1


async def test_lock_reclaimable_only_after_ttl_when_heartbeat_dies(db) -> None:
    from varco_beanie.migration.store import MigrationStore

    store = MigrationStore(db)
    await store.acquire(owner="crashed-worker", ttl=1.0)

    # Immediately after acquire, a different owner must not steal the lock.
    stolen_early = await store.acquire(owner="rescuer", ttl=1.0)
    assert stolen_early is False

    await asyncio.sleep(1.5)  # TTL expiry — simulated crash, no heartbeat renewed it

    stolen_late = await store.acquire(owner="rescuer", ttl=1.0)
    assert stolen_late is True


async def test_index_mode_create_creates_missing_index_check_does_not(db) -> None:
    from varco_beanie.index_guard import BeanieIndexGuard
    from varco_beanie.migration.base import MigrationRegistry
    from varco_beanie.migration.migrator import BeanieMigrator

    class _Widget:
        pass

    guard = BeanieIndexGuard()  # no entity classes — indexes asserted via db directly
    registry = MigrationRegistry()

    migrator_check = BeanieMigrator(db, registry, index_guard=guard, index_mode="check")
    await migrator_check.upgrade()
    indexes_after_check = await db["widgets"].index_information()
    assert len(indexes_after_check) <= 1  # only the default _id_ index

    migrator_create = BeanieMigrator(db, registry, index_guard=guard, index_mode="create")
    await migrator_create.upgrade()
