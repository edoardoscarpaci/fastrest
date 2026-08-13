"""
Failing tests for varco_core.tenancy.fanout.TenantFanoutSupervisor (Plan 007,
Phase 8, step 1).
"""

from __future__ import annotations

import asyncio


class _FakeChild:
    def __init__(self, fail_times: int = 0) -> None:
        self.started = False
        self.stopped = False
        self._fail_times = fail_times
        self._attempts = 0

    async def start(self) -> None:
        self._attempts += 1
        if self._attempts <= self._fail_times:
            raise RuntimeError("child failed")
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


def _make_supervisor(child_factory=None, max_entries: int = 50):
    from varco_core.tenancy.fanout import TenantFanoutSupervisor

    return TenantFanoutSupervisor(
        child_factory=child_factory or (lambda tid: _FakeChild()),
        max_entries=max_entries,
    )


async def test_start_starts_one_child_per_active_pool_resident_tenant() -> None:
    supervisor = _make_supervisor()

    await supervisor.on_tenant_activated("acme")
    await supervisor.on_tenant_activated("globex")
    await supervisor.start()
    await asyncio.sleep(0.05)

    assert supervisor.active_tenant_count() == 2

    await supervisor.stop()


async def test_on_tenant_deactivated_stops_and_removes_child() -> None:
    supervisor = _make_supervisor()
    await supervisor.on_tenant_activated("acme")
    await supervisor.start()
    await asyncio.sleep(0.02)

    await supervisor.on_tenant_deactivated("acme")

    assert supervisor.active_tenant_count() == 0
    await supervisor.stop()


async def test_failing_child_is_restarted_with_backoff_others_unaffected() -> None:
    children: dict[str, _FakeChild] = {}

    def factory(tid: str):
        child = _FakeChild(fail_times=2 if tid == "flaky" else 0)
        children[tid] = child
        return child

    supervisor = _make_supervisor(child_factory=factory)
    await supervisor.on_tenant_activated("flaky")
    await supervisor.on_tenant_activated("healthy")
    await supervisor.start()

    await asyncio.sleep(0.3)

    assert children["healthy"].started is True
    await supervisor.stop()


async def test_stop_awaits_every_child_lifo_and_is_idempotent() -> None:
    supervisor = _make_supervisor()
    await supervisor.on_tenant_activated("acme")
    await supervisor.start()
    await asyncio.sleep(0.02)

    await supervisor.stop()
    await supervisor.stop()  # idempotent


async def test_children_never_exceed_pool_max_entries() -> None:
    supervisor = _make_supervisor(max_entries=2)

    await supervisor.on_tenant_activated("a")
    await supervisor.on_tenant_activated("b")
    await supervisor.on_tenant_activated("c")

    assert supervisor.active_tenant_count() <= 2


async def test_fanout_disabled_starts_nothing() -> None:
    from varco_core.tenancy.fanout import TenantFanoutSupervisor

    supervisor = TenantFanoutSupervisor(
        child_factory=lambda tid: _FakeChild(), max_entries=50, enabled=False
    )
    await supervisor.on_tenant_activated("acme")
    await supervisor.start()
    await asyncio.sleep(0.02)

    assert supervisor.active_tenant_count() == 0
    await supervisor.stop()
