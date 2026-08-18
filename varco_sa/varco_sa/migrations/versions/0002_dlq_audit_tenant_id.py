"""dlq/audit tenant_id — Plan 009 Phase 6 (R4)

Revision ID: 0002_dlq_audit_tenant_id
Revises: 0001_varco_framework_baseline
Create Date: 2026-08-18

Adds a nullable ``tenant_id`` column to ``varco_dead_letters`` and
``varco_audit_log``. No backfill — existing rows get ``tenant_id=NULL``,
which is the documented "framework-level, no owning tenant" value (see
``DeadLetterEntry.tenant_id`` / ``AuditEntry.tenant_id`` docstrings), not an
error condition.

DESIGN: idempotent column-exists guard, not a bare ``ADD COLUMN``
    ✅ A database created via ``0001_varco_framework_baseline`` AFTER this
       column was added to the live model already has the column (the
       baseline revision builds tables straight from
       ``framework_metadata()``, which is not frozen the way a revision's
       own DDL is) — running this revision against it must be a no-op, not
       a "column already exists" failure.
    ❌ One extra inspector round-trip per table vs. a bare ``ADD COLUMN`` —
       negligible, one-time, at migration time only.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_dlq_audit_tenant_id"
down_revision = "0001_varco_framework_baseline"
branch_labels = None
depends_on = None

_TABLES = ("varco_dead_letters", "varco_audit_log")


def _has_column(bind: sa.engine.Connection, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        if not _has_column(bind, table, "tenant_id"):
            op.add_column(table, sa.Column("tenant_id", sa.String(255), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        if _has_column(bind, table, "tenant_id"):
            op.drop_column(table, "tenant_id")
