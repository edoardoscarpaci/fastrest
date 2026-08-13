"""
varco_beanie.tenancy
======================
Beanie/MongoDB backend for the multitenancy contracts declared in
``varco_core.tenancy`` (Plan 007).

Landed so far: Phase 4's durable tenant catalog and Phase 7's
database-per-tenant primitives (``BeanieTenantBinding``,
``BeanieTenantPool``, ``BeanieDatabaseProvisioner``). See
``plans/007-multitenancy-isolation-strategies.md`` for the full surface.
"""

from __future__ import annotations

from varco_beanie.tenancy.binding import BeanieTenantBinding, build_tenant_binding
from varco_beanie.tenancy.catalog import BeanieTenantCatalog
from varco_beanie.tenancy.pool import BeanieTenantPool
from varco_beanie.tenancy.provisioner import BeanieDatabaseProvisioner

__all__ = [
    "BeanieTenantCatalog",
    "BeanieTenantBinding",
    "build_tenant_binding",
    "BeanieTenantPool",
    "BeanieDatabaseProvisioner",
]
