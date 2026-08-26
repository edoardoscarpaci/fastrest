"""
Job-lease fencing after a worker crash, against a real Postgres
``SAJobStore`` (Plan 018 / RT7a, Step 26).

**No ``chaos`` marker, deliberately** (§RT7-shape): "worker crash" means
*this process stopped renewing*. The store is not what fails, so nothing is
killed at the container level. Restarting Postgres here would prove nothing
about fencing and would add ~20 s of boot time per test.

The four-method lease protocol under test —
``try_claim`` / ``renew`` / ``reap_expired_leases`` / ``save(expected_epoch=)``
(``varco_core/varco_core/job/base.py:82,682,770,1027,1063``) — exists
entirely to be correct under a crash, and until Plan 018 it was proven only
by ``varco_core/tests/test_job.py``'s unit tests against in-memory stores.

The crash is produced by ``varco_chaos.leases.abandon_lease`` so this test
and its ``varco_redis`` twin
(``varco_redis/tests/test_redis_job_lease_crash.py``) drive **one** scenario
through **one** helper against **two** stores. If the two backends disagree,
that disagreement is precisely the finding worth having.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from varco_chaos.leases import abandon_lease
from varco_core.job.base import Job, JobStatus, StaleLeaseError
from varco_sa.job_store import SAJobStore

pytestmark = pytest.mark.integration

_LEASE_TTL = 1.0
"""Short lease so the test can wait past expiry without a long sleep."""


async def _store(postgres_url: str) -> tuple[SAJobStore, object]:
    engine = create_async_engine(postgres_url)
    store = SAJobStore(engine)
    await store.ensure_table()
    return store, engine


async def test_reaped_lease_fences_the_zombie_worker_on_save(postgres_url: str) -> None:
    """
    Worker A claims, crashes, is reaped, B re-claims — then A's late write is
    refused with ``StaleLeaseError``.

    This is the Kleppmann fencing point: rejection happens at the moment of
    **write**, not merely at claim time. A store that detects the stall only
    at claim time still lets a zombie clobber the survivor's result.

    Raises:
        StaleLeaseError: expected, from A's ``save(expected_epoch=<old>)``.

    Edge cases:
        - The job is left in B's COMPLETED state afterwards; the test asserts
          that too, because "raised StaleLeaseError but wrote anyway" would
          otherwise pass.
    """
    store, engine = await _store(postgres_url)
    try:
        job = Job(job_id=uuid.uuid4(), status=JobStatus.PENDING)
        await store.save(job)

        claimed_a = await store.try_claim(job.job_id, owner_id="worker-a", lease_ttl=_LEASE_TTL)
        assert claimed_a is not None
        epoch_a = claimed_a.lease_epoch

        # Worker A crashes: its renew task is cancelled, nothing is renewed.
        await abandon_lease(store, job.job_id)

        # Time advances past lease_expires_at.
        await asyncio.sleep(_LEASE_TTL + 1.5)

        reaped = await store.reap_expired_leases(now=datetime.now(UTC))
        assert any(j.job_id == job.job_id for j in reaped), (
            "the abandoned lease was never reaped, so nothing could fence A"
        )

        claimed_b = await store.try_claim(job.job_id, owner_id="worker-b", lease_ttl=30.0)
        assert claimed_b is not None, "worker B could not claim a reaped job"
        assert claimed_b.lease_epoch > epoch_a, (
            "reap/re-claim did not advance lease_epoch — there is no fencing token"
        )

        await store.save(
            dataclasses.replace(
                claimed_b,
                status=JobStatus.COMPLETED,
                completed_at=datetime.now(UTC),
                result=b"worker-b-result",
            ),
            expected_epoch=claimed_b.lease_epoch,
        )

        # Zombie A resumes, unaware it was fenced out.
        with pytest.raises(StaleLeaseError):
            await store.save(
                dataclasses.replace(
                    claimed_a,
                    status=JobStatus.COMPLETED,
                    completed_at=datetime.now(UTC),
                    result=b"worker-a-result",
                ),
                expected_epoch=epoch_a,
            )

        final = await store.get(job.job_id)
        assert final is not None
        assert final.result == b"worker-b-result", (
            "the zombie's write landed despite StaleLeaseError being raised"
        )
    finally:
        await engine.dispose()


async def test_renewed_lease_keeps_a_second_worker_locked_out(postgres_url: str) -> None:
    """
    The negative half: a worker that keeps renewing is never reaped, and a
    second worker cannot claim its job.

    Without this, the test above would pass on a store that reaps
    unconditionally — which would fence *healthy* workers, the opposite bug.

    Edge cases:
        - ``reap_expired_leases`` is called explicitly after the renew, at a
          wall-clock point past the ORIGINAL expiry, so the assertion is
          about the renewal having moved the deadline, not about timing luck.
    """
    store, engine = await _store(postgres_url)
    try:
        job = Job(job_id=uuid.uuid4(), status=JobStatus.PENDING)
        await store.save(job)

        claimed = await store.try_claim(job.job_id, owner_id="worker-a", lease_ttl=_LEASE_TTL)
        assert claimed is not None

        renewed = await store.renew(
            job.job_id,
            owner_id="worker-a",
            epoch=claimed.lease_epoch,
            lease_ttl=60.0,
        )
        assert renewed is not None, "renew() refused a live lease held by its own owner"

        # Past the ORIGINAL expiry, but well inside the renewed one.
        await asyncio.sleep(_LEASE_TTL + 1.5)

        reaped = await store.reap_expired_leases(now=datetime.now(UTC))
        assert all(j.job_id != job.job_id for j in reaped), (
            "a renewed (live) lease was reaped — a healthy worker would be fenced out"
        )

        assert await store.try_claim(job.job_id, owner_id="worker-b", lease_ttl=30.0) is None, (
            "worker B claimed a job whose lease is still held and renewed by worker A"
        )
    finally:
        await engine.dispose()
