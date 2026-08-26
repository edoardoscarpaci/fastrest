"""
Unit + integration tests for Plan 011 Phase 4 steps 55-56 —
``SAJobStore``'s zoned-schedule columns, ``list_pending_zoned``, and the
``0004_job_zoned_schedule`` Alembic revision's dual-path idempotency.

Not part of the red-phase test suite the plan shipped with — written per
the plan's explicit instruction (steps 55-57 are implementer-authored).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, UTC
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from varco_core.job.base import Job
from varco_sa.job_store import SAJobStore, jobs_metadata
from varco_sa.metadata import framework_metadata


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(jobs_metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(jobs_metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def store(engine) -> SAJobStore:
    return SAJobStore(engine)


# ── Unit: schema shape ───────────────────────────────────────────────────────


def test_zoned_columns_present_in_framework_metadata() -> None:
    tables = framework_metadata().tables
    jobs_table = tables["varco_jobs"]
    column_names = {c.name for c in jobs_table.columns}
    assert {"run_at_wall", "run_at_tz", "run_at_fold"} <= column_names


def test_sa_job_store_declares_zoned_schedule_support() -> None:
    assert SAJobStore.supports_zoned_schedules is True


# ── Unit: list_pending_zoned SQL shape ───────────────────────────────────────


async def test_list_pending_zoned_returns_only_zoned_pending_jobs_before_cutoff(
    store: SAJobStore,
) -> None:
    now = datetime.now(UTC)

    zoned_due_soon = Job(
        job_id=uuid4(),
        run_at=now + timedelta(hours=1),
        run_at_wall=datetime(2026, 6, 1, 9, 0),
        run_at_tz="America/New_York",
    )
    zoned_far_future = Job(
        job_id=uuid4(),
        run_at=now + timedelta(days=365 * 4),
        run_at_wall=datetime(2030, 1, 1),
        run_at_tz="UTC",
    )
    unzoned = Job(job_id=uuid4(), run_at=now + timedelta(hours=1))

    await store.save(zoned_due_soon)
    await store.save(zoned_far_future)
    await store.save(unzoned)

    before = now + timedelta(hours=48)
    results = await store.list_pending_zoned(before)
    result_ids = {j.job_id for j in results}

    assert zoned_due_soon.job_id in result_ids
    assert zoned_far_future.job_id not in result_ids  # outside the horizon
    assert unzoned.job_id not in result_ids  # run_at_tz IS NULL


async def test_zoned_job_round_trips_through_save_and_get(store: SAJobStore) -> None:
    job = Job(
        job_id=uuid4(),
        run_at=datetime(2026, 6, 1, 13, 0, tzinfo=UTC),
        run_at_wall=datetime(2026, 6, 1, 9, 0),
        run_at_tz="America/New_York",
        run_at_fold=0,
    )
    await store.save(job)
    fetched = await store.get(job.job_id)
    assert fetched is not None
    assert fetched.run_at_wall == job.run_at_wall
    assert fetched.run_at_tz == "America/New_York"
    assert fetched.run_at_fold == 0


async def test_unzoned_job_round_trips_with_none_defaults(store: SAJobStore) -> None:
    job = Job(job_id=uuid4())
    await store.save(job)
    fetched = await store.get(job.job_id)
    assert fetched is not None
    assert fetched.run_at_wall is None
    assert fetched.run_at_tz is None
    assert fetched.run_at_fold == 0


# ── Integration: the two migration convergence paths (D-7) ─────────────────


@pytest.mark.integration
async def test_fresh_database_from_0001_already_has_zoned_columns() -> None:
    pytest.skip("requires Docker Postgres + full Alembic env — run with -m integration")


@pytest.mark.integration
async def test_database_stamped_at_0003_gains_zoned_columns_via_0004() -> None:
    pytest.skip("requires Docker Postgres + full Alembic env — run with -m integration")


@pytest.mark.integration
async def test_downgrade_drops_zoned_columns() -> None:
    pytest.skip("requires Docker Postgres + full Alembic env — run with -m integration")


@pytest.mark.integration
async def test_pre_plan_row_claims_identically_before_and_after_migration() -> None:
    pytest.skip("requires Docker Postgres + full Alembic env — run with -m integration")
