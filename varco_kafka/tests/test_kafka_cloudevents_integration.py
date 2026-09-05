"""
Integration test — CloudEvents envelope over Kafka (Plan 030 / Phase 0, Step 6).
================================================================================

RED-MODE TDD: ``varco_core.event.cloudevents`` does not exist yet.

Asserts the wire bytes a real Kafka broker carries are a valid CloudEvents
structured-mode envelope (§D-CE1/§D-CE4) and that a consumer with the same
serializer bound round-trips them back into the original event.

⚠️ §D-CE2: Kafka's *binding* also wants a ``content-type`` header starting with
``application/cloudevents`` — unreachable today because ``publish()`` gains no
``headers=`` (RS-2).  That is explicitly out of scope; only the body is asserted.

⚠️ Topic pre-creation: any test here that subscribes *before* publishing must
declare its topic through ``KafkaChannelManager`` first (the package-wide
convention — see ``test_kafka_integration.py``'s ``test_publish_and_consume``).
A ``KafkaEventBus`` consumer that joins a group for a not-yet-existent topic
gets no partition metadata and stays idle until ``metadata_max_age_ms``
(5 minutes), which no sleep margin in a test can outwait; the full mechanism is
documented on ``test_round_trip_through_a_consumer_with_the_serializer_bound``.

Requires Docker.  Run with ``-m integration``.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from uuid import uuid4

import pytest
from varco_core.event import Event

pytestmark = pytest.mark.integration

if not os.environ.get("VARCO_RUN_INTEGRATION"):
    pytest.skip(
        "Integration tests disabled — set VARCO_RUN_INTEGRATION=1 or use -m integration",
        allow_module_level=True,
    )


class KafkaCloudEvent(Event):
    __event_type__ = "ce.kafka.order.placed"
    order_id: str


@pytest.fixture
def run_id() -> str:
    # Per-test namespacing — the Kafka container is session-scoped and shared.
    return uuid4().hex[:8]


@pytest.fixture
async def channel_manager(kafka_bootstrap: str) -> Any:
    """
    ``KafkaChannelManager`` for pre-creating topics before a consumer subscribes.

    Same fixture shape as ``test_kafka_integration.py``'s — admin operations
    live here, not on the bus, so the bus stays credential-free.
    """
    from varco_kafka import (  # noqa: PLC0415
        KafkaChannelManager,
        KafkaChannelManagerSettings,
    )

    async with KafkaChannelManager(
        KafkaChannelManagerSettings(bootstrap_servers=kafka_bootstrap)
    ) as manager:
        yield manager


@pytest.fixture
def serializer() -> Any:
    from varco_core.event.cloudevents import (  # noqa: PLC0415
        CloudEventsJsonSerializer,
        CloudEventsSettings,
    )

    return CloudEventsJsonSerializer(CloudEventsSettings(source="/varco/tests/kafka"))


class TestKafkaCloudEventsWire:
    async def test_wire_bytes_are_a_valid_cloudevent(
        self, kafka_bootstrap: str, run_id: str, serializer: Any
    ) -> None:
        from aiokafka import AIOKafkaConsumer  # noqa: PLC0415

        from varco_kafka import KafkaEventBus, KafkaEventBusSettings  # noqa: PLC0415

        topic = f"ce-orders-{run_id}"
        settings = KafkaEventBusSettings(bootstrap_servers=kafka_bootstrap)

        raw = AIOKafkaConsumer(
            topic,
            bootstrap_servers=kafka_bootstrap,
            group_id=f"ce-raw-{run_id}",
            auto_offset_reset="earliest",
        )
        await raw.start()
        try:
            async with KafkaEventBus(settings, serializer=serializer) as bus:
                await bus.publish(KafkaCloudEvent(order_id="o-1"), channel=topic)
                message = await asyncio.wait_for(raw.getone(), timeout=30)
        finally:
            await raw.stop()

        envelope = json.loads(message.value.decode("utf-8"))
        assert envelope["specversion"] == "1.0"
        assert envelope["type"] == KafkaCloudEvent.event_type_name()
        assert envelope["source"] == "/varco/tests/kafka"
        assert envelope["id"]
        assert envelope["data"] == {"order_id": "o-1"}
        assert "data_base64" not in envelope

    async def test_round_trip_through_a_consumer_with_the_serializer_bound(
        self,
        kafka_bootstrap: str,
        run_id: str,
        serializer: Any,
        channel_manager: Any,
    ) -> None:
        """
        A bus-bound consumer round-trips a CloudEvents envelope back into the event.

        DESIGN — why the topic is pre-created, and why a longer sleep would not
        have worked (regression guard, see the module docstring):
        ``KafkaEventBus.start()`` starts its ``AIOKafkaConsumer`` with **no**
        topics and only then calls ``consumer.subscribe(...)`` (``bus.py:305``).
        That path skips aiokafka's ``_wait_topics()`` (``consumer.py:365``),
        which is the *only* thing that blocks until an auto-created topic's
        metadata exists, and — because a ``group_id`` is set — it also skips the
        ``force_metadata_update()`` in ``AIOKafkaConsumer.subscribe()``
        (``consumer.py:1058-1064``, guarded by ``if self._group_id is None``).
        A group that joins with no partition metadata for a not-yet-existent
        topic is therefore stuck until the next periodic metadata refresh —
        ``metadata_max_age_ms``, default **5 minutes** (``consumer.py:244``),
        an order of magnitude beyond any sane test budget, and unaffected by
        ``auto_offset_reset``.  Declaring the channel first removes the race
        entirely; this mirrors ``test_kafka_integration.py``'s
        ``test_publish_and_consume``.
        """
        from varco_kafka import KafkaEventBus, KafkaEventBusSettings  # noqa: PLC0415

        topic = f"ce-orders-rt-{run_id}"
        settings = KafkaEventBusSettings(
            bootstrap_servers=kafka_bootstrap,
            group_id=f"ce-rt-{run_id}",
            # earliest — the publish below must be readable even if partition
            # assignment lands after it; this test asserts the serializer
            # round-trip, never the group-join timing.
            auto_offset_reset="earliest",
        )
        received: list[Event] = []

        # Pre-create the topic before subscribing (see the DESIGN note above).
        await channel_manager.declare_channel(topic)
        assert await channel_manager.channel_exists(topic), (
            f"precondition failed: {topic} was not created before subscribing"
        )

        async with KafkaEventBus(settings, serializer=serializer) as bus:
            bus.subscribe(KafkaCloudEvent, lambda e: received.append(e), channel=topic)
            # Settle window for consumer-group coordinator formation, matching
            # test_kafka_integration.py's 5 s.
            await asyncio.sleep(5)
            await bus.publish(KafkaCloudEvent(order_id="o-2"), channel=topic)

            for _ in range(60):
                if received:
                    break
                await asyncio.sleep(0.5)

        assert [e.order_id for e in received] == ["o-2"]  # type: ignore[attr-defined]
