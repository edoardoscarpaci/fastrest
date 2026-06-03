"""
varco_nats
==========
NATS JetStream event bus backend for varco.

All public symbols are importable directly from ``varco_nats``::

    from varco_nats import NatsEventBus, NatsEventBusSettings
    from varco_nats import NatsStreamManager, NatsChannelManagerSettings
    from varco_nats import NatsDLQ, NatsDLQConfiguration
    from varco_nats.di import bootstrap            # Providify scan

Layer map::

    varco_core.event.AbstractEventBus
        ↑ implemented by
    varco_nats.NatsEventBus           ← THIS PACKAGE
        ↑ configured by
    varco_nats.NatsEventBusSettings
        ↑ wired by
    varco_nats.di.bootstrap()         ← Providify scan

    varco_core.event.channel.ChannelManager
        ↑ implemented by
    varco_nats.NatsStreamManager
        ↑ configured by
    varco_nats.NatsChannelManagerSettings

    varco_core.event.dlq.AbstractDeadLetterQueue
        ↑ implemented by
    varco_nats.NatsDLQ
        ↑ wired by
    varco_nats.NatsDLQConfiguration   ← Providify @Configuration

Delivery layer
--------------
``varco_nats`` targets **NATS JetStream only** — the persistent, at-least-once
layer (the NATS analogue of Kafka).  Core NATS at-most-once pub/sub is not
exposed; use ``varco_redis``'s Pub/Sub bus if fire-and-forget is required.

Usage (standalone bus)::

    from varco_nats import NatsEventBus, NatsEventBusSettings
    from varco_core.event import BusEventProducer, listen, EventConsumer

    config = NatsEventBusSettings(servers="nats://localhost:4222")

    async with NatsEventBus(config) as bus:
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

Usage (Providify DI)::

    from varco_nats.di import bootstrap
    from varco_core.event import AbstractEventBus

    container = bootstrap()
    bus = await container.aget(AbstractEventBus)   # NatsEventBus singleton
"""

from __future__ import annotations

from varco_nats.bus import NatsEventBus
from varco_nats.channel import NatsChannelManagerSettings, NatsStreamManager
from varco_nats.config import NatsDeliverySemantics, NatsEventBusSettings
from varco_nats.connection import NatsConnectionSettings
from varco_nats.dlq import NatsDLQ, NatsDLQConfiguration
from varco_nats.health import NatsHealthCheck

__all__ = [
    # ── Bus ────────────────────────────────────────────────────────────────────
    "NatsEventBus",
    "NatsDeliverySemantics",
    "NatsEventBusSettings",
    # ── Connection ─────────────────────────────────────────────────────────────
    "NatsConnectionSettings",
    # ── Stream / channel management ────────────────────────────────────────────
    "NatsStreamManager",
    "NatsChannelManagerSettings",
    # ── Dead Letter Queue ──────────────────────────────────────────────────────
    "NatsDLQ",
    "NatsDLQConfiguration",
    # ── Health ─────────────────────────────────────────────────────────────────
    "NatsHealthCheck",
]
