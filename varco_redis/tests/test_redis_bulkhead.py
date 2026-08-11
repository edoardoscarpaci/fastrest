"""
Integration tests for varco_redis.bulkhead
============================================
Spins up a real Redis instance via testcontainers and verifies end-to-end
distributed-concurrency-limiting behaviour of ``RedisBulkhead``.

Plan 005, Phase 8, Step 89 (U-7's second leg).

DISABLED BY DEFAULT — requires Docker.  Run with::

    pytest -m integration tests/test_redis_bulkhead.py

Or set the ``VARCO_RUN_INTEGRATION`` env var::

    VARCO_RUN_INTEGRATION=1 pytest tests/test_redis_bulkhead.py

Prerequisites:
    - Docker daemon running
    - testcontainers[redis] installed (see pyproject.toml dev dependencies)
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

pytestmark = pytest.mark.integration

if not os.environ.get("VARCO_RUN_INTEGRATION"):
    pytest.skip(
        "Integration tests disabled — set VARCO_RUN_INTEGRATION=1 or use -m integration",
        allow_module_level=True,
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def redis_container():
    """Start a Redis instance for the integration test module."""
    from testcontainers.redis import RedisContainer

    with RedisContainer() as redis:
        yield redis


def _redis_url(redis_container) -> str:
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    return f"redis://{host}:{port}/0"


@pytest.fixture
async def bulkhead(redis_container):
    """
    Connected ``RedisBulkhead`` (max_concurrent=3, fail-fast) backed by the
    testcontainers Redis instance.  Uses a unique key prefix + name per test
    to prevent cross-test interference.
    """
    from varco_core.resilience.bulkhead import BulkheadConfig
    from varco_redis.bulkhead import RedisBulkhead
    from varco_redis.config import RedisEventBusSettings

    prefix = f"test:{uuid.uuid4().hex[:8]}:"
    settings = RedisEventBusSettings(
        url=_redis_url(redis_container), channel_prefix=prefix
    )
    cfg = BulkheadConfig(max_concurrent=3, max_wait=0.0)
    async with RedisBulkhead(
        cfg, settings=settings, name=f"bh-{uuid.uuid4().hex[:8]}"
    ) as bh:
        yield bh


# ── N+1 concurrent acquirers ────────────────────────────────────────────────


async def test_nplus1_concurrent_acquirers_the_extra_one_fails_fast(bulkhead) -> None:
    """
    With max_concurrent=3 and max_wait=0.0 (fail-fast), 4 concurrent holders
    contending for slots should see exactly 3 succeed and 1 raise
    ``BulkheadFullError``.
    """
    from varco_core.resilience.bulkhead import BulkheadFullError

    release_gate = asyncio.Event()

    async def hold() -> str:
        await release_gate.wait()
        return "done"

    async def try_call() -> str | Exception:
        try:
            return await bulkhead.call(hold)
        except BulkheadFullError as exc:
            return exc

    tasks = [asyncio.create_task(try_call()) for _ in range(4)]
    # Give the first 3 a chance to claim their slots before releasing.
    await asyncio.sleep(0.2)
    release_gate.set()
    results = await asyncio.gather(*tasks)

    succeeded = [r for r in results if r == "done"]
    failed = [r for r in results if isinstance(r, BulkheadFullError)]
    assert len(succeeded) == 3
    assert len(failed) == 1


async def test_nplus1_concurrent_acquirers_waits_when_max_wait_positive(
    redis_container,
) -> None:
    """
    With ``max_wait > 0``, the (N+1)th acquirer waits for a slot to free up
    instead of failing immediately, and succeeds once a holder releases.
    """
    from varco_core.resilience.bulkhead import BulkheadConfig
    from varco_redis.bulkhead import RedisBulkhead
    from varco_redis.config import RedisEventBusSettings

    prefix = f"test:{uuid.uuid4().hex[:8]}:"
    settings = RedisEventBusSettings(
        url=_redis_url(redis_container), channel_prefix=prefix
    )
    cfg = BulkheadConfig(max_concurrent=2, max_wait=2.0)

    async with RedisBulkhead(
        cfg, settings=settings, name=f"bh-wait-{uuid.uuid4().hex[:8]}"
    ) as bh:
        release_gate = asyncio.Event()

        async def hold() -> str:
            await release_gate.wait()
            return "done"

        # Two long-held slots + one waiter that should succeed once we
        # release one of the holders shortly after.
        holder_tasks = [asyncio.create_task(bh.call(hold)) for _ in range(2)]
        await asyncio.sleep(0.1)  # let holders claim their slots

        async def release_soon() -> None:
            await asyncio.sleep(0.2)
            release_gate.set()

        asyncio.create_task(release_soon())

        async def quick() -> str:
            return "quick-done"

        waiter_result = await bh.call(quick)
        assert waiter_result == "quick-done"

        await asyncio.gather(*holder_tasks)


# ── Crashed-holder reclaim via slot_ttl ─────────────────────────────────────


async def test_crashed_holder_slot_reclaimed_after_ttl(redis_container) -> None:
    """
    A holder that acquires a slot and never releases (simulating a crash)
    has its slot reclaimed once ``slot_ttl`` seconds elapse — a subsequent
    acquire attempt on a full bulkhead succeeds after the TTL, not before.
    """
    from varco_core.resilience.bulkhead import BulkheadConfig, BulkheadFullError
    from varco_redis.bulkhead import RedisBulkhead
    from varco_redis.config import RedisEventBusSettings

    prefix = f"test:{uuid.uuid4().hex[:8]}:"
    settings = RedisEventBusSettings(
        url=_redis_url(redis_container), channel_prefix=prefix
    )
    cfg = BulkheadConfig(max_concurrent=1, max_wait=0.0)

    async with RedisBulkhead(
        cfg,
        settings=settings,
        slot_ttl=0.3,
        name=f"bh-ttl-{uuid.uuid4().hex[:8]}",
    ) as bh:
        # Acquire the only slot and "crash" — never release it.
        token = await bh._acquire()  # noqa: SLF001 — simulating a crashed holder
        assert token

        # Bulkhead is now full — a second acquire fails fast.
        with pytest.raises(BulkheadFullError):
            await bh._acquire()  # noqa: SLF001

        # Wait past slot_ttl — the crashed holder's slot must be reclaimed.
        await asyncio.sleep(0.4)

        assert await bh.available_slots() == 1

        new_token = await bh._acquire()  # noqa: SLF001
        assert new_token
        await bh._release(new_token)  # noqa: SLF001


# ── Export + DI registration resolve ────────────────────────────────────────


async def test_export_and_di_registration_resolve(redis_container) -> None:
    """
    ``RedisBulkhead``/``RedisBulkheadConfiguration`` are exported from
    ``varco_redis`` and the opt-in ``@Configuration`` resolves a connected
    singleton through the DI container.
    """
    from providify import DIContainer, Provider
    from varco_redis import RedisBulkhead, RedisBulkheadConfiguration
    from varco_redis.config import RedisEventBusSettings

    prefix = f"test:{uuid.uuid4().hex[:8]}:"
    settings = RedisEventBusSettings(
        url=_redis_url(redis_container), channel_prefix=prefix
    )

    @Provider(singleton=True)
    def _settings() -> RedisEventBusSettings:
        return settings

    container = DIContainer()
    container.provide(_settings)
    await container.ainstall(RedisBulkheadConfiguration)

    resolved = await container.aget(RedisBulkhead)
    assert isinstance(resolved, RedisBulkhead)

    result = await resolved.call(_return_ok)
    assert result == "ok"

    await container.ashutdown()


async def _return_ok() -> str:
    return "ok"
