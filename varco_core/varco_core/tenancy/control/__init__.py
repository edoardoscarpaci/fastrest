"""
varco_core.tenancy.control
============================
Tenant control plane — event-driven onboarding/offboarding, orchestration,
fleet broadcast, and fleet-readiness (Plan 007, Phase 5; Plan 008).
"""

from __future__ import annotations

from varco_core.tenancy.control.consumer import TenantProvisionConsumer
from varco_core.tenancy.control.events import (
    TenantCatalogChanged,
    TenantDeprovisionRequested,
    TenantNodeReady,
    TenantProvisionRequested,
)
from varco_core.tenancy.control.readiness import (
    TenantReadiness,
    TenantReadinessCoordinator,
)
from varco_core.tenancy.control.service import TenantControlService

__all__ = [
    "TenantProvisionRequested",
    "TenantDeprovisionRequested",
    "TenantCatalogChanged",
    "TenantNodeReady",
    "TenantControlService",
    "TenantProvisionConsumer",
    "TenantReadinessCoordinator",
    "TenantReadiness",
]
