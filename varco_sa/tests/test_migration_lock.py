"""
Failing tests for varco_sa.migration.lock.migration_lock (Plan 006, Phase 2,
step 23).

Mandatory Postgres — SQLite cannot express advisory locks, concurrent DDL, or
the ``idle_in_transaction_session_timeout`` mechanic (D2). Follows the
local-fixture pattern established in ``varco_sa/tests/test_rls.py``
(module-scoped ``PostgresContainer`` + function-scoped ``AsyncEngine``).
"""

from __future__ import annotations

import asyncio
import os

import pytest
import pytest_asyncio

pytestmark = [
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

    url = pg_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql+asyncpg://"
    )
    eng = create_async_engine(url, echo=False)
    yield eng
    await eng.dispose()


async def test_two_migrators_concurrent_upgrade_exactly_one_applies(engine) -> None:
    from varco_sa.migration.migrator import AlembicMigrator

    migrator_a = AlembicMigrator(engine, include_framework_branch=False)
    migrator_b = AlembicMigrator(engine, include_framework_branch=False)

    report_a, report_b = await asyncio.gather(
        migrator_a.upgrade(), migrator_b.upgrade()
    )

    reports = [report_a, report_b]
    skipped = [r for r in reports if r.skipped_locked]
    applied = [r for r in reports if not r.skipped_locked]

    assert len(applied) == 1
    assert len(skipped) == 1
    assert skipped[0].applied == ()

    await migrator_a.close()
    await migrator_b.close()


async def test_lock_timeout_raises_when_revisions_still_pending(engine) -> None:
    from varco_core.migration.errors import MigrationLockTimeout
    from varco_sa.migration.lock import migration_lock

    async with migration_lock(engine, "varco:migrate", timeout=60.0):
        with pytest.raises(MigrationLockTimeout):
            async with migration_lock(engine, "varco:migrate", timeout=0.5):
                pass  # never reached — outer lock is held


async def test_lock_timeout_clean_return_when_nothing_pending(engine) -> None:
    from varco_sa.migration.migrator import AlembicMigrator

    migrator = AlembicMigrator(engine, include_framework_branch=False)
    await migrator.upgrade()  # nothing to apply — empty app branch

    # Second upgrade contends the same lock key; since plan() is empty
    # afterwards, this must return cleanly (not raise).
    report = await migrator.upgrade()

    assert report.applied == ()
    await migrator.close()


async def test_set_local_idle_in_transaction_timeout_overrides_role_setting(
    engine,
) -> None:
    """D2's core invariant: SET LOCAL ... = 0 survives a role-level timeout."""
    from sqlalchemy import text

    from varco_sa.migration.lock import migration_lock

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "ALTER ROLE CURRENT_USER SET idle_in_transaction_session_timeout = '1s'"
            )
        )

    async with migration_lock(engine, "varco:migrate", timeout=30.0):
        await asyncio.sleep(3)  # would be killed by the 1s role setting if unset
        # If we reach here, SET LOCAL ... = 0 held — the regression this test guards.
