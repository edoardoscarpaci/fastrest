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


# ── Plan 018 / RT5, Step 21 — rebalance-listener deepening ────────────────────
#
# ✅ VERIFIED before extending (the plan's Risks section flags this as
# unverified): the existing test above DOES pre-create its topic explicitly,
# with ``ChannelConfig(num_partitions=3, replication_factor=1)`` via
# ``KafkaChannelManager`` (lines 59-72). It does NOT rely on auto-creation,
# so it is not passing for the wrong reason (1 partition = no rebalance to
# observe). The two tests below pre-create their topics the same way.
#
# These two drive ``aiokafka.ConsumerRebalanceListener`` directly rather than
# through ``KafkaEventBus``: varco's bus does not expose a rebalance-listener
# hook, so the contract under test is the aiokafka behaviour varco's manual
# commit path depends on. Written against **eager** rebalancing (full revoke
# → full reassign) — aiokafka 0.13.0 documents eager only; cooperative
# (KIP-429) support is undocumented and out of scope (plan Non-goals).
#
# ⚠️ ``session_timeout_ms`` is kept ≥ 6000: a client-requested value below the
# broker's ``group.min.session.timeout.ms`` is rejected at join time, and the
# floor on the testcontainers image is unverified.

_SESSION_TIMEOUT_MS = 6000
_METADATA_MAX_AGE_MS = 1000


async def _declare(kafka_bootstrap: str, prefix: str, channel: str, partitions: int) -> None:
    """Pre-create a multi-partition topic — never rely on auto-creation."""
    manager = KafkaChannelManager(
        KafkaChannelManagerSettings(bootstrap_servers=kafka_bootstrap, topic_prefix=prefix)
    )
    await manager.start()
    try:
        await manager.declare_channel(
            channel, ChannelConfig(num_partitions=partitions, replication_factor=1)
        )
    finally:
        await manager.stop()


async def test_rebalance_listener_callbacks_fire_in_order(kafka_bootstrap: str) -> None:
    """
    ``on_partitions_revoked`` must fire **before** ``on_partitions_assigned``
    when a second consumer joins the group.

    That ordering is the whole basis of the commit-on-revoke pattern the next
    test exercises: a handler that flushes state in ``on_partitions_revoked``
    is only correct if it is guaranteed to run before the new owner starts
    consuming.

    Edge cases:
        - Eager semantics assumed: the first consumer's revoke carries its
          full assignment, not a partial set.
        - ⚠️ CHARACTERIZED, not assumed: aiokafka 0.13 fires
          ``on_partitions_revoked`` with an **empty** set on the *initial*
          join too, before the very first ``on_partitions_assigned``. So the
          call log legitimately starts ``["revoked", "assigned"]``, not
          ``["assigned"]``. The contract under test is therefore stated as
          "a revoke, then an assign, both *after* the initial join
          completed" — anchoring on the initial-join index rather than on
          index 0. A test that asserted ``calls[:1] == ["assigned"]`` would
          fail against a correct broker.
    """
    from aiokafka import AIOKafkaConsumer  # noqa: PLC0415
    from aiokafka.abc import ConsumerRebalanceListener  # noqa: PLC0415

    run_id = uuid.uuid4().hex[:8]
    prefix = f"rblisten{run_id}-"
    channel = "orders"
    topic = f"{prefix}{channel}"
    group_id = f"rblisten-{run_id}"
    await _declare(kafka_bootstrap, prefix, channel, 3)

    calls: list[str] = []
    second_joined = asyncio.Event()

    class _Listener(ConsumerRebalanceListener):
        async def on_partitions_revoked(self, revoked) -> None:
            calls.append("revoked")

        async def on_partitions_assigned(self, assigned) -> None:
            calls.append("assigned")
            if calls.count("assigned") >= 2:
                second_joined.set()

    def _consumer(listener=None) -> AIOKafkaConsumer:
        return AIOKafkaConsumer(
            bootstrap_servers=kafka_bootstrap,
            group_id=group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            session_timeout_ms=_SESSION_TIMEOUT_MS,
            metadata_max_age_ms=_METADATA_MAX_AGE_MS,
        )

    consumer_a = _consumer()
    await consumer_a.start()
    consumer_b = _consumer()
    try:
        consumer_a.subscribe([topic], listener=_Listener())
        # Poll so the join actually happens (aiokafka joins lazily).
        await consumer_a.getmany(timeout_ms=5000)
        assert "assigned" in calls, f"the first consumer's initial assignment never fired: {calls}"
        # Everything from here on belongs to the second consumer's join.
        initial_join_idx = len(calls)

        # A second consumer joining forces a rebalance of the existing member.
        await consumer_b.start()
        consumer_b.subscribe([topic])
        await consumer_b.getmany(timeout_ms=5000)

        deadline = asyncio.get_event_loop().time() + _JOIN_TIMEOUT
        while not second_joined.is_set() and asyncio.get_event_loop().time() < deadline:
            await consumer_a.getmany(timeout_ms=1000)
    finally:
        await consumer_a.stop()
        await consumer_b.stop()

    rebalance = calls[initial_join_idx:]
    assert "revoked" in rebalance, (
        f"on_partitions_revoked never fired when a second member joined: {calls}"
    )
    revoke_idx = rebalance.index("revoked")
    assert rebalance[revoke_idx + 1 :].count("assigned") >= 1, (
        f"on_partitions_assigned did not follow on_partitions_revoked: {calls} "
        f"(post-initial-join slice: {rebalance})"
    )


async def test_offsets_committed_in_on_partitions_revoked_prevent_duplicate_delivery(
    kafka_bootstrap: str,
) -> None:
    """
    Committing in ``on_partitions_revoked`` must stop the *new* owner of a
    partition from re-delivering already-processed records.

    Research 003 §Rebalance API calls this "critical" under manual commit:
    without the commit-on-revoke, everything the departing member processed
    since its last commit is replayed by whoever inherits the partition.

    Edge cases:
        - ⚠️ CHARACTERIZED: the commit-on-revoke handler must guard on
          ``consumer.assignment()`` — see the inline comment; aiokafka's
          initial-join revoke carries no partitions.
        - The event envelope is **flat** (``{"__event_type__", "event_id",
          "timestamp", <fields>}``) — there is no ``"data"`` wrapper.
        - The assertion is on **duplicates**, not on total count: rebalance
          may legitimately deliver records to either member, so the invariant
          is "no record is processed twice", not "member A got exactly k".
    """
    from aiokafka import AIOKafkaConsumer  # noqa: PLC0415
    from aiokafka.abc import ConsumerRebalanceListener  # noqa: PLC0415

    run_id = uuid.uuid4().hex[:8]
    prefix = f"rbcommit{run_id}-"
    channel = "orders"
    topic = f"{prefix}{channel}"
    group_id = f"rbcommit-{run_id}"
    total = 30
    await _declare(kafka_bootstrap, prefix, channel, 3)

    publish_settings = _settings(kafka_bootstrap, group_id=f"pub-{run_id}", prefix=prefix)
    async with KafkaEventBus(publish_settings) as pub:
        for i in range(total):
            await pub.publish(RebalanceOrderEvent(order_id=str(i)), channel=channel)

    processed: list[int] = []

    def _make_consumer(consumer_holder: dict) -> AIOKafkaConsumer:
        class _CommitOnRevoke(ConsumerRebalanceListener):
            async def on_partitions_revoked(self, revoked) -> None:
                # THE contract under test: flush progress before losing the
                # partitions, so the next owner resumes past it.
                consumer = consumer_holder.get("consumer")
                # ⚠️ aiokafka fires this with an EMPTY set on the initial
                # join, before anything is assigned; an unconditional
                # commit() there raises IllegalStateError("No partitions
                # assigned") and would fail the test for a reason that has
                # nothing to do with the contract under test.
                if consumer is not None and consumer.assignment():
                    await consumer.commit()

            async def on_partitions_assigned(self, assigned) -> None:
                return None

        consumer = AIOKafkaConsumer(
            bootstrap_servers=kafka_bootstrap,
            group_id=group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            session_timeout_ms=_SESSION_TIMEOUT_MS,
            metadata_max_age_ms=_METADATA_MAX_AGE_MS,
        )
        consumer_holder["consumer"] = consumer
        consumer.subscribe([topic], listener=_CommitOnRevoke())
        return consumer

    async def _drain(consumer: AIOKafkaConsumer, budget: float) -> None:
        deadline = asyncio.get_event_loop().time() + budget
        while asyncio.get_event_loop().time() < deadline:
            batch = await consumer.getmany(timeout_ms=1000)
            for records in batch.values():
                for record in records:
                    import json  # noqa: PLC0415

                    processed.append(int(json.loads(record.value)["order_id"]))

    holder_a: dict = {}
    consumer_a = _make_consumer(holder_a)
    await consumer_a.start()
    holder_b: dict = {}
    consumer_b = _make_consumer(holder_b)
    try:
        await _drain(consumer_a, 10.0)

        # B joins → A is revoked (and commits) → both drain the remainder.
        await consumer_b.start()
        await asyncio.gather(_drain(consumer_a, 15.0), _drain(consumer_b, 15.0))
    finally:
        await consumer_a.stop()
        await consumer_b.stop()

    duplicates = [oid for oid in set(processed) if processed.count(oid) > 1]
    assert not duplicates, (
        "records were processed more than once across a rebalance despite "
        f"committing in on_partitions_revoked: {sorted(duplicates)}"
    )
    assert sorted(processed) == list(range(total)), (
        f"expected every record exactly once across the pair, got {sorted(processed)}"
    )
