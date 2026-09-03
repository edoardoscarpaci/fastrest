"""
Plan 024 / Phase 1, Step 18 — Tier B `@Disposes` teardown for
`NatsDLQConfiguration` (binds `AbstractDeadLetterQueue`).

RED MODE: Tier B — `NatsDLQ` carries no `@PreDestroy`, so providify's
`UNREACHABLE_PRE_DESTROY` detector cannot see this leak. Expected failure:
`ashutdown()` never calls `NatsDLQ.stop()` for a provider-produced instance,
because no `@Disposes(AbstractDeadLetterQueue)` exists on
`NatsDLQConfiguration` yet.

No Docker required — NATS I/O is monkeypatched to no-ops.
"""

from __future__ import annotations

from typing import Any

from providify import DIContainer
from varco_core.event.dlq import AbstractDeadLetterQueue
from varco_nats.dlq import NatsDLQ, NatsDLQConfiguration


async def _noop_start(self: Any) -> None:
    self._nc = object()


async def test_nats_dlq_stop_runs_on_ashutdown(monkeypatch) -> None:
    monkeypatch.setattr(NatsDLQ, "start", _noop_start)
    calls: list[str] = []

    async def _record_stop(self: Any) -> None:
        calls.append("stopped")
        self._nc = None

    monkeypatch.setattr(NatsDLQ, "stop", _record_stop)

    container = DIContainer()
    await container.ainstall(NatsDLQConfiguration)
    await container.aget(AbstractDeadLetterQueue)
    await container.ashutdown()

    assert calls == ["stopped"]
