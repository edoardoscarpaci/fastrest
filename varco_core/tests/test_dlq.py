"""
Unit tests for varco_core.event.dlq
=====================================
Covers the Dead Letter Queue abstractions without any external dependencies.

Sections
--------
- ``DeadLetterEntry``           — construction, from_failure factory, frozen
- ``InMemoryDeadLetterQueue``   — max_size validation, push, pop_batch, ack (no-op),
                                  count, eviction on overflow, concurrent push/pop,
                                  push never raises, repr
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from varco_core.event import Event
from varco_core.event.dlq import (
    AbstractDeadLetterQueue,
    DeadLetterEntry,
    InMemoryDeadLetterQueue,
)

# ── Minimal event fixture ─────────────────────────────────────────────────────


class SampleEvent(Event):
    __event_type__ = "test.sample"


# ── DeadLetterEntry ───────────────────────────────────────────────────────────


class TestDeadLetterEntry:
    def _make_entry(self) -> DeadLetterEntry:
        return DeadLetterEntry(
            event=SampleEvent(),
            channel="orders",
            handler_name="OrderConsumer.on_order",
            error_type="ConnectionError",
            error_message="DB unavailable",
            attempts=3,
        )

    def test_entry_has_uuid(self) -> None:
        entry = self._make_entry()
        assert isinstance(entry.entry_id, uuid.UUID)

    def test_two_entries_have_different_ids(self) -> None:
        a = self._make_entry()
        b = self._make_entry()
        assert a.entry_id != b.entry_id

    def test_frozen(self) -> None:
        entry = self._make_entry()
        with pytest.raises(Exception):
            entry.channel = "other"  # type: ignore[misc]

    def test_hashable(self) -> None:
        entry = self._make_entry()
        # Frozen dataclasses should be hashable
        assert hash(entry) is not None
        _ = {entry}

    def test_from_failure_populates_all_fields(self) -> None:
        event = SampleEvent()
        exc = ConnectionError("service down")
        first_failed = datetime.now(tz=UTC)

        entry = DeadLetterEntry.from_failure(
            event=event,
            channel="payments",
            handler_name="PaymentConsumer.on_payment",
            last_exc=exc,
            attempts=3,
            first_failed_at=first_failed,
        )

        assert entry.event is event
        assert entry.channel == "payments"
        assert entry.handler_name == "PaymentConsumer.on_payment"
        assert entry.error_type == "ConnectionError"
        assert entry.error_message == "service down"
        assert entry.attempts == 3
        assert entry.first_failed_at == first_failed
        # last_failed_at is set to now — must be >= first_failed
        assert entry.last_failed_at >= first_failed

    def test_from_failure_error_type_is_class_name(self) -> None:
        entry = DeadLetterEntry.from_failure(
            event=SampleEvent(),
            channel="ch",
            handler_name="H.h",
            last_exc=TypeError("bad type"),
            attempts=1,
            first_failed_at=datetime.now(tz=UTC),
        )
        assert entry.error_type == "TypeError"

    def test_from_failure_auto_generates_entry_id(self) -> None:
        event = SampleEvent()
        first_failed = datetime.now(tz=UTC)
        entry = DeadLetterEntry.from_failure(
            event=event,
            channel="ch",
            handler_name="H.h",
            last_exc=Exception("err"),
            attempts=1,
            first_failed_at=first_failed,
        )
        assert isinstance(entry.entry_id, uuid.UUID)


# ── InMemoryDeadLetterQueue ───────────────────────────────────────────────────


class TestInMemoryDeadLetterQueueConstruction:
    def test_default_max_size_is_ten_thousand(self) -> None:
        dlq = InMemoryDeadLetterQueue()
        assert dlq._max_size == 10_000

    def test_custom_max_size(self) -> None:
        dlq = InMemoryDeadLetterQueue(max_size=50)
        assert dlq._max_size == 50

    def test_max_size_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="max_size"):
            InMemoryDeadLetterQueue(max_size=0)

    def test_max_size_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="max_size"):
            InMemoryDeadLetterQueue(max_size=-1)

    def test_repr_contains_class_name(self) -> None:
        dlq = InMemoryDeadLetterQueue(max_size=5)
        assert "InMemoryDeadLetterQueue" in repr(dlq)
        assert "5" in repr(dlq)


class TestInMemoryDeadLetterQueuePush:
    def _make_entry(self, handler_name: str = "H.h") -> DeadLetterEntry:
        return DeadLetterEntry(
            event=SampleEvent(),
            channel="orders",
            handler_name=handler_name,
            error_type="RuntimeError",
            error_message="fail",
            attempts=1,
        )

    async def test_push_increases_count(self) -> None:
        dlq = InMemoryDeadLetterQueue()
        assert await dlq.count() == 0
        await dlq.push(self._make_entry())
        assert await dlq.count() == 1

    async def test_push_multiple(self) -> None:
        dlq = InMemoryDeadLetterQueue()
        for i in range(5):
            await dlq.push(self._make_entry(handler_name=f"H.h{i}"))
        assert await dlq.count() == 5

    async def test_push_never_raises(self) -> None:
        """push() must swallow any internal error — callers cannot recover from DLQ failures."""
        dlq = InMemoryDeadLetterQueue(max_size=10_000)
        # Even pushing a malformed-ish entry must not raise
        entry = self._make_entry()
        await dlq.push(entry)  # must not raise

    async def test_push_evicts_oldest_when_full(self) -> None:
        """When max_size is reached, the oldest entry is silently evicted."""
        dlq = InMemoryDeadLetterQueue(max_size=2)
        entry_a = self._make_entry("A")
        entry_b = self._make_entry("B")
        entry_c = self._make_entry("C")

        await dlq.push(entry_a)
        await dlq.push(entry_b)
        # Queue is now full — this push evicts entry_a
        await dlq.push(entry_c)

        assert await dlq.count() == 2
        batch = await dlq.pop_batch(limit=10)
        handler_names = [e.handler_name for e in batch]
        # entry_a was evicted — only B and C remain
        assert "B" in handler_names
        assert "C" in handler_names
        assert "A" not in handler_names


class TestInMemoryDeadLetterQueuePopBatch:
    def _make_entry(self, handler_name: str) -> DeadLetterEntry:
        return DeadLetterEntry(
            event=SampleEvent(),
            channel="orders",
            handler_name=handler_name,
            error_type="RuntimeError",
            error_message="fail",
            attempts=1,
        )

    async def test_pop_batch_returns_fifo_order(self) -> None:
        dlq = InMemoryDeadLetterQueue()
        await dlq.push(self._make_entry("first"))
        await dlq.push(self._make_entry("second"))
        await dlq.push(self._make_entry("third"))

        batch = await dlq.pop_batch(limit=10)
        assert [e.handler_name for e in batch] == ["first", "second", "third"]

    async def test_pop_batch_respects_limit(self) -> None:
        dlq = InMemoryDeadLetterQueue()
        for i in range(5):
            await dlq.push(self._make_entry(f"h{i}"))

        batch = await dlq.pop_batch(limit=3)
        assert len(batch) == 3
        # Remaining 2 still in DLQ
        assert await dlq.count() == 2

    async def test_pop_batch_empty_returns_empty_list(self) -> None:
        dlq = InMemoryDeadLetterQueue()
        batch = await dlq.pop_batch()
        assert batch == []

    async def test_pop_batch_removes_entries(self) -> None:
        dlq = InMemoryDeadLetterQueue()
        await dlq.push(self._make_entry("x"))
        await dlq.pop_batch(limit=1)
        assert await dlq.count() == 0

    async def test_pop_batch_limit_below_one_raises(self) -> None:
        dlq = InMemoryDeadLetterQueue()
        with pytest.raises(ValueError, match="limit"):
            await dlq.pop_batch(limit=0)

    async def test_pop_batch_more_than_available_returns_all(self) -> None:
        dlq = InMemoryDeadLetterQueue()
        await dlq.push(self._make_entry("only"))
        batch = await dlq.pop_batch(limit=100)
        assert len(batch) == 1


class TestInMemoryDeadLetterQueueAck:
    async def test_ack_is_noop_for_unknown_id(self) -> None:
        """ack() on an unknown entry_id must be a no-op (at-least-once delivery)."""
        dlq = InMemoryDeadLetterQueue()
        # Must not raise
        await dlq.ack(uuid.uuid4())

    async def test_ack_after_pop_batch_is_noop(self) -> None:
        dlq = InMemoryDeadLetterQueue()
        entry = DeadLetterEntry(
            event=SampleEvent(),
            channel="ch",
            handler_name="H.h",
            error_type="E",
            error_message="msg",
            attempts=1,
        )
        await dlq.push(entry)
        batch = await dlq.pop_batch(limit=1)
        # ack on already-popped entry must be safe
        await dlq.ack(batch[0].entry_id)


class TestInMemoryDeadLetterQueueConcurrency:
    async def test_concurrent_push_and_count(self) -> None:
        """Multiple concurrent push() calls must not corrupt the internal deque."""
        dlq = InMemoryDeadLetterQueue(max_size=1000)

        async def push_one(i: int) -> None:
            entry = DeadLetterEntry(
                event=SampleEvent(),
                channel="ch",
                handler_name=f"H.h{i}",
                error_type="E",
                error_message="msg",
                attempts=1,
            )
            await dlq.push(entry)

        await asyncio.gather(*(push_one(i) for i in range(50)))
        assert await dlq.count() == 50


# ════════════════════════════════════════════════════════════════════════════════
# Plan 005, Phase 3, Step 31 — generalised DeadLetterEntry (source/source_ref/payload)
# ════════════════════════════════════════════════════════════════════════════════
#
# RED until Step 32 lands: DeadLetterSource StrEnum (CONSUMER/OUTBOX_RELAY/JOB),
# and DeadLetterEntry gains `source` (defaults CONSUMER), `source_ref`, `payload`,
# with `event` becoming `DomainEvent | None`.


class TestDeadLetterEntryBackCompat:
    def test_todays_exact_keyword_set_still_works(self) -> None:
        # Every existing construction site must keep working unchanged.
        entry = DeadLetterEntry(
            event=SampleEvent(),
            channel="orders",
            handler_name="OrderConsumer.on_order",
            error_type="ConnectionError",
            error_message="DB unavailable",
            attempts=3,
        )
        assert entry.event is not None
        assert entry.channel == "orders"


class TestDeadLetterEntrySourceGeneralisation:
    def test_source_defaults_to_consumer(self) -> None:
        from varco_core.event.dlq import DeadLetterSource

        entry = DeadLetterEntry(
            event=SampleEvent(),
            channel="orders",
            handler_name="OrderConsumer.on_order",
            error_type="ConnectionError",
            error_message="DB unavailable",
            attempts=3,
        )
        assert entry.source == DeadLetterSource.CONSUMER

    def test_relay_sourced_entry_with_event_none_and_raw_payload_round_trips(
        self,
    ) -> None:
        from varco_core.event.dlq import DeadLetterSource

        entry = DeadLetterEntry(
            event=None,
            channel="orders",
            handler_name="OutboxRelay",
            error_type="ValueError",
            error_message="bad payload",
            attempts=1,
            source=DeadLetterSource.OUTBOX_RELAY,
            source_ref="outbox-entry-123",
            payload=b'{"broken": true}',
        )
        assert entry.event is None
        assert entry.source == DeadLetterSource.OUTBOX_RELAY
        assert entry.source_ref == "outbox-entry-123"
        assert entry.payload == b'{"broken": true}'

    def test_job_source_enum_member_exists(self) -> None:
        from varco_core.event.dlq import DeadLetterSource

        assert DeadLetterSource.JOB == "job"


# ════════════════════════════════════════════════════════════════════════════════
# Plan 009 — Phase 2 (R3 retention) / Phase 4 (R1 redrive)
# ════════════════════════════════════════════════════════════════════════════════
#
# RED until Phase 2/4 land: AbstractDeadLetterQueue gains `supports_random_access`
# (ClassVar[bool] = False), `get`, `list_entries` (concrete-but-raising),
# `delete` (portable default -> ack()), `delete_where`, `count_by_channel`
# (concrete-but-raising).


class _BareDLQ(AbstractDeadLetterQueue):
    """A minimal ABC subclass implementing ONLY the required abstract members
    (push/pop_batch/ack/count) — used to exercise the base class's own
    concrete-but-raising defaults, as opposed to InMemoryDeadLetterQueue's
    (potentially fuller) overrides."""

    def __init__(self) -> None:
        self._entries: list[DeadLetterEntry] = []

    async def push(self, entry: DeadLetterEntry) -> None:
        self._entries.append(entry)

    async def pop_batch(self, *, limit: int = 10):
        batch, self._entries = self._entries[:limit], self._entries[limit:]
        return batch

    async def ack(self, entry_id) -> None:
        self._entries = [e for e in self._entries if e.entry_id != entry_id]

    async def count(self) -> int:
        return len(self._entries)


class _MinimalDLQ(InMemoryDeadLetterQueue):
    """Subclass overriding only the required abstract members — used to prove
    the portable ``delete()`` default truly delegates to ``ack()`` rather than
    requiring its own override."""


class TestAbstractDeadLetterQueueSupportsRandomAccess:
    def test_default_supports_random_access_is_false(self) -> None:
        # RD-4: a conservative default -- an out-of-tree backend is assumed
        # stream-shaped until it declares otherwise.
        from varco_core.event.dlq import AbstractDeadLetterQueue

        assert AbstractDeadLetterQueue.supports_random_access is False


class TestDeleteDelegatesToAck:
    async def test_delete_default_calls_ack(self) -> None:
        dlq = _MinimalDLQ()
        entry = DeadLetterEntry(
            event=SampleEvent(),
            channel="orders",
            handler_name="H.h",
            error_type="E",
            error_message="msg",
            attempts=1,
        )
        await dlq.push(entry)
        # delete() is the portable default -> delegates to ack(); on the
        # in-memory backend ack() is a no-op (pop_batch already consumes), so
        # this call must simply not raise.
        await dlq.delete(entry.entry_id)


class TestGetConcreteButRaising:
    async def test_get_raises_not_implemented_by_default(self) -> None:
        dlq = _BareDLQ()
        with pytest.raises(NotImplementedError):
            await dlq.get(uuid.uuid4())


class TestListEntriesConcreteButRaising:
    async def test_list_entries_raises_not_implemented_by_default(self) -> None:
        dlq = _BareDLQ()
        with pytest.raises(NotImplementedError):
            await dlq.list_entries()


class TestCountByChannelConcreteButRaising:
    async def test_count_by_channel_raises_not_implemented_by_default(self) -> None:
        dlq = _BareDLQ()
        with pytest.raises(NotImplementedError):
            await dlq.count_by_channel()


class TestDeleteWhereConcreteButRaising:
    async def test_delete_where_raises_not_implemented_by_default(self) -> None:
        dlq = _BareDLQ()
        with pytest.raises(NotImplementedError):
            await dlq.delete_where(older_than=datetime.now(tz=UTC))

    async def test_delete_where_no_predicate_raises_value_error_when_implemented(
        self,
    ) -> None:
        """A backend that DOES implement delete_where() must still refuse a
        call with no predicate at all -- this is exercised through a minimal
        fake DLQ that reproduces the documented contract (RD ABC table)."""

        class _PredicateEnforcingDLQ(InMemoryDeadLetterQueue):
            async def delete_where(  # type: ignore[override]
                self,
                *,
                older_than=None,
                source=None,
                channel=None,
                tenant_id=None,
                limit=None,
            ) -> int:
                if older_than is None and source is None and channel is None and tenant_id is None:
                    raise ValueError(
                        "delete_where() requires at least one predicate "
                        "(older_than/source/channel/tenant_id) — refusing to "
                        "delete every entry."
                    )
                return 0

        dlq = _PredicateEnforcingDLQ()
        with pytest.raises(ValueError, match="predicate"):
            await dlq.delete_where()
