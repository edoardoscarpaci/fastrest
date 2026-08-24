"""
varco_sa.rls_framework
=========================
``framework_rls_upgrade`` / ``framework_rls_downgrade`` — one-call RLS
helpers for the two framework tables (``varco_audit_log``,
``varco_dead_letters``) (Plan 009, Phase 6 / R4).

These wrap ``varco_sa.migration.ops.rls_upgrade`` (itself a thin Alembic
wrapper over ``varco_sa.rls.enable_rls_ddl``) so the correct
``(SELECT current_setting(..., true))`` InitPlan form is always used — the
documented, non-negotiable performance regression this codebase guards
against everywhere RLS is touched.

**Nothing calls these automatically.** Paste them into a reviewed app
revision, per `technical_docs/features/postgres-rls.md`'s "RLS enabled by a
startup hook" pitfall — the same rule as every other RLS helper in this
codebase.

Usage (inside an Alembic revision)::

    from varco_sa.rls_framework import framework_rls_upgrade, framework_rls_downgrade

    def upgrade() -> None:
        framework_rls_upgrade(op)

    def downgrade() -> None:
        framework_rls_downgrade(op)

Thread safety:  N/A — one-shot DDL emission at migration time.
Async safety:   N/A — Alembic's ``op`` proxy is synchronous.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

FRAMEWORK_RLS_TABLES: Final[tuple[str, ...]] = ("varco_audit_log", "varco_dead_letters")


def framework_rls_upgrade(
    op: Any,
    *,
    tables: Sequence[str] = FRAMEWORK_RLS_TABLES,
    tenant_column: str = "tenant_id",
    cast_type: str = "text",
) -> None:
    """
    Enable RLS on each of ``tables`` (default: both framework tables).

    Args:
        op:            The Alembic ``op`` module (or any object exposing
                       ``execute()``, matching Alembic's ``Operations`` proxy).
        tables:        Table names to enable RLS on. Defaults to
                       ``FRAMEWORK_RLS_TABLES``.
        tenant_column: Column name carrying the tenant id. Defaults to
                       ``"tenant_id"`` (matches both framework tables' schema
                       from Phase 6).
        cast_type:     Postgres type the ``rls.tenant_id`` GUC is cast to.
                       Defaults to ``"text"`` — NOT ``enable_rls_ddl``'s
                       ``"uuid"`` default — because both framework tables
                       declare ``tenant_id`` as ``String(255)``
                       (``DeadLetterEntry.tenant_id``/``AuditEntry.tenant_id``
                       are ``str | None``, never ``UUID``). A ``uuid`` cast
                       here aborts the revision with ``operator does not
                       exist: character varying = uuid``.

    Raises:
        Nothing directly — DDL errors surface from ``op.execute`` at
        migration-apply time.

    Edge cases:
        - Re-running against a table that already has the policy fails with
          Postgres' "policy already exists"; pair with
          ``framework_rls_downgrade`` for an idempotent revision.
    """
    # DESIGN: call enable_rls_ddl() directly (not migration.ops.rls_upgrade)
    #   ✅ rls_upgrade()'s non-Postgres no-op guard needs op.get_bind() — a
    #      real Alembic Operations proxy, not the minimal execute()-only
    #      shape this module documents accepting. Calling enable_rls_ddl()
    #      directly keeps framework_rls_upgrade usable with any op-like
    #      object that can execute() a string, matching the docstring's own
    #      "any object exposing execute()" contract.
    #   ❌ The non-Postgres skip-with-warning behaviour is NOT inherited here
    #      — callers targeting a non-Postgres dialect must guard themselves
    #      (this module is Postgres-only by construction: RLS doesn't exist
    #      anywhere else).
    from varco_sa.rls import enable_rls_ddl

    for table in tables:
        for stmt in enable_rls_ddl(
            table, tenant_column=tenant_column, cast_type=cast_type
        ):
            op.execute(stmt)


def framework_rls_downgrade(
    op: Any, *, tables: Sequence[str] = FRAMEWORK_RLS_TABLES
) -> None:
    """Reverse ``framework_rls_upgrade`` — drop policies, disable RLS."""
    for table in tables:
        name = f"{table}_tenant_isolation"
        op.execute(f"DROP POLICY IF EXISTS {name} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


__all__ = ["FRAMEWORK_RLS_TABLES", "framework_rls_downgrade", "framework_rls_upgrade"]
