"""
Failing tests for varco_core.tenancy.cache_key (Plan 007, Phase 2, step 9).

tenancy_cache_key() — namespaces iff the entity is TENANT-scoped; fails
closed outside a tenant context for TENANT-scoped entities.
"""

from __future__ import annotations

import pytest


def test_tenant_scoped_key_is_namespaced() -> None:
    from varco_core.service.tenant import tenant_context
    from varco_core.tenancy.cache_key import tenancy_cache_key

    class TenantEntity:
        pass

    with tenant_context("acme"):
        key = tenancy_cache_key(TenantEntity, "42")

    assert "acme" in key
    assert "42" in key


def test_global_scoped_key_is_not_namespaced_and_identical_across_tenants() -> None:
    from varco_core.service.tenant import tenant_context
    from varco_core.tenancy.cache_key import tenancy_cache_key

    class GlobalEntity:
        class Meta:
            tenant_scope = "global"

    with tenant_context("acme"):
        key_acme = tenancy_cache_key(GlobalEntity, "42")
    with tenant_context("globex"):
        key_globex = tenancy_cache_key(GlobalEntity, "42")

    assert key_acme == key_globex
    assert "acme" not in key_acme
    assert "globex" not in key_acme


def test_tenant_scoped_key_outside_tenant_context_raises() -> None:
    from varco_core.tenancy.cache_key import tenancy_cache_key

    class TenantEntity:
        pass

    with pytest.raises(RuntimeError):
        tenancy_cache_key(TenantEntity, "42")
