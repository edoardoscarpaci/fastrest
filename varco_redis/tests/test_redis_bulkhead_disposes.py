"""
Plan 024 / Phase 1, Step 18 — Tier B `@Disposes` teardown for
`RedisBulkheadConfiguration` (binds `RedisBulkhead`).

RED MODE: Tier B — `RedisBulkhead` carries no `@PreDestroy`, so providify's
`UNREACHABLE_PRE_DESTROY` detector cannot see this leak. The module docstring
at `varco_redis/varco_redis/bulkhead.py:457-459` currently (falsely) claims
"disconnected automatically by `await container.ashutdown()`". Expected
failure: `ashutdown()` never calls `RedisBulkhead.disconnect()` for a
provider-produced instance, because no `@Disposes(RedisBulkhead)` exists on
`RedisBulkheadConfiguration` yet.

No Docker required — Redis I/O is monkeypatched to no-ops.
"""

from __future__ import annotations

from typing import Any

from providify import DIContainer, Provider
from varco_redis.bulkhead import RedisBulkhead, RedisBulkheadConfiguration
from varco_redis.config import RedisEventBusSettings


async def _noop_connect(self: Any) -> None:
    self._redis = object()


@Provider(singleton=True)
def _redis_event_bus_settings() -> RedisEventBusSettings:
    return RedisEventBusSettings(url="redis://localhost:6379/0")


async def test_redis_bulkhead_disconnect_runs_on_ashutdown(monkeypatch) -> None:
    monkeypatch.setattr(RedisBulkhead, "connect", _noop_connect)
    calls: list[str] = []

    async def _record_disconnect(self: Any) -> None:
        calls.append("disconnected")
        self._redis = None

    monkeypatch.setattr(RedisBulkhead, "disconnect", _record_disconnect)

    container = DIContainer()
    container.provide(_redis_event_bus_settings)
    await container.ainstall(RedisBulkheadConfiguration)
    await container.aget(RedisBulkhead)
    await container.ashutdown()

    assert calls == ["disconnected"]
