"""
Integration tests for ``BeanieIdempotencyStore`` (Plan 029 / D1, Step 11/14).

Requires a real MongoDB broker (session-scoped ``mongo_url`` fixture,
CLAUDE.md shared-container convention). Each test gets its own database
name (``uuid4().hex[:8]``) since the container is shared across the whole
session.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from varco_beanie.idempotency import BeanieIdempotencyStore, IdempotencyDocument
from varco_core.idempotency.base import ReserveOutcome
from varco_core.idempotency.record import IdempotencyRecord

pytestmark = pytest.mark.integration


def _key() -> str:
    return f"idempotency-conformance-{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def store(mongo_url: str):
    from beanie import init_beanie
    from pymongo import AsyncMongoClient

    db_name = f"test_idempotency_{uuid.uuid4().hex[:8]}"
    client = AsyncMongoClient(mongo_url)
    db = client[db_name]
    await init_beanie(database=db, document_models=[IdempotencyDocument])
    try:
        yield BeanieIdempotencyStore()
    finally:
        await client.drop_database(db_name)
        await client.close()


async def test_reserve_acquired_then_in_flight(store: BeanieIdempotencyStore) -> None:
    key = _key()
    first = await store.reserve(key, "fp", ttl=60.0)
    second = await store.reserve(key, "fp", ttl=60.0)
    assert first is ReserveOutcome.ACQUIRED
    assert second is ReserveOutcome.IN_FLIGHT


async def test_complete_then_reserve_is_replay(store: BeanieIdempotencyStore) -> None:
    key = _key()
    await store.reserve(key, "fp", ttl=60.0)
    await store.complete(
        key, IdempotencyRecord(status=200, body=b"ok", headers={}, fingerprint="fp")
    )
    outcome = await store.reserve(key, "fp", ttl=60.0)
    assert outcome is ReserveOutcome.REPLAY


async def test_concurrent_reserve_race_against_real_mongo_exactly_one_acquired(
    store: BeanieIdempotencyStore,
) -> None:
    # The genuine unique-index + DuplicateKeyError atomicity check against
    # the real backend — a sequential-only test would not catch a race.
    key = _key()
    n = 15
    outcomes = await asyncio.gather(*(store.reserve(key, "fp", ttl=60.0) for _ in range(n)))
    acquired = sum(1 for o in outcomes if o is ReserveOutcome.ACQUIRED)
    assert acquired == 1
