"""idempotency table — Plan 029 / D1b

Revision ID: 0005_idempotency_table
Revises: 0004_job_zoned_schedule
Create Date: 2026-09-05

Adds ``varco_idempotency`` (``varco_sa/idempotency.py``) — the eleventh
framework table, backing ``SAIdempotencyStore``.

**Why a table-create revision, not a column-add one** (contrast with
0002-0004, which all ALTER existing tables): this is a wholly NEW table. A
database created fresh via ``0001_varco_framework_baseline`` already has it
— that revision iterates ``framework_metadata().tables.values()``
dynamically and this module registers itself via
``register_framework_metadata()`` at import time (§D-D1-atomic's SA
implementation), so ``0001`` on an up-to-date wheel creates it for free.
This revision exists for a database already stamped at ``0004`` (or
earlier) on an older wheel, upgrading to a version of ``varco_sa`` that
ships this table — the same "0001 is dynamic, everyone after it must be
idempotent and explicit" reasoning ``0002``/``0003``/``0004`` already
document for column-level changes, applied here at the table level.

``checkfirst=True`` (via ``Table.create()``, exactly like ``0001``'s own
per-table loop) makes this a no-op on a fresh database that already has
the table, and a real ``CREATE TABLE`` on one that does not.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0005_idempotency_table"
down_revision = "0004_job_zoned_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from varco_sa.idempotency import idempotency_metadata

    bind = op.get_bind()
    for table in idempotency_metadata.tables.values():
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    from varco_sa.idempotency import idempotency_metadata

    bind = op.get_bind()
    for table in reversed(list(idempotency_metadata.tables.values())):
        table.drop(bind=bind, checkfirst=True)
