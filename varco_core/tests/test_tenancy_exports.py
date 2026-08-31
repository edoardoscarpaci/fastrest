"""
Failing tests for varco_core top-level re-exports of varco_core.tenancy
(Plan 007, Phase 1, step 10).

Every new tenancy name must resolve, from the top-level package, to the
exact same class object defined in varco_core.tenancy — and must not
collide with any pre-existing top-level name (the SchemaMigrationError/
SchemaMigrationPlan lesson from Plan 006).
"""

from __future__ import annotations

import pytest

_EXPECTED_EXPORTS = [
    "TenantIsolation",
    "TenantScope",
    "TenantStatus",
    "TenancySettings",
    "TenantDescriptor",
    "AbstractTenantCatalog",
    "StaticTenantCatalog",
    "TenantNotFoundError",
    "TenantIsolationError",
    "TenantResourcePool",
    "DynamicTenantUoWProvider",
    "AbstractTenantProvisioner",
    "ExternalTenantProvisioner",
    "DestructiveOperationRefused",
]


@pytest.mark.parametrize("name", _EXPECTED_EXPORTS)
def test_top_level_export_matches_tenancy_module_class(name: str) -> None:
    import varco_core.tenancy as tenancy_pkg

    import varco_core

    assert hasattr(varco_core, name), f"varco_core is missing top-level export {name!r}"
    assert getattr(varco_core, name) is getattr(tenancy_pkg, name)
