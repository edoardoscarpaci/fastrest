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

from tests.conftest import asyncpg_url, create_isolated_database_url

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("VARCO_RUN_INTEGRATION"),
        reason="Integration tests disabled — set VARCO_RUN_INTEGRATION=1",
    ),
]


# The local, module-scoped pg_container fixture was replaced by the
# session-scoped postgres_container fixture in tests/conftest.py
# (Plan 012 / RT1, Step 6/9).


@pytest_asyncio.fixture
async def engine(postgres_container):
    from sqlalchemy.ext.asyncio import create_async_engine

    url = asyncpg_url(postgres_container)
    eng = create_async_engine(url, echo=False)
    yield eng
    await eng.dispose()


async def test_two_migrators_concurrent_upgrade_exactly_one_applies(
    postgres_container,
) -> None:
    """
    Two migrators racing the same pending revisions: exactly one applies them.

    DESIGN: include_framework_branch=True — the contention must be real.
        This test previously ran with ``include_framework_branch=False``, which
        leaves the app branch empty. ``AlembicMigrator.upgrade()`` short-
        circuits on ``plan.is_empty`` *before* acquiring the migration lock
        (a deliberate fast path — never take a heavyweight lock to do nothing),
        so neither migrator ever reached the lock and the test asserted on a
        run with no work to do. The framework branch ships 3 real revisions,
        so both migrators now genuinely contend.
        ✅ Without working exclusion the losing migrator runs the same DDL
           concurrently and Postgres raises "relation already exists" — the
           failure this test exists to catch.
        ❌ Couples the test to the framework branch having pending revisions,
           which is true for any fresh database by construction.

    The loser may legitimately report either shape, both of which are correct
    lock behaviour, so neither is asserted:
      * it waited for the lock, re-planned, found nothing → applied=()
      * it timed out, re-planned, found nothing → applied=(), skipped_locked
    The invariant under test is that exactly ONE migrator applied revisions.
    """
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine
    from varco_sa.migration.migrator import AlembicMigrator

    # Isolated database: this test actually applies revisions, and the
    # alembic_version rows it stamps would break sibling tests that build a
    # migrator with include_framework_branch=False against the same database.
    url = await create_isolated_database_url(postgres_container, "migration_lock_race")
    engine = create_async_engine(url, echo=False)

    migrator_a = AlembicMigrator(engine, include_framework_branch=True)
    migrator_b = AlembicMigrator(engine, include_framework_branch=True)

    # Sanity-check the premise: there must be work to contend over.
    assert not (await migrator_a.plan()).is_empty

    report_a, report_b = await asyncio.gather(
        migrator_a.upgrade(), migrator_b.upgrade()
    )

    reports = [report_a, report_b]
    did_apply = [r for r in reports if r.applied]
    did_nothing = [r for r in reports if not r.applied]

    assert len(did_apply) == 1, f"expected exactly one applier, got {reports}"
    assert len(did_nothing) == 1, f"expected exactly one no-op, got {reports}"

    # Both migrators are now at head, and the DDL ran exactly once.
    assert (await migrator_a.plan()).is_empty
    async with engine.connect() as conn:
        count = await conn.scalar(
            sa.text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name = 'varco_outbox'"
            )
        )
    assert count == 1

    await migrator_a.close()
    await migrator_b.close()
    await engine.dispose()


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
