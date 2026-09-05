"""
IdempotencyStoreConformance — shared contract tests for
``AbstractIdempotencyStore`` implementations (Plan 029 / D1, Step 15).

Subclass and override the ``store`` fixture to opt a backend in::

    from varco_conformance.idempotency_store import IdempotencyStoreConformance

    class TestRedisIdempotencyStoreConformance(IdempotencyStoreConformance):
        @pytest.fixture
        async def store(self, redis_url):
            yield RedisIdempotencyStore(...)

Not named ``Test*`` — never collected standalone (see package docstring
convention shared with ``dlq.py``/``event_bus.py``/``cache.py``/``job_store.py``).
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from varco_core.idempotency.base import AbstractIdempotencyStore, ReserveOutcome
from varco_core.idempotency.record import IdempotencyRecord


class IdempotencyStoreConformance:
    """Shared behavioural contract for ``AbstractIdempotencyStore``."""

    @pytest.fixture
    async def store(self) -> AbstractIdempotencyStore:
        """Abstract — must be overridden by every subclass."""
        raise NotImplementedError(
            "IdempotencyStoreConformance subclasses must override the "
            "`store` fixture with a concrete AbstractIdempotencyStore "
            "implementation."
        )

    def _key(self) -> str:
        return f"conformance-{uuid4().hex[:8]}"

    async def test_first_reserve_is_acquired(self, store: AbstractIdempotencyStore) -> None:
        key = self._key()
        outcome = await store.reserve(key, "fp", ttl=60.0)
        assert outcome is ReserveOutcome.ACQUIRED

    async def test_second_reserve_before_complete_is_in_flight(
        self, store: AbstractIdempotencyStore
    ) -> None:
        key = self._key()
        await store.reserve(key, "fp", ttl=60.0)
        outcome = await store.reserve(key, "fp", ttl=60.0)
        assert outcome is ReserveOutcome.IN_FLIGHT

    async def test_reserve_after_complete_is_replay(self, store: AbstractIdempotencyStore) -> None:
        key = self._key()
        await store.reserve(key, "fp", ttl=60.0)
        await store.complete(
            key,
            IdempotencyRecord(status=200, body=b"ok", headers={}, fingerprint="fp"),
        )
        outcome = await store.reserve(key, "fp", ttl=60.0)
        assert outcome is ReserveOutcome.REPLAY

    async def test_release_frees_the_key_for_a_fresh_reserve(
        self, store: AbstractIdempotencyStore
    ) -> None:
        key = self._key()
        await store.reserve(key, "fp", ttl=60.0)
        await store.release(key)
        outcome = await store.reserve(key, "fp", ttl=60.0)
        assert outcome is ReserveOutcome.ACQUIRED

    async def test_get_returns_none_before_complete(self, store: AbstractIdempotencyStore) -> None:
        key = self._key()
        await store.reserve(key, "fp", ttl=60.0)
        assert await store.get(key) is None

    async def test_get_returns_the_completed_record(self, store: AbstractIdempotencyStore) -> None:
        key = self._key()
        await store.reserve(key, "fp", ttl=60.0)
        record = IdempotencyRecord(status=201, body=b"created", headers={}, fingerprint="fp")
        await store.complete(key, record)
        fetched = await store.get(key)
        assert fetched is not None
        assert fetched.status == 201
        assert fetched.body == b"created"

    async def test_concurrent_reserve_race_yields_exactly_one_acquired(
        self, store: AbstractIdempotencyStore
    ) -> None:
        # The load-bearing conformance assertion (§D-D1-atomic): every
        # backend's native atomic primitive must behave identically under
        # a genuine concurrent race, not just sequential calls.
        key = self._key()
        n = 10
        outcomes = await asyncio.gather(*(store.reserve(key, "fp", ttl=60.0) for _ in range(n)))
        acquired = sum(1 for o in outcomes if o is ReserveOutcome.ACQUIRED)
        assert acquired == 1
