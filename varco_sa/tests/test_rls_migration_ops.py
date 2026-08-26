"""
Failing tests for varco_sa.migration.ops.rls_upgrade / rls_downgrade
(Plan 006, Phase 6, step 57).

``rls_upgrade`` must render the exact same statements as
``enable_rls_ddl`` does today (the regression guard that the
``(SELECT current_setting(..., true))`` InitPlan form is preserved — see
CLAUDE.md's pitfall table and ``varco_sa/rls.py``'s module docstring).
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from varco_sa.rls import enable_rls_ddl


class _RecordingOp:
    """Fake Alembic ``op`` module — records every ``execute()`` call."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, stmt: str) -> None:
        self.executed.append(str(stmt))

    def get_bind(self) -> MagicMock:
        bind = MagicMock()
        bind.dialect.name = "postgresql"
        return bind


class _SqliteOp(_RecordingOp):
    def get_bind(self) -> MagicMock:
        bind = MagicMock()
        bind.dialect.name = "sqlite"
        return bind


async def test_rls_upgrade_renders_same_statements_as_enable_rls_ddl() -> None:
    from varco_sa.migration.ops import rls_upgrade

    expected = enable_rls_ddl("orders")

    op = _RecordingOp()
    rls_upgrade(op, "orders")

    assert op.executed == expected


async def test_rls_upgrade_preserves_initplan_select_form() -> None:
    from varco_sa.migration.ops import rls_upgrade

    op = _RecordingOp()
    rls_upgrade(op, "orders")

    create_policy_stmt = next(s for s in op.executed if "CREATE POLICY" in s)
    assert "(SELECT NULLIF(current_setting(" in create_policy_stmt


async def test_rls_downgrade_renders_drop_policy_and_disable_rls() -> None:
    from varco_sa.migration.ops import rls_downgrade

    op = _RecordingOp()
    rls_downgrade(op, "orders")

    joined = "\n".join(op.executed)
    assert "DROP POLICY" in joined
    assert "DISABLE ROW LEVEL SECURITY" in joined


async def test_rls_upgrade_on_non_postgres_dialect_is_noop_with_logged_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from varco_sa.migration.ops import rls_upgrade

    op = _SqliteOp()

    with caplog.at_level(logging.WARNING):
        rls_upgrade(op, "orders")  # must not raise

    assert op.executed == []
    assert any(record.levelno == logging.WARNING for record in caplog.records)


async def test_rls_downgrade_on_non_postgres_dialect_is_noop_with_logged_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from varco_sa.migration.ops import rls_downgrade

    op = _SqliteOp()

    with caplog.at_level(logging.WARNING):
        rls_downgrade(op, "orders")  # must not raise

    assert op.executed == []
    assert any(record.levelno == logging.WARNING for record in caplog.records)
