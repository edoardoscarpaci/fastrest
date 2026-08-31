"""
Plan 022 / Phase 4 (RL-8a), Step 21 — repurposed as regression tests.

Step 21's original budget was "fix any non-idempotent ``stop()``".  The Step-6
measurement (``design/api-freeze-and-standards/measurements/predestroy-vs-lifespan.md``
Part 2) read all ten implementations and found **10 idempotent / 0 non-idempotent**,
so there is nothing to fix.  The measurement's own closing caveat asks for the
budget to be re-spent here instead: it is a read of the current source, not a
test, and §D-8a2(b) is about to make the property **load-bearing** — under the
new ``shutdown=`` hook every component reachable by both ``_stop_all()`` and
``container.ashutdown()`` gets ``stop()`` called twice.

These tests pin the property for the varco_redis half of the table (#5, #6, #7,
#8).  They require no broker: ``RedisChannelManager.start()`` only flips a flag
and ``RedisCache.start()`` builds a lazy ``redis.asyncio`` client that connects
on first command, so start→stop→stop completes offline.  The two event buses do
connect in ``start()``, so only their never-started double-``stop()`` path is
covered here; the started path belongs to the integration suite.

NOTE: these may legitimately pass on arrival — they are regressions guarding an
already-true property, not red tests driving a change.

Thread safety:  N/A (unit test)
Async safety:   ✅ every stop() is awaited.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from varco_redis.bus import RedisEventBus, RedisEventBusSettings
from varco_redis.cache import RedisCache
from varco_redis.channel import RedisChannelManager
from varco_redis.streams import RedisStreamEventBus


def _redis_channel_manager() -> RedisChannelManager:
    return RedisChannelManager(RedisEventBusSettings())


def _redis_cache() -> RedisCache:
    return RedisCache()


def _redis_event_bus() -> RedisEventBus:
    return RedisEventBus(config=RedisEventBusSettings())


def _redis_stream_event_bus() -> RedisStreamEventBus:
    return RedisStreamEventBus(config=RedisEventBusSettings())


NEVER_STARTED: list[tuple[str, Callable[[], Any]]] = [
    ("RedisEventBus", _redis_event_bus),
    ("RedisStreamEventBus", _redis_stream_event_bus),
    ("RedisChannelManager", _redis_channel_manager),
    ("RedisCache", _redis_cache),
]

STARTABLE_OFFLINE: list[tuple[str, Callable[[], Any]]] = [
    ("RedisChannelManager", _redis_channel_manager),
    ("RedisCache", _redis_cache),
]


@pytest.mark.parametrize(("name", "factory"), NEVER_STARTED, ids=[n for n, _ in NEVER_STARTED])
async def test_stop_twice_on_never_started_component_is_a_noop(
    name: str, factory: Callable[[], Any]
) -> None:
    # A container sweep can reach a singleton the lifespan never started.
    component = factory()

    await component.stop()
    await component.stop()  # must not raise


@pytest.mark.parametrize(
    ("name", "factory"), STARTABLE_OFFLINE, ids=[n for n, _ in STARTABLE_OFFLINE]
)
async def test_stop_twice_after_start_is_a_noop(name: str, factory: Callable[[], Any]) -> None:
    # The §D-8a2(b) double-stop path itself: _stop_all() then container.ashutdown().
    component = factory()
    await component.start()

    await component.stop()
    await component.stop()  # must not raise


async def test_redis_cache_second_stop_does_not_reclose_the_client() -> None:
    """
    The line that makes #8 idempotent is ``if self._redis is None: return``
    (cache.py:216) paired with ``self._redis = None`` (:221).  Assert the
    sentinel directly — a future refactor that closes the client without
    clearing it would double-close under the new double-stop model.
    """
    cache = RedisCache()
    await cache.start()

    await cache.stop()

    assert cache._redis is None  # noqa: SLF001 — the sentinel IS the contract

    await cache.stop()

    assert cache._redis is None  # noqa: SLF001
