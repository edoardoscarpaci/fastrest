"""
Regression guard: framework RLS policies must cast to the framework schema's
own tenant_id type.

User reports: ``framework_rls_upgrade(op)`` — the shipped, documented one-call
helper for ``varco_audit_log``/``varco_dead_letters`` — aborts the migration
with ``operator does not exist: character varying = uuid``.  Correct behaviour
is that it applies cleanly, because both framework tables declare
``tenant_id`` as ``String(255)`` (and ``DeadLetterEntry.tenant_id`` /
``AuditEntry.tenant_id`` are ``str | None`` in varco_core), so the policy must
compare varchar against text, not against uuid.

This was invisible until now: the only test exercising ``framework_rls_upgrade``
against real Postgres died at a ``NameError`` before reaching the DDL.
"""

from __future__ import annotations

from varco_sa.rls import enable_rls_ddl
from varco_sa.rls_framework import FRAMEWORK_RLS_TABLES, framework_rls_upgrade


class _RecordingOp:
    """Collects DDL instead of executing it."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, stmt: str) -> None:
        self.statements.append(str(stmt))


def test_regression_framework_rls_policy_casts_to_text_not_uuid() -> None:
    """The framework tables' tenant_id is varchar — a ::uuid cast cannot apply."""
    op = _RecordingOp()
    framework_rls_upgrade(op)

    policies = [s for s in op.statements if s.startswith("CREATE POLICY")]
    assert len(policies) == len(FRAMEWORK_RLS_TABLES)
    for stmt in policies:
        assert "::uuid" not in stmt, f"varchar tenant_id cannot compare to uuid: {stmt}"
        assert "::text" in stmt, stmt


def test_regression_framework_rls_keeps_the_initplan_form() -> None:
    """The 150x-cliff guard must survive the cast fix."""
    op = _RecordingOp()
    framework_rls_upgrade(op)
    for stmt in (s for s in op.statements if s.startswith("CREATE POLICY")):
        assert "(SELECT NULLIF(current_setting(" in stmt, stmt


def test_regression_framework_rls_still_forces_row_level_security() -> None:
    """FORCE closes the table-owner exemption — it must stay emitted."""
    op = _RecordingOp()
    framework_rls_upgrade(op)
    for table in FRAMEWORK_RLS_TABLES:
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in op.statements


def test_regression_enable_rls_ddl_default_cast_is_unchanged() -> None:
    """App tables keep the uuid default — this fix must not change them."""
    stmts = enable_rls_ddl("orders")
    policy = next(s for s in stmts if s.startswith("CREATE POLICY"))
    assert "::uuid" in policy


def test_regression_policy_tolerates_the_guc_reset_to_empty_string() -> None:
    """
    An RLS policy must survive a reused pooled connection.

    User reports: after a ``set_tenant_local()`` transaction commits, the very
    next query on the same connection dies with
    ``invalid input syntax for type uuid: ""`` instead of returning zero rows.
    Correct behaviour is zero rows, because RLS with no tenant set must hide
    everything rather than crash.

    Root cause: Postgres resets a ``set_config(..., local => true)`` GUC to the
    empty string — NOT to NULL — when the transaction ends, so
    ``current_setting(setting, true)::uuid`` casts ``''`` and raises.  Wrapping
    in ``NULLIF(..., '')`` maps both "never set" and "reset after SET LOCAL" to
    NULL, which the comparison correctly evaluates to no rows.
    """
    policy = next(s for s in enable_rls_ddl("orders") if s.startswith("CREATE POLICY"))
    assert "NULLIF(" in policy, policy
    # The InitPlan wrapper must still be the outermost form.
    assert "(SELECT NULLIF(current_setting(" in policy, policy
