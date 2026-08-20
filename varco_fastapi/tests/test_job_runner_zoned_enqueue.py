"""
Regression tests — Plan 011 / RD-5 / T2, drift item 1.

User reports: ``JobRunner.enqueue()`` (the shipped, production runner
callers actually use) was never extended with the T2 zoned-schedule kwargs
(``run_at_wall=``, ``tz=``, ``fold=``, ``gap=``, ``overlap=``), so
``AbstractJobRunner._prepare_zoned_job()``'s RD-5 guard — which must raise
``ValueError`` when a caller targets a zoned schedule against a store that
hasn't declared ``supports_zoned_schedules = True`` — can never fire for a
real caller going through the standard runner. Correct behaviour: calling
``JobRunner.enqueue(job, coro, run_at_wall=..., tz=...)`` must route through
``_prepare_zoned_job()`` exactly like any other concrete ``enqueue()``
implementation (see ``test_job_enqueue_zoned.py``'s minimal-runner stub),
because the guard and the T2 materialization are meaningless if the shipped
runner never calls them.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from varco_core.job.base import AbstractJobStore, Job, JobStatus
from varco_fastapi.job.runner import JobRunner
from varco_fastapi.job.store import InMemoryJobStore


class _UnzoneAwareStore(AbstractJobStore):
    """A store that never opted into T2 zoned-schedule persistence."""

    def __init__(self) -> None:
        self._jobs: dict = {}

    async def save(self, job: Job, *, expected_epoch: int | None = None) -> None:
        self._jobs[job.job_id] = job

    async def get(self, job_id):
        return self._jobs.get(job_id)

    async def list_by_status(self, status, *, limit: int = 100):
        return []

    async def delete(self, job_id) -> None:
        self._jobs.pop(job_id, None)

    async def try_claim(self, job_id, *, owner_id=None, lease_ttl=None):
        return None


async def _noop() -> None:
    return None


async def test_regression_job_runner_enqueue_zoned_guard_fires() -> None:
    # Symptom: JobRunner.enqueue() silently accepted run_at_wall=/tz= against
    # a store with no zoned-schedule support, because it never called
    # _prepare_zoned_job(). Correct behaviour: ValueError naming the store
    # class, exactly as the ABC-level guard promises (RD-5).
    store = _UnzoneAwareStore()
    runner = JobRunner(store=store)

    job = Job(job_id=uuid4())

    with pytest.raises(ValueError, match="_UnzoneAwareStore"):
        await runner.enqueue(
            job,
            _noop(),
            run_at_wall=datetime(2026, 6, 1, 9, 0),
            tz="America/New_York",
        )


async def test_regression_job_runner_enqueue_zoned_materializes_run_at() -> None:
    # Correct behaviour: against a store that DOES declare
    # supports_zoned_schedules = True, enqueue(run_at_wall=, tz=) succeeds
    # and the persisted Job has a materialized UTC run_at plus run_at_tz set.
    store = InMemoryJobStore()
    runner = JobRunner(store=store)

    job = Job(job_id=uuid4())

    await runner.enqueue(
        job,
        _noop(),
        run_at_wall=datetime(2026, 6, 1, 9, 0),
        tz="America/New_York",
    )

    saved = await store.get(job.job_id)
    assert saved is not None
    assert saved.run_at is not None
    assert saved.run_at_tz == "America/New_York"
    assert saved.status == JobStatus.PENDING


async def test_regression_job_runner_enqueue_run_at_and_zoned_mutually_exclusive() -> (
    None
):
    store = InMemoryJobStore()
    runner = JobRunner(store=store)
    job = Job(job_id=uuid4())

    with pytest.raises(ValueError):
        await runner.enqueue(
            job,
            _noop(),
            run_at=datetime(2026, 6, 1, 9, 0),
            run_at_wall=datetime(2026, 6, 1, 9, 0),
            tz="America/New_York",
        )
