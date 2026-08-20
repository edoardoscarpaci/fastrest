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

    @pytest.mark.xfail(
        reason=(
            "BUG: RedisCache.set(ttl=) (varco_redis/varco_redis/cache.py:283-290) "
            "truncates a sub-second float ttl to int() before passing it to "
            "SETEX — ttl=0.05 becomes 0, and Redis's SETEX rejects a 0 (or "
            "negative) expire time with 'invalid expire time in setex command', "
            "raising instead of storing a very-short-lived entry. "
            "CacheBackend.set()'s ttl: float | None contract implies sub-second "
            "precision is valid. See BACKLOG.md. Not fixed here per Plan 012 "
            "Non-goals (no production code changes)."
        ),
        strict=True,
    )
    async def test_ttl_expiry(self, cache) -> None:  # type: ignore[override]
        await super().test_ttl_expiry(cache)


class TestRedisJobStoreConformance(JobStoreConformance):
    @pytest.fixture
    async def store(self, redis_url: str):
        client = aioredis.from_url(redis_url)
        try:
            yield RedisJobStore(client)
        finally:
            await client.aclose()

    @pytest.mark.xfail(
        reason=(
            "BUG: RedisJobStore.try_claim() grants a non-zero lease_epoch (lease "
            "support advertised) but RedisJobStore.save() has no expected_epoch= "
            "parameter at all — TypeError: unexpected keyword argument "
            "'expected_epoch', instead of fencing a stale write with "
            "StaleLeaseError as AbstractJobStore.save() documents. See "
            "BACKLOG.md. Not fixed here per Plan 012 Non-goals (no production "
            "code changes)."
        ),
        strict=True,
    )
    async def test_save_with_stale_expected_epoch_raises(self, store) -> None:  # type: ignore[override]
        await super().test_save_with_stale_expected_epoch_raises(store)


class TestRedisDLQConformance(DeadLetterQueueConformance):
    @pytest.fixture
    async def dlq(self, redis_url: str):
        async with RedisDLQ(RedisEventBusSettings(url=redis_url)) as dlq:
            yield dlq


class TestLayeredCacheConformance(CacheBackendConformance):
    """
    ``LayeredCache`` conformance with a real L2 (Step 27's TODO) —
    ``InMemoryCache`` as L1, real ``RedisCache`` as L2.
    """

    @pytest.fixture
    async def cache(self, redis_url: str):
        l1 = InMemoryCache()
        l2 = RedisCache(RedisCacheSettings(url=redis_url))
        async with LayeredCache(l1, l2, promote_ttl=30.0) as cache:
            yield cache

    @pytest.mark.xfail(
        reason=(
            "BUG: RedisCache.set(ttl=) truncates a sub-second float ttl to "
            "int() before passing it to SETEX (see KI-3 in BACKLOG.md) — the "
            "same underlying issue as TestRedisCacheConformance.test_ttl_expiry, "
            "inherited here because LayeredCache's L2 is a real RedisCache. "
            "Not fixed here per Plan 012 Non-goals (no production code changes)."
        ),
        strict=True,
    )
    async def test_ttl_expiry(self, cache) -> None:  # type: ignore[override]
        await super().test_ttl_expiry(cache)
