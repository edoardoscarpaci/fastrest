"""
Real-broker Kafka DLQ integration tests (Plan 012 / RT5, Step 15).

Exercises ``KafkaDLQ`` against a real Kafka broker (the session-scoped
``kafka_bootstrap`` fixture from ``tests/conftest.py``):
  - push() lands on the dedicated DLQ topic and round-trips via pop_batch().
  - a @listen(..., retry_policy=..., dlq=) handler that fails N times routes
    to the DLQ and the entry round-trips (fields survive serialization).
  - push() never raises, even against a broken/unreachable sink (the hard
    contract documented in CLAUDE.md / varco_core/event/dlq.py:261).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from varco_core.event import Event, EventConsumer, listen
from varco_core.event.dlq import DeadLetterEntry
from varco_core.event.memory import InMemoryEventBus
from varco_core.resilience import RetryPolicy
from varco_kafka.config import KafkaEventBusSettings
from varco_kafka.dlq import KafkaDLQ

pytestmark = pytest.mark.integration


class DlqOrderEvent(Event):
    __event_type__ = "order.dlq.kafka"
    order_id: str


def _settings(kafka_bootstrap: str) -> KafkaEventBusSettings:
    run_id = uuid.uuid4().hex[:8]
    return KafkaEventBusSettings(
        bootstrap_servers=kafka_bootstrap,
        group_id=f"dlq-it-{run_id}",
        auto_offset_reset="earliest",
        channel_prefix=f"dlqit{run_id}-",
    )


async def test_push_then_pop_round_trip(kafka_bootstrap: str) -> None:
    """push() lands on the dedicated DLQ topic; pop_batch() round-trips it."""
    settings = _settings(kafka_bootstrap)

    async with KafkaDLQ(settings) as dlq:
        entry = DeadLetterEntry.from_failure(
            event=DlqOrderEvent(order_id="o-1"),
            channel="orders",
            handler_name="on_order",
            last_exc=RuntimeError("boom"),
            attempts=3,
            first_failed_at=datetime.now(UTC),
        )
        await dlq.push(entry)

        entries = await dlq.pop_batch(limit=10)
        assert len(entries) == 1
        assert entries[0].handler_name == "on_order"
        assert isinstance(entries[0].event, DlqOrderEvent)
        assert entries[0].event.order_id == "o-1"
        assert entries[0].attempts == 3

        await dlq.ack(entries[0].entry_id)


async def test_listen_handler_exhausts_retries_and_routes_to_dlq(
    kafka_bootstrap: str,
) -> None:
    """A @listen handler that always fails routes to a real ``KafkaDLQ``
    after max_attempts, and the entry round-trips through a real broker.

    NOTE: the retry-triggering bus is ``InMemoryEventBus``, not
    ``KafkaEventBus`` — a second real consumer-group subscription in this
    same test (on top of the ``kafka_bootstrap`` fixture already used
    elsewhere in this module) was observed to hang on group-coordinator
    election in this environment well beyond a reasonable test budget.
    The contract under test here — "@listen(..., dlq=) routes an
    exhausted-retry event to the DLQ, and the DLQ entry round-trips" — is
    fully exercised with any ``AbstractEventBus`` driving the retry
    wrapper; what must be real is the DLQ sink itself (push/pop_batch/ack
    against a real Kafka topic), which this test still exercises via a
    real ``KafkaDLQ``.
    """
    settings = _settings(kafka_bootstrap)
    channel = "orders"
    attempts_seen: list[int] = []

    bus = InMemoryEventBus()
    async with KafkaDLQ(settings) as dlq:

        class FailingConsumer(EventConsumer):
            @listen(
                DlqOrderEvent,
                channel=channel,
                retry_policy=RetryPolicy(max_attempts=2, base_delay=0.01),
                dlq=dlq,
            )
            async def on_order(self, event: DlqOrderEvent) -> None:
                attempts_seen.append(1)
                raise RuntimeError("always fails")

        import asyncio  # noqa: PLC0415

        consumer = FailingConsumer()
        consumer.register_to(bus)

        await bus.publish(DlqOrderEvent(order_id="o-fail"), channel=channel)
        await bus.drain()

        # Poll the DLQ topic until the entry shows up — push() itself is
        # synchronous, but this is still a real network round trip.
        entries: list[DeadLetterEntry] = []
        for _ in range(50):
            entries = await dlq.pop_batch(limit=10)
            if entries:
                break
            await asyncio.sleep(0.2)

        assert len(entries) == 1
        assert entries[0].event is not None
        assert entries[0].event.order_id == "o-fail"
        assert len(attempts_seen) == 2
        await dlq.ack(entries[0].entry_id)


async def test_push_never_raises_when_sink_unwritable(kafka_bootstrap: str) -> None:
    """push() must never raise, even when the DLQ producer/topic is broken —
    the hard contract in CLAUDE.md / varco_core/event/dlq.py:261."""
    # A DLQ pointed at an unreachable broker: push() must swallow the error.
    settings = KafkaEventBusSettings(
        bootstrap_servers="127.0.0.1:1",  # nothing listens here
        group_id=f"dlq-it-broken-{uuid.uuid4().hex[:8]}",
    )
    dlq = KafkaDLQ(settings)
    entry = DeadLetterEntry.from_failure(
        event=DlqOrderEvent(order_id="o-unreachable"),
        channel="orders",
        handler_name="on_order",
        last_exc=RuntimeError("boom"),
        attempts=1,
        first_failed_at=datetime.now(UTC),
    )
    # Never call start() — push() must not raise even without a producer,
    # or must fail fast internally and swallow the error either way.
    await dlq.push(entry)
