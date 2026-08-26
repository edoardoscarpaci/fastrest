"""
varco_sa.migration.ops
=======================
``rls_upgrade``/``rls_downgrade`` — thin Alembic-``op`` wrappers around
``varco_sa.rls.enable_rls_ddl``, so Row-Level Security lands as a reviewed
migration revision instead of a startup hook (Plan 006 Phase 6).

Nothing auto-enables RLS, ever — these functions must be called from inside
an application's own Alembic revision's ``upgrade()``/``downgrade()``.

DESIGN: thin wrapper, reuse ``rls.py``'s statement construction
    ✅ ``rls_upgrade`` renders byte-identical statements to
       ``enable_rls_ddl()`` — a single source of truth for the
       ``(SELECT current_setting(..., true))`` InitPlan form (see
       ``varco_sa/rls.py``'s module docstring for the 150x-cliff this
       guards against).
    ✅ No-op with a logged warning on a non-Postgres dialect (SQLite has no
       RLS) rather than raising — a migration environment that runs the
       same revisions against SQLite in CI and Postgres in production does
       not crash on the SQLite leg.
    ❌ ``op.execute()`` calls are not batched/transactional beyond whatever
       transaction Alembic itself wraps the revision in.

Thread safety:  ✅ Pure functions except for the ``op.execute()`` calls.
Async safety:   ✅ Synchronous — Alembic revisions run outside the event loop.
"""

from __future__ import annotations

import logging
from typing import Any

from varco_sa.rls import enable_rls_ddl

logger = logging.getLogger(__name__)


def _dialect_name(op: Any) -> str:
    return str(op.get_bind().dialect.name)


def rls_upgrade(
    op: Any,
    table: str,
    *,
    tenant_column: str = "tenant_id",
    policy_name: str | None = None,
    setting: str = "rls.tenant_id",
) -> None:
    """
    Enable Row-Level Security on ``table`` from inside an Alembic revision.

    Args:
        op:            The Alembic ``op`` module (or any object exposing
                        ``execute()``/``get_bind()`` with that shape —
                        matches Alembic's ``Operations`` proxy).
        table:         Table name to protect.
        tenant_column: Column holding the tenant identifier.
        policy_name:   Optional policy name override.
        setting:       Postgres GUC name read via ``current_setting()``.

    Edge cases:
        - No-op with a logged ``WARNING`` on any non-PostgreSQL dialect —
          never raises, so the same revision can run against SQLite in CI.
    """
    if _dialect_name(op) != "postgresql":
        logger.warning(
            "rls_upgrade: dialect %r has no Row-Level Security — skipping for table %r.",
            _dialect_name(op),
            table,
        )
        return

    for stmt in enable_rls_ddl(
        table,
        tenant_column=tenant_column,
        setting=setting,
        policy_name=policy_name,
    ):
        op.execute(stmt)


def rls_downgrade(
    op: Any,
    table: str,
    *,
    policy_name: str | None = None,
) -> None:
    """
    Reverse ``rls_upgrade`` — drop the policy and disable RLS on ``table``.

    Args:
        op:          The Alembic ``op`` module.
        table:       Table name.
        policy_name: Must match the ``policy_name`` used in ``rls_upgrade``,
                     if a custom one was given. Defaults to the same
                     ``f"{table}_tenant_isolation"`` scheme.

    Edge cases:
        - No-op with a logged ``WARNING`` on any non-PostgreSQL dialect.
    """
    if _dialect_name(op) != "postgresql":
        logger.warning(
            "rls_downgrade: dialect %r has no Row-Level Security — skipping for table %r.",
            _dialect_name(op),
            table,
        )
        return

    name = policy_name or f"{table.replace('.', '_')}_tenant_isolation"
    op.execute(f"DROP POLICY IF EXISTS {name} ON {table}")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


__all__ = ["rls_downgrade", "rls_upgrade"]
