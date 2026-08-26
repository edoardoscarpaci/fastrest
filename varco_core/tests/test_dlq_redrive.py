"""
tests.test_dlq_redrive
=======================
Plan 009, Phase 4 (R1) — DlqRedriver.

RED until ``varco_core/event/redrive.py`` lands.  Uses ``InMemoryDeadLetterQueue``
+ ``InMemoryEventBus`` per repo test conventions (no broker required).
"""

from __future__ import annotations

import uuid

import pytest
from varco_core.event import Event
from varco_core.event.dlq import (
    AbstractDeadLetterQueue,
    DeadLetterEntry,
    DeadLetterSource,
    InMemoryDeadLetterQueue,
)
from varco_core.event.memory import InMemoryEventBus


class SampleEvent(Event):
    __event_type__ = "test.redrive.sample"


def _entry(**kwargs) -> DeadLetterEntry:
    defaults = dict(
        event=SampleEvent(),
        channel="orders",
        handler_name="H.h",
        error_type="RuntimeError",
        error_message="boom",
        attempts=3,
    )
    defaults.update(kwargs)
    return DeadLetterEntry(**defaults)


class _StreamShapedDLQ(AbstractDeadLetterQueue):
    """A fake stream-backed DLQ (Kafka/NATS-shaped): no random access."""

    supports_random_access = False

    def __init__(self) -> None:
        self._entries: list[DeadLetterEntry] = []
        self.acked: list[uuid.UUID] = []

    async def push(self, entry: DeadLetterEntry) -> None:
        self._entries.append(entry)

    async def pop_batch(self, *, limit: int = 10) -> list[DeadLetterEntry]:
        batch, self._entries = self._entries[:limit], self._entries[limit:]
        return batch

    async def ack(self, entry_id: uuid.UUID) -> None:
        self.acked.append(entry_id)

    async def count(self) -> int:
        return len(self._entries)

    async def get(self, entry_id: uuid.UUID) -> DeadLetterEntry | None:
        raise NotImplementedError("stream-backed DLQ has no random access")


class TestDlqRedriverHappyPath:
    async def test_redrive_publishes_and_acks(self) -> None:
        from varco_core.event.redrive import DlqRedriver

        dlq = InMemoryDeadLetterQueue()
        bus = InMemoryEventBus()
        entry = _entry()
        await dlq.push(entry)

        redriver = DlqRedriver(dlq, bus)
        outcome = await redriver.redrive(entry.entry_id)
        await bus.drain()

        assert outcome.published is True
        assert outcome.acked is True
        assert outcome.error is None

    async def test_redrive_batch_reports_attempted_and_succeeded(self) -> None:
        from varco_core.event.redrive import DlqRedriver

        dlq = InMemoryDeadLetterQueue()
        bus = InMemoryEventBus()
        for _ in range(3):
            await dlq.push(_entry())

        redriver = DlqRedriver(dlq, bus)
        report = await redriver.redrive_batch(limit=10)
        await bus.drain()

        assert report.attempted == 3
        assert report.succeeded == 3
        assert report.failed == 0
        assert len(report.outcomes) == 3


class TestDlqRedriverPublishFailure:
    async def test_publish_failure_leaves_entry_unacked(self) -> None:
        from varco_core.event.redrive import DlqRedriver

        dlq = InMemoryDeadLetterQueue()
        entry = _entry()
        await dlq.push(entry)

        class _BrokenBus(InMemoryEventBus):
            async def publish(self, event, *, channel=None):  # type: ignore[override]
                raise ConnectionError("bus down")

        redriver = DlqRedriver(dlq, _BrokenBus())
        outcome = await redriver.redrive(entry.entry_id)

        assert outcome.published is False
        assert outcome.acked is False
        assert outcome.error is not None


class TestDlqRedriverPayloadOnlyEntry:
    async def test_payload_only_entry_is_refused(self) -> None:
        from varco_core.event.redrive import DlqRedriver

        dlq = InMemoryDeadLetterQueue()
        entry = _entry(
            event=None,
            source=DeadLetterSource.OUTBOX_RELAY,
            payload=b"not-deserializable",
        )
        await dlq.push(entry)

        redriver = DlqRedriver(dlq, InMemoryEventBus())
        outcome = await redriver.redrive(entry.entry_id)

        assert outcome.published is False
        assert outcome.acked is False
        assert "not republishable" in (outcome.error or "")


class TestDlqRedriverJobSourcedEntry:
    async def test_job_sourced_entry_is_refused(self) -> None:
        from varco_core.event.redrive import DlqRedriver

        dlq = InMemoryDeadLetterQueue()
        entry = _entry(
            event=None,
            source=DeadLetterSource.JOB,
            source_ref="job-123",
            payload=b"{}",
        )
        await dlq.push(entry)

        redriver = DlqRedriver(dlq, InMemoryEventBus())
        outcome = await redriver.redrive(entry.entry_id)

        assert outcome.published is False
        assert "job" in (outcome.error or "").lower()


class TestDlqRedriverDryRun:
    async def test_dry_run_publishes_nothing(self) -> None:
        from varco_core.event.redrive import DlqRedriver

        dlq = InMemoryDeadLetterQueue()
        bus = InMemoryEventBus()
        entry = _entry()
        await dlq.push(entry)

        redriver = DlqRedriver(dlq, bus)
        outcome = await redriver.redrive(entry.entry_id, dry_run=True)
        await bus.drain()

        assert outcome.published is False
        assert outcome.acked is False
        # entry must still be in the DLQ afterwards (nothing acked)
        assert await dlq.count() == 1


class TestDlqRedriverStreamBackend:
    async def test_single_entry_redrive_raises_not_addressable(self) -> None:
        from varco_core.event.redrive import DeadLetterNotAddressable, DlqRedriver

        dlq = _StreamShapedDLQ()
        redriver = DlqRedriver(dlq, InMemoryEventBus())

        with pytest.raises(DeadLetterNotAddressable):
            await redriver.redrive(uuid.uuid4())

    async def test_redrive_batch_still_works_on_stream_backend(self) -> None:
        from varco_core.event.redrive import DlqRedriver

        dlq = _StreamShapedDLQ()
        bus = InMemoryEventBus()
        await dlq.push(_entry())
        await dlq.push(_entry())

        redriver = DlqRedriver(dlq, bus)
        report = await redriver.redrive_batch(limit=10)
        await bus.drain()

        assert report.succeeded == 2
        assert len(dlq.acked) == 2


class TestDlqRedriverEmptyChannel:
    async def test_empty_channel_and_no_default_raises_value_error(self) -> None:
        from varco_core.event.redrive import DlqRedriver

        dlq = InMemoryDeadLetterQueue()
        entry = _entry(channel="")
        await dlq.push(entry)

        redriver = DlqRedriver(dlq, InMemoryEventBus())
        with pytest.raises(ValueError, match=str(entry.entry_id)):
            await redriver.redrive(entry.entry_id)


class TestDlqRedriverUnknownEntry:
    async def test_unknown_entry_id_returns_not_found_outcome(self) -> None:
        from varco_core.event.redrive import DlqRedriver

        dlq = InMemoryDeadLetterQueue()
        redriver = DlqRedriver(dlq, InMemoryEventBus())

        outcome = await redriver.redrive(uuid.uuid4())
        assert outcome.published is False
        assert outcome.error == "not found"
