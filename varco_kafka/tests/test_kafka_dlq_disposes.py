"""
Plan 024 / Phase 1, Step 18 — Tier B `@Disposes` teardown for
`KafkaDLQConfiguration` (binds `AbstractDeadLetterQueue`).

RED MODE: Tier B — `KafkaDLQ` carries no `@PreDestroy`, so providify's
`UNREACHABLE_PRE_DESTROY` detector cannot see this leak. Expected failure:
`ashutdown()` never calls `KafkaDLQ.stop()` for a provider-produced
instance, because no `@Disposes(AbstractDeadLetterQueue)` exists on
`KafkaDLQConfiguration` yet.

No Docker required — Kafka I/O is monkeypatched to no-ops.
"""

from __future__ import annotations

from typing import Any

from providify import DIContainer
from varco_core.event.dlq import AbstractDeadLetterQueue
from varco_kafka.dlq import KafkaDLQ, KafkaDLQConfiguration


async def _noop_start(self: Any) -> None:
    self._producer = object()


async def test_kafka_dlq_stop_runs_on_ashutdown(monkeypatch) -> None:
    monkeypatch.setattr(KafkaDLQ, "start", _noop_start)
    calls: list[str] = []

    async def _record_stop(self: Any) -> None:
        calls.append("stopped")
        self._producer = None

    monkeypatch.setattr(KafkaDLQ, "stop", _record_stop)

    container = DIContainer()
    await container.ainstall(KafkaDLQConfiguration)
    await container.aget(AbstractDeadLetterQueue)
    await container.ashutdown()

    assert calls == ["stopped"]
