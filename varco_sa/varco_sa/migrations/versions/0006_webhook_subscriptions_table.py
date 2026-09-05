"""webhook subscriptions table — Plan 031 / D4a

Revision ID: 0006_webhook_subscriptions_table
Revises: 0005_idempotency_table
Create Date: 2026-09-05

Adds ``webhook_subscriptions`` (``varco_sa/webhook.py``) — the twelfth
framework table, backing ``SAWebhookSubscriptionRepository``.

Same "table-create revision" shape as ``0005_idempotency_table`` — this is
a wholly NEW table, already created for free on a fresh database via
``0001_varco_framework_baseline`` (which iterates
``framework_metadata().tables.values()`` dynamically and
``varco_sa.webhook`` registers itself via ``register_framework_metadata()``
at import time). This revision exists for a database already stamped at
``0005`` (or earlier) on an older wheel, upgrading to a version of
``varco_sa`` that ships this table.

``checkfirst=True`` makes this a no-op on a fresh database that already has
the table, and a real ``CREATE TABLE`` on one that does not.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0006_webhook_subscriptions_table"
down_revision = "0005_idempotency_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from varco_sa.webhook import webhook_metadata

    bind = op.get_bind()
    for table in webhook_metadata.tables.values():
        table.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    from varco_sa.webhook import webhook_metadata

    bind = op.get_bind()
    for table in reversed(list(webhook_metadata.tables.values())):
        table.drop(bind=bind, checkfirst=True)
