"""
Plan 024 / Phase 1, Step 18 — Tier B `@Disposes` teardown for
`RedisDLQConfiguration` (binds `AbstractDeadLetterQueue`).

RED MODE: Tier B — `RedisDLQ` carries no `@PreDestroy`, so providify's
`UNREACHABLE_PRE_DESTROY` detector cannot see this leak at all (§D-C2 edge
case). Only this behavioural round-trip test can catch a regression here.
Expected failure: `ashutdown()` never calls `RedisDLQ.disconnect()` for a
provider-produced instance, because no `@Disposes(AbstractDeadLetterQueue)`
exists on `RedisDLQConfiguration` yet.

No Docker required — Redis I/O is monkeypatched to no-ops.
"""

from __future__ import annotations

from typing import Any

from providify import DIContainer
from varco_core.event.dlq import AbstractDeadLetterQueue
from varco_redis.dlq import RedisDLQ, RedisDLQConfiguration


async def _noop_connect(self: Any) -> None:
    self._redis = object()


async def test_redis_dlq_disconnect_runs_on_ashutdown(monkeypatch) -> None:
    monkeypatch.setattr(RedisDLQ, "connect", _noop_connect)
    calls: list[str] = []

    async def _record_disconnect(self: Any) -> None:
        calls.append("disconnected")
        self._redis = None

    monkeypatch.setattr(RedisDLQ, "disconnect", _record_disconnect)

    container = DIContainer()
    await container.ainstall(RedisDLQConfiguration)
    await container.aget(AbstractDeadLetterQueue)
    await container.ashutdown()

    assert calls == ["disconnected"]
