"""
Failing tests for the framework Alembic branch shipped inside varco_sa
(Plan 006, Phase 2, step 27) — ``varco_sa/migrations/versions/`` +
``AlembicMigrator.adopt_framework_tables()``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import create_async_engine

FRAMEWORK_TABLE_NAMES = frozenset(
    {
        "varco_outbox",
        "varco_inbox",
        "varco_jobs",
        "varco_sagas",
        "varco_conversation_turns",
        "varco_dedup_log",
        "varco_audit_log",
        "varco_dead_letters",
        "varco_encryption_keys",
    }
)


@pytest.fixture
def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'framework.db'}"


async def test_upgrade_varco_at_head_creates_all_nine_framework_tables(
    sqlite_url: str,
) -> None:
    from varco_sa.migration.migrator import AlembicMigrator

    engine = create_async_engine(sqlite_url)
    migrator = AlembicMigrator(engine, include_framework_branch=True)

    await migrator.upgrade("varco@head")

    async with engine.connect() as conn:
        table_names = set(await conn.run_sync(lambda c: sa_inspect(c).get_table_names()))
    assert FRAMEWORK_TABLE_NAMES.issubset(table_names)
    await migrator.close()


async def test_ensure_table_then_upgrade_succeeds_idempotently(
    sqlite_url: str,
) -> None:
    """
    Source correction 3: an ensure_table()-built DB must upgrade cleanly
    rather than erroring on CREATE TABLE varco_jobs.
    """
    from varco_sa.job_store import SAJobStore
    from varco_sa.migration.migrator import AlembicMigrator

    engine = create_async_engine(sqlite_url)
    store = SAJobStore(engine)
    await store.ensure_table()

    migrator = AlembicMigrator(engine, include_framework_branch=True)

    # Must not raise "table varco_jobs already exists".
    await migrator.upgrade("varco@head")

    await migrator.close()


async def test_adopt_framework_tables_stamps_without_executing_ddl(
    sqlite_url: str,
) -> None:
    from varco_sa.job_store import SAJobStore
    from varco_sa.migration.migrator import AlembicMigrator

    engine = create_async_engine(sqlite_url)
    # Build every framework table the ensure_table() way first.
    store = SAJobStore(engine)
    await store.ensure_table()

    migrator = AlembicMigrator(engine, include_framework_branch=True)

    adopted = await migrator.adopt_framework_tables()

    assert "varco_jobs" in adopted

    plan = await migrator.plan()
    # Only the varco branch is adopted; app branch (none registered here) is
    # trivially empty, and varco branch pending must now be empty too.
    varco_pending = [r for r in plan.pending if r.branch == "varco"]
    assert varco_pending == []
    await migrator.close()
