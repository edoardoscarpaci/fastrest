"""
Real-Mongo migration lock coverage (Plan 019 / §RT9-beanie, Step 33).

RT9-beanie is a pure test-coverage row, not a design row (Plan 019 Status
corrections): ``MigrationStore.acquire()`` (``migration/store.py:86-145``)
already implements research 007 §A's sanctioned pattern — a single
conditional ``find_one_and_update(upsert=True)`` on
``{_id: "__lock__", $or: [{expires_at: {$lt: now}}, {owner: owner}]}``, with
``_id`` uniqueness supplying atomicity and a racing upsert's
``DuplicateKeyError`` read as "another live owner holds it"
(``store.py:121-138``). ``BeanieMigrator.upgrade()`` already polls
``acquire()`` to a ``lock_timeout`` deadline and raises
``MigrationLockTimeout`` (``migration/migrator.py:173-193``). Nothing here
is a production change — every test below drives the existing mechanism
against a real ``mongod`` instead of the hand-rolled fake collection
``test_beanie_migrator.py``/``test_beanie_migration_lock.py`` use.

**No TTL index, deliberately.** Lock expiry is an application-level
``expires_at`` predicate evaluated *at acquire time* (``store.py:121-138``'s
``$or`` filter), not a Mongo TTL index — so research 007 §B's 60-120s
TTL-monitor window and its 180s test-timeout advice do **not** apply here.
A TTL index would additionally be inert on the standalone (non-replica-set)
``mongod`` that ``MongoDbContainer("mongo:7")`` starts (007 §B: the TTL
background thread does not run at all on a standalone server) — the
absence of a TTL index in this design is correct and deliberate, not an
oversight.

Per-test namespacing: the ``mongo_url`` container is session-scoped and
shared with every other test in this package, so every test owns a
``uuid4().hex[:8]``-suffixed database name.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("VARCO_RUN_INTEGRATION"),
        reason="Integration tests disabled — set VARCO_RUN_INTEGRATION=1",
    ),
]


def _db_name() -> str:
    return f"varco_migint_{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def db(mongo_url: str):
    from motor.motor_asyncio import AsyncIOMotorClient

    name = _db_name()
    client = AsyncIOMotorClient(mongo_url)
    database = client[name]
    yield database
    await client.drop_database(name)
    client.close()


# ── (1) index-mode lifecycle: upgrade, assert, upgrade again, idempotent ─────


_INDEX_CREATE_SKIPPED_WITHOUT_PENDING_MIGRATIONS_REASON = (
    "BUG: BeanieMigrator.upgrade() (migration/migrator.py) computes "
    "`pending_migrations` (hand-written Migration subclasses only) and "
    "returns early via `if not pending_migrations: return ...` BEFORE the "
    "lock is ever acquired and BEFORE the `index_mode == 'create'` block is "
    "reached — so an index_mode='create' migrator with an empty/fully-"
    "applied MigrationRegistry never creates a missing index, even though "
    "plan() independently reports index drift via `_index_pending()`. "
    "This is a genuine BeanieMigrator/MigrationStore defect discovered "
    "while writing RT9-beanie coverage (Plan 019 Phase 5), not a design "
    "row — the licence to patch production code in this plan covers only "
    "the four named RT2-B/RT2-C/RT7a/RT7b-port rows, so per the standing "
    "'a conformance/coverage failure that reveals a genuine defect becomes "
    "xfail(strict=True) + a BACKLOG row, never an in-place fix' rule, this "
    "is xfail'd rather than fixed. See BACKLOG.md's RT9-beanie-index-mode-"
    "no-pending-migrations row."
)


@pytest.mark.xfail(reason=_INDEX_CREATE_SKIPPED_WITHOUT_PENDING_MIGRATIONS_REASON, strict=True)
async def test_index_mode_upgrade_creates_indexes_and_is_idempotent(db) -> None:
    from varco_beanie.index_guard import BeanieIndexGuard
    from varco_beanie.migration.base import MigrationRegistry
    from varco_beanie.migration.migrator import BeanieMigrator
    from varco_core.meta import FieldHint
    from varco_core.model import DomainModel

    @dataclass
    class _Widget(DomainModel):
        name: Annotated[str, FieldHint(index=True)]
        sku: Annotated[str, FieldHint(unique=True)]

        class Meta:
            table = "widgets_migint"

    guard = BeanieIndexGuard(_Widget)
    registry = MigrationRegistry()

    migrator = BeanieMigrator(db, registry, index_guard=guard, index_mode="create")
    await migrator.upgrade()

    first_indexes = await db["widgets_migint"].index_information()
    # _id_ + the two declared indexes.
    assert len(first_indexes) == 3

    # Second upgrade — createIndex is idempotent (MongoDB 4.4+, research
    # 007 §E); must not raise and must not create a duplicate index.
    await migrator.upgrade()
    second_indexes = await db["widgets_migint"].index_information()
    assert second_indexes.keys() == first_indexes.keys()


# ── (2) two concurrent migrators serialize; exactly one applies ─────────────


async def test_two_concurrent_migrators_serialize_and_only_one_applies(db) -> None:
    from varco_beanie.migration.base import Migration, MigrationRegistry
    from varco_beanie.migration.migrator import BeanieMigrator

    class _Slow(Migration):
        version = "0001"
        name = "slow"

        async def up(self, db) -> None:  # noqa: ANN001
            await asyncio.sleep(1.0)

    registry = MigrationRegistry()
    registry.register(_Slow)

    migrator_a = BeanieMigrator(
        db, registry, index_mode="off", owner_id=f"a-{uuid.uuid4().hex[:4]}"
    )
    migrator_b = BeanieMigrator(
        db, registry, index_mode="off", owner_id=f"b-{uuid.uuid4().hex[:4]}"
    )

    report_a, report_b = await asyncio.gather(migrator_a.upgrade(), migrator_b.upgrade())

    reports = [report_a, report_b]
    applied = [r for r in reports if not r.skipped_locked]
    skipped = [r for r in reports if r.skipped_locked]

    assert len(applied) == 1
    assert len(skipped) == 1


# ── (3) lock_timeout raises when the holder never releases ──────────────────


async def test_lock_timeout_raises_when_holder_never_releases(db) -> None:
    from varco_beanie.migration.base import Migration, MigrationRegistry
    from varco_beanie.migration.migrator import BeanieMigrator
    from varco_beanie.migration.store import MigrationStore
    from varco_core.migration.errors import MigrationLockTimeout
    from varco_core.migration.settings import MigrationSettings

    class _NeverRuns(Migration):
        version = "0001"
        name = "never-runs"

        async def up(self, db) -> None:  # noqa: ANN001
            raise AssertionError("must never run — the lock holder never releases")

    registry = MigrationRegistry()
    registry.register(_NeverRuns)

    # The test itself holds the lock — a live, non-expired holder that
    # never releases (deterministic: no other process needed).
    store = MigrationStore(db)
    acquired = await store.acquire("holder-that-never-releases", ttl=60.0)
    assert acquired is True

    settings = MigrationSettings(lock_key="varco:migrate", lock_timeout=1.0)
    migrator = BeanieMigrator(db, registry, index_mode="off", settings=settings, owner_id="waiter")

    with pytest.raises(MigrationLockTimeout):
        await migrator.upgrade()


# ── (4) a crashed holder's lock is reclaimed after its expires_at passes ────


async def test_crashed_holder_lock_is_reclaimed_after_expiry(db) -> None:
    from varco_beanie.migration.store import COLLECTION_NAME, MigrationStore

    # Simulate a holder that crashed without releasing: write the lock
    # document directly with an expires_at already in the past.
    now = datetime.now(UTC)
    await db[COLLECTION_NAME].insert_one(
        {
            "_id": "__lock__",
            "owner": "crashed-worker",
            "acquired_at": now - timedelta(seconds=120),
            "expires_at": now - timedelta(seconds=60),
            "heartbeat_at": now - timedelta(seconds=90),
        }
    )

    store = MigrationStore(db)
    # No TTL monitor involved — the $or: [{expires_at: {$lt: now}}] filter
    # is evaluated at acquire time, so this must succeed immediately
    # (seconds-scale test, not the 180s research 007 §B would suggest for a
    # TTL-index-based design, which this deliberately is not).
    acquired = await store.acquire("rescuer", ttl=30.0)
    assert acquired is True

    record = await db[COLLECTION_NAME].find_one({"_id": "__lock__"})
    assert record is not None
    assert record["owner"] == "rescuer"


# ── (5) two acquire() calls racing on an absent lock document ───────────────


async def test_duplicate_key_on_concurrent_upsert_is_read_as_lock_lost(db) -> None:
    from varco_beanie.migration.store import MigrationStore

    store = MigrationStore(db)

    # No lock document exists yet — race two acquire() calls on it. Exactly
    # one must win; the loser's upsert collides on _id (E11000
    # DuplicateKeyError) and store.acquire() reads that as "lock lost",
    # returning False rather than propagating the error (store.py:121-138).
    # This branch can never be exercised against the fake collection used
    # by test_beanie_migration_lock.py — only a real mongod raises it.
    results = await asyncio.gather(
        store.acquire("racer-a", ttl=30.0),
        store.acquire("racer-b", ttl=30.0),
    )

    assert sorted(results) == [False, True]
