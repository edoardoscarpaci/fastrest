"""
Red-mode tests for Plan 011 Phase 4, step 53 — RD-1's T2 proof, half 2:
varco_core.job.reschedule.ScheduleRematerializer.

Plan line (step 52): "ScheduleRematerializer(store, *, interval=0.0,
horizon=timedelta(hours=48)). interval=0.0 -> never started. Sweeps
list_pending_zoned(before=now + horizon), recomputes under current tzdata,
writes back ONLY when the value actually changed, fenced with
save(expected_epoch=...), catching and skipping StaleLeaseError."
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from varco_core.job.base import AbstractJobStore, Job, JobStatus
from varco_core.job.reschedule import ScheduleRematerializer


class _RematStore(AbstractJobStore):
    def __init__(self) -> None:
        self._jobs: dict = {}
        self.save_calls: list = []

    async def save(self, job: Job, *, expected_epoch: int | None = None) -> None:
        self.save_calls.append(job.job_id)
        self._jobs[job.job_id] = job

    async def get(self, job_id):
        return self._jobs.get(job_id)

    async def list_by_status(self, status, *, limit: int = 100):
        statuses = {status} if isinstance(status, JobStatus) else set(status)
        return [j for j in self._jobs.values() if j.status in statuses][:limit]

    async def delete(self, job_id) -> None:
        self._jobs.pop(job_id, None)

    async def try_claim(self, job_id, *, owner_id=None, lease_ttl=None):
        return None

    async def list_pending_zoned(self, before, limit=100):
        return [
            j
            for j in self._jobs.values()
            if j.status == JobStatus.PENDING
            and j.run_at_tz is not None
            and j.run_at < before
        ][:limit]


def _zoned_job(run_at, run_at_wall, run_at_tz) -> Job:
    return Job(
        job_id=uuid4(),
        run_at=run_at,
        run_at_wall=run_at_wall,
        run_at_tz=run_at_tz,
    )


async def test_interval_zero_never_starts_a_background_task() -> None:
    store = _RematStore()
    remat = ScheduleRematerializer(store, interval=0.0)
    await remat.start()
    # RD-1: no task object created at all when interval=0.0.
    assert getattr(remat, "_task", None) is None
    await remat.stop()


async def test_unchanged_run_at_produces_zero_writes() -> None:
    store = _RematStore()
    # DEVIATION: the fixture's run_at must be the ACTUAL materialization of
    # (run_at_wall, run_at_tz) for "unchanged" to be a meaningful case — the
    # original fixture paired an unrelated `now + 1h` run_at with a fixed
    # 2026-06-01 09:00 UTC wall/zone, which a correct recompute would always
    # see as "changed" (contradicting the test's own title/intent). Fixed to
    # use the wall time's real UTC materialization so sweep_once() has
    # nothing to change.
    run_at_wall = datetime(2026, 6, 1, 9, 0)
    run_at = run_at_wall.replace(tzinfo=UTC)
    job = _zoned_job(run_at, run_at_wall, "UTC")
    await store.save(job)
    store.save_calls.clear()

    remat = ScheduleRematerializer(store, interval=0.0, horizon=timedelta(hours=48))
    await remat.sweep_once()

    assert store.save_calls == []


async def test_run_at_tz_is_null_job_is_never_touched() -> None:
    store = _RematStore()
    unzoned = Job(
        job_id=uuid4(), run_at=datetime.now(UTC) + timedelta(hours=1)
    )
    await store.save(unzoned)
    store.save_calls.clear()

    remat = ScheduleRematerializer(store, interval=0.0)
    await remat.sweep_once()

    assert store.save_calls == []


async def test_horizon_bounds_the_query() -> None:
    store = _RematStore()
    far_future = datetime.now(UTC) + timedelta(days=365 * 4)
    job = _zoned_job(far_future, datetime(2030, 1, 1), "UTC")
    await store.save(job)
    store.save_calls.clear()

    remat = ScheduleRematerializer(store, interval=0.0, horizon=timedelta(hours=48))
    await remat.sweep_once()

    # Far outside the 48h horizon — list_pending_zoned(before=now+48h) must
    # not have returned it, so no recompute/write happens.
    assert store.save_calls == []
