"""
varco_kafka
===========
Apache Kafka event bus backend for varco.

All public symbols are importable directly from ``varco_kafka``::

    from varco_kafka import KafkaEventBus, KafkaEventBusSettings
    from varco_kafka import KafkaChannelManager, KafkaChannelManagerSettings

Layer map::

    varco_core.event.AbstractEventBus
        ↑ implemented by
    varco_kafka.KafkaEventBus   ← THIS PACKAGE
        ↑ configured by
    varco_kafka.KafkaEventBusSettings
        ↑ discovered by
    container.scan("varco_kafka", recursive=True)

    varco_core.event.channel.ChannelManager
        ↑ implemented by
    varco_kafka.KafkaChannelManager
        ↑ configured by
    varco_kafka.KafkaChannelManagerSettings

Usage (standalone bus)::

    from varco_kafka import KafkaEventBus, KafkaEventBusSettings
    from varco_core.event import BusEventProducer, listen, EventConsumer

    config = KafkaEventBusSettings(
        bootstrap_servers="localhost:9092",
        group_id="my-service",
    )

    async with KafkaEventBus(config) as bus:
        # producer side
        producer = BusEventProducer(bus)
        await producer._produce(MyEvent(...), channel="my-channel")

        # consumer side
        class MyConsumer(EventConsumer):
            @listen(MyEvent, channel="my-channel")
            async def on_event(self, event: MyEvent) -> None:
                ...

        consumer = MyConsumer()
        consumer.register_to(bus)

Usage (standalone channel management)::

    from varco_kafka import KafkaChannelManager, KafkaChannelManagerSettings
    from varco_core.event.base import ChannelConfig

    settings = KafkaChannelManagerSettings(bootstrap_servers="localhost:9092")
    async with KafkaChannelManager(settings) as manager:
        await manager.declare_channel("orders", ChannelConfig(num_partitions=6))

Usage (Providify DI)::

    from providify import DIContainer
    from varco_kafka.di import bootstrap
    from varco_core.event import AbstractEventBus

    container = bootstrap(DIContainer())   # scans varco_kafka, registers @Singletons

    bus = await container.aget(AbstractEventBus)  # KafkaEventBus singleton
"""

from __future__ import annotations

from varco_kafka.bus import KafkaEventBus
from varco_kafka.channel import KafkaChannelManager, KafkaChannelManagerSettings
from varco_kafka.config import KafkaDeliverySemantics, KafkaEventBusSettings
from varco_kafka.dlq import KafkaDLQ, KafkaDLQConfiguration

__all__ = [
    # ── Bus ────────────────────────────────────────────────────────────────────
    "KafkaEventBus",
    "KafkaDeliverySemantics",
    "KafkaEventBusSettings",
    # ── Channel management ─────────────────────────────────────────────────────
    "KafkaChannelManager",
    "KafkaChannelManagerSettings",
    # ── Dead Letter Queue ──────────────────────────────────────────────────────
    "KafkaDLQ",
    "KafkaDLQConfiguration",
]
