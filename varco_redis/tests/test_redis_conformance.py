"""
Real-Redis conformance opt-in (Plan 012 / RT6, Step 27).

Consumes the session-scoped ``redis_url`` fixture that Phase 1 (Steps 6-7)
adds to ``varco_redis/tests/conftest.py``. Until that fixture exists, every
test class below errors at collection/fixture-resolution time with
``fixture 'redis_url' not found``.

Also depends on ``pythonpath = ["../testkit"]`` in
``varco_redis/pyproject.toml`` — until then every import below fails with
``ModuleNotFoundError: No module named 'varco_conformance'``.
"""

from __future__ import annotations

import pytest
import redis.asyncio as aioredis

from varco_core.cache.invalidation import TTLStrategy
from varco_core.cache.layered import LayeredCache
from varco_core.cache.memory import InMemoryCache
from varco_redis.bus import RedisEventBus
from varco_redis.cache import RedisCache, RedisCacheSettings
from varco_redis.config import RedisEventBusSettings
from varco_redis.dlq import RedisDLQ
from varco_redis.job_store import RedisJobStore
from varco_redis.streams import RedisStreamEventBus

from varco_conformance.cache import CacheBackendConformance
from varco_conformance.dlq import DeadLetterQueueConformance
from varco_conformance.event_bus import EventBusConformance
from varco_conformance.job_store import JobStoreConformance

pytestmark = pytest.mark.integration


class TestRedisEventBusConformance(EventBusConformance):
    @pytest.fixture
    async def bus(self, redis_url: str):
        async with RedisEventBus(RedisEventBusSettings(url=redis_url)) as bus:
            yield bus


class TestRedisStreamEventBusConformance(EventBusConformance):
    @pytest.fixture
    async def bus(self, redis_url: str):
        async with RedisStreamEventBus(RedisEventBusSettings(url=redis_url)) as bus:
            yield bus


class TestRedisCacheConformance(CacheBackendConformance):
    @pytest.fixture
    async def cache(self, redis_url: str):
        async with RedisCache(RedisCacheSettings(url=redis_url)) as cache:
            yield cache


class TestRedisJobStoreConformance(JobStoreConformance):
    @pytest.fixture
    async def store(self, redis_url: str):
        client = aioredis.from_url(redis_url)
        try:
            yield RedisJobStore(client)
        finally:
            await client.aclose()


class TestRedisDLQConformance(DeadLetterQueueConformance):
    @pytest.fixture
    async def dlq(self, redis_url: str):
        async with RedisDLQ(RedisEventBusSettings(url=redis_url)) as dlq:
            yield dlq


class TestLayeredCacheConformance(CacheBackendConformance):
    """
    ``LayeredCache`` conformance with a real L2 (Step 27's TODO) —
    ``InMemoryCache`` as L1, real ``RedisCache`` as L2.

    L1 is given a ``TTLStrategy`` — per ``InMemoryCache``'s own contract
    ("If strategy is None, should_invalidate() is never called — entries
    persist until delete() or clear()"), a strategy-less L1 never expires
    a ttl-bearing entry on its own; only L2 (Redis, which enforces TTL
    natively) would. Without this, ``test_ttl_expiry`` would keep failing
    post-KI-3 for a second, unrelated reason (a mis-configured fixture, not
    a RedisCache bug) — L1 would keep serving the value forever regardless
    of L2's real expiry.
    """

    @pytest.fixture
    async def cache(self, redis_url: str):
        l1 = InMemoryCache(strategy=TTLStrategy())
        l2 = RedisCache(RedisCacheSettings(url=redis_url))
        async with LayeredCache(l1, l2, promote_ttl=30.0) as cache:
            yield cache
