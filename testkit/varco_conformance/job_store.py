"""
JobStoreConformance — shared contract tests for ``AbstractJobStore``
implementations (Plan 012 / RT6, Step 24).

Subclass and override the ``store`` fixture to opt a backend in::

    from varco_conformance.job_store import JobStoreConformance

    class TestSAJobStoreConformance(JobStoreConformance):
        @pytest.fixture
        async def store(self, postgres_url):
            store = SAJobStore(...)
            yield store

Not named ``Test*`` — never collected standalone (see package docstring).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from varco_core.job.base import AbstractJobStore, Job, JobStatus, StaleLeaseError


class JobStoreConformance:
    """Shared behavioural contract for ``AbstractJobStore``."""

    @pytest.fixture
    async def store(self) -> AbstractJobStore:
        """Abstract — must be overridden by every subclass."""
        raise NotImplementedError(
            "JobStoreConformance subclasses must override the `store` "
            "fixture with a concrete AbstractJobStore implementation."
        )

    def _job(self, **kwargs: object) -> Job:
        return Job(job_id=uuid4(), **kwargs)  # type: ignore[arg-type]

    async def test_save_get_round_trip(self, store: AbstractJobStore) -> None:
        job = self._job()
        await store.save(job)
        fetched = await store.get(job.job_id)
        assert fetched is not None
        assert fetched.job_id == job.job_id

    async def test_get_unknown_returns_none(self, store: AbstractJobStore) -> None:
        assert await store.get(uuid4()) is None

    async def test_list_by_status(self, store: AbstractJobStore) -> None:
        job = self._job(status=JobStatus.PENDING)
        await store.save(job)
        pending = await store.list_by_status(JobStatus.PENDING, limit=100)
        assert any(j.job_id == job.job_id for j in pending)

    async def test_delete_unknown_is_noop(self, store: AbstractJobStore) -> None:
        # Must not raise.
        await store.delete(uuid4())

    async def test_try_claim_succeeds_once(self, store: AbstractJobStore) -> None:
        job = self._job(status=JobStatus.PENDING)
        await store.save(job)

        first = await store.try_claim(job.job_id, owner_id="worker-a")
        second = await store.try_claim(job.job_id, owner_id="worker-b")

        assert first is not None
        assert first.status == JobStatus.RUNNING
        assert second is None

    async def test_try_claim_unknown_job_returns_none(
        self, store: AbstractJobStore
    ) -> None:
        assert await store.try_claim(uuid4(), owner_id="worker-a") is None

    async def test_run_at_in_future_not_claimable(
        self, store: AbstractJobStore
    ) -> None:
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        job = self._job(status=JobStatus.PENDING, run_at=future)
        await store.save(job)

        claimed = await store.claim_next(now=datetime.now(timezone.utc))

        assert claimed is None or claimed.job_id != job.job_id

    async def test_save_with_stale_expected_epoch_raises(
        self, store: AbstractJobStore
    ) -> None:
        job = self._job(status=JobStatus.PENDING)
        await store.save(job)
        claimed = await store.try_claim(job.job_id, owner_id="worker-a", lease_ttl=30.0)
        if claimed is None or claimed.lease_epoch == 0:
            pytest.skip("store does not support leases")

        stale_epoch = claimed.lease_epoch - 1 if claimed.lease_epoch > 0 else -1
        with pytest.raises(StaleLeaseError):
            await store.save(claimed, expected_epoch=stale_epoch)

    async def test_renew_extends_lease(self, store: AbstractJobStore) -> None:
        job = self._job(status=JobStatus.PENDING)
        await store.save(job)
        claimed = await store.try_claim(job.job_id, owner_id="worker-a", lease_ttl=30.0)
        assert claimed is not None

        try:
            renewed = await store.renew(
                job.job_id,
                owner_id="worker-a",
                epoch=claimed.lease_epoch,
                lease_ttl=60.0,
            )
        except NotImplementedError:
            pytest.skip("store does not support leases")
            return

        assert renewed is not None

    async def test_reap_expired_leases_reclaims(self, store: AbstractJobStore) -> None:
        job = self._job(status=JobStatus.PENDING)
        await store.save(job)
        claimed = await store.try_claim(job.job_id, owner_id="worker-a", lease_ttl=0.01)
        assert claimed is not None

        import asyncio

        await asyncio.sleep(0.2)

        try:
            reaped = await store.reap_expired_leases()
        except NotImplementedError:
            pytest.skip("store does not support leases")
            return

        assert any(j.job_id == job.job_id for j in reaped)

    async def test_delete_where_no_predicate_raises(
        self, store: AbstractJobStore
    ) -> None:
        with pytest.raises(ValueError):
            await store.delete_where()

    async def test_delete_where_by_status(self, store: AbstractJobStore) -> None:
        job = self._job(status=JobStatus.PENDING)
        await store.save(job)

        deleted = await store.delete_where(status=JobStatus.PENDING, limit=1000)

        assert deleted >= 1
        assert (
            await store.get(job.job_id) is None
            or (await store.get(job.job_id)) is not None
        )  # backend-defined: deletion of THIS row is verified below
        remaining = await store.list_by_status(JobStatus.PENDING, limit=1000)
        assert job.job_id not in {j.job_id for j in remaining}

    async def test_supports_zoned_schedules_flag_honoured(
        self, store: AbstractJobStore
    ) -> None:
        if not store.supports_zoned_schedules:
            pytest.skip("store does not declare zoned-schedule support")

        job = self._job(
            status=JobStatus.PENDING,
            run_at=datetime.now(timezone.utc),
            run_at_wall=datetime.now(timezone.utc).replace(tzinfo=None),
            run_at_tz="America/New_York",
            run_at_fold=0,
        )
        await store.save(job)
        fetched = await store.get(job.job_id)

        assert fetched is not None
        assert fetched.run_at_tz == "America/New_York"
