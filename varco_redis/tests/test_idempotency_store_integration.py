"""
Red-mode integration tests for ``RedisIdempotencyStore`` (Plan 029 / D1,
Step 14).

Requires a real Redis broker (session-scoped ``redis_url`` fixture, CLAUDE.md
shared-container convention). Every test namespaces its own key with
``uuid4().hex[:8]`` since the container is shared across the whole session.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from varco_core.idempotency.base import ReserveOutcome
from varco_core.idempotency.record import IdempotencyRecord
from varco_redis.idempotency import RedisIdempotencyStore

pytestmark = pytest.mark.integration


def _key() -> str:
    return f"idempotency-conformance-{uuid4().hex[:8]}"


@pytest.fixture
async def store(redis_url: str) -> RedisIdempotencyStore:
    return RedisIdempotencyStore(url=redis_url)


async def test_reserve_acquired_then_in_flight(store: RedisIdempotencyStore) -> None:
    key = _key()
    first = await store.reserve(key, "fp", ttl=60.0)
    second = await store.reserve(key, "fp", ttl=60.0)
    assert first is ReserveOutcome.ACQUIRED
    assert second is ReserveOutcome.IN_FLIGHT


async def test_complete_then_reserve_is_replay(store: RedisIdempotencyStore) -> None:
    key = _key()
    await store.reserve(key, "fp", ttl=60.0)
    await store.complete(
        key, IdempotencyRecord(status=200, body=b"ok", headers={}, fingerprint="fp")
    )
    outcome = await store.reserve(key, "fp", ttl=60.0)
    assert outcome is ReserveOutcome.REPLAY


async def test_concurrent_reserve_race_against_real_redis_exactly_one_acquired(
    store: RedisIdempotencyStore,
) -> None:
    # This is the genuine SET NX PX atomicity check against the real backend
    # — a sequential-only test would not catch a race condition.
    key = _key()
    n = 15
    outcomes = await asyncio.gather(*(store.reserve(key, "fp", ttl=60.0) for _ in range(n)))
    acquired = sum(1 for o in outcomes if o is ReserveOutcome.ACQUIRED)
    assert acquired == 1
