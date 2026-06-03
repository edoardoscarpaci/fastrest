"""
varco_redis
===========
Redis event bus backends (Pub/Sub and Streams) for varco.

All public symbols are importable directly from ``varco_redis``::

    from varco_redis import RedisEventBus, RedisEventBusSettings
    from varco_redis import RedisStreamEventBus              # at-least-once
    from varco_redis import RedisChannelManager, RedisChannelManagerSettings

Layer map::

    varco_core.event.AbstractEventBus
        ↑ implemented by
    varco_redis.RedisEventBus   ← THIS PACKAGE
        ↑ configured by
    varco_redis.RedisEventBusSettings
        ↑ discovered by
    container.scan("varco_redis", recursive=True)

    varco_core.event.channel.ChannelManager
        ↑ implemented by
    varco_redis.RedisChannelManager
        ↑ configured by
    varco_redis.RedisChannelManagerSettings (alias for RedisEventBusSettings)

Usage (standalone bus)::

    from varco_redis import RedisEventBus, RedisEventBusSettings
    from varco_core.event import BusEventProducer, listen, EventConsumer

    config = RedisEventBusSettings(url="redis://localhost:6379/0")

    async with RedisEventBus(config) as bus:
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

    from providify import DIContainer
    from varco_redis.di import bootstrap
    from varco_core.event import AbstractEventBus

    container = bootstrap(DIContainer())   # scans varco_redis, registers @Singletons

    bus = await container.aget(AbstractEventBus)  # RedisEventBus singleton
"""

from __future__ import annotations

from varco_redis.bus import RedisEventBus
from varco_redis.cache import RedisCache, RedisCacheConfiguration, RedisCacheSettings
from varco_redis.channel import RedisChannelManager, RedisChannelManagerSettings
from varco_redis.config import RedisEventBusSettings
from varco_redis.dlq import RedisDLQ, RedisDLQConfiguration
from varco_redis.conversation import RedisConversationStore
from varco_redis.job_store import RedisJobStore
from varco_redis.rate_limit import RedisRateLimiter
from varco_redis.streams import RedisStreamEventBus

__all__ = [
    # ── Pub/Sub bus ────────────────────────────────────────────────────────────
    "RedisEventBus",
    "RedisEventBusSettings",
    # ── Streams bus (at-least-once) ────────────────────────────────────────────
    "RedisStreamEventBus",
    # ── Channel management ─────────────────────────────────────────────────────
    "RedisChannelManager",
    "RedisChannelManagerSettings",
    # ── Cache ──────────────────────────────────────────────────────────────────
    "RedisCache",
    "RedisCacheSettings",
    "RedisCacheConfiguration",
    # ── Dead Letter Queue ──────────────────────────────────────────────────────
    "RedisDLQ",
    "RedisDLQConfiguration",
    # ── Conversation store ─────────────────────────────────────────────────────
    "RedisConversationStore",
    # ── Job store ──────────────────────────────────────────────────────────────
    "RedisJobStore",
    # ── Rate limiting ──────────────────────────────────────────────────────────
    "RedisRateLimiter",
]
