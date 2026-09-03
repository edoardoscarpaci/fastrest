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
from varco_core.meta import FieldHint
from varco_core.model import DomainModel

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


async def test_index_mode_upgrade_creates_indexes_and_is_idempotent(db) -> None:
    # Plan 024 / Phase 2, Step 23: strict=True xfail deleted — this must now
    # PASS for the real reason (the §D-C3 restructure at migrator.py:160-242),
    # not be waived. RED MODE today: BeanieMigrator.upgrade() still returns
    # early at `if not pending_migrations: return ...` (migrator.py:170-171)
    # BEFORE the index_mode=="create" block ever runs, so no index is ever
    # created and `len(first_indexes) == 3` fails (only `_id_` exists).
    from varco_beanie.index_guard import BeanieIndexGuard
    from varco_beanie.migration.base import MigrationRegistry
    from varco_beanie.migration.migrator import BeanieMigrator

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


async def test_index_mode_create_with_nonpending_registry_still_creates_indexes(db) -> None:
    """
    Regression guard on the §D-C3 restructure (Plan 024 Step 23a): a
    migrator whose ``MigrationRegistry`` has migrations, but none of them
    are pending (already applied), must still reconcile indexes when
    ``index_mode="create"`` — this is the case the original bug hid, now
    with a *non-empty* registry rather than an entirely empty one.

    RED MODE: fails today for the same underlying reason as sibling (1) —
    the early return at migrator.py:170-171 only looks at
    ``pending_migrations``, so a registry with zero *pending* (but
    non-empty) migrations still short-circuits before the index block.
    """
    from varco_beanie.index_guard import BeanieIndexGuard
    from varco_beanie.migration.base import Migration, MigrationRegistry
    from varco_beanie.migration.migrator import BeanieMigrator

    @dataclass
    class _Gadget(DomainModel):
        name: Annotated[str, FieldHint(index=True)]

        class Meta:
            table = "gadgets_migint"

    class _AlreadyApplied(Migration):
        version = "0001"
        name = "already_applied"

        async def up(self, db) -> None:
            pass

        async def down(self, db) -> None:
            pass

    guard = BeanieIndexGuard(_Gadget)
    registry = MigrationRegistry()
    registry.register(_AlreadyApplied)

    # Apply the one migration first with index_mode="off" so the registry
    # is non-empty but has zero PENDING migrations from here on.
    setup_migrator = BeanieMigrator(db, registry, index_guard=None, index_mode="off")
    await setup_migrator.upgrade()

    migrator = BeanieMigrator(db, registry, index_guard=guard, index_mode="create")
    await migrator.upgrade()

    indexes = await db["gadgets_migint"].index_information()
    # _id_ + the one declared index.
    assert len(indexes) == 2


async def test_second_upgrade_with_no_drift_acquires_no_lock(db, monkeypatch) -> None:
    """
    Edge case from Plan 024's Edge cases section: "Second upgrade() with no
    drift -> no lock is taken." A no-op ``MigrationStore.acquire`` spy
    proves the common, cheap startup path stays lock-free.

    RED MODE: today's early return (`if not pending_migrations: return`,
    migrator.py:170-171) IS lock-free already for an empty registry with no
    index guard — so this specific assertion can already pass for that
    narrow shape. It is written here, red-first, against the shape the plan
    actually specifies: index_mode="create" with a guard that reports zero
    drift on the second call. Today that goes through the *same* early
    return without ever calling `_index_pending()`, so this test cannot
    distinguish "lock-free because index-aware" from "lock-free because the
    bug never even looks" — that distinction is exactly what Step 24 must
    fix, and `test_index_mode_upgrade_creates_indexes_and_is_idempotent`
    above is the test that forces the fix to happen at all.
    """
    from varco_beanie.index_guard import BeanieIndexGuard
    from varco_beanie.migration.base import MigrationRegistry
    from varco_beanie.migration.migrator import BeanieMigrator
    from varco_beanie.migration.store import MigrationStore

    @dataclass
    class _Doohickey(DomainModel):
        name: Annotated[str, FieldHint(index=True)]

        class Meta:
            table = "doohickeys_migint"

    guard = BeanieIndexGuard(_Doohickey)
    registry = MigrationRegistry()

    migrator = BeanieMigrator(db, registry, index_guard=guard, index_mode="create")
    await migrator.upgrade()  # first call — creates the index (drift -> lock)

    acquire_calls: list[str] = []
    original_acquire = MigrationStore.acquire

    async def _spy_acquire(self, owner: str, ttl: float) -> bool:
        acquire_calls.append(owner)
        return await original_acquire(self, owner, ttl)

    monkeypatch.setattr(MigrationStore, "acquire", _spy_acquire)

    await migrator.upgrade()  # second call — no drift, must not acquire

    assert acquire_calls == []


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
