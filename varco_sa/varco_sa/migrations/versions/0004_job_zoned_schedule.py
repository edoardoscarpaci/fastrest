"""job zoned schedule — Plan 011 T2 (D-7)

Revision ID: 0004_job_zoned_schedule
Revises: 0003_audit_hash_chain
Create Date: 2026-08-20

Adds ``run_at_wall`` (naive local wall-clock, no tzinfo), ``run_at_tz``
(IANA zone name), and ``run_at_fold`` (PEP 495 fold, ``server_default 0``)
to ``varco_jobs`` — all nullable/defaulted, no backfill.

**``run_at`` is materialized, not replaced** (D-7): these three columns are
the *intent*; the pre-existing ``run_at`` column remains the claim
predicate's materialization, unchanged in meaning. A row with
``run_at_tz IS NULL`` is byte-identical to today in every respect.

**Why the idempotent column-exists guard is mandatory, not stylistic**:
``0001_varco_framework_baseline`` is dynamic — it iterates
``varco_sa.metadata.framework_metadata().tables`` and creates whatever the
*installed wheel* declares. Since ``job_store.py``'s ``_jobs_table`` Table
object now declares these columns, a FRESH database created by ``0001``
already has them, and this revision must be a no-op there. A database
stamped at ``0003`` before upgrading does NOT have them, and this revision
adds them. Both paths converge on the same schema — see
``test_job_store_zoned.py``'s ``@pytest.mark.integration`` cases.

**No new index** — the claim predicate (``run_at``/``status``) is
unchanged, so the existing ``ix_varco_jobs_claim`` index is still the
right one (D-7).

Same idempotent column-exists guard as ``0002_dlq_audit_tenant_id`` /
``0003_audit_hash_chain`` — see those revisions' DESIGN blocks.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0004_job_zoned_schedule"
down_revision = "0003_audit_hash_chain"
branch_labels = None
depends_on = None

_TABLE = "varco_jobs"
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine, dict], ...] = (
    ("run_at_wall", sa.DateTime(timezone=False), {"nullable": True}),
    ("run_at_tz", sa.String(64), {"nullable": True}),
    ("run_at_fold", sa.Integer(), {"nullable": False, "server_default": "0"}),
)


def _has_column(bind: sa.engine.Connection, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for name, col_type, kwargs in _COLUMNS:
        if not _has_column(bind, _TABLE, name):
            op.add_column(_TABLE, sa.Column(name, col_type, **kwargs))


def downgrade() -> None:
    bind = op.get_bind()
    for name, _col_type, _kwargs in _COLUMNS:
        if _has_column(bind, _TABLE, name):
            op.drop_column(_TABLE, name)
