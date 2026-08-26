"""
varco_sa.tenancy.rls_check
=============================
``assert_rls_enabled()`` — the RD-6 assertion: **never emits DDL**, only
reads ``pg_class``/``pg_policies`` and reports (or raises naming) tables
missing row-level security.

DESIGN: assert-only, and the failure must teach (RD-6)
    "assert-only and maybe add a guide or an error that point to the
    documentation that explain how to tenable" — the resolved user answer.
    ``TenantIsolationError`` names the table, the concrete remediation
    (``varco_sa.migration.ops.rls_upgrade(op, "<table>")`` in a reviewed
    revision), and the doc path
    (``technical_docs/features/postgres-rls.md``).

``GLOBAL``-scoped tables and the ten framework tables are **skipped, not
flagged** — the RD-6 trap this module exists to close: without the skip, a
shared reference table (which legitimately carries no RLS policy — it has
no ``tenant_id`` to filter on) would be reported as "missing a policy" and
the assertion would be unusable in any deployment with global tables.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING

from varco_core.tenancy.catalog import TenantIsolationError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)

_REMEDIATION_DOC = "technical_docs/features/postgres-rls.md"

# Tables with a policy AND relrowsecurity=true — pg_policies already implies
# a policy exists; relrowsecurity confirms RLS is actually turned on for the
# table (a table can have a stale policy while RLS itself is disabled).
_RLS_ENABLED_QUERY = """
SELECT DISTINCT c.relname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_policies p ON p.schemaname = n.nspname AND p.tablename = c.relname
WHERE c.relrowsecurity = true
  AND c.relname = ANY(:table_names)
"""


async def assert_rls_enabled(
    conn: AsyncConnection,
    *,
    tables: Iterable[str],
    global_tables: set[str],
    framework_tables: set[str],
    enforce: bool,
) -> list[str]:
    """
    Return (and, if ``enforce``, raise on) tables missing Postgres RLS.

    Args:
        conn:             An open async connection.
        tables:           Every routed (``TENANT``-scoped) table name to
                          check.
        global_tables:    Table names to **skip** — ``GLOBAL``-scoped
                          entities legitimately carry no ``tenant_id`` and
                          no RLS policy (the RD-6 trap).
        framework_tables: The ten framework tables — also skipped (they are
                          forced ``GLOBAL``, see Phase 4).
        enforce:          When ``True`` and any non-skipped table is
                          missing a policy, raises. When ``False``, this
                          function still queries and returns the missing
                          list — never emits DDL either way (RD-6:
                          assert-only).

    Returns:
        Sorted list of table names missing RLS. Always ``[]`` on a
        non-Postgres dialect (skipped with one WARNING — mirrors
        ``SAAuditRepository``'s dialect fallback), regardless of
        ``enforce``.

    Raises:
        TenantIsolationError: ``enforce=True`` and one or more non-skipped
            tables are missing a policy. The message names every missing
            table, the literal remediation
            (``varco_sa.migration.ops.rls_upgrade(op, "<table>")``), and
            the doc path — asserted on the text by
            ``test_rls_assertion.py``.

    Edge cases:
        - ``enforce=False`` never emits DDL — the only forbidden
          statement is a write/DDL one; reads are always allowed.
    """
    dialect_name = getattr(conn.dialect, "name", None)
    if dialect_name != "postgresql":
        logger.warning(
            "assert_rls_enabled(): dialect %r is not postgresql — RLS is a "
            "Postgres-only feature. Skipping the check entirely (no rows "
            "read, nothing raised).",
            dialect_name,
        )
        return []

    candidates = sorted(t for t in tables if t not in global_tables and t not in framework_tables)
    if not candidates:
        return []

    import sqlalchemy as sa

    result = await conn.execute(sa.text(_RLS_ENABLED_QUERY), {"table_names": candidates})
    enabled: set[str] = set(result.scalars().all())

    missing = sorted(t for t in candidates if t not in enabled)

    if missing and enforce:
        table_list = ", ".join(missing)
        remediations = "\n".join(
            f'  varco_sa.migration.ops.rls_upgrade(op, "{t}")' for t in missing
        )
        raise TenantIsolationError(
            f"Row-Level Security is not enabled for table(s): {table_list}. "
            f"Add a reviewed migration revision calling:\n{remediations}\n"
            f"See {_REMEDIATION_DOC} for the full guide. "
            "(GLOBAL-scoped and framework tables are never flagged here — "
            "only TenancySettings.enforce_rls=True routed tables are.)"
        )

    return missing
