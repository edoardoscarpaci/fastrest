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
        self, kafka_bootstrap: str, run_id: str, serializer: Any
    ) -> None:
        from varco_kafka import KafkaEventBus, KafkaEventBusSettings  # noqa: PLC0415

        topic = f"ce-orders-rt-{run_id}"
        settings = KafkaEventBusSettings(
            bootstrap_servers=kafka_bootstrap, group_id=f"ce-rt-{run_id}"
        )
        received: list[Event] = []

        async with KafkaEventBus(settings, serializer=serializer) as bus:
            bus.subscribe(KafkaCloudEvent, lambda e: received.append(e), channel=topic)
            await asyncio.sleep(2)
            await bus.publish(KafkaCloudEvent(order_id="o-2"), channel=topic)

            for _ in range(60):
                if received:
                    break
                await asyncio.sleep(0.5)

        assert [e.order_id for e in received] == ["o-2"]  # type: ignore[attr-defined]
