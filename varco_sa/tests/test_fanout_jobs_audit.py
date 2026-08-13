"""
Failing tests for per-tenant job/audit fan-out children (Plan 007, Phase 8,
step 4). Unit-level with fakes; full Postgres coverage lives in
test_fanout_integration.py under -m integration.
"""

from __future__ import annotations


async def test_job_enqueued_in_tenant_a_never_claimed_by_tenant_b_poller() -> None:
    from varco_core.tenancy.fanout import TenantFanoutSupervisor

    claimed_by: dict[str, list[str]] = {"tenant-a": [], "tenant-b": []}

    class _FakeJobPollerChild:
        def __init__(self, tenant_id: str, store) -> None:
            self._tenant_id = tenant_id
            self._store = store

        async def start(self) -> None:
            for job_id in list(self._store.get(self._tenant_id, [])):
                claimed_by[self._tenant_id].append(job_id)

        async def stop(self) -> None:
            pass

    store = {"tenant-a": ["job-1"], "tenant-b": []}

    supervisor = TenantFanoutSupervisor(
        child_factory=lambda tid: _FakeJobPollerChild(tid, store), max_entries=50
    )
    await supervisor.on_tenant_activated("tenant-a")
    await supervisor.on_tenant_activated("tenant-b")
    await supervisor.start()

    assert claimed_by["tenant-b"] == []
    await supervisor.stop()
