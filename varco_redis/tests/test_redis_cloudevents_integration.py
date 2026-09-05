"""
Integration test — CloudEvents envelope over Redis (Plan 030 / Phase 0, Step 6).
================================================================================

RED-MODE TDD: ``varco_core.event.cloudevents`` does not exist yet.

Asserts two things against a real Redis:

1. Pub/Sub — the published body is a valid CloudEvents structured envelope.
2. Streams — **varco's own named convention** (§D-CE4 convention 1, brief 001
   §Evidence-gap 2: no official Redis binding exists): the whole envelope goes
   in a **single stream field named ``ce``**, never one field per attribute, so
   ``XADD`` field names can never collide with a future varco field.

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


class RedisCloudEvent(Event):
    __event_type__ = "ce.redis.order.placed"
    order_id: str


@pytest.fixture
def run_id() -> str:
    # Per-test namespacing — the Redis container is session-scoped and shared.
    return uuid4().hex[:8]


@pytest.fixture
def serializer() -> Any:
    from varco_core.event.cloudevents import (  # noqa: PLC0415
        CloudEventsJsonSerializer,
        CloudEventsSettings,
    )

    return CloudEventsJsonSerializer(CloudEventsSettings(source="/varco/tests/redis"))


class TestRedisPubSubCloudEvents:
    async def test_published_body_is_a_valid_cloudevent(
        self, redis_url: str, run_id: str, serializer: Any
    ) -> None:
        import redis.asyncio as aioredis  # noqa: PLC0415

        from varco_redis import RedisEventBus, RedisEventBusSettings  # noqa: PLC0415

        channel = f"ce-orders-{run_id}"
        client = aioredis.from_url(redis_url)
        pubsub = client.pubsub()
        await pubsub.subscribe(channel)
        try:
            async with RedisEventBus(
                RedisEventBusSettings(url=redis_url), serializer=serializer
            ) as bus:
                await asyncio.sleep(0.2)
                await bus.publish(RedisCloudEvent(order_id="o-1"), channel=channel)

                raw = None
                for _ in range(60):
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
                    if message is not None:
                        raw = message["data"]
                        break
        finally:
            await pubsub.aclose()
            await client.aclose()

        assert raw is not None
        envelope = json.loads(raw.decode("utf-8"))
        assert envelope["specversion"] == "1.0"
        assert envelope["type"] == RedisCloudEvent.event_type_name()
        assert envelope["source"] == "/varco/tests/redis"
        assert envelope["data"] == {"order_id": "o-1"}
        assert "data_base64" not in envelope

    async def test_round_trip_through_a_subscriber(
        self, redis_url: str, run_id: str, serializer: Any
    ) -> None:
        from varco_redis import RedisEventBus, RedisEventBusSettings  # noqa: PLC0415

        channel = f"ce-orders-rt-{run_id}"
        received: list[RedisCloudEvent] = []

        async with RedisEventBus(
            RedisEventBusSettings(url=redis_url), serializer=serializer
        ) as bus:
            bus.subscribe(RedisCloudEvent, lambda e: received.append(e), channel=channel)
            await asyncio.sleep(0.5)
            await bus.publish(RedisCloudEvent(order_id="o-2"), channel=channel)

            for _ in range(60):
                if received:
                    break
                await asyncio.sleep(0.2)

        assert [e.order_id for e in received] == ["o-2"]


class TestRedisStreamsCloudEventsConvention:
    async def test_whole_envelope_lives_in_a_single_stream_field_named_ce(
        self, redis_url: str, run_id: str, serializer: Any
    ) -> None:
        # §D-CE4 convention 1 — one field named `ce`, NEVER one field per
        # CloudEvents attribute.  This is varco's own named, versioned
        # convention; downstreams implement against it.
        import redis.asyncio as aioredis  # noqa: PLC0415

        from varco_redis import (  # noqa: PLC0415
            RedisEventBusSettings,
            RedisStreamEventBus,
        )

        channel = f"ce-stream-{run_id}"
        async with RedisStreamEventBus(
            RedisEventBusSettings(url=redis_url),
            consumer_group=f"ce-grp-{run_id}",
            serializer=serializer,
        ) as bus:
            await bus.publish(RedisCloudEvent(order_id="o-3"), channel=channel)

        client = aioredis.from_url(redis_url)
        try:
            # The bus may prefix the stream key — locate it rather than guess.
            keys = await client.keys(f"*{channel}*")
            assert keys, "no stream key was written"
            entries = await client.xrange(keys[0])
        finally:
            await client.aclose()

        assert entries, "no stream entry was written"
        (_entry_id, fields) = entries[-1]
        decoded = {(k.decode() if isinstance(k, bytes) else k): v for k, v in fields.items()}
        assert list(decoded) == ["ce"], (
            "the CloudEvents envelope must occupy exactly one stream field named 'ce'"
        )
        envelope = json.loads(decoded["ce"].decode("utf-8"))
        assert envelope["specversion"] == "1.0"
        assert envelope["data"] == {"order_id": "o-3"}
