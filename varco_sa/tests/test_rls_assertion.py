"""
Failing tests for varco_sa.tenancy.rls_check.assert_rls_enabled() (Plan 007,
Phase 3, step 7). Assert-only — never emits DDL (RD-6).
"""

from __future__ import annotations

import logging

import pytest


class _FakeConnection:
    def __init__(self, dialect_name: str, rls_enabled_tables: set[str]) -> None:
        self.dialect = type("dialect", (), {"name": dialect_name})()
        self._rls_enabled_tables = rls_enabled_tables
        self.executed: list[str] = []

    async def execute(self, stmt, *args, **kwargs):
        self.executed.append(str(stmt))

        class _Result:
            def __init__(self, rows):
                self._rows = rows

            def scalars(self):
                return self

            def all(self):
                return self._rows

        return _Result([])


async def test_assert_rls_enabled_returns_tables_missing_a_policy() -> None:
    from varco_sa.tenancy.rls_check import assert_rls_enabled

    conn = _FakeConnection("postgresql", rls_enabled_tables=set())

    missing = await assert_rls_enabled(
        conn,
        tables=["orders"],
        global_tables=set(),
        framework_tables=set(),
        enforce=False,
    )

    assert "orders" in missing


async def test_enforce_true_with_missing_policy_raises_tenant_isolation_error() -> None:
    from varco_core.tenancy.catalog import TenantIsolationError
    from varco_sa.tenancy.rls_check import assert_rls_enabled

    conn = _FakeConnection("postgresql", rls_enabled_tables=set())

    with pytest.raises(TenantIsolationError) as exc:
        await assert_rls_enabled(
            conn,
            tables=["orders"],
            global_tables=set(),
            framework_tables=set(),
            enforce=True,
        )

    message = str(exc.value)
    assert "orders" in message
    assert "varco_sa.migration.ops.rls_upgrade(op," in message
    assert "technical_docs/features/postgres-rls.md" in message


async def test_global_and_framework_tables_are_skipped_not_flagged() -> None:
    from varco_sa.tenancy.rls_check import assert_rls_enabled

    conn = _FakeConnection("postgresql", rls_enabled_tables=set())

    missing = await assert_rls_enabled(
        conn,
        tables=["orders", "reference_data", "varco_outbox"],
        global_tables={"reference_data"},
        framework_tables={"varco_outbox"},
        enforce=False,
    )

    assert missing == ["orders"]


async def test_enforce_false_never_queries() -> None:
    from varco_sa.tenancy.rls_check import assert_rls_enabled

    conn = _FakeConnection("postgresql", rls_enabled_tables=set())

    await assert_rls_enabled(
        conn,
        tables=["orders"],
        global_tables=set(),
        framework_tables=set(),
        enforce=False,
    )

    # enforce=False still returns the missing list without emitting DDL —
    # the only forbidden thing is a write/DDL statement, never a read.
    assert not any("CREATE" in stmt.upper() or "ALTER" in stmt.upper() for stmt in conn.executed)


async def test_non_postgres_dialect_skips_with_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from varco_sa.tenancy.rls_check import assert_rls_enabled

    conn = _FakeConnection("sqlite", rls_enabled_tables=set())

    with caplog.at_level(logging.WARNING):
        missing = await assert_rls_enabled(
            conn,
            tables=["orders"],
            global_tables=set(),
            framework_tables=set(),
            enforce=True,
        )

    assert missing == []
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
