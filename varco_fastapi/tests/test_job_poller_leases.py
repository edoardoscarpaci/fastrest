"""
Unit tests for JobPoller lease-aware reaping (plan 005, Phase 4, Step 59).
=============================================================================

RED until Step 58 lands: ``JobPoller`` gains ``lease_aware: bool = True``.
When the store supports ``reap_expired_leases`` (Step 48's new
``AbstractJobStore`` method), the poller detects death by lease expiry and
returns reaped jobs to PENDING (fencing the stale owner via the bumped
epoch) instead of marking them FAILED by wall-clock age. When the store
raises ``NotImplementedError`` (no lease support), the poller falls back
to today's age-threshold behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from varco_core.job.base import Job, JobStatus
from varco_fastapi.job.poller import JobPoller
from varco_fastapi.job.store import InMemoryJobStore


class TestJobPollerReapsExpiredLease:
    async def test_reaps_expired_lease_to_pending(self) -> None:
        store = InMemoryJobStore()
        job_id = uuid4()
        job = Job(
            job_id=job_id,
            status=JobStatus.RUNNING,
            started_at=datetime.now(UTC) - timedelta(seconds=1),
            # The job HOLDS a lease and that lease has expired — this is the
            # signal reap_expired_leases reads. A NULL lease means "no lease
            # taken", which is the age threshold's business, not the lease
            # reaper's (plan Step 48: "RUNNING rows whose lease_expires_at
            # <= now").
            owner_id="worker-crashed",
            lease_expires_at=datetime.now(UTC) - timedelta(seconds=30),
            lease_epoch=3,
        )
        await store.save(job)

        poller = JobPoller(store=store, lease_aware=True, poll_interval=999.0)
        await poller._recover_stale_jobs()

        recovered = await store.get(job_id)
        assert recovered is not None
        assert recovered.status == JobStatus.PENDING
        # The stale owner is fenced out: its next write with
        # expected_epoch=3 must now be refused.
        assert recovered.lease_epoch == 4

    async def test_does_not_touch_running_job_with_live_lease_however_old(
        self,
    ) -> None:
        # The regression test for the wall-clock bug: a legitimately
        # long-running job with a live (unexpired) lease must be left alone
        # no matter how old started_at is.
        store = InMemoryJobStore()
        job_id = uuid4()
        job = Job(
            job_id=job_id,
            status=JobStatus.RUNNING,
            # Started a very long time ago — would trip the old age threshold.
            started_at=datetime.now(UTC) - timedelta(hours=10),
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        await store.save(job)

        poller = JobPoller(store=store, lease_aware=True, poll_interval=999.0)
        await poller._recover_stale_jobs()

        still_running = await store.get(job_id)
        assert still_running is not None
        assert still_running.status == JobStatus.RUNNING


class TestJobPollerFallsBackToAgeThresholdWithoutLeaseSupport:
    async def test_store_without_lease_support_uses_age_threshold(self) -> None:
        # A store whose reap_expired_leases raises NotImplementedError (no
        # lease support) must fall back to today's wall-clock age check.
        class _NoLeaseStore(InMemoryJobStore):
            async def reap_expired_leases(self, *, now=None, limit: int = 100):
                raise NotImplementedError(f"{type(self).__name__} does not support leases")

        store = _NoLeaseStore()
        job_id = uuid4()
        job = Job(
            job_id=job_id,
            status=JobStatus.RUNNING,
            started_at=datetime.now(UTC) - timedelta(hours=1),
        )
        await store.save(job)

        poller = JobPoller(
            store=store,
            lease_aware=True,
            stale_threshold=timedelta(minutes=5),
            poll_interval=999.0,
        )
        await poller._recover_stale_jobs()

        recovered = await store.get(job_id)
        assert recovered is not None
        assert recovered.status == JobStatus.FAILED
