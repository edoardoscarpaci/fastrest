"""
Simulated worker-crash chaos test for ``BeanieJobStore`` (Plan 012 / RT7,
Step 32), against real MongoDB — the identical scenario proven on
``SAJobStore`` in ``varco_sa/tests/test_job_lease_chaos_integration.py``.
The whole point of RT6+RT7 together is that a guarantee proven on one
backend is not assumed on another.

Worker A ``try_claim(owner_id="a", lease_ttl=...)``, then A is simulated as
crashed (never renews). After ``reap_expired_leases()``, worker B claims
the same job and completes it. When A "resumes" and calls
``save(expected_epoch=<A's old epoch>)`` it must raise ``StaleLeaseError``
and must NOT clobber B's result.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient

from varco_beanie.job_store import BeanieJobStore, JobDocument
from varco_core.job.base import Job, JobStatus, StaleLeaseError

pytestmark = pytest.mark.integration


async def test_stalled_worker_fenced_after_reap_does_not_clobber_survivor(
    mongo_url: str,
) -> None:
    client = AsyncIOMotorClient(mongo_url)
    db_name = f"job_lease_chaos_{uuid4().hex[:8]}"
    await init_beanie(database=client[db_name], document_models=[JobDocument])

    try:
        store = BeanieJobStore()

        job = Job(job_id=uuid4(), status=JobStatus.PENDING)
        await store.save(job)

        # Worker A claims the job with a short lease — simulating a worker
        # that will crash before renewing.
        claimed_a = await store.try_claim(
            job.job_id, owner_id="worker-a", lease_ttl=0.5
        )
        assert claimed_a is not None
        assert claimed_a.lease_epoch > 0

        # A "crashes" here — no renew(), no save(). Wait out the lease and
        # reap it (generous margin: lease_ttl=0.5s, wait well past it).
        await asyncio.sleep(2.0)
        reaped = await store.reap_expired_leases()
        assert any(j.job_id == job.job_id for j in reaped)

        # Worker B claims the now-reaped job and completes it.
        claimed_b = await store.try_claim(
            job.job_id, owner_id="worker-b", lease_ttl=30.0
        )
        assert claimed_b is not None
        assert claimed_b.lease_epoch > claimed_a.lease_epoch

        completed_by_b = dataclasses.replace(
            claimed_b,
            status=JobStatus.COMPLETED,
            completed_at=datetime.now(timezone.utc),
            result=b"worker-b-result",
        )
        await store.save(completed_by_b, expected_epoch=claimed_b.lease_epoch)

        # A "resumes" (a zombie worker that never knew it was fenced out)
        # and tries to write its own (now-stale) view of the job.
        completed_by_stale_a = dataclasses.replace(
            claimed_a,
            status=JobStatus.COMPLETED,
            completed_at=datetime.now(timezone.utc),
            result=b"worker-a-result-MUST-NOT-WIN",
        )
        with pytest.raises(StaleLeaseError):
            await store.save(completed_by_stale_a, expected_epoch=claimed_a.lease_epoch)

        # B's result must survive untouched.
        final = await store.get(job.job_id)
        assert final is not None
        assert final.result == b"worker-b-result"
        assert final.status == JobStatus.COMPLETED
    finally:
        await client.drop_database(db_name)
        client.close()
