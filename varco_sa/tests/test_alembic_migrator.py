"""
Failing tests for varco_sa.migration.migrator.AlembicMigrator (Plan 006,
Phase 2, step 20).

All against in-memory/temp-file SQLite with a temp ``alembic/`` directory
built by a fixture — Alembic's ``ScriptDirectory`` caches, so each test needs
its own fresh version-locations directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine


REVISION_TEMPLATE = '''
"""{message}"""
from alembic import op
import sqlalchemy as sa

revision = "{revision}"
down_revision = {down_revision!r}
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "{table}",
        sa.Column("id", sa.Integer, primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("{table}")
'''

FAILING_REVISION_TEMPLATE = '''
"""{message}"""
from alembic import op
import sqlalchemy as sa

revision = "{revision}"
down_revision = {down_revision!r}
branch_labels = None
depends_on = None


def upgrade() -> None:
    raise RuntimeError("boom — this revision always fails")


def downgrade() -> None:
    pass
'''


@pytest.fixture
def versions_dir(tmp_path: Path) -> Path:
    """Fresh temp alembic version-locations directory per test."""
    d = tmp_path / "versions"
    d.mkdir()
    return d


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def sqlite_url(db_path: Path) -> str:
    return f"sqlite+aiosqlite:///{db_path}"


def _write_two_revisions(versions_dir: Path) -> None:
    (versions_dir / "0001_first.py").write_text(
        REVISION_TEMPLATE.format(
            message="first",
            revision="0001",
            down_revision=None,
            table="widgets",
        )
    )
    (versions_dir / "0002_second.py").write_text(
        REVISION_TEMPLATE.format(
            message="second",
            revision="0002",
            down_revision="0001",
            table="gadgets",
        )
    )


async def test_plan_on_virgin_db_shows_all_revisions_pending(
    versions_dir: Path, sqlite_url: str
) -> None:
    from varco_sa.migration.migrator import AlembicMigrator

    _write_two_revisions(versions_dir)
    engine = create_async_engine(sqlite_url)

    migrator = AlembicMigrator(
        engine, version_locations=[versions_dir], include_framework_branch=False
    )

    plan = await migrator.plan()

    assert plan.current == ()
    assert len(plan.pending) == 2
    await migrator.close()


async def test_upgrade_applies_pending_and_plan_becomes_empty(
    versions_dir: Path, sqlite_url: str
) -> None:
    from varco_sa.migration.migrator import AlembicMigrator

    _write_two_revisions(versions_dir)
    engine = create_async_engine(sqlite_url)
    migrator = AlembicMigrator(
        engine, version_locations=[versions_dir], include_framework_branch=False
    )

    report = await migrator.upgrade()

    assert len(report.applied) == 2
    plan = await migrator.plan()
    assert plan.is_empty is True
    await migrator.close()


async def test_upgrade_second_time_is_a_noop(
    versions_dir: Path, sqlite_url: str
) -> None:
    from varco_sa.migration.migrator import AlembicMigrator

    _write_two_revisions(versions_dir)
    engine = create_async_engine(sqlite_url)
    migrator = AlembicMigrator(
        engine, version_locations=[versions_dir], include_framework_branch=False
    )

    await migrator.upgrade()
    report = await migrator.upgrade()

    assert report.applied == ()
    await migrator.close()


async def test_downgrade_base_reverses_all_revisions(
    versions_dir: Path, sqlite_url: str
) -> None:
    from varco_sa.migration.migrator import AlembicMigrator

    _write_two_revisions(versions_dir)
    engine = create_async_engine(sqlite_url)
    migrator = AlembicMigrator(
        engine, version_locations=[versions_dir], include_framework_branch=False
    )
    await migrator.upgrade()

    await migrator.downgrade("base")

    plan = await migrator.plan()
    assert len(plan.pending) == 2
    await migrator.close()


async def test_stamp_marks_without_executing_ddl(
    versions_dir: Path, sqlite_url: str
) -> None:
    from sqlalchemy import inspect as sa_inspect

    from varco_sa.migration.migrator import AlembicMigrator

    _write_two_revisions(versions_dir)
    engine = create_async_engine(sqlite_url)
    migrator = AlembicMigrator(
        engine, version_locations=[versions_dir], include_framework_branch=False
    )

    await migrator.stamp("heads")

    plan = await migrator.plan()
    assert plan.is_empty is True  # stamped as current, per alembic semantics

    async with engine.connect() as conn:
        table_names = await conn.run_sync(
            lambda sync_conn: sa_inspect(sync_conn).get_table_names()
        )
    assert "widgets" not in table_names
    assert "gadgets" not in table_names
    await migrator.close()


async def test_upgrade_dry_run_emits_sql_and_touches_no_table(
    versions_dir: Path, sqlite_url: str
) -> None:
    from sqlalchemy import inspect as sa_inspect

    from varco_sa.migration.migrator import AlembicMigrator

    _write_two_revisions(versions_dir)
    engine = create_async_engine(sqlite_url)
    migrator = AlembicMigrator(
        engine, version_locations=[versions_dir], include_framework_branch=False
    )

    report = await migrator.upgrade(dry_run=True)

    assert report.applied != () or "CREATE TABLE" in report.format()
    async with engine.connect() as conn:
        table_names = await conn.run_sync(
            lambda sync_conn: sa_inspect(sync_conn).get_table_names()
        )
    assert "widgets" not in table_names
    await migrator.close()


async def test_failing_revision_leaves_alembic_version_at_n_minus_1_and_resumes(
    versions_dir: Path, sqlite_url: str
) -> None:
    from varco_sa.migration.migrator import AlembicMigrator

    (versions_dir / "0001_first.py").write_text(
        REVISION_TEMPLATE.format(
            message="first", revision="0001", down_revision=None, table="widgets"
        )
    )
    (versions_dir / "0002_boom.py").write_text(
        FAILING_REVISION_TEMPLATE.format(
            message="boom", revision="0002", down_revision="0001"
        )
    )
    engine = create_async_engine(sqlite_url)
    migrator = AlembicMigrator(
        engine, version_locations=[versions_dir], include_framework_branch=False
    )

    with pytest.raises(Exception):  # noqa: B017 — alembic raises RuntimeError
        await migrator.upgrade()

    plan = await migrator.plan()
    assert plan.current == ("0001",)
    assert len(plan.pending) == 1
    await migrator.close()
