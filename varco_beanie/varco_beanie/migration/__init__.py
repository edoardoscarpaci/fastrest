"""
varco_beanie.migration
=======================
``BeanieMigrator`` — the MongoDB migration backend implementing
``varco_core.migration.AbstractMigrator``: a versioned ``Migration``
registry/runner (``varco_migrations`` collection + lock document) and an
opt-in index reconciler built on ``BeanieIndexGuard``.

See ``technical_docs/features/schema-migrations.md`` for the full guide.
"""

from __future__ import annotations

from varco_beanie.migration.base import Migration, MigrationRegistry
from varco_beanie.migration.indexes import IndexReconciler
from varco_beanie.migration.migrator import BeanieMigrator, ChecksumMismatchError

__all__ = [
    "BeanieMigrator",
    "ChecksumMismatchError",
    "IndexReconciler",
    "Migration",
    "MigrationRegistry",
]
