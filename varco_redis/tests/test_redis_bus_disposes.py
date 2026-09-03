"""
Plan 024 / Phase 1, Step 17 — `@Disposes` teardown for
`RedisEventBusSelectorConfiguration` (binds `AbstractEventBus`).

RED MODE: `RedisEventBusSelectorConfiguration` has no `@Disposes(AbstractEventBus)`
method yet — `RedisEventBus.stop()` is only reachable via `@PreDestroy`, never
invoked on a `@Provider`-produced instance. Expected failures:

- The `validate()` assertion (Tier A — this site's produced class DOES carry
  `@PreDestroy`, `bus.py:217`) fails with `AttributeError` because the pinned
  providify (Step 12 not yet applied) has no `IssueKind.UNREACHABLE_PRE_DESTROY`.
- The round-trip assertion fails because `ashutdown()` never calls
  `RedisEventBus.stop()` for a provider-produced instance.
- The double-stop-safety assertion fails for the identical reason — `stop()`
  is never called by `ashutdown()` at all, so it cannot be proven idempotent
  via this path (a manual `bus.stop()` followed by `ashutdown()` should not
  raise once the disposer exists).

No Docker required — Redis I/O is monkeypatched to no-ops.
"""

from __future__ import annotations

from typing import Any

import pytest
from providify import DIContainer, IssueKind, Provider
from varco_core.event.base import AbstractEventBus
from varco_redis.bus import RedisEventBus, RedisEventBusSelectorConfiguration
from varco_redis.config import RedisEventBusSettings


async def _noop_start(self: Any) -> None:
    self._redis = object()


@Provider(singleton=True)
def _redis_event_bus_settings() -> RedisEventBusSettings:
    # RedisEventBusSettings is a pydantic BaseSettings, registered elsewhere
    # by container.scan("varco_redis") in production — provide it directly
    # since we only install the selector configuration here.
    return RedisEventBusSettings(url="redis://localhost:6379/0")


def _make_recorder() -> tuple[list[str], Any]:
    calls: list[str] = []

    async def _record_stop(self: Any) -> None:
        calls.append("stopped")
        self._redis = None

    return calls, _record_stop


async def test_redis_bus_configuration_reports_zero_unreachable_pre_destroy_issues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(RedisEventBus, "start", _noop_start)
    _, record_stop = _make_recorder()
    monkeypatch.setattr(RedisEventBus, "stop", record_stop)

    container = DIContainer()
    container.provide(_redis_event_bus_settings)
    await container.ainstall(RedisEventBusSelectorConfiguration)
    await container.aget(AbstractEventBus)

    report = container.validate(raise_on_error=False)
    # Force the attribute lookup unconditionally — see the redis-cache
    # sibling test for why a comprehension guard would vacuously pass here.
    target_kind = IssueKind.UNREACHABLE_PRE_DESTROY
    unreachable = [i for i in report.issues if i.kind == target_kind]
    assert unreachable == []

    await container.ashutdown()


async def test_redis_bus_stop_runs_on_ashutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(RedisEventBus, "start", _noop_start)
    calls, record_stop = _make_recorder()
    monkeypatch.setattr(RedisEventBus, "stop", record_stop)

    container = DIContainer()
    container.provide(_redis_event_bus_settings)
    await container.ainstall(RedisEventBusSelectorConfiguration)
    await container.aget(AbstractEventBus)
    await container.ashutdown()

    assert calls == ["stopped"]


async def test_redis_bus_stop_is_idempotent_across_manual_stop_and_ashutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mirrors the varco_fastapi lifespan shape: something stops the bus
    # explicitly, then container.ashutdown() runs the disposer too — must
    # not raise (bus.py:217-219 documents stop() as idempotent).
    monkeypatch.setattr(RedisEventBus, "start", _noop_start)
    calls, record_stop = _make_recorder()
    monkeypatch.setattr(RedisEventBus, "stop", record_stop)

    container = DIContainer()
    container.provide(_redis_event_bus_settings)
    await container.ainstall(RedisEventBusSelectorConfiguration)
    bus = await container.aget(AbstractEventBus)

    await bus.stop()  # explicit stop, e.g. from a FastAPI lifespan
    await container.ashutdown()  # disposer must not blow up on a second stop()

    assert calls == ["stopped", "stopped"]
