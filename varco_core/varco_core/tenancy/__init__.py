"""
varco_core.tenancy
===================
Backend-agnostic multitenancy contracts (Plan 007) — isolation strategy
selection, the tenant catalog, the bounded per-tenant resource pool, the
dynamic UoW provider, and the tenant provisioner ABC.

Zero third-party dependencies. Backend packages (``varco_sa``,
``varco_beanie``) and ``varco_fastapi`` build on these contracts; this
package never imports ``sqlalchemy``, ``pymongo``, or ``beanie``.

See ``plans/007-multitenancy-isolation-strategies.md`` for the full design.
"""

from __future__ import annotations

from varco_core.tenancy.cache_key import tenancy_cache_key
from varco_core.tenancy.catalog import (
    AbstractTenantCatalog,
    StaticTenantCatalog,
    TenantDescriptor,
    TenantIsolationError,
    TenantNotFoundError,
)
from varco_core.tenancy.global_scope import (
    GlobalScopeReadOnlyError,
    GlobalUoWProvider,
    is_global_entity,
)
from varco_core.tenancy.pool import TenantResourcePool
from varco_core.tenancy.provider import DynamicTenantUoWProvider
from varco_core.tenancy.provisioner import (
    AbstractTenantProvisioner,
    DestructiveOperationRefused,
    ExternalTenantProvisioner,
)
from varco_core.tenancy.scope_guard import validate_service_scope
from varco_core.tenancy.settings import (
    TenancySettings,
    TenantIsolation,
    TenantScope,
    TenantStatus,
)

__all__ = [
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
    "GlobalUoWProvider",
    "GlobalScopeReadOnlyError",
    "is_global_entity",
    "validate_service_scope",
    "tenancy_cache_key",
]
