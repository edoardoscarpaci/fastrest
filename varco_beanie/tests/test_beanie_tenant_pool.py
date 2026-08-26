"""
Failing tests for varco_beanie.tenancy.pool.BeanieTenantPool (Plan 007,
Phase 7, step 3).
"""

from __future__ import annotations


async def test_eviction_does_not_close_a_shared_client() -> None:
    from varco_beanie.tenancy.pool import BeanieTenantPool

    closed = []

    class _FakeSharedClient:
        def close(self):
            closed.append("shared")

    shared_client = _FakeSharedClient()
    pool = BeanieTenantPool(client=shared_client, client_per_tenant=False, max_entries=1)

    await pool.ensure("acme")
    await pool.ensure("globex")  # evicts "acme"'s binding, must not close shared_client

    assert closed == []


async def test_client_per_tenant_mode_closes_on_eviction() -> None:
    from varco_beanie.tenancy.pool import BeanieTenantPool

    closed = []

    def _client_factory(tenant_id: str):
        client = type("C", (), {"close": lambda self: closed.append(tenant_id)})()
        return client

    pool = BeanieTenantPool(client_factory=_client_factory, client_per_tenant=True, max_entries=1)

    await pool.ensure("acme")
    await pool.ensure("globex")

    assert "acme" in closed


async def test_clone_count_bounded_by_max_entries() -> None:
    from varco_beanie.tenancy.pool import BeanieTenantPool

    class _FakeSharedClient:
        def close(self):
            pass

    pool = BeanieTenantPool(client=_FakeSharedClient(), client_per_tenant=False, max_entries=2)

    await pool.ensure("a")
    await pool.ensure("b")
    await pool.ensure("c")

    assert pool.active_clone_count() <= 2
