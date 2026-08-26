"""
varco_core.migration.inmemory
==============================
``InMemoryMigrator`` — the standard unit-test double for
``AbstractMigrator``, mirroring ``InMemoryEventBus``/
``InMemoryDeadLetterQueue``.

DESIGN: list + cursor, not a real revision graph
    ✅ Enough to exercise ``MigrationLifecycle``, the CLI, and any consumer
       of ``AbstractMigrator`` without a real database.
    ✅ ``fail_on_upgrade_call`` lets a test script "the Nth upgrade call
       raises" without a real broken revision.
    ❌ No branch/merge semantics — ``target``/``dry_run`` are recorded but
       do not change which revisions are considered pending; a test that
       needs real Alembic-branch behaviour belongs in
       ``varco_sa/tests/test_alembic_migrator.py`` instead.

Thread safety:  ⚠️ Test double — expects single-caller use, no locking.
Async safety:   ✅ All methods are ``async def`` (no real I/O).
"""

from __future__ import annotations

import time

from varco_core.migration.base import (
    AbstractMigrator,
    MigrationPlan,
    MigrationReport,
    Revision,
)
from varco_core.migration.errors import IrreversibleMigrationError


class InMemoryMigrator(AbstractMigrator):
    """
    Test double for ``AbstractMigrator`` — an in-process revision list.

    Args:
        revisions:          Ordered list of ``Revision``s, applied in the
                             given order by ``upgrade()``.
        fail_on_upgrade_call: If set, the Nth call (1-indexed) to
                             ``upgrade()`` raises ``RuntimeError`` instead
                             of applying anything.
        skipped_locked:     When ``True``, ``upgrade()`` behaves as if
                             another holder already applied everything —
                             records the call but returns an empty
                             ``MigrationReport(skipped_locked=True)`` and
                             does not advance the cursor.

    Attributes:
        calls: List of method names called, in order — for test assertions.
    """

    def __init__(
        self,
        *,
        revisions: list[Revision] | None = None,
        fail_on_upgrade_call: int | None = None,
        skipped_locked: bool = False,
        skip_locked_on_upgrade: bool = False,
        pending_after_skip: list[Revision] | None = None,
        upgrade_delay_s: float = 0.0,
        name: str | None = None,
        call_log: list[str] | None = None,
    ) -> None:
        self._revisions = list(revisions or [])
        self._applied_count = 0
        self._fail_on_upgrade_call = fail_on_upgrade_call
        self._upgrade_call_count = 0
        self._skipped_locked = skipped_locked
        # ``skip_locked_on_upgrade`` simulates "another pod is mid-migration":
        # the FIRST upgrade() call returns skipped_locked=True without
        # advancing the cursor; subsequent plan() calls report
        # ``pending_after_skip`` instead of the real revision-list diff, so
        # a test can drive both branches of the D4 "re-check after lock
        # timeout" algorithm (empty → serve, non-empty → raise).
        self._skip_locked_on_upgrade = skip_locked_on_upgrade
        self._pending_after_skip = pending_after_skip
        self._skip_triggered = False
        self._upgrade_delay_s = upgrade_delay_s
        self._name = name
        self._call_log = call_log
        self.calls: list[str] = []
        self.closed = False

    async def plan(self) -> MigrationPlan:
        self.calls.append("plan")
        if self._skip_triggered and self._pending_after_skip is not None:
            return MigrationPlan(current=(), pending=tuple(self._pending_after_skip))
        applied = self._revisions[: self._applied_count]
        pending = tuple(self._revisions[self._applied_count :])
        return MigrationPlan(
            current=tuple(rev.id for rev in applied),
            pending=pending,
        )

    async def upgrade(self, target: str = "heads", *, dry_run: bool = False) -> MigrationReport:
        self.calls.append("upgrade")
        if self._name is not None and self._call_log is not None:
            self._call_log.append(self._name)
        self._upgrade_call_count += 1
        start = time.monotonic()

        if self._upgrade_delay_s:
            import asyncio as _asyncio

            await _asyncio.sleep(self._upgrade_delay_s)

        if (
            self._fail_on_upgrade_call is not None
            and self._upgrade_call_count == self._fail_on_upgrade_call
        ):
            raise RuntimeError("InMemoryMigrator: configured upgrade failure")

        if self._skip_locked_on_upgrade and not self._skip_triggered:
            self._skip_triggered = True
            return MigrationReport(
                applied=(), duration_s=time.monotonic() - start, skipped_locked=True
            )

        if self._skipped_locked:
            return MigrationReport(
                applied=(), duration_s=time.monotonic() - start, skipped_locked=True
            )

        if dry_run:
            pending = tuple(self._revisions[self._applied_count :])
            return MigrationReport(applied=pending, duration_s=time.monotonic() - start)

        applied = tuple(self._revisions[self._applied_count :])
        self._applied_count = len(self._revisions)
        return MigrationReport(applied=applied, duration_s=time.monotonic() - start)

    async def downgrade(self, target: str) -> MigrationReport:
        self.calls.append("downgrade")
        start = time.monotonic()
        if self._applied_count == 0:
            return MigrationReport(applied=(), duration_s=time.monotonic() - start)
        raise IrreversibleMigrationError(
            "InMemoryMigrator has no downgrade scripts — it is a test double."
        )

    async def stamp(self, target: str = "heads") -> None:
        self.calls.append("stamp")
        self._applied_count = len(self._revisions)

    async def close(self) -> None:
        self.calls.append("close")
        self.closed = True


__all__ = ["InMemoryMigrator"]
