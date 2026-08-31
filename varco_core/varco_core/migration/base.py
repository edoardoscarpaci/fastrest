"""
varco_core.migration.base
==========================
Backend-agnostic migration contracts: ``Revision``, ``SchemaMigrationPlan``,
``MigrationReport``, and ``AbstractMigrator``.

The vocabulary mirrors Alembic's (``current``/``heads``/``pending``/
``upgrade``/``downgrade``/``stamp``) because that vocabulary is already in
every SQLAlchemy user's fingers, and ``BeanieMigrator`` can satisfy it
honestly — its "revisions" are sortable version strings.

DESIGN: contracts-only module, zero third-party deps
    ✅ ``varco_fastapi`` and the CLI depend only on this module — never on
       ``alembic`` or ``pymongo`` — keeping the backend-agnostic seam that
       ``AbstractEventBus``/``AbstractJobStore`` already establish.
    ✅ ``check()``/``close()`` are concrete so a third-party migrator is not
       broken by their addition (same rule Plan 005 applied to
       ``AbstractJobStore``).
    ❌ A migrator that wants a smarter ``check()`` (e.g. also running
       ``SchemaGuard``) must override it explicitly.

Thread safety:  ✅ All dataclasses are frozen; ``AbstractMigrator`` holds no
                   shared mutable state at this layer.
Async safety:   ✅ All I/O-bearing methods are ``async def``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from varco_core.migration.errors import PendingMigrationsError


@dataclass(frozen=True)
class Revision:
    """
    A single migration revision, backend-agnostic.

    Args:
        id:     Alembic revision hash, or a Beanie migration's ``version``
                string.
        label:  Human-readable name / commit message for the revision.
        branch: ``"varco"`` for a framework-owned revision (Phase 2's
                packaged Alembic branch), ``None`` for an app revision.
    """

    id: str
    label: str
    branch: str | None = None

    def format(self) -> str:
        """Render as ``"<id> — <label>"``, with the branch in brackets if set."""
        suffix = f" [{self.branch}]" if self.branch else ""
        return f"{self.id} — {self.label}{suffix}"


@dataclass(frozen=True)
class SchemaMigrationPlan:
    """
    The result of ``AbstractMigrator.plan()`` — what is applied vs. pending.

    Args:
        current: Revision ids currently applied (heads). Empty tuple on a
                 virgin database.
        pending: Revisions not yet applied, in application order.
    """

    current: tuple[str, ...]
    pending: tuple[Revision, ...]

    @property
    def is_empty(self) -> bool:
        """``True`` when there is nothing pending — the schema is current."""
        return len(self.pending) == 0

    def format(self) -> str:
        """Render a short human-readable summary of current/pending state."""
        if self.is_empty:
            return f"current: {', '.join(self.current) or '(none)'} — up to date"
        pending_lines = "\n".join(f"  - {rev.format()}" for rev in self.pending)
        return (
            f"current: {', '.join(self.current) or '(none)'}\n"
            f"pending ({len(self.pending)}):\n{pending_lines}"
        )


@dataclass(frozen=True)
class MigrationReport:
    """
    The result of ``AbstractMigrator.upgrade()`` / ``downgrade()``.

    Args:
        applied:        Revisions actually applied during this call.
        duration_s:      Wall-clock seconds spent running the migration.
        skipped_locked: ``True`` when this call did not run any migrations
                         because another holder already did the work while
                         waiting for the lock — the normal rolling-deploy
                         path, not a failure.
    """

    applied: tuple[Revision, ...]
    duration_s: float
    skipped_locked: bool = False

    def format(self) -> str:
        """Render a short human-readable summary of what was applied."""
        if self.skipped_locked:
            return "skipped — another holder applied pending migrations"
        if not self.applied:
            return f"nothing to apply ({self.duration_s:.2f}s)"
        lines = "\n".join(f"  - {rev.format()}" for rev in self.applied)
        return f"applied {len(self.applied)} revision(s) in {self.duration_s:.2f}s:\n{lines}"


class AbstractMigrator(ABC):
    """
    Backend-agnostic migration contract.

    Concrete implementations: ``varco_sa.migration.AlembicMigrator`` (wraps
    ``alembic.command``), ``varco_beanie.migration.BeanieMigrator`` (a
    versioned Mongo migration runner), and
    ``varco_core.migration.InMemoryMigrator`` (the standard unit-test
    double).

    Thread safety:  ⚠️ Implementation-defined — concrete migrators typically
                       expect single-caller use during startup/CLI, not
                       concurrent calls from multiple coroutines.
    Async safety:   ✅ Every abstract method is ``async def``.
    """

    @abstractmethod
    async def plan(self) -> SchemaMigrationPlan:
        """Return the current/pending revision state without applying anything."""
        raise NotImplementedError

    @abstractmethod
    async def upgrade(self, target: str = "heads", *, dry_run: bool = False) -> MigrationReport:
        """
        Apply pending revisions up to ``target``.

        Args:
            target:  Revision id (or ``"heads"``) to upgrade to.
            dry_run: When ``True``, render what would run without writing
                     any DDL/data.

        Returns:
            A ``MigrationReport`` describing what was applied.
        """
        raise NotImplementedError

    @abstractmethod
    async def downgrade(self, target: str) -> MigrationReport:
        """
        Reverse revisions down to ``target``.

        Args:
            target: Revision id (or ``"base"``) to downgrade to.

        Raises:
            IrreversibleMigrationError: A revision in the reversal path has
                no ``down()``/downgrade script.
        """
        raise NotImplementedError

    @abstractmethod
    async def stamp(self, target: str = "heads") -> None:
        """
        Mark ``target`` as applied without executing any DDL/data changes.

        Used to adopt a database whose schema was built by another means
        (e.g. ``ensure_table()``) into migration tracking.
        """
        raise NotImplementedError

    async def check(self) -> SchemaMigrationPlan:
        """
        Resolve the current plan and raise if anything is pending.

        Concrete (not abstract) — a third-party migrator inherits a correct
        default and is not broken by this method's addition.

        Returns:
            The ``SchemaMigrationPlan`` when nothing is pending.

        Raises:
            PendingMigrationsError: The plan has pending revisions.
        """
        plan = await self.plan()
        if not plan.is_empty:
            raise PendingMigrationsError(plan)
        return plan

    async def close(self) -> None:
        """
        Release any resources held by this migrator (connections, etc.).

        Concrete no-op by default — engines that hold a connection/pool
        override this. Safe to call multiple times.
        """
        return


__all__ = [
    "AbstractMigrator",
    "SchemaMigrationPlan",
    "MigrationReport",
    "Revision",
]
