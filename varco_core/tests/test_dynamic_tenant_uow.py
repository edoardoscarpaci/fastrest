"""
Failing tests for varco_core.tenancy.provider (Plan 007, Phase 1, step 7).

DynamicTenantUoWProvider — routes make_uow() through a TenantResourcePool
keyed by the ambient tenant_context(), reusing varco_core.service.tenant's
current_tenant()/tenant_context() unchanged.
"""

from __future__ import annotations

import pytest


def _make_provider(pool=None):
    from varco_core.tenancy.provider import DynamicTenantUoWProvider

    if pool is None:

        class _StubPool:
            async def ensure(self, tenant_id: str):
                raise AssertionError("ensure() should not be called implicitly")

            def peek(self, tenant_id: str):
                return None

        pool = _StubPool()

    return DynamicTenantUoWProvider(pool=pool)


def test_make_uow_outside_tenant_context_raises_runtime_error() -> None:
    provider = _make_provider()

    with pytest.raises(RuntimeError) as exc:
        provider.make_uow()

    # Message shape mirrors TenantUoWProvider's existing wording.
    assert "tenant_context" in str(exc.value)


def test_make_uow_active_tenant_not_ensured_raises_runtime_error_naming_ensure() -> (
    None
):
    from varco_core.service.tenant import tenant_context

    provider = _make_provider()

    with tenant_context("acme"):
        with pytest.raises(RuntimeError) as exc:
            provider.make_uow()

    assert "ensure(" in str(exc.value) or "ensure()" in str(exc.value)


async def test_make_uow_after_ensure_returns_that_tenants_provider() -> None:
    from varco_core.service.tenant import tenant_context

    class _FakeUoWProvider:
        def make_uow(self):
            return "uow-for-acme"

    class _StubPool:
        def __init__(self) -> None:
            self._cache: dict[str, object] = {}

        async def ensure(self, tenant_id: str):
            provider = _FakeUoWProvider()
            self._cache[tenant_id] = provider
            return provider

        def peek(self, tenant_id: str):
            return self._cache.get(tenant_id)

    pool = _StubPool()
    provider = _make_provider(pool=pool)

    await pool.ensure("acme")

    with tenant_context("acme"):
        uow = provider.make_uow()

    assert uow == "uow-for-acme"


async def test_two_tenants_resolve_to_two_distinct_providers() -> None:
    from varco_core.service.tenant import tenant_context

    class _StubPool:
        def __init__(self) -> None:
            self._cache: dict[str, object] = {}

        async def ensure(self, tenant_id: str):
            provider = object()
            self._cache[tenant_id] = provider
            return provider

        def peek(self, tenant_id: str):
            return self._cache.get(tenant_id)

    pool = _StubPool()
    provider = _make_provider(pool=pool)

    await pool.ensure("acme")
    await pool.ensure("globex")

    with tenant_context("acme"):
        uow_acme = provider.make_uow()
    with tenant_context("globex"):
        uow_globex = provider.make_uow()

    assert uow_acme is not uow_globex
