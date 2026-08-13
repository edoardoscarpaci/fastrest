"""
varco_fastapi.tenancy
========================
FastAPI wiring for the multitenancy contracts declared in
``varco_core.tenancy`` (Plan 007).

Imports **only** ``varco_core.tenancy`` — never ``varco_sa``,
``varco_beanie``, ``sqlalchemy``, or ``pymongo`` (enforced by
``test_tenancy_import_guard.py``). Same seam rule as
``varco_core.migration`` -> ``varco_fastapi.migrate``.

Landed so far: Phase 5's REST admin surface (``build_tenant_router``,
``mount_tenant_admin``) and Phase 10's lifecycle wiring
(``TenancyLifecycle``; ``TenantResolutionMiddleware`` lives in
``varco_fastapi.middleware.tenant_resolution``, mirroring every other
middleware in this package). See
``plans/007-multitenancy-isolation-strategies.md`` for the full surface.
"""

from __future__ import annotations

from varco_fastapi.tenancy.lifecycle import TenancyLifecycle
from varco_fastapi.tenancy.mount import mount_tenant_admin
from varco_fastapi.tenancy.router import build_tenant_router

__all__ = ["build_tenant_router", "mount_tenant_admin", "TenancyLifecycle"]
