"""audit hash chain — Plan 009 Phase 12 (R8)

Revision ID: 0003_audit_hash_chain
Revises: 0002_dlq_audit_tenant_id
Create Date: 2026-08-18

Adds ``seq`` (monotone sequence number), ``prev_hash``, and ``entry_hash``
(both 64-char hex SHA-256 digests) to ``varco_audit_log`` — all nullable, no
backfill. Existing pre-Phase-12 rows are simply unchained
(``seq=NULL``/``prev_hash=NULL``) — ``AuditRepository.verify_chain()``
treats a ``seq=None`` entry as outside the chain rather than a break (see
its docstring), and ``SAAuditRepository(hash_chain=True)`` only chains rows
written after this column exists.

Same idempotent column-exists guard as ``0002_dlq_audit_tenant_id`` — see
that revision's DESIGN block for the rationale.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_audit_hash_chain"
down_revision = "0002_dlq_audit_tenant_id"
branch_labels = None
depends_on = None

_TABLE = "varco_audit_log"
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("seq", sa.Integer()),
    ("prev_hash", sa.String(64)),
    ("entry_hash", sa.String(64)),
)


def _has_column(bind: sa.engine.Connection, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for name, col_type in _COLUMNS:
        if not _has_column(bind, _TABLE, name):
            op.add_column(_TABLE, sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    for name, _col_type in _COLUMNS:
        if _has_column(bind, _TABLE, name):
            op.drop_column(_TABLE, name)
