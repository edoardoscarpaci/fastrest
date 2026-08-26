"""
Job-lease fencing after a worker crash, against a real Redis
``RedisJobStore`` (Plan 018 / RT7a, Step 27).

Byte-for-byte the same scenario as
``varco_sa/tests/test_sa_job_lease_crash.py``, driven through the same
``varco_chaos.leases.abandon_lease`` helper against the other store. Two
stores, one scenario, one helper — **if the two backends disagree, that is
precisely the finding worth having**, and it is the reason both files exist
rather than one parameterised over a fixture (they live in different
packages with different container fixtures).

**No ``chaos`` marker, deliberately** (§RT7-shape): "worker crash" means
this process stopped renewing. Redis is not what fails, and nothing is
killed at the container level.

Per-test namespacing: the ``redis_url`` container is session-scoped and
shared, so each test uses a ``uuid4().hex[:8]``-suffixed ``key_prefix`` and
owns its whole keyspace exclusively.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
import redis.asyncio as aioredis
from varco_chaos.leases import abandon_lease
from varco_core.job.base import Job, JobStatus, StaleLeaseError
from varco_redis.job_store import RedisJobStore

pytestmark = pytest.mark.integration

_LEASE_TTL = 1.0
"""Short lease so the test can wait past expiry without a long sleep."""


@asynccontextmanager
async def _store(redis_url: str) -> AsyncIterator[RedisJobStore]:
    """
    Yield a ``RedisJobStore`` on a keyspace this test owns exclusively.

    Yields:
        A store whose ``key_prefix`` carries a per-test run id — the
        container is shared with the whole package session, so a fixed
        prefix would collide with every other Redis job test.
    """
    client = aioredis.from_url(redis_url)
    try:
        yield RedisJobStore(client, key_prefix=f"varco:jobcrash:{uuid.uuid4().hex[:8]}:")
    finally:
        await client.aclose()


@pytest.mark.xfail(
    reason=(
        "BUG: RedisJobStore.reap_expired_leases() does not release the "
        "SET-NX-EX claim guard key try_claim() created for the original "
        "claim (job_store.py:595-605). The guard's TTL (`claim_ttl`, default "
        "30s) is independent of `lease_ttl` and is never cleared on reap, so "
        "worker B's try_claim() is refused with 'claim key already held' for "
        "up to `claim_ttl` seconds after a legitimate reap — even though the "
        "job is correctly PENDING again with an advanced lease_epoch. "
        "varco_sa's SAJobStore has no equivalent second guard key and does "
        "not exhibit this — the two backends disagree, exactly the finding "
        "this twin-test pair (Plan 018 / RT7a) exists to surface. See "
        "BACKLOG.md."
    ),
    strict=True,
)
async def test_reaped_lease_fences_the_zombie_worker_on_save(redis_url: str) -> None:
    """
    Worker A claims, crashes, is reaped, B re-claims — then A's late write is
    refused with ``StaleLeaseError``.

    Raises:
        StaleLeaseError: expected, from A's ``save(expected_epoch=<old>)``.

    Edge cases:
        - The final state must be B's result: "raised StaleLeaseError but
          wrote anyway" would otherwise pass.
    """
    async with _store(redis_url) as store:
        job = Job(job_id=uuid.uuid4(), status=JobStatus.PENDING)
        await store.save(job)

        claimed_a = await store.try_claim(job.job_id, owner_id="worker-a", lease_ttl=_LEASE_TTL)
        assert claimed_a is not None
        epoch_a = claimed_a.lease_epoch

        await abandon_lease(store, job.job_id)
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


async def test_renewed_lease_keeps_a_second_worker_locked_out(redis_url: str) -> None:
    """
    The negative half: a worker that keeps renewing is never reaped, and a
    second worker cannot claim its job.

    Without this, the test above would pass on a store that reaps
    unconditionally — fencing *healthy* workers, the opposite bug.
    """
    async with _store(redis_url) as store:
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

        await asyncio.sleep(_LEASE_TTL + 1.5)

        reaped = await store.reap_expired_leases(now=datetime.now(UTC))
        assert all(j.job_id != job.job_id for j in reaped), (
            "a renewed (live) lease was reaped — a healthy worker would be fenced out"
        )

        assert await store.try_claim(job.job_id, owner_id="worker-b", lease_ttl=30.0) is None, (
            "worker B claimed a job whose lease is still held and renewed by worker A"
        )
