"""
varco_core.migration.errors
============================
Exception hierarchy for the migration layer.

All exceptions inherit from ``SchemaMigrationError`` so callers can catch the
whole family with a single ``except SchemaMigrationError`` clause and dispatch on
subtype when finer handling is needed — the same pattern as
``ServiceException`` in ``varco_core.exception.service``.

Thread safety:  ✅ Exception objects are immutable after construction.
Async safety:   ✅ Safe to raise and catch in async contexts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from varco_core.migration.base import SchemaMigrationPlan


class SchemaMigrationError(Exception):
    """Base class for all migration-layer exceptions."""


class PendingMigrationsError(SchemaMigrationError):
    """
    Raised by ``AbstractMigrator.check()`` when the schema is behind.

    Carries the ``SchemaMigrationPlan`` so the caller (typically
    ``MigrationLifecycle`` in ``mode="check"``) can render the pending
    revisions in the failure message without a second round-trip.

    Args:
        plan: The ``SchemaMigrationPlan`` whose ``pending`` tuple is non-empty.
    """

    def __init__(self, plan: SchemaMigrationPlan) -> None:
        self.plan = plan
        super().__init__(f"Pending migrations detected — schema is behind:\n{plan.format()}")


class MigrationLockTimeout(SchemaMigrationError):
    """
    Raised when the migration lock could not be acquired within the
    configured timeout AND migrations are still pending after re-checking.

    Args:
        lock_key: The distributed lock key that timed out.
        waited_s: How many seconds were spent waiting.
    """

    def __init__(self, lock_key: str, waited_s: float) -> None:
        self.lock_key = lock_key
        self.waited_s = waited_s
        super().__init__(
            f"Timed out waiting {waited_s:.1f}s for migration lock "
            f"{lock_key!r}, and migrations are still pending."
        )


class IrreversibleMigrationError(SchemaMigrationError):
    """
    Raised when ``downgrade()`` is attempted on a migration with no
    ``down()`` implementation (MongoDB migrations, primarily).
    """


class MigrationBackendUnavailable(SchemaMigrationError):
    """
    Raised when a migration backend's optional dependency is not installed
    (e.g. ``varco_sa.migration`` imported without ``alembic``).

    The message names the exact ``pip install`` line so the operator does
    not have to guess the extra name.
    """


__all__ = [
    "IrreversibleMigrationError",
    "MigrationBackendUnavailable",
    "SchemaMigrationError",
    "MigrationLockTimeout",
    "PendingMigrationsError",
]
