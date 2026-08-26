"""
Failing integration test for RLS applied via a migration op (Plan 006,
Phase 6, step 59). Postgres mandatory — SQLite has no RLS. Follows the
local-fixture pattern from ``varco_sa/tests/test_rls.py``.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio

from tests.conftest import SyncOp, provision_rls_app_url

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("VARCO_RUN_INTEGRATION"),
        reason="Integration tests disabled — set VARCO_RUN_INTEGRATION=1",
    ),
]


# pg_container (module-scoped) was replaced by the session-scoped
# postgres_container fixture in tests/conftest.py (Plan 012 / RT1, Step 6/9).


@pytest_asyncio.fixture
async def engine(postgres_container):
    from sqlalchemy.ext.asyncio import create_async_engine

    url = await provision_rls_app_url(postgres_container)
    eng = create_async_engine(url, echo=False)
    yield eng
    await eng.dispose()


_SyncOp = SyncOp


async def test_rls_upgrade_hides_cross_tenant_rows_then_downgrade_restores(
    engine,
) -> None:
    import sqlalchemy as sa
    from varco_sa.migration.ops import rls_downgrade, rls_upgrade
    from varco_sa.rls import set_tenant_local

    table = f"rls_migration_test_{uuid.uuid4().hex[:8]}"
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())

    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                f"CREATE TABLE {table} (id SERIAL PRIMARY KEY, tenant_id UUID NOT NULL, value TEXT)"
            )
        )
        await conn.execute(
            sa.text(f"INSERT INTO {table} (tenant_id, value) VALUES (:t, 'a-row')"),
            {"t": tenant_a},
        )
        await conn.execute(
            sa.text(f"INSERT INTO {table} (tenant_id, value) VALUES (:t, 'b-row')"),
            {"t": tenant_b},
        )
        await conn.run_sync(lambda sync_conn: rls_upgrade(_SyncOp(sync_conn), table))

    async with engine.connect() as conn:
        await set_tenant_local(conn, tenant_a)
        result = await conn.execute(sa.text(f"SELECT value FROM {table}"))
        rows = [r[0] for r in result.fetchall()]
        assert rows == ["a-row"]

    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: rls_downgrade(_SyncOp(sync_conn), table))

    async with engine.connect() as conn:
        result = await conn.execute(sa.text(f"SELECT value FROM {table} ORDER BY value"))
        rows = [r[0] for r in result.fetchall()]
        assert rows == ["a-row", "b-row"]
