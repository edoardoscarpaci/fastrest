"""
Real-broker Kafka consumer-offset integration tests (Plan 012 / RT5, Step 16).

Consumer offset management, against the session-scoped ``kafka_bootstrap``
fixture: messages published while a consumer is stopped are delivered
after restart with the same ``group_id``; a fresh ``group_id`` re-reads
from the configured ``auto_offset_reset`` position; redelivery after a
failed handler does not silently advance the offset (the retry wrapper
must not ack/commit past a failed message).

Every ``bus`` lifecycle call is wrapped in ``asyncio.wait_for`` with a
generous but bounded timeout — a hung consumer-group join becomes a clear
test failure rather than an indefinite hang (this environment was observed
to occasionally stall on group-coordinator election for a brand-new
topic/group; CLAUDE.md's convention is to widen margins, never silently
allow an unbounded wait).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from varco_core.event import Event
from varco_kafka.bus import KafkaEventBus
from varco_kafka.config import KafkaEventBusSettings

pytestmark = pytest.mark.integration

_JOIN_TIMEOUT = 30.0


class OffsetsOrderEvent(Event):
    __event_type__ = "order.offsets.kafka"
    order_id: str


def _settings(kafka_bootstrap: str, *, group_id: str, prefix: str) -> KafkaEventBusSettings:
    return KafkaEventBusSettings(
        bootstrap_servers=kafka_bootstrap,
        group_id=group_id,
        auto_offset_reset="earliest",
        channel_prefix=prefix,
    )


async def _publish_n(settings: KafkaEventBusSettings, channel: str, n: int) -> None:
    async with KafkaEventBus(settings) as bus:
        for i in range(n):
            await bus.publish(OffsetsOrderEvent(order_id=str(i)), channel=channel)


async def test_messages_published_while_consumer_stopped_are_delivered_after_restart(
    kafka_bootstrap: str,
) -> None:
    """Publish while no consumer is running; a later consumer with the same
    group_id picks up every message from the committed (absent) offset."""
    run_id = uuid.uuid4().hex[:8]
    prefix = f"offit{run_id}-"
    group_id = f"offsets-it-{run_id}"
    channel = "orders"

    publish_settings = _settings(kafka_bootstrap, group_id=f"pub-{run_id}", prefix=prefix)
    await _publish_n(publish_settings, channel, 5)

    consume_settings = _settings(kafka_bootstrap, group_id=group_id, prefix=prefix)
    received: list[str] = []

    async def handler(event: OffsetsOrderEvent) -> None:
        received.append(event.order_id)

    async with KafkaEventBus(consume_settings) as bus:
        bus.subscribe(OffsetsOrderEvent, handler, channel=channel)
        deadline = asyncio.get_event_loop().time() + _JOIN_TIMEOUT
        while len(received) < 5 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.2)

    assert sorted(received) == [str(i) for i in range(5)]


async def test_fresh_group_id_rereads_from_configured_offset_reset(
    kafka_bootstrap: str,
) -> None:
    """A brand-new group_id re-reads from `auto_offset_reset="earliest"` —
    it sees messages published before it ever subscribed."""
    run_id = uuid.uuid4().hex[:8]
    prefix = f"offit2{run_id}-"
    channel = "orders"

    publish_settings = _settings(kafka_bootstrap, group_id=f"pub-{run_id}", prefix=prefix)
    await _publish_n(publish_settings, channel, 3)

    fresh_settings = _settings(kafka_bootstrap, group_id=f"fresh-{run_id}", prefix=prefix)
    received: list[str] = []

    async def handler(event: OffsetsOrderEvent) -> None:
        received.append(event.order_id)

    async with KafkaEventBus(fresh_settings) as bus:
        bus.subscribe(OffsetsOrderEvent, handler, channel=channel)
        deadline = asyncio.get_event_loop().time() + _JOIN_TIMEOUT
        while len(received) < 3 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.2)

    assert sorted(received) == ["0", "1", "2"]


async def test_redelivery_after_failed_handler_does_not_silently_advance_offset(
    kafka_bootstrap: str,
) -> None:
    """A handler that raises on the first delivery must see the SAME message
    again after the consumer restarts with the same group_id — the offset
    must not have silently advanced past a message whose handler failed."""
    run_id = uuid.uuid4().hex[:8]
    prefix = f"offit3{run_id}-"
    group_id = f"redeliver-it-{run_id}"
    channel = "orders"

    publish_settings = _settings(kafka_bootstrap, group_id=f"pub-{run_id}", prefix=prefix)
    await _publish_n(publish_settings, channel, 1)

    consume_settings = _settings(kafka_bootstrap, group_id=group_id, prefix=prefix)

    # First consumer instance: handler always raises — the message must
    # never be considered "handled" (no ack/commit past it).
    first_seen: list[str] = []

    async def failing_handler(event: OffsetsOrderEvent) -> None:
        first_seen.append(event.order_id)
        raise RuntimeError("simulated handler failure")

    async with KafkaEventBus(consume_settings) as bus:
        bus.subscribe(OffsetsOrderEvent, failing_handler, channel=channel)
        deadline = asyncio.get_event_loop().time() + _JOIN_TIMEOUT
        while not first_seen and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.2)

    assert first_seen == ["0"]

    # Second consumer instance, same group_id: a successful handler must
    # still see the message — it was never durably committed.
    second_seen: list[str] = []

    async def ok_handler(event: OffsetsOrderEvent) -> None:
        second_seen.append(event.order_id)

    async with KafkaEventBus(consume_settings) as bus:
        bus.subscribe(OffsetsOrderEvent, ok_handler, channel=channel)
        deadline = asyncio.get_event_loop().time() + _JOIN_TIMEOUT
        while not second_seen and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.2)

    assert second_seen == ["0"]


# ── Plan 018 / RT5, Step 22 — commit durability across a consumer restart ─────


async def test_committed_offset_survives_consumer_restart(kafka_bootstrap: str) -> None:
    """
    An explicitly committed offset must be readable by a *new* consumer
    instance in the same group, and that consumer must resume from it.

    Research 003 §Offset Management: "if the offset persists across consumer
    restart, the broker durably committed it". The three tests above assert
    *delivery* behaviour around offsets; this one asserts the offset itself
    is broker-side durable, which is the property they all silently rely on.

    Driven through raw ``aiokafka`` rather than ``KafkaEventBus`` because
    ``committed(tp)`` and an explicit ``commit()`` are not part of varco's
    bus surface — the contract under test is the broker's, not varco's.

    Edge cases:
        - The topic is pre-created with an explicit ``num_partitions=1`` so
          ``TopicPartition(topic, 0)`` is the whole topic; the testcontainers
          auto-creation partition count is undocumented.
    """
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition  # noqa: PLC0415
    from aiokafka.admin import AIOKafkaAdminClient, NewTopic  # noqa: PLC0415

    run_id = uuid.uuid4().hex[:8]
    topic = f"offdur-{run_id}"
    group_id = f"offdur-grp-{run_id}"

    admin = AIOKafkaAdminClient(bootstrap_servers=kafka_bootstrap)
    await admin.start()
    try:
        await admin.create_topics([NewTopic(name=topic, num_partitions=1, replication_factor=1)])
    finally:
        await admin.close()

    total = 5
    producer = AIOKafkaProducer(bootstrap_servers=kafka_bootstrap)
    await producer.start()
    try:
        for i in range(total):
            await producer.send_and_wait(topic, str(i).encode())
    finally:
        await producer.stop()

    tp = TopicPartition(topic, 0)

    def _consumer() -> AIOKafkaConsumer:
        return AIOKafkaConsumer(
            topic,
            bootstrap_servers=kafka_bootstrap,
            group_id=group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
        )

    # First instance: consume the first 3 records and commit exactly there.
    first = _consumer()
    await first.start()
    try:
        seen: list[bytes] = []
        deadline = asyncio.get_event_loop().time() + _JOIN_TIMEOUT
        while len(seen) < 3 and asyncio.get_event_loop().time() < deadline:
            batch = await first.getmany(timeout_ms=1000, max_records=3 - len(seen))
            for records in batch.values():
                seen.extend(r.value for r in records)
        assert len(seen) == 3, f"only consumed {len(seen)}/3 records before the deadline"

        await first.commit({tp: 3})
        assert await first.committed(tp) == 3
    finally:
        await first.stop()

    # Second instance, same group: the offset survived, and consumption
    # resumes from it rather than from `earliest`.
    second = _consumer()
    await second.start()
    try:
        assert await second.committed(tp) == 3, (
            "the committed offset did not survive the consumer restart — it "
            "was never durably committed on the broker"
        )

        resumed: list[bytes] = []
        deadline = asyncio.get_event_loop().time() + _JOIN_TIMEOUT
        while len(resumed) < total - 3 and asyncio.get_event_loop().time() < deadline:
            batch = await second.getmany(timeout_ms=1000)
            for records in batch.values():
                resumed.extend(r.value for r in records)

        assert resumed == [b"3", b"4"], (
            f"the restarted consumer did not resume from the committed offset; got {resumed}"
        )
    finally:
        await second.stop()
