"""
Red-mode tests for Plan 029 / D1a — ``varco_core.idempotency``.

Covers plan Step 7: fingerprint stability/sensitivity, all three
``reserve()`` outcomes, and a genuine concurrency test (``asyncio.gather``
of N reservations asserting exactly one ACQUIRED).

None of ``varco_core.idempotency.*`` exists yet — every test below must fail
with ``ModuleNotFoundError``/``ImportError``, not a typo in the test itself.
"""

from __future__ import annotations

import asyncio

import pytest
from varco_core.idempotency.base import AbstractIdempotencyStore, ReserveOutcome
from varco_core.idempotency.fingerprint import compute_fingerprint
from varco_core.idempotency.memory import InMemoryIdempotencyStore
from varco_core.idempotency.record import IdempotencyRecord
from varco_core.idempotency.settings import IdempotencySettings

# ── fingerprint ───────────────────────────────────────────────────────────────


def test_fingerprint_is_stable_across_repeated_calls() -> None:
    # Same inputs must always hash the same, or replay-vs-mismatch detection
    # would be nondeterministic.
    fp1 = compute_fingerprint("POST", "/orders", "a=1&b=2", b'{"x": 1}')
    fp2 = compute_fingerprint("POST", "/orders", "a=1&b=2", b'{"x": 1}')
    assert fp1 == fp2


def test_fingerprint_is_hex_sha256_shaped() -> None:
    fp = compute_fingerprint("POST", "/orders", "", b"{}")
    assert isinstance(fp, str)
    assert len(fp) == 64
    int(fp, 16)  # must be valid hex


def test_fingerprint_differs_when_method_differs() -> None:
    fp_post = compute_fingerprint("POST", "/orders", "", b"{}")
    fp_patch = compute_fingerprint("PATCH", "/orders", "", b"{}")
    assert fp_post != fp_patch


def test_fingerprint_differs_when_path_differs() -> None:
    fp1 = compute_fingerprint("POST", "/orders", "", b"{}")
    fp2 = compute_fingerprint("POST", "/orders/1", "", b"{}")
    assert fp1 != fp2


def test_fingerprint_differs_when_query_differs() -> None:
    fp1 = compute_fingerprint("POST", "/orders", "a=1", b"{}")
    fp2 = compute_fingerprint("POST", "/orders", "a=2", b"{}")
    assert fp1 != fp2


def test_fingerprint_differs_when_body_differs() -> None:
    fp1 = compute_fingerprint("POST", "/orders", "", b'{"x": 1}')
    fp2 = compute_fingerprint("POST", "/orders", "", b'{"x": 2}')
    assert fp1 != fp2


def test_fingerprint_is_insensitive_to_query_param_order() -> None:
    # §D-D1-fingerprint: "sorted_query" — reordering the same params must
    # not produce a spurious mismatch.
    fp1 = compute_fingerprint("POST", "/orders", "a=1&b=2", b"{}")
    fp2 = compute_fingerprint("POST", "/orders", "b=2&a=1", b"{}")
    assert fp1 == fp2


# ── reserve() outcomes ────────────────────────────────────────────────────────


@pytest.fixture
def store() -> InMemoryIdempotencyStore:
    return InMemoryIdempotencyStore()


async def test_reserve_first_caller_gets_acquired(store: InMemoryIdempotencyStore) -> None:
    outcome = await store.reserve("key-1", "fp-1", ttl=60.0)
    assert outcome is ReserveOutcome.ACQUIRED


async def test_reserve_second_caller_before_complete_gets_in_flight(
    store: InMemoryIdempotencyStore,
) -> None:
    # First caller reserves but never completes — simulates a still-running request.
    first = await store.reserve("key-2", "fp-2", ttl=60.0)
    assert first is ReserveOutcome.ACQUIRED

    second = await store.reserve("key-2", "fp-2", ttl=60.0)
    assert second is ReserveOutcome.IN_FLIGHT


async def test_reserve_after_complete_gets_replay(store: InMemoryIdempotencyStore) -> None:
    await store.reserve("key-3", "fp-3", ttl=60.0)
    record = IdempotencyRecord(
        status=200,
        body=b'{"ok": true}',
        headers={"content-type": "application/json"},
        fingerprint="fp-3",
    )
    await store.complete("key-3", record)

    outcome = await store.reserve("key-3", "fp-3", ttl=60.0)
    assert outcome is ReserveOutcome.REPLAY


async def test_get_returns_completed_record(store: InMemoryIdempotencyStore) -> None:
    await store.reserve("key-4", "fp-4", ttl=60.0)
    record = IdempotencyRecord(
        status=201,
        body=b"created",
        headers={},
        fingerprint="fp-4",
    )
    await store.complete("key-4", record)

    fetched = await store.get("key-4")
    assert fetched is not None
    assert fetched.status == 201
    assert fetched.body == b"created"
    assert fetched.fingerprint == "fp-4"


async def test_get_returns_none_for_unknown_key(store: InMemoryIdempotencyStore) -> None:
    assert await store.get("never-seen") is None


async def test_release_allows_a_fresh_reserve(store: InMemoryIdempotencyStore) -> None:
    # §D-D1-replay: a streaming/over-ceiling response releases the reservation
    # so a retry can re-execute rather than getting stuck IN_FLIGHT forever.
    await store.reserve("key-5", "fp-5", ttl=60.0)
    await store.release("key-5")

    outcome = await store.reserve("key-5", "fp-5", ttl=60.0)
    assert outcome is ReserveOutcome.ACQUIRED


# ── genuine concurrency ───────────────────────────────────────────────────────


async def test_concurrent_reserve_exactly_one_acquired(
    store: InMemoryIdempotencyStore,
) -> None:
    # This is the whole point of §D-D1-atomic: N concurrent retries racing on
    # the same key must yield exactly one ACQUIRED, never two.
    n = 20
    outcomes = await asyncio.gather(
        *(store.reserve("race-key", "race-fp", ttl=60.0) for _ in range(n))
    )
    acquired_count = sum(1 for o in outcomes if o is ReserveOutcome.ACQUIRED)
    in_flight_count = sum(1 for o in outcomes if o is ReserveOutcome.IN_FLIGHT)
    assert acquired_count == 1
    assert in_flight_count == n - 1


# ── record / settings / ABC shape ────────────────────────────────────────────


def test_idempotency_record_is_frozen_dataclass() -> None:
    record = IdempotencyRecord(status=200, body=b"x", headers={}, fingerprint="f")
    with pytest.raises(Exception):  # FrozenInstanceError subclasses AttributeError
        record.status = 500  # type: ignore[misc]


def test_idempotency_settings_defaults() -> None:
    settings = IdempotencySettings()
    assert settings.enabled is False
    assert settings.ttl_seconds == 86400
    assert settings.require_key is False
    assert settings.max_key_length == 255
    assert settings.max_stored_body_bytes == 1048576


def test_abstract_idempotency_store_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        AbstractIdempotencyStore()  # type: ignore[abstract]


# ── exceptions ────────────────────────────────────────────────────────────────


def test_idempotency_exceptions_exist_with_message_keys() -> None:
    from varco_core.exception import (
        IdempotencyFingerprintMismatchError,
        IdempotencyKeyConflictError,
        IdempotencyKeyInvalidError,
    )
    from varco_core.exception.service import ServiceException

    assert issubclass(IdempotencyKeyConflictError, ServiceException)
    assert issubclass(IdempotencyFingerprintMismatchError, ServiceException)
    assert issubclass(IdempotencyKeyInvalidError, ServiceException)
    assert IdempotencyKeyConflictError.message_key
    assert IdempotencyFingerprintMismatchError.message_key
    assert IdempotencyKeyInvalidError.message_key
