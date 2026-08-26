"""
varco_beanie.migration.migrator
================================
``BeanieMigrator`` — the MongoDB migration backend implementing
``varco_core.migration.AbstractMigrator``: a versioned ``Migration`` runner
plus opt-in index reconciliation (D5).

DESIGN: two independent mechanisms behind one AbstractMigrator
    ✅ Hand-written migrations (``MigrationRegistry``) and index
       reconciliation (``IndexReconciler``) are genuinely different
       concerns — a migration is arbitrary code; an index is a declarative
       fact derivable from domain metadata. ``plan()`` reports both
       uniformly (``Revision(branch="index")`` for missing indexes) so
       ``mode="check"`` sees a single picture.
    ✅ ``index_mode`` defaults to ``"check"`` **independent of** ``mode``
       (Plan 006 D5) — ``mode="upgrade"`` never silently starts an index
       build; that is always a separate opt-in.

Thread safety:  ⚠️ One ``BeanieMigrator`` instance is not safe for
                   concurrent ``upgrade()`` calls from the SAME process —
                   cross-process exclusion is the lock document.
Async safety:   ✅ Every public method is ``async def``; the heartbeat runs
                   as a background ``asyncio.Task``, cancelled in a
                   ``finally`` block.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import time
from typing import TYPE_CHECKING, Any, Literal

from varco_core.migration.base import (
    AbstractMigrator,
    MigrationPlan,
    MigrationReport,
    Revision,
)
from varco_core.migration.errors import MigrationError
from varco_core.migration.settings import MigrationSettings

from varco_beanie.migration.store import MigrationStore

if TYPE_CHECKING:
    from varco_beanie.index_guard import BeanieIndexGuard
    from varco_beanie.migration.base import Migration, MigrationRegistry


class ChecksumMismatchError(MigrationError):
    """Raised when a recorded migration's source no longer matches its checksum."""


def _default_owner_id() -> str:
    import socket

    return f"{socket.gethostname()}:{os.getpid()}"


def _checksum(migration_cls: type[Migration]) -> str:
    """Content hash of a migration's source (falls back to name if unavailable)."""
    try:
        source = inspect.getsource(migration_cls)
    except (OSError, TypeError):
        source = f"{migration_cls.__module__}.{migration_cls.__qualname__}"
    payload = f"{migration_cls.version}:{migration_cls.name}:{source}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class BeanieMigrator(AbstractMigrator):
    """
    Async ``AbstractMigrator`` implementation backed by a Mongo migration
    collection + optional index reconciliation.

    Args:
        db:               An ``AsyncIOMotorDatabase`` (or compatible fake).
        registry:         The ``MigrationRegistry`` of hand-written migrations.
        index_guard:       Optional ``BeanieIndexGuard`` for index reconciliation.
        index_mode:       ``"off"`` / ``"check"`` (default) / ``"create"`` —
                          independent of ``mode`` (Plan 006 D5).
        settings:          ``None`` → ``MigrationSettings.from_env()``.
        verify_checksums: When ``True`` (default), a recorded migration
                          whose source no longer matches its stored
                          checksum raises ``ChecksumMismatchError`` on
                          ``plan()``.
        owner_id:          Lock-holder identity. ``None`` →
                          ``f"{hostname}:{pid}"``.
    """

    def __init__(
        self,
        db: Any,
        registry: MigrationRegistry,
        *,
        index_guard: BeanieIndexGuard | None = None,
        index_mode: Literal["off", "check", "create"] = "check",
        settings: MigrationSettings | None = None,
        verify_checksums: bool = True,
        owner_id: str | None = None,
    ) -> None:
        self._db = db
        self._registry = registry
        self._index_guard = index_guard
        self._index_mode = index_mode
        self._settings = settings or MigrationSettings.from_env()
        self._verify_checksums = verify_checksums
        self._owner_id = owner_id or _default_owner_id()
        self._store = MigrationStore(db)

    async def _pending_migrations(self) -> list[type[Migration]]:
        applied = await self._store.applied_versions()
        return [m for m in self._registry.ordered() if m.version not in applied]

    async def _verify_recorded_checksums(self) -> None:
        if not self._verify_checksums:
            return
        applied = await self._store.applied_versions()
        by_version = {m.version: m for m in self._registry.ordered()}
        for version in applied:
            migration_cls = by_version.get(version)
            if migration_cls is None:
                continue
            record = await self._store.get_record(version)
            if record is None:
                continue
            recorded_checksum = record.get("checksum")
            expected_checksum = _checksum(migration_cls)
            if recorded_checksum is not None and recorded_checksum != expected_checksum:
                raise ChecksumMismatchError(
                    f"Migration {version!r} ({migration_cls.name!r}) checksum "
                    f"mismatch — recorded {recorded_checksum!r}, source now "
                    f"hashes to {expected_checksum!r}. Pass verify_checksums=False "
                    "to opt out."
                )

    async def _index_pending(self) -> list[Revision]:
        if self._index_mode == "off" or self._index_guard is None:
            return []
        drift = await self._index_guard.report(self._db)
        pending: list[Revision] = []
        for collection, labels in drift.missing_indexes.items():
            for label in labels:
                pending.append(
                    Revision(id=f"index:{collection}:{label}", label=label, branch="index")
                )
        return pending

    async def plan(self) -> MigrationPlan:
        await self._verify_recorded_checksums()

        applied = await self._store.applied_versions()
        pending_migrations = await self._pending_migrations()
        pending = [Revision(id=m.version, label=m.name) for m in pending_migrations]
        pending.extend(await self._index_pending())

        return MigrationPlan(current=tuple(sorted(applied)), pending=tuple(pending))

    async def upgrade(self, target: str = "heads", *, dry_run: bool = False) -> MigrationReport:
        start = time.monotonic()
        await self._verify_recorded_checksums()

        pending_migrations = await self._pending_migrations()

        if dry_run:
            applied = tuple(Revision(id=m.version, label=m.name) for m in pending_migrations)
            return MigrationReport(applied=applied, duration_s=time.monotonic() - start)

        if not pending_migrations:
            return MigrationReport(applied=(), duration_s=time.monotonic() - start)

        # DESIGN: poll acquire() up to lock_timeout, mirroring the SA D2
        # algorithm (poll every 0.5s until acquired or the deadline) — a
        # single acquire() attempt would treat the NORMAL rolling-deploy
        # case (another instance is mid-migration) as an immediate failure
        # instead of waiting for it to finish.
        deadline = time.monotonic() + self._settings.lock_timeout
        acquired = await self._store.acquire(self._owner_id, self._settings.lock_timeout)
        while not acquired and time.monotonic() < deadline:
            await asyncio.sleep(min(0.5, max(deadline - time.monotonic(), 0)))
            acquired = await self._store.acquire(self._owner_id, self._settings.lock_timeout)

        if not acquired:
            replanned = await self.plan()
            revision_pending = [r for r in replanned.pending if r.branch != "index"]
            if not revision_pending:
                return MigrationReport(
                    applied=(), duration_s=time.monotonic() - start, skipped_locked=True
                )
            from varco_core.migration.errors import MigrationLockTimeout

            raise MigrationLockTimeout(self._settings.lock_key, self._settings.lock_timeout)

        # Re-check pending migrations now that the lock is actually held —
        # the list computed before acquiring may be stale if another
        # instance finished applying them while this one was waiting
        # (contended-and-then-acquired is still "another instance did the
        # work while we waited" — report it as skipped_locked=True rather
        # than a normal empty-report, since we started with non-empty
        # pending at function entry).
        pending_migrations = await self._pending_migrations()
        if not pending_migrations:
            await self._store.release(self._owner_id)
            return MigrationReport(
                applied=(), duration_s=time.monotonic() - start, skipped_locked=True
            )

        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        applied_revisions: list[Revision] = []
        try:
            for migration_cls in pending_migrations:
                migration = migration_cls()
                migration_start = time.monotonic()
                await migration.up(self._db)
                duration_ms = (time.monotonic() - migration_start) * 1000
                await self._store.record_applied(
                    migration_cls.version,
                    name=migration_cls.name,
                    checksum=_checksum(migration_cls),
                    duration_ms=duration_ms,
                    applied_by=self._owner_id,
                )
                applied_revisions.append(
                    Revision(id=migration_cls.version, label=migration_cls.name)
                )

            if self._index_mode == "create" and self._index_guard is not None:
                from varco_beanie.migration.indexes import IndexReconciler

                await IndexReconciler(self._index_guard, self._db).apply()
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            await self._store.release(self._owner_id)

        return MigrationReport(
            applied=tuple(applied_revisions), duration_s=time.monotonic() - start
        )

    async def _heartbeat_loop(self) -> None:
        # DESIGN: interval = ttl / 3 (Plan 006 Risks section) — a hung
        # migration must have its heartbeat cancelled in a finally block or
        # the lock is silently held forever; a too-long interval risks the
        # lock expiring mid-migration on a slow-but-alive holder.
        interval = max(self._settings.lock_timeout / 3, 0.1)
        while True:
            await asyncio.sleep(interval)
            await self._store.heartbeat(self._owner_id, self._settings.lock_timeout)

    async def downgrade(self, target: str) -> MigrationReport:
        start = time.monotonic()
        applied_versions = sorted(await self._store.applied_versions(), reverse=True)
        by_version = {m.version: m for m in self._registry.ordered()}

        reversed_revisions: list[Revision] = []
        for version in applied_versions:
            if version <= target:
                break
            migration_cls = by_version.get(version)
            if migration_cls is None:
                continue
            migration = migration_cls()
            await migration.down(self._db)  # raises IrreversibleMigrationError if unset
            await self._store.remove_record(version)
            reversed_revisions.append(Revision(id=version, label=migration_cls.name))

        return MigrationReport(
            applied=tuple(reversed_revisions), duration_s=time.monotonic() - start
        )

    async def stamp(self, target: str = "heads") -> None:
        pending = await self._pending_migrations()
        for migration_cls in pending:
            await self._store.record_applied(
                migration_cls.version,
                name=migration_cls.name,
                checksum=_checksum(migration_cls),
                duration_ms=0.0,
                applied_by=self._owner_id,
            )

    async def close(self) -> None:
        return None


__all__ = ["BeanieMigrator", "ChecksumMismatchError"]
