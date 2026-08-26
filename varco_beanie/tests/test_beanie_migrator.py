"""
Failing tests for varco_beanie.migration.migrator.BeanieMigrator (Plan 006,
Phase 3, step 30).

Unit-level: a hand-rolled fake ``db`` exposing ``find``, ``find_one_and_update``,
``insert_one``, ``list_collection_names`` — no real MongoDB.
"""

from __future__ import annotations

from typing import Any

import pytest


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def __aiter__(self):
        return self._aiter()

    async def _aiter(self):
        for doc in self._docs:
            yield doc


class _FakeCollection:
    """Minimal collection double covering exactly the ops the runner needs."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def find(self, *_args: Any, **_kwargs: Any) -> _FakeCursor:
        return _FakeCursor(list(self.docs.values()))

    async def find_one(self, filt: dict[str, Any]) -> dict[str, Any] | None:
        _id = filt.get("_id")
        return self.docs.get(_id)

    async def insert_one(self, doc: dict[str, Any]) -> Any:
        self.docs[doc["_id"]] = doc
        return doc

    async def find_one_and_update(
        self, filt: dict[str, Any], update: dict[str, Any], *, upsert: bool = False
    ) -> dict[str, Any] | None:
        _id = filt.get("_id")
        existing = self.docs.get(_id)
        if existing is None and not upsert:
            return None
        doc = dict(existing or {"_id": _id})
        doc.update(update.get("$set", {}))
        self.docs[_id] = doc
        return doc

    async def delete_one(self, filt: dict[str, Any]) -> None:
        self.docs.pop(filt.get("_id"), None)


class _FakeDB:
    def __init__(self) -> None:
        self._collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self._collections.setdefault(name, _FakeCollection())

    async def list_collection_names(self) -> list[str]:
        return list(self._collections.keys())


@pytest.fixture
def fake_db() -> _FakeDB:
    return _FakeDB()


def _make_registry(*migration_classes: type) -> Any:
    from varco_beanie.migration.base import MigrationRegistry

    registry = MigrationRegistry()
    registry.register(*migration_classes)
    return registry


def _migration_cls(version: str, name: str, applied: list[str], *, raises: bool = False):
    from varco_beanie.migration.base import Migration

    class _M(Migration):
        pass

    _M.version = version
    _M.name = name

    async def up(self, db: Any) -> None:  # noqa: ANN401
        if raises:
            raise RuntimeError(f"{version} boom")
        applied.append(version)

    _M.up = up
    return _M


async def test_plan_on_virgin_db_lists_every_migration_pending(
    fake_db: _FakeDB,
) -> None:
    applied: list[str] = []
    registry = _make_registry(
        _migration_cls("0001", "first", applied),
        _migration_cls("0002", "second", applied),
    )
    from varco_beanie.migration.migrator import BeanieMigrator

    migrator = BeanieMigrator(fake_db, registry, index_mode="off")

    plan = await migrator.plan()

    assert len(plan.pending) == 2


async def test_upgrade_applies_in_version_order_and_writes_one_record_each(
    fake_db: _FakeDB,
) -> None:
    applied: list[str] = []
    registry = _make_registry(
        _migration_cls("0002", "second", applied),
        _migration_cls("0001", "first", applied),
    )
    from varco_beanie.migration.migrator import BeanieMigrator

    migrator = BeanieMigrator(fake_db, registry, index_mode="off")

    report = await migrator.upgrade()

    assert applied == ["0001", "0002"]
    assert len(report.applied) == 2

    collection = fake_db["varco_migrations"]
    assert "0001" in collection.docs
    assert "0002" in collection.docs


async def test_second_upgrade_applies_nothing(fake_db: _FakeDB) -> None:
    applied: list[str] = []
    registry = _make_registry(_migration_cls("0001", "first", applied))
    from varco_beanie.migration.migrator import BeanieMigrator

    migrator = BeanieMigrator(fake_db, registry, index_mode="off")
    await migrator.upgrade()

    report = await migrator.upgrade()

    assert report.applied == ()


async def test_raising_migration_leaves_earlier_ones_recorded_and_itself_unrecorded(
    fake_db: _FakeDB,
) -> None:
    applied: list[str] = []
    registry = _make_registry(
        _migration_cls("0001", "first", applied),
        _migration_cls("0002", "boom", applied, raises=True),
    )
    from varco_beanie.migration.migrator import BeanieMigrator

    migrator = BeanieMigrator(fake_db, registry, index_mode="off")

    with pytest.raises(RuntimeError):
        await migrator.upgrade()

    collection = fake_db["varco_migrations"]
    assert "0001" in collection.docs
    assert "0002" not in collection.docs


async def test_downgrade_of_migration_with_no_down_raises_irreversible() -> None:
    from varco_beanie.migration.base import Migration
    from varco_core.migration.errors import IrreversibleMigrationError

    class _NoDown(Migration):
        version = "0001"
        name = "no-down"

        async def up(self, db: Any) -> None:
            pass

    migration = _NoDown()

    with pytest.raises(IrreversibleMigrationError):
        await migration.down(None)


async def test_checksum_mismatch_raises_unless_verify_checksums_false(
    fake_db: _FakeDB,
) -> None:
    applied: list[str] = []
    migration_cls = _migration_cls("0001", "first", applied)
    registry = _make_registry(migration_cls)
    from varco_beanie.migration.migrator import BeanieMigrator

    migrator = BeanieMigrator(fake_db, registry, index_mode="off")
    await migrator.upgrade()

    # Tamper with the recorded checksum to simulate a changed source file.
    fake_db["varco_migrations"].docs["0001"]["checksum"] = "tampered"

    migrator2 = BeanieMigrator(fake_db, registry, index_mode="off")
    with pytest.raises(Exception):  # noqa: B017 — exact type is migrator-defined
        await migrator2.plan()

    migrator3 = BeanieMigrator(fake_db, registry, index_mode="off", verify_checksums=False)
    # Must not raise when verification is disabled.
    await migrator3.plan()
