"""
varco_core.migration
=====================
Backend-agnostic migration contracts — ``AbstractMigrator``,
``MigrationPlan``, ``MigrationReport``, ``MigrationSettings``, the migration
exception hierarchy, and ``InMemoryMigrator`` (the standard unit-test
double).

Nothing above the storage layer imports ``alembic`` or ``pymongo`` — those
live in ``varco_sa.migration`` and ``varco_beanie.migration`` respectively,
which both implement ``AbstractMigrator`` defined here.

See ``technical_docs/features/schema-migrations.md`` for the full guide.
"""

from __future__ import annotations

from varco_core.migration.base import (
    AbstractMigrator,
    MigrationPlan,
    MigrationReport,
    Revision,
)
from varco_core.migration.errors import (
    IrreversibleMigrationError,
    MigrationBackendUnavailable,
    MigrationError,
    MigrationLockTimeout,
    PendingMigrationsError,
)
from varco_core.migration.inmemory import InMemoryMigrator
from varco_core.migration.settings import MigrationSettings

__all__ = [
    "AbstractMigrator",
    "InMemoryMigrator",
    "IrreversibleMigrationError",
    "MigrationBackendUnavailable",
    "MigrationError",
    "MigrationLockTimeout",
    "MigrationPlan",
    "MigrationReport",
    "MigrationSettings",
    "PendingMigrationsError",
    "Revision",
]
