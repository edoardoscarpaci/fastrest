"""
Failing test: supervisor stop/start order relative to pool.aclose() (Plan
007, Phase 8, step 5).
"""

from __future__ import annotations


async def test_supervisor_stopped_before_pool_aclose() -> None:
    from varco_fastapi.tenancy.lifecycle import TenancyLifecycle

    call_order: list[str] = []

    class _FakeSupervisor:
        async def start(self) -> None:
            call_order.append("supervisor.start")

        async def stop(self) -> None:
            call_order.append("supervisor.stop")

    class _FakePool:
        async def start_sweeper(self) -> None:
            call_order.append("pool.start_sweeper")

        async def aclose(self) -> None:
            call_order.append("pool.aclose")

    lifecycle = TenancyLifecycle(pool=_FakePool(), supervisor=_FakeSupervisor())

    await lifecycle.start()
    await lifecycle.stop()

    stop_index = call_order.index("supervisor.stop")
    aclose_index = call_order.index("pool.aclose")
    assert stop_index < aclose_index
