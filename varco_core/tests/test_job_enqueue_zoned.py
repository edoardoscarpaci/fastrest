"""
Red-mode tests for Plan 011 Phase 4, step 50 — RD-5's refusal test.

Plan line (step 50): "enqueueing with tz= into a store whose
supports_zoned_schedules is False raises ValueError naming the store class;
the same enqueue against InMemoryJobStore succeeds and materializes a
correct run_at; run_at= and run_at_wall=+tz= together raise."

Plan line (step 51): "AbstractJobRunner.enqueue(..., run_at_wall=None,
tz=None, fold=0, gap=GapPolicy.NEXT_VALID, overlap=OverlapPolicy.FIRST); the
RD-5 guard; the materialization call to resolve_zoned."

AMBIGUITY NOTE: AbstractJobRunner.enqueue(self, job, coro, *, run_at=None,
delay=None) is abstract with no store attribute defined on the ABC itself —
concrete implementations (e.g. varco_fastapi.JobRunner) own the store. This
suite builds a minimal concrete subclass assuming the conventional
``self._store`` attribute the class docstring's "Steps performed by
implementations: 1. store.save(job)" implies. The implementer should
reconcile the exact attribute name / wiring; the *behavioural* assertions
(ValueError naming the store class; run_at_tz materialized) are the load-
bearing part of this test.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from varco_core.job.base import AbstractJobRunner, AbstractJobStore, Job


class _MinimalStore(AbstractJobStore):
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


class _UnzoneAwareStore(_MinimalStore):
    """supports_zoned_schedules left at its ABC default (False)."""


class _ZonedAwareStore(_MinimalStore):
    supports_zoned_schedules = True


class _MinimalRunner(AbstractJobRunner):
    """Concrete AbstractJobRunner exercising only the store.save() half of
    enqueue() — task scheduling is irrelevant to the RD-5 guard."""

    def __init__(self, store: AbstractJobStore) -> None:
        self._store = store

    async def enqueue(self, job, coro=None, *, run_at=None, delay=None, **kwargs):
        # DEVIATION (resolving the AMBIGUITY NOTE above): AbstractJobRunner
        # exposes a concrete, reusable `_prepare_zoned_job()` helper
        # (RD-5 guard + T2 materialization via resolve_zoned) that every
        # concrete enqueue() implementation calls before store.save(job) —
        # see job/base.py. This stub wires that helper against the
        # conventional `self._store` attribute the ABC's own docstring
        # implies ("Steps performed by implementations: 1. store.save(job)")
        # rather than raising NotImplementedError, so the RD-5 guard is
        # actually exercised through this minimal concrete subclass.
        job = self._prepare_zoned_job(
            job,
            self._store,
            run_at=run_at,
            run_at_wall=kwargs.get("run_at_wall"),
            tz=kwargs.get("tz"),
            fold=kwargs.get("fold", 0),
        )
        await self._store.save(job)
        return job

    async def submit(self, job_id, coro):
        raise NotImplementedError

    async def cancel(self, job_id) -> bool:
        return False

    async def start(self) -> None:
        return None

    async def stop(self, *, timeout: float = 30.0) -> None:
        return None

    async def enqueue_task(self, task, *, run_at=None, delay=None, **kwargs):
        raise NotImplementedError

    async def recover(self, registry) -> int:
        return 0


async def test_enqueue_with_tz_into_unaware_store_raises_value_error_naming_class() -> (
    None
):
    store = _UnzoneAwareStore()
    runner = _MinimalRunner(store)
    job = Job(job_id=uuid4())

    with pytest.raises(ValueError, match="_UnzoneAwareStore"):
        await runner.enqueue(
            job,
            run_at_wall=datetime(2026, 6, 1, 9, 0),
            tz="America/New_York",
        )


async def test_enqueue_with_tz_into_aware_store_succeeds_and_materializes_run_at() -> (
    None
):
    store = _ZonedAwareStore()
    runner = _MinimalRunner(store)
    job = Job(job_id=uuid4())

    saved = await runner.enqueue(
        job,
        run_at_wall=datetime(2026, 6, 1, 9, 0),
        tz="America/New_York",
    )
    assert saved.run_at is not None
    assert saved.run_at_tz == "America/New_York"


async def test_enqueue_with_both_run_at_and_run_at_wall_tz_raises() -> None:
    store = _ZonedAwareStore()
    runner = _MinimalRunner(store)
    job = Job(job_id=uuid4())

    with pytest.raises(ValueError):
        await runner.enqueue(
            job,
            run_at=datetime(2026, 6, 1, 9, 0),
            run_at_wall=datetime(2026, 6, 1, 9, 0),
            tz="America/New_York",
        )
