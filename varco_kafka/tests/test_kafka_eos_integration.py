"""
Real-broker Kafka delivery-semantics coverage (Plan 018 / RT5, Step 20).

Companion to ``varco_kafka/tests/test_kafka_eos.py``, which asserts
**wiring** against ``FakeProducer``/``FakeConsumer``/``FakeTransaction``
(that ``transactional_id`` is set only for ``EXACTLY_ONCE``, that
``enable_auto_commit=False``, that ``isolation_level="read_committed"`` is
passed, that at-most-once commits *before* dispatch and at-least-once
*after*). Those are real assertions about real code, they run in
milliseconds, and they would be strictly *worse* expressed against a broker
— you cannot observe "which kwarg was passed" from Kafka.

What a fake can never assert is **observable semantics**, which is what
lives here:

- a message inside an *aborted* transaction is invisible to a
  ``read_committed`` consumer (with a ``read_uncommitted`` control proving
  the message was genuinely produced);
- transactional offsets become visible only on commit;
- an at-least-once consumer that dies before committing **redelivers**;
- an at-most-once consumer that dies after committing but before
  dispatching **loses** the message — the documented, *intended* weakness
  of the mode. Asserting a guarantee's documented weakness matters as much
  as asserting its strength (§RT5-eos).

Per-test namespacing (⚠️ load-bearing): the ``kafka_bootstrap`` container
is session-scoped and shared, and a reused ``transactional_id`` **fences**
the other producer with a non-retriable ``ProducerFenced``. Every test
therefore carries a ``uuid4().hex[:8]``-suffixed topic **and**
``transactional_id``, and pre-creates its topic explicitly with an explicit
``num_partitions`` — the testcontainers default partition count for
auto-created topics is undocumented, so auto-creation is never relied on.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from varco_core.event import Event
from varco_kafka.bus import KafkaEventBus
from varco_kafka.config import KafkaDeliverySemantics, KafkaEventBusSettings

pytestmark = pytest.mark.integration

_JOIN_TIMEOUT = 45.0
_QUIET_PERIOD = 5.0


class EosOrderEvent(Event):
    __event_type__ = "order.eos.kafka"
    order_id: str


async def _create_topic(bootstrap: str, topic: str, *, partitions: int = 1) -> None:
    """
    Pre-create ``topic`` with an explicit partition count.

    Args:
        bootstrap:  ``bootstrap.servers`` string.
        topic:      Fully qualified topic name (already namespaced).
        partitions: Explicit partition count — never left to auto-creation,
                    whose default is broker/image dependent and undocumented
                    for testcontainers.
    """
    from aiokafka.admin import AIOKafkaAdminClient, NewTopic  # noqa: PLC0415

    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap)
    await admin.start()
    try:
        await admin.create_topics(
            [NewTopic(name=topic, num_partitions=partitions, replication_factor=1)]
        )
    finally:
        await admin.close()


async def _consume_raw(
    bootstrap: str,
    topic: str,
    *,
    isolation_level: str,
    group_id: str,
    timeout: float = 10.0,
) -> list[bytes]:
    """
    Drain ``topic`` from the beginning with an explicit isolation level.

    Returns:
        The raw message values seen before ``timeout`` elapsed with no new
        message. An empty list is a legitimate result (that is the point of
        the aborted-transaction test).
    """
    from aiokafka import AIOKafkaConsumer  # noqa: PLC0415

    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=bootstrap,
        group_id=group_id,
        auto_offset_reset="earliest",
        isolation_level=isolation_level,
        enable_auto_commit=False,
    )
    await consumer.start()
    try:
        values: list[bytes] = []
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            batch = await consumer.getmany(timeout_ms=1000)
            for records in batch.values():
                values.extend(r.value for r in records)
        return values
    finally:
        await consumer.stop()


async def test_exactly_once_aborted_transaction_invisible_to_read_committed(
    kafka_bootstrap: str,
) -> None:
    """
    A message produced inside a transaction that then aborts must be
    invisible to a ``read_committed`` consumer, while a ``read_uncommitted``
    consumer sees it.

    The ``read_uncommitted`` half is the control: without it, "the
    read_committed consumer saw nothing" would also pass if the produce had
    silently failed or gone to the wrong topic.
    """
    from aiokafka import AIOKafkaProducer  # noqa: PLC0415

    run_id = uuid.uuid4().hex[:8]
    topic = f"eos-abort-{run_id}"
    txn_id = f"eos-abort-txn-{run_id}"
    await _create_topic(kafka_bootstrap, topic)

    producer = AIOKafkaProducer(bootstrap_servers=kafka_bootstrap, transactional_id=txn_id)
    await producer.start()
    try:
        with pytest.raises(RuntimeError, match="deliberate abort"):
            async with producer.transaction():
                await producer.send_and_wait(topic, b"aborted-payload")
                raise RuntimeError("deliberate abort")
    finally:
        await producer.stop()

    committed = await _consume_raw(
        kafka_bootstrap,
        topic,
        isolation_level="read_committed",
        group_id=f"rc-{run_id}",
    )
    uncommitted = await _consume_raw(
        kafka_bootstrap,
        topic,
        isolation_level="read_uncommitted",
        group_id=f"ru-{run_id}",
    )

    assert b"aborted-payload" in uncommitted, (
        "control failed: the message was never produced at all, so the "
        "read_committed assertion below would be meaningless"
    )
    assert committed == [], (
        f"a read_committed consumer saw a message from an ABORTED transaction: {committed}"
    )


async def test_exactly_once_offsets_committed_atomically_with_produce(
    kafka_bootstrap: str,
) -> None:
    """
    ``send_offsets_to_transaction`` must make the offset visible to
    ``committed()`` only *after* the transaction commits — offsets and
    produced records land atomically or not at all.

    Edge cases:
        - ``committed(tp)`` is polled from a **separate** consumer in the
          same group so the assertion reads broker state, not the writing
          consumer's local bookkeeping.
    """
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition  # noqa: PLC0415

    run_id = uuid.uuid4().hex[:8]
    topic = f"eos-offsets-{run_id}"
    out_topic = f"eos-offsets-out-{run_id}"
    txn_id = f"eos-offsets-txn-{run_id}"
    group_id = f"eos-offsets-grp-{run_id}"
    await _create_topic(kafka_bootstrap, topic)
    await _create_topic(kafka_bootstrap, out_topic)

    # Seed one input record.
    seeder = AIOKafkaProducer(bootstrap_servers=kafka_bootstrap)
    await seeder.start()
    try:
        await seeder.send_and_wait(topic, b"input-0")
    finally:
        await seeder.stop()

    tp = TopicPartition(topic, 0)

    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=kafka_bootstrap,
        group_id=group_id,
        auto_offset_reset="earliest",
        isolation_level="read_committed",
        enable_auto_commit=False,
    )
    producer = AIOKafkaProducer(bootstrap_servers=kafka_bootstrap, transactional_id=txn_id)
    await consumer.start()
    await producer.start()
    try:
        batch = await consumer.getmany(timeout_ms=15000)
        records = [r for rs in batch.values() for r in rs]
        assert records, "the seeded input record was never consumed"

        # Nothing committed yet.
        assert await consumer.committed(tp) is None, (
            "an offset was committed before the transaction ever began"
        )

        async with producer.transaction():
            await producer.send_and_wait(out_topic, b"output-0")
            await producer.send_offsets_to_transaction(
                {tp: records[-1].offset + 1},
                group_id,
            )

        expected = records[-1].offset + 1
    finally:
        await producer.stop()
        await consumer.stop()

    # Read broker state from a fresh consumer in the same group.
    checker = AIOKafkaConsumer(
        bootstrap_servers=kafka_bootstrap,
        group_id=group_id,
        enable_auto_commit=False,
    )
    await checker.start()
    try:
        assert await checker.committed(tp) == expected, (
            "the transactionally-sent offset is not visible after commit — "
            "offsets and produced records did not land atomically"
        )
    finally:
        await checker.stop()


async def test_at_least_once_redelivers_when_consumer_dies_before_commit(
    kafka_bootstrap: str,
) -> None:
    """
    ``AT_LEAST_ONCE`` commits *after* dispatch, so a consumer stopped before
    the commit must see the same message again on restart in the same group.
    """
    run_id = uuid.uuid4().hex[:8]
    prefix = f"eosalo{run_id}-"
    group_id = f"eos-alo-{run_id}"
    channel = "orders"
    await _create_topic(kafka_bootstrap, f"{prefix}{channel}")

    def _settings(gid: str) -> KafkaEventBusSettings:
        return KafkaEventBusSettings(
            bootstrap_servers=kafka_bootstrap,
            group_id=gid,
            auto_offset_reset="earliest",
            channel_prefix=prefix,
            delivery_semantics=KafkaDeliverySemantics.AT_LEAST_ONCE,
        )

    async with KafkaEventBus(_settings(f"pub-{run_id}")) as pub:
        await pub.publish(EosOrderEvent(order_id="o-1"), channel=channel)

    first_seen: list[str] = []
    dispatched = asyncio.Event()

    async def dying_handler(event: EosOrderEvent) -> None:
        first_seen.append(event.order_id)
        dispatched.set()
        # Never returns normally: the commit that would follow a clean
        # dispatch never happens — this is "the consumer died mid-dispatch".
        raise RuntimeError("simulated consumer death before commit")

    bus = KafkaEventBus(_settings(group_id))
    await bus.start()
    bus.subscribe(EosOrderEvent, dying_handler, channel=channel)
    try:
        await asyncio.wait_for(dispatched.wait(), timeout=_JOIN_TIMEOUT)
    finally:
        await bus.stop()

    assert first_seen == ["o-1"]

    second_seen: list[str] = []

    async def ok_handler(event: EosOrderEvent) -> None:
        second_seen.append(event.order_id)

    async with KafkaEventBus(_settings(group_id)) as restarted:
        restarted.subscribe(EosOrderEvent, ok_handler, channel=channel)
        deadline = asyncio.get_event_loop().time() + _JOIN_TIMEOUT
        while not second_seen and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.2)

    assert second_seen == ["o-1"], (
        "AT_LEAST_ONCE must redeliver a message whose dispatch never completed; "
        f"the restarted consumer saw {second_seen}"
    )


async def test_at_most_once_loses_message_when_consumer_dies_after_commit_before_dispatch(
    kafka_bootstrap: str,
) -> None:
    """
    ``AT_MOST_ONCE`` commits *before* dispatch, so a consumer that dies
    during dispatch **loses** the message: the restarted consumer never sees
    it again.

    This asserts the mode's documented, intended data-loss behaviour. It is
    not a bug report — it is the guarantee. A silent upgrade to
    at-least-once would change the mode's latency/throughput contract with
    nobody noticing (§RT5-eos).
    """
    run_id = uuid.uuid4().hex[:8]
    prefix = f"eosamo{run_id}-"
    group_id = f"eos-amo-{run_id}"
    channel = "orders"
    await _create_topic(kafka_bootstrap, f"{prefix}{channel}")

    def _settings(gid: str, semantics: KafkaDeliverySemantics) -> KafkaEventBusSettings:
        return KafkaEventBusSettings(
            bootstrap_servers=kafka_bootstrap,
            group_id=gid,
            auto_offset_reset="earliest",
            channel_prefix=prefix,
            delivery_semantics=semantics,
        )

    async with KafkaEventBus(
        _settings(f"pub-{run_id}", KafkaDeliverySemantics.AT_LEAST_ONCE)
    ) as pub:
        await pub.publish(EosOrderEvent(order_id="o-1"), channel=channel)

    first_seen: list[str] = []
    dispatched = asyncio.Event()

    async def dying_handler(event: EosOrderEvent) -> None:
        first_seen.append(event.order_id)
        dispatched.set()
        raise RuntimeError("simulated consumer death during dispatch")

    bus = KafkaEventBus(_settings(group_id, KafkaDeliverySemantics.AT_MOST_ONCE))
    await bus.start()
    bus.subscribe(EosOrderEvent, dying_handler, channel=channel)
    try:
        await asyncio.wait_for(dispatched.wait(), timeout=_JOIN_TIMEOUT)
    finally:
        await bus.stop()

    assert first_seen == ["o-1"]

    second_seen: list[str] = []

    async def ok_handler(event: EosOrderEvent) -> None:
        second_seen.append(event.order_id)

    async with KafkaEventBus(_settings(group_id, KafkaDeliverySemantics.AT_MOST_ONCE)) as restarted:
        restarted.subscribe(EosOrderEvent, ok_handler, channel=channel)
        # Quiet period rather than a poll: we are asserting that NOTHING
        # arrives, so the wait must be a full budget, not an early exit.
        await asyncio.sleep(_QUIET_PERIOD + 10.0)

    assert second_seen == [], (
        "AT_MOST_ONCE commits before dispatch — a message whose dispatch "
        f"failed must be lost, not redelivered; got {second_seen}"
    )
