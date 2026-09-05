"""
Plan 024 / Phase 6, Step 40 — N-concurrent-claimers tests for
``RedisJobStore.try_claim`` (C9, 🟢 nice, **DROPPED PHASE**).

⚠️ PHASE 6 WAS DROPPED (2026-09-02) — per its own pre-authorization in
``plans/024-3-0-1-cleanup.md:571-586``. Steps 41-43 (the atomic Lua
compare-and-set rewrite that would delete the guard key entirely) were
never executed, and ``BACKLOG.md``'s C9 row is deliberately left **open**.
This file is Step 40's pre-written test, kept as the guard that will fail
loudly the day C9 lands.

Guard-key lifecycle as actually implemented (``job_store.py:608-655``):

- ``SET claim_key "1" NX EX claim_ttl`` acquires it (default
  ``claim_ttl=30``, ``job_store.py:_DEFAULT_CLAIM_TTL``).
- The ``claimed``-flag/``finally`` block (Plan 019 / RT7a-guard) releases it
  on **every non-success path** — a missing job, a non-PENDING job, a
  future ``run_at``, or any exception.
- On the **success** path the winner's guard is deliberately **retained**,
  with its TTL, until it expires or ``reap_expired_leases()`` deletes it.
  It is the only protection the ``lease_ttl=None`` no-lease path has (that
  path never advances ``lease_epoch``, so it has no fence) — which is
  exactly why Plan 019 §RT7a-guard *rejected* deleting the guard concept
  outright and filed it as C9 instead.

So ``test_no_claim_key_survives_after_a_claim_round`` asserts the
**post-C9** contract, not today's. It is ``xfail(strict=True)``: it stays
red while C9 is open and turns the suite red the moment the CAS rewrite
lands, at which point the marker (not the assertion) is what must be
deleted. Never "fix" it by deleting the guard on the success path — that is
the design change C9 owns.

The invariant that *does* hold today, and that a real production leak would
break, is asserted separately by
``test_regression_claim_guard_key_is_never_ttl_less`` and the
non-success-path tests below: the guard is never TTL-less, so a runner that
crashes mid-claim can never lock a job out permanently.

RED-only, not run: the crash-between-guard-and-save edge case
(``job_store.py:576-579``'s own documented drawback) cannot be reproduced
without killing the process mid-``try_claim``, and asserting recovery
demands a real-time wait longer than a fast integration test should take.

DOCKER-GATED: `@pytest.mark.integration`, needs a real Redis
(`redis_url` fixture, `varco_redis/tests/conftest.py`).

Per-test namespacing: the ``redis_url`` container is session-scoped and
shared, so every test here uses a ``uuid4().hex[:8]``-suffixed
``key_prefix``.
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


@pytest.mark.xfail(
    reason=(
        "BUG/C9: the WINNING claimer's `claim:` guard key is retained by design "
        "until its TTL expires (or `reap_expired_leases()` deletes it) — "
        "`try_claim`'s `finally` releases the guard only on non-success paths "
        "(`job_store.py:645-647`, `if not claimed:`). This assertion describes "
        "the post-C9 atomic-CAS contract (BACKLOG.md C9, plans/024 Phase 6, "
        "DROPPED 2026-09-02), not today's. strict=True so it fails loudly the "
        "day the CAS rewrite lands. The anti-leak invariant that DOES hold "
        "today is asserted by test_regression_claim_guard_key_is_never_ttl_less."
    ),
    strict=True,
)
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


# ── Guard-key lifecycle invariants that hold TODAY (Plan 019 / RT7a-guard) ────
#
# These are the regression guards for the property C9's absence must never be
# allowed to erode: the guard key is a *short-TTL lock*, not a leak. A
# TTL-less guard key would lock a job out forever after a runner crash — that
# would be a genuine production bug, and is what these tests would catch.


async def test_regression_claim_guard_key_is_never_ttl_less(redis_url: str) -> None:
    # A winning claimer legitimately leaves its guard key in place (see the
    # module docstring / BACKLOG C9), but it MUST carry a TTL: `SET NX EX`,
    # never a bare `SET NX`. Symptom guarded against: a runner that crashes
    # after acquiring the guard but before updating the job JSON would lock
    # the job out permanently, because nothing else deletes that key on the
    # unleased (`lease_ttl=None`) path.
    async with _store(redis_url) as store:
        job = Job(job_id=uuid.uuid4(), status=JobStatus.PENDING, created_at=datetime.now(UTC))
        await store.save(job)

        assert await store.try_claim(job.job_id) is not None

        claim_key = store._claim_key(job.job_id)  # noqa: SLF001 — test-only introspection
        ttl = await store._client.ttl(claim_key)  # noqa: SLF001
        # redis TTL: -1 == key exists with no expiry, -2 == key absent.
        assert ttl != -1, "claim guard key has no TTL — a crashed runner would lock the job out"
        assert ttl > 0


async def test_regression_guard_released_when_job_does_not_exist(redis_url: str) -> None:
    # Non-success path #1 (Plan 019 / RT7a-guard): claiming a job_id that was
    # never saved must not leave the guard behind for `claim_ttl` seconds.
    async with _store(redis_url) as store:
        missing = uuid.uuid4()

        assert await store.try_claim(missing) is None

        claim_key = store._claim_key(missing)  # noqa: SLF001
        assert await store._client.get(claim_key) is None  # noqa: SLF001


async def test_regression_guard_released_when_job_is_not_pending(redis_url: str) -> None:
    # Non-success path #2: an already-RUNNING job is refused, and the refusing
    # caller must release the guard it just acquired.
    async with _store(redis_url) as store:
        job = Job(job_id=uuid.uuid4(), status=JobStatus.PENDING, created_at=datetime.now(UTC))
        await store.save(job)
        await store.save(job.as_running())
        # Clear the (nonexistent) guard state explicitly: nothing claimed yet.
        claim_key = store._claim_key(job.job_id)  # noqa: SLF001
        assert await store._client.get(claim_key) is None  # noqa: SLF001

        assert await store.try_claim(job.job_id) is None

        assert await store._client.get(claim_key) is None  # noqa: SLF001


async def test_regression_guard_released_when_run_at_is_in_the_future(redis_url: str) -> None:
    # Non-success path #3: a future `run_at` blocks the claim, and must not
    # leave a guard that would additionally block the job once it IS due.
    async with _store(redis_url) as store:
        job = Job(
            job_id=uuid.uuid4(),
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            run_at=datetime.now(UTC) + timedelta(hours=1),
        )
        await store.save(job)

        assert await store.try_claim(job.job_id) is None

        claim_key = store._claim_key(job.job_id)  # noqa: SLF001
        assert await store._client.get(claim_key) is None  # noqa: SLF001


async def test_regression_reaper_releases_the_guard_of_the_lease_it_reaps(
    redis_url: str,
) -> None:
    # The other half of Plan 019 / RT7a-guard: a reaped lease's guard is
    # deleted immediately after the `save()` that advances `lease_epoch`, so a
    # second worker can re-claim at once instead of waiting out `claim_ttl`.
    async with _store(redis_url) as store:
        job = Job(job_id=uuid.uuid4(), status=JobStatus.PENDING, created_at=datetime.now(UTC))
        await store.save(job)

        claimed = await store.try_claim(job.job_id, owner_id="worker-a", lease_ttl=0.01)
        assert claimed is not None
        claim_key = store._claim_key(job.job_id)  # noqa: SLF001
        assert await store._client.get(claim_key) is not None  # noqa: SLF001 — winner keeps it

        reaped = await store.reap_expired_leases(now=datetime.now(UTC) + timedelta(seconds=60))
        assert [j.job_id for j in reaped] == [job.job_id]

        assert await store._client.get(claim_key) is None  # noqa: SLF001
        # …and the re-claim really is possible immediately.
        assert await store.try_claim(job.job_id, owner_id="worker-b", lease_ttl=30.0) is not None
