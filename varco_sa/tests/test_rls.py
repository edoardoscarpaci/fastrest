"""
Tests for varco_sa.rls — Postgres RLS DDL helpers (Plan 005, Phase 8, Step 86).
==================================================================================

Unit tests (no DB): ``enable_rls_ddl()`` output shape — the non-negotiable
regression test for the 150x InitPlan cliff (Risks section of
``plans/005-upstream-gaps.md``): the literal ``(SELECT `` substring MUST be
present in the generated ``USING``/``WITH CHECK`` clauses, and the
``, true`` (``current_setting``'s missing-ok flag) must be present too.

Integration tests (``-m integration``, real Postgres via testcontainers):
with the policy applied via ``enable_rls_ddl()``, a session that never calls
``set_tenant_local()`` sees zero rows; after ``set_tenant_local(t)`` it sees
exactly tenant ``t``'s rows; the setting does not survive the transaction.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio

from varco_sa.rls import enable_rls_ddl, set_tenant_local

from tests.conftest import provision_rls_app_url


# ════════════════════════════════════════════════════════════════════════════════
# Unit tests — enable_rls_ddl() output shape (no DB required)
# ════════════════════════════════════════════════════════════════════════════════


class TestEnableRlsDdlInitPlanForm:
    def test_output_contains_literal_select_subquery(self) -> None:
        """
        Non-negotiable regression test for the 150x InitPlan cliff: the naive
        ``current_setting(...)`` form (no subquery) defeats the planner's
        index-usage InitPlan optimisation. Every generated clause referencing
        ``current_setting`` MUST wrap it in ``(SELECT ...)``.
        """
        ddl = enable_rls_ddl("orders")
        joined = "\n".join(ddl)
        assert "(SELECT " in joined

    def test_missing_ok_true_flag_present(self) -> None:
        """
        ``current_setting(name, true)`` — the second, missing-ok argument —
        must be present so a session with no tenant set yet raises no error
        (returns NULL instead), which is what makes "unset session sees zero
        rows" the failure mode instead of a Postgres exception.
        """
        ddl = enable_rls_ddl("orders")
        joined = "\n".join(ddl)
        assert ", true)" in joined

    def test_default_setting_name(self) -> None:
        ddl = enable_rls_ddl("orders")
        joined = "\n".join(ddl)
        assert "rls.tenant_id" in joined

    def test_custom_setting_name(self) -> None:
        ddl = enable_rls_ddl("orders", setting="rls.custom_tenant")
        joined = "\n".join(ddl)
        assert "rls.custom_tenant" in joined
        assert "rls.tenant_id" not in joined

    def test_default_tenant_column(self) -> None:
        ddl = enable_rls_ddl("orders")
        joined = "\n".join(ddl)
        assert "tenant_id = " in joined

    def test_custom_tenant_column(self) -> None:
        ddl = enable_rls_ddl("orders", tenant_column="org_id")
        joined = "\n".join(ddl)
        assert "org_id = " in joined
        assert "tenant_id = " not in joined

    def test_enables_and_forces_row_level_security(self) -> None:
        ddl = enable_rls_ddl("orders")
        joined = "\n".join(ddl)
        assert "ENABLE ROW LEVEL SECURITY" in joined
        assert "FORCE ROW LEVEL SECURITY" in joined

    def test_creates_policy_with_using_and_with_check(self) -> None:
        ddl = enable_rls_ddl("orders")
        joined = "\n".join(ddl)
        assert "CREATE POLICY" in joined
        assert "USING (" in joined
        assert "WITH CHECK (" in joined

    def test_default_policy_name(self) -> None:
        ddl = enable_rls_ddl("orders")
        joined = "\n".join(ddl)
        assert "orders_tenant_isolation" in joined

    def test_custom_policy_name(self) -> None:
        ddl = enable_rls_ddl("orders", policy_name="my_custom_policy")
        joined = "\n".join(ddl)
        assert "my_custom_policy" in joined
        assert "orders_tenant_isolation" not in joined

    def test_no_io_returns_plain_list_of_strings(self) -> None:
        ddl = enable_rls_ddl("orders")
        assert isinstance(ddl, list)
        assert all(isinstance(stmt, str) for stmt in ddl)
        assert len(ddl) == 3


# ════════════════════════════════════════════════════════════════════════════════
# Integration tests — real Postgres, policy applied, set_tenant_local behaviour
# ════════════════════════════════════════════════════════════════════════════════

pytestmark_integration = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("VARCO_RUN_INTEGRATION"),
        reason="Integration tests disabled — set VARCO_RUN_INTEGRATION=1",
    ),
]


@pytest.fixture(scope="module")
def pg_container():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:15-alpine") as pg:
        yield pg


@pytest_asyncio.fixture
async def engine(pg_container):
    from sqlalchemy.ext.asyncio import create_async_engine

    # Non-superuser role: the container's own role has BYPASSRLS and would
    # never see the policy enforced. See conftest.provision_rls_app_url.
    url = await provision_rls_app_url(pg_container)
    eng = create_async_engine(url, echo=False)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def rls_protected_table(engine):
    """
    Creates a table with a non-superuser owner (RLS is a no-op for
    superusers/table owners unless FORCE is applied — testcontainers' default
    role IS the table owner, so FORCE ROW LEVEL SECURITY is what makes this
    fixture meaningful), inserts two tenants' rows, and applies the RLS
    policy via ``enable_rls_ddl()``.
    """
    import sqlalchemy as sa

    table = f"rls_test_{uuid.uuid4().hex[:8]}"
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())

    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                f"CREATE TABLE {table} "
                "(id SERIAL PRIMARY KEY, tenant_id UUID NOT NULL, value TEXT)"
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
        for stmt in enable_rls_ddl(table):
            await conn.execute(sa.text(stmt))

    yield table, tenant_a, tenant_b

    async with engine.begin() as conn:
        await conn.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("VARCO_RUN_INTEGRATION"),
    reason="Integration tests disabled — set VARCO_RUN_INTEGRATION=1",
)
class TestRlsPolicyEnforcement:
    async def test_unset_session_sees_zero_rows(
        self, session_factory, rls_protected_table
    ) -> None:
        import sqlalchemy as sa

        table, _tenant_a, _tenant_b = rls_protected_table
        async with session_factory() as session:
            result = await session.execute(sa.text(f"SELECT * FROM {table}"))
            assert result.fetchall() == []

    async def test_set_tenant_local_sees_exactly_that_tenants_rows(
        self, session_factory, rls_protected_table
    ) -> None:
        import sqlalchemy as sa

        table, tenant_a, _tenant_b = rls_protected_table
        async with session_factory() as session:
            async with session.begin():
                await set_tenant_local(session, tenant_a)
                result = await session.execute(sa.text(f"SELECT value FROM {table}"))
                rows = [r[0] for r in result.fetchall()]
                assert rows == ["a-row"]

    async def test_setting_does_not_survive_the_transaction(
        self, session_factory, rls_protected_table
    ) -> None:
        import sqlalchemy as sa

        table, tenant_a, _tenant_b = rls_protected_table
        async with session_factory() as session:
            async with session.begin():
                await set_tenant_local(session, tenant_a)
                result = await session.execute(sa.text(f"SELECT value FROM {table}"))
                assert len(result.fetchall()) == 1

            # New transaction on the same session — the SET LOCAL scope has
            # ended; no tenant is set, so RLS again hides every row.
            async with session.begin():
                result = await session.execute(sa.text(f"SELECT value FROM {table}"))
                assert result.fetchall() == []
