"""
Real-broker Kafka partition-rebalance integration test (Plan 012 / RT5,
Step 17).

Creates a multi-partition topic, runs two consumers in one ``group_id``,
asserts every published event is received exactly once across the pair,
then stops one consumer and asserts the survivor takes over the orphaned
partitions with no message lost.

Rebalance is inherently seconds-scale and jittery — generous, explicitly
commented timing margins throughout (CLAUDE.md's convention: widen the
sleep margin rather than mark it xfail).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from varco_core.event import ChannelConfig, Event
from varco_kafka.bus import KafkaEventBus
from varco_kafka.channel import KafkaChannelManager, KafkaChannelManagerSettings
from varco_kafka.config import KafkaEventBusSettings

pytestmark = pytest.mark.integration

# Rebalance + group join on a fresh single-broker container can legitimately
# take many seconds — this is the widened margin CLAUDE.md's convention
# calls for, not a workaround for a real bug.
_JOIN_TIMEOUT = 45.0
_REBALANCE_SETTLE = 15.0


class RebalanceOrderEvent(Event):
    __event_type__ = "order.rebalance.kafka"
    order_id: str


def _settings(kafka_bootstrap: str, *, group_id: str, prefix: str) -> KafkaEventBusSettings:
    return KafkaEventBusSettings(
        bootstrap_servers=kafka_bootstrap,
        group_id=group_id,
        auto_offset_reset="earliest",
        channel_prefix=prefix,
    )


async def test_two_consumers_share_partitions_then_survivor_takes_over(
    kafka_bootstrap: str,
) -> None:
    run_id = uuid.uuid4().hex[:8]
    prefix = f"rebal{run_id}-"
    group_id = f"rebalance-it-{run_id}"
    channel = "orders"

    consume_settings = _settings(kafka_bootstrap, group_id=group_id, prefix=prefix)

    # Explicitly create a multi-partition topic — auto-create defaults to a
    # single partition, which would never exercise a real rebalance (only
    # one consumer in the group could ever be assigned anything).
    channel_settings = KafkaChannelManagerSettings(
        bootstrap_servers=kafka_bootstrap, topic_prefix=prefix
    )
    channel_manager = KafkaChannelManager(channel_settings)
    await channel_manager.start()
    try:
        await channel_manager.declare_channel(
            channel, ChannelConfig(num_partitions=3, replication_factor=1)
        )
    finally:
        await channel_manager.stop()

    received_a: list[str] = []
    received_b: list[str] = []

    async def handler_a(event: RebalanceOrderEvent) -> None:
        received_a.append(event.order_id)

    async def handler_b(event: RebalanceOrderEvent) -> None:
        received_b.append(event.order_id)

    bus_a = KafkaEventBus(consume_settings)
    bus_b = KafkaEventBus(consume_settings)

    await bus_a.start()
    bus_a.subscribe(RebalanceOrderEvent, handler_a, channel=channel)
    await bus_b.start()
    bus_b.subscribe(RebalanceOrderEvent, handler_b, channel=channel)

    # Give the group time to form and settle its initial partition
    # assignment before publishing — publishing too early risks messages
    # landing before either consumer has joined the group.
    await asyncio.sleep(_REBALANCE_SETTLE)

    total = 20
    publish_settings = _settings(kafka_bootstrap, group_id=f"pub-{run_id}", prefix=prefix)
    async with KafkaEventBus(publish_settings) as pub_bus:
        for i in range(total):
            await pub_bus.publish(RebalanceOrderEvent(order_id=str(i)), channel=channel)

    deadline = asyncio.get_event_loop().time() + _JOIN_TIMEOUT
    while len(received_a) + len(received_b) < total and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.3)

    all_received = received_a + received_b
    assert sorted(all_received, key=int) == [str(i) for i in range(total)], (
        f"expected every message exactly once across both consumers, "
        f"got a={received_a} b={received_b}"
    )

    # Stop consumer B — its partitions must be reassigned to A (survivor).
    await bus_b.stop()

    # Give the group time to detect B's departure and rebalance.
    await asyncio.sleep(_REBALANCE_SETTLE)

    more = 10
    async with KafkaEventBus(publish_settings) as pub_bus:
        for i in range(total, total + more):
            await pub_bus.publish(RebalanceOrderEvent(order_id=str(i)), channel=channel)

    deadline = asyncio.get_event_loop().time() + _JOIN_TIMEOUT
    while (
        len(received_a) < total  # at least what A already had
        or len(received_a) + len(received_b) < total + more
    ) and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.3)

    await bus_a.stop()

    all_received_after = received_a + received_b
    assert sorted(all_received_after, key=int) == [str(i) for i in range(total + more)], (
        "expected every post-rebalance message to be delivered exactly once, "
        f"survivor took over: a={received_a} b={received_b}"
    )
