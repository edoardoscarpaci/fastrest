"""
Red-mode integration tests for ``SAIdempotencyStore`` (Plan 029 / D1,
Step 14).

Requires a real Postgres broker (session-scoped ``postgres_url`` fixture,
CLAUDE.md shared-container convention). Every test namespaces its own key
with ``uuid4().hex[:8]`` since the container is shared across the whole
session.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from varco_core.idempotency.base import ReserveOutcome
from varco_core.idempotency.record import IdempotencyRecord
from varco_sa.idempotency import SAIdempotencyStore

pytestmark = pytest.mark.integration


def _key() -> str:
    return f"idempotency-conformance-{uuid4().hex[:8]}"


@pytest.fixture
async def store(postgres_url: str) -> SAIdempotencyStore:
    store = SAIdempotencyStore(url=postgres_url)
    await store.start()
    yield store
    await store.stop()


async def test_reserve_acquired_then_in_flight(store: SAIdempotencyStore) -> None:
    key = _key()
    first = await store.reserve(key, "fp", ttl=60.0)
    second = await store.reserve(key, "fp", ttl=60.0)
    assert first is ReserveOutcome.ACQUIRED
    assert second is ReserveOutcome.IN_FLIGHT


async def test_complete_then_reserve_is_replay(store: SAIdempotencyStore) -> None:
    key = _key()
    await store.reserve(key, "fp", ttl=60.0)
    await store.complete(
        key, IdempotencyRecord(status=200, body=b"ok", headers={}, fingerprint="fp")
    )
    outcome = await store.reserve(key, "fp", ttl=60.0)
    assert outcome is ReserveOutcome.REPLAY


async def test_concurrent_reserve_race_against_real_postgres_exactly_one_acquired(
    store: SAIdempotencyStore,
) -> None:
    # Genuine UNIQUE(key) + IntegrityError race check against the real
    # database — a sequential-only test would not catch this.
    key = _key()
    n = 15
    outcomes = await asyncio.gather(*(store.reserve(key, "fp", ttl=60.0) for _ in range(n)))
    acquired = sum(1 for o in outcomes if o is ReserveOutcome.ACQUIRED)
    assert acquired == 1
