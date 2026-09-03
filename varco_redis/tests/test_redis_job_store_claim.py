"""
Plan 024 / Phase 6, Step 40 — N-concurrent-claimers test for
``RedisJobStore.try_claim`` (C9, 🟢 nice, **DROPPABLE**).

⚠️ DROPPABLE PHASE: per §D-C9 / Phase 6 of Plan 024, this whole phase
"replaces a fix that already works" and "nothing may wait on it; drop it if
anything above runs long." This test is written per the RED-mode instruction
("write the test anyway but mark it clearly") but the current
``try_claim()`` implementation (``SET claim_key NX EX`` + a separate job-key
write, ``job_store.py:608-655``) is **not proven wrong** by this test today
— it is believed to already satisfy "exactly one winner" for the common
case. What this test additionally exercises, which the current design's own
documented drawback (``job_store.py:576-579``) calls out, is that **no
``claim:`` key survives** a completed claim round — today's implementation
deletes the claim key inside ``try_claim`` on every exit path (RT7a-guard),
so this assertion is expected to ALREADY PASS pre-Step-41; it is here to
catch a regression if Step 41's CAS rewrite ever lands partially.

RED-only assertion: the crash-between-guard-and-save edge case
(``job_store.py:576-579``'s own documented drawback — a crash after the
``SET NX`` guard but before the job JSON is updated) cannot be reproduced
without literally killing the process mid-``try_claim``, so it is
approximated by monkeypatching ``save()`` to raise after the guard is
acquired, and asserting that a later caller CAN reclaim once the guard TTL
naturally expires (not exercised synchronously here — this variant is
noted, not run, since it demands a real-time TTL wait longer than a fast
integration test should take. See the module docstring in
``job_store.py:568-590`` for the authoritative drawback description).

DOCKER-GATED: `@pytest.mark.integration`, needs a real Redis
(`redis_url` fixture, `varco_redis/tests/conftest.py`). NOT run by this
report — no Docker available in this session.

Per-test namespacing: the ``redis_url`` container is session-scoped and
shared, so this test uses a ``uuid4().hex[:8]``-suffixed ``key_prefix``.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
import redis.asyncio as aioredis
from varco_core.job.base import Job, JobStatus
from varco_redis.job_store import RedisJobStore

pytestmark = pytest.mark.integration

_N_CLAIMERS = 10


@asynccontextmanager
async def _store(redis_url: str) -> AsyncIterator[RedisJobStore]:
    client = aioredis.from_url(redis_url)
    try:
        yield RedisJobStore(client, key_prefix=f"varco:jobclaim:{uuid.uuid4().hex[:8]}:")
    finally:
        await client.aclose()


async def test_exactly_one_of_n_concurrent_claimers_wins(redis_url: str) -> None:
    async with _store(redis_url) as store:
        job = Job(job_id=uuid.uuid4(), status=JobStatus.PENDING, created_at=datetime.now(UTC))
        await store.save(job)

        results = await asyncio.gather(*(store.try_claim(job.job_id) for _ in range(_N_CLAIMERS)))

        winners = [r for r in results if r is not None]
        assert len(winners) == 1
        assert winners[0].status == JobStatus.RUNNING


async def test_no_claim_key_survives_after_a_claim_round(redis_url: str) -> None:
    async with _store(redis_url) as store:
        job = Job(job_id=uuid.uuid4(), status=JobStatus.PENDING, created_at=datetime.now(UTC))
        await store.save(job)

        await asyncio.gather(*(store.try_claim(job.job_id) for _ in range(_N_CLAIMERS)))

        # Access the underlying client through the store's own key builder —
        # no `claim:` guard key should remain after every caller has
        # resolved (whether it won or lost).
        claim_key = store._claim_key(job.job_id)  # noqa: SLF001 — test-only introspection
        remaining = await store._client.get(claim_key)  # noqa: SLF001
        assert remaining is None


async def test_run_at_in_future_prevents_all_claimers_from_winning(redis_url: str) -> None:
    # Edge case named in the current design's DESIGN block: run_at honours
    # "IS NULL OR run_at <= now" — a future run_at must still block every
    # concurrent claimer, not just serialize them.
    async with _store(redis_url) as store:
        job = Job(
            job_id=uuid.uuid4(),
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            run_at=datetime.now(UTC) + timedelta(hours=1),
        )
        await store.save(job)

        results = await asyncio.gather(*(store.try_claim(job.job_id) for _ in range(_N_CLAIMERS)))

        assert all(r is None for r in results)
