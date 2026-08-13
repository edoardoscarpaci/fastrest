"""varco framework baseline — creates every framework table idempotently

Revision ID: 0001_varco_framework_baseline
Revises:
Create Date: 2026-08-12

DESIGN: checkfirst=True Table.create(), not op.create_table()
    ✅ A database whose framework tables were already built by
       ``ensure_table()`` (SAOutboxRepository, SAJobStore, SASagaRepository,
       SAConversationStore, SADeduplicator, SADeadLetterQueue,
       SAEncryptionKeyStore — source correction 3) upgrades cleanly instead
       of failing on "table already exists" — ``checkfirst=True`` is the
       same idempotence guard ``ensure_table()`` itself uses
       (``metadata.create_all(checkfirst=True)``).
    ✅ Table definitions are taken directly from
       ``varco_sa.metadata.framework_metadata()`` — the single source of
       truth every owning module registers into — so this revision never
       drifts from the real table shapes, including all constraints and
       indexes (``Table.create()`` emits the full DDL, not just columns).
    ❌ This revision can never be edited after release (Alembic revisions
       are immutable history) — any framework schema *change* is a NEW
       revision that ALTERs, not an edit to this file.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001_varco_framework_baseline"
down_revision = None
branch_labels = ("varco",)
depends_on = None


def upgrade() -> None:
    from varco_sa.metadata import framework_metadata

    bind = op.get_bind()
    for table in framework_metadata().tables.values():
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    from varco_sa.metadata import framework_metadata

    bind = op.get_bind()
    for table in reversed(list(framework_metadata().tables.values())):
        table.drop(bind=bind, checkfirst=True)
