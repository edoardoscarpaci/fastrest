"""schedules table — Plan 032 / D6

Revision ID: 0007_schedules_table
Revises: 0006_webhook_subscriptions_table
Create Date: 2026-09-05

Adds ``schedules`` (``varco_sa/schedule.py``) — the thirteenth framework
table, backing ``SAScheduleRepository``.

Same "table-create revision" shape as ``0006_webhook_subscriptions_table``
— this is a wholly NEW table, already created for free on a fresh database
via ``0001_varco_framework_baseline`` (which iterates
``framework_metadata().tables.values()`` dynamically and
``varco_sa.schedule`` registers itself via ``register_framework_metadata()``
at import time). This revision exists for a database already stamped at
``0006`` (or earlier) on an older wheel, upgrading to a version of
``varco_sa`` that ships this table.

``checkfirst=True`` makes this a no-op on a fresh database that already has
the table, and a real ``CREATE TABLE`` on one that does not.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0007_schedules_table"
down_revision = "0006_webhook_subscriptions_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from varco_sa.schedule import schedule_metadata

    bind = op.get_bind()
    for table in schedule_metadata.tables.values():
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    from varco_sa.schedule import schedule_metadata

    bind = op.get_bind()
    for table in reversed(list(schedule_metadata.tables.values())):
        table.drop(bind=bind, checkfirst=True)
