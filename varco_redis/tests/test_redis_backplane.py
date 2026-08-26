"""
tests.test_redis_backplane
=============================
Plan 010 Phase 3, steps 28/30/31 — ``varco_redis.backplane``.

Unit tests run against a minimal FakePubSubRedis double — no Docker needed.
The ``@pytest.mark.integration`` test at the bottom requires a real broker
and is skipped by default.

RED until ``varco_redis/varco_redis/backplane.py`` lands.
"""

from __future__ import annotations

import asyncio
import json

import pytest


class FakePubSub:
    """Minimal pubsub double: records subscriptions, lets tests push messages."""

    def __init__(self, client: FakePubSubRedis) -> None:
        self._client = client
        self.subscribed_channels: list[str] = []
        self._queue: asyncio.Queue = asyncio.Queue()

    async def subscribe(self, channel: str) -> None:
        self.subscribed_channels.append(channel)

    async def get_message(
        self, ignore_subscribe_messages: bool = True, timeout: float = 1.0
    ):
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except TimeoutError:
            return None

    def push(self, channel: str, data: bytes) -> None:
        self._queue.put_nowait(
            {"type": "message", "channel": channel.encode(), "data": data}
        )

    async def close(self) -> None:
        pass


class FakePubSubRedis:
    """Minimal Redis-asyncio-shaped double supporting publish + pubsub()."""

    def __init__(self, *, fail_publish: bool = False) -> None:
        self.published: list[tuple[str, bytes]] = []
        self._fail_publish = fail_publish
        self._pubsub: FakePubSub | None = None

    async def publish(self, channel: str, message: bytes) -> int:
        if self._fail_publish:
            raise ConnectionError("redis down")
        self.published.append((channel, message))
        return 1

    def pubsub(self) -> FakePubSub:
        self._pubsub = FakePubSub(self)
        return self._pubsub


class TestRedisPubSubBackplaneUnit:
    async def test_publish_encodes_expected_json(self) -> None:
        from varco_core.cache.backplane import InvalidationMessage
        from varco_redis.backplane import RedisPubSubBackplane

        client = FakePubSubRedis()
        backplane = RedisPubSubBackplane(
            client=client, channel="varco.cache.invalidate"
        )

        msg = InvalidationMessage(
            kind="key", payload="user:1", origin=backplane.origin, ts=1.0
        )
        await backplane.publish(msg)

        assert len(client.published) == 1
        channel, raw = client.published[0]
        assert channel == "varco.cache.invalidate"
        decoded = json.loads(raw)
        assert decoded["kind"] == "key"
        assert decoded["payload"] == "user:1"
        assert decoded["origin"] == backplane.origin

    async def test_publish_never_raises_when_client_errors(self) -> None:
        from varco_core.cache.backplane import InvalidationMessage
        from varco_redis.backplane import RedisPubSubBackplane

        client = FakePubSubRedis(fail_publish=True)
        backplane = RedisPubSubBackplane(
            client=client, channel="varco.cache.invalidate"
        )

        msg = InvalidationMessage(
            kind="key", payload="user:1", origin=backplane.origin, ts=1.0
        )
        await backplane.publish(msg)  # must swallow, not raise

    async def test_received_self_origin_message_is_skipped(self) -> None:
        from varco_redis.backplane import RedisPubSubBackplane

        client = FakePubSubRedis()
        backplane = RedisPubSubBackplane(
            client=client, channel="varco.cache.invalidate"
        )

        received: list = []

        async def handler(message) -> None:
            received.append(message)

        backplane.subscribe(handler)
        await backplane.start()

        own_payload = json.dumps(
            {"kind": "key", "payload": "k", "origin": backplane.origin, "ts": 1.0}
        ).encode()
        client._pubsub.push("varco.cache.invalidate", own_payload)
        await asyncio.sleep(0.05)

        assert received == []
        await backplane.stop()

    async def test_hash_keys_true_publishes_hash_and_degrades_prefix_to_clear(
        self,
    ) -> None:
        from varco_core.cache.backplane import InvalidationMessage
        from varco_redis.backplane import RedisPubSubBackplane

        client = FakePubSubRedis()
        backplane = RedisPubSubBackplane(
            client=client, channel="varco.cache.invalidate", hash_keys=True
        )

        msg = InvalidationMessage(
            kind="prefix", payload="tenant:acme:", origin=backplane.origin, ts=1.0
        )
        await backplane.publish(msg)

        _, raw = client.published[0]
        decoded = json.loads(raw)
        # A prefix cannot be hash-matched — must degrade to a local clear.
        assert decoded["kind"] == "clear"
        assert decoded["payload"] != "tenant:acme:"


@pytest.mark.integration
class TestRedisPubSubBackplaneIntegration:
    async def test_cross_node_l1_invalidation_end_to_end(self) -> None:
        pytest.skip("requires a real Redis broker — run with -m integration")
