"""
Unit tests for AbstractJobStore.delete_where (plan 005, Phase 6, Step 67).
=============================================================================

RED until Step 68 lands: AbstractJobStore gains a concrete
``delete_where(*, status=None, completed_before=None, expires_before=None,
limit=None) -> int`` with a portable default over ``list_by_status`` +
``delete``. ``limit`` bounds the sweep; no predicate at all raises
``ValueError`` to refuse an accidental full-table wipe.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from varco_core.job.base import AbstractJobStore, Job, JobStatus


class _InMemoryStore(AbstractJobStore):
    """Minimal in-memory AbstractJobStore for exercising the ABC default."""

    def __init__(self) -> None:
        self._jobs: dict = {}

    async def save(self, job: Job) -> None:
        self._jobs[job.job_id] = job

    async def get(self, job_id):
        return self._jobs.get(job_id)

    async def list_by_status(self, status, *, limit: int = 100):
        if isinstance(status, JobStatus):
            statuses = {status}
        else:
            statuses = set(status)
        matches = [j for j in self._jobs.values() if j.status in statuses]
        return matches[:limit]

    async def delete(self, job_id) -> None:
        self._jobs.pop(job_id, None)

    async def try_claim(self, job_id):
        return None


def _completed_job(*, completed_at: datetime) -> Job:
    job = Job(job_id=uuid4(), status=JobStatus.PENDING).as_running()
    job = job.as_completed(result=b"{}")
    import dataclasses

    return dataclasses.replace(job, completed_at=completed_at)


class TestDeleteWhereNoPredicateRaises:
    async def test_no_predicate_at_all_raises_value_error(self) -> None:
        store = _InMemoryStore()
        with pytest.raises(ValueError):
            await store.delete_where()


class TestDeleteWhereStatusAndCompletedBefore:
    async def test_removes_exactly_matching_rows_and_returns_count(self) -> None:
        store = _InMemoryStore()
        now = datetime.now(timezone.utc)

        old_completed = _completed_job(completed_at=now - timedelta(days=10))
        recent_completed = _completed_job(completed_at=now - timedelta(minutes=1))
        await store.save(old_completed)
        await store.save(recent_completed)

        deleted = await store.delete_where(
            status=JobStatus.COMPLETED,
            completed_before=now - timedelta(days=1),
        )

        assert deleted == 1
        assert await store.get(old_completed.job_id) is None
        assert await store.get(recent_completed.job_id) is not None


class TestDeleteWhereLimit:
    async def test_limit_deletes_at_most_n_and_returns_n(self) -> None:
        store = _InMemoryStore()
        now = datetime.now(timezone.utc)
        for _ in range(5):
            await store.save(_completed_job(completed_at=now - timedelta(days=10)))

        deleted = await store.delete_where(
            status=JobStatus.COMPLETED,
            completed_before=now,
            limit=2,
        )
        assert deleted == 2
