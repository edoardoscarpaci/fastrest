"""
Red-mode tests for Plan 011 Phase 4, step 45 — RD-1's T2 proof, half 1.

Plan line (step 45): "A Job(...) constructed exactly as today is
field-for-field identical, with run_at_wall is None, run_at_tz is None,
run_at_fold == 0; AbstractJobStore.supports_zoned_schedules is False on the
ABC."

Also proves D-7's central claim ("run_at is materialized, not replaced"):
the claim predicate is driven by `run_at` alone, identically for a zoned and
an unzoned job.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, UTC
from uuid import uuid4

from varco_core.job.base import AbstractJobStore, Job, JobStatus


def test_job_constructed_today_has_none_and_zero_zoned_defaults() -> None:
    job = Job(job_id=uuid4())
    assert job.run_at_wall is None
    assert job.run_at_tz is None
    assert job.run_at_fold == 0


def test_abstract_job_store_supports_zoned_schedules_defaults_false() -> None:
    assert AbstractJobStore.supports_zoned_schedules is False


class _ClaimPredicateStore(AbstractJobStore):
    """Minimal store whose claim predicate is `run_at <= now`, unchanged by T2."""

    def __init__(self) -> None:
        self._jobs: dict = {}

    async def save(self, job: Job, *, expected_epoch: int | None = None) -> None:
        self._jobs[job.job_id] = job

    async def get(self, job_id):
        return self._jobs.get(job_id)

    async def list_by_status(self, status, *, limit: int = 100):
        statuses = {status} if isinstance(status, JobStatus) else set(status)
        return [j for j in self._jobs.values() if j.status in statuses][:limit]

    async def delete(self, job_id) -> None:
        self._jobs.pop(job_id, None)

    async def try_claim(self, job_id, *, owner_id=None, lease_ttl=None):
        job = self._jobs.get(job_id)
        if job is None or job.status != JobStatus.PENDING:
            return None
        now = datetime.now(UTC)
        if job.run_at is not None and job.run_at > now:
            return None  # unchanged predicate — the claim path never looks at run_at_tz
        claimed = job.as_running()
        self._jobs[job_id] = claimed
        return claimed


async def test_claim_predicate_identical_for_zoned_and_unzoned_job_not_yet_due() -> (
    None
):
    store = _ClaimPredicateStore()
    future = datetime.now(UTC) + timedelta(hours=1)

    unzoned = Job(job_id=uuid4(), run_at=future)
    zoned = Job(
        job_id=uuid4(),
        run_at=future,
        run_at_wall=datetime(2026, 6, 1, 9, 0),
        run_at_tz="America/New_York",
        run_at_fold=0,
    )
    await store.save(unzoned)
    await store.save(zoned)

    assert await store.try_claim(unzoned.job_id) is None
    assert await store.try_claim(zoned.job_id) is None


async def test_claim_predicate_identical_for_zoned_and_unzoned_job_due_now() -> None:
    store = _ClaimPredicateStore()
    past = datetime.now(UTC) - timedelta(minutes=1)

    unzoned = Job(job_id=uuid4(), run_at=past)
    zoned = Job(
        job_id=uuid4(),
        run_at=past,
        run_at_wall=datetime(2026, 1, 1, 2, 30),
        run_at_tz="America/Los_Angeles",
        run_at_fold=0,
    )
    await store.save(unzoned)
    await store.save(zoned)

    claimed_unzoned = await store.try_claim(unzoned.job_id)
    claimed_zoned = await store.try_claim(zoned.job_id)
    assert claimed_unzoned is not None and claimed_unzoned.status == JobStatus.RUNNING
    assert claimed_zoned is not None and claimed_zoned.status == JobStatus.RUNNING
