"""
varco_core.tenancy.scope_guard
================================
``validate_service_scope()`` — catches the ``TenantAwareService`` × ``GLOBAL``
entity mismatch in both directions (Plan 007, Phase 2, step 7-8).

DESIGN: no ``GlobalScopedService`` marker mixin
    A marker mixin with an empty body is noise; instead the mistake is
    guarded structurally at startup via this function. See the plan's
    "Service layer" subsection under the global/shared scope design.
"""

from __future__ import annotations

import logging

from varco_core.tenancy.catalog import TenantIsolationError
from varco_core.tenancy.settings import TenantScope

logger = logging.getLogger(__name__)


def validate_service_scope(
    service_cls: type, *, entity_cls: type, tenant_scope: TenantScope
) -> None:
    """
    Validate that a service's tenancy mixin matches its entity's declared scope.

    Args:
        service_cls:  The concrete ``AsyncService`` subclass being wired.
        entity_cls:   The domain entity the service serves.
        tenant_scope: The entity's declared ``TenantScope``.

    Raises:
        TenantIsolationError: ``TenantAwareService`` is in ``service_cls``'s
            MRO while ``entity_cls`` is ``GLOBAL`` — ``_scoped_params``
            would filter on a non-existent ``tenant_id``.

    Edge cases:
        - The reverse mismatch (a ``TENANT``-scoped entity served by a
          service with no tenant filtering) only WARNs — under ``SHARED``
          this is a real but non-fatal risk (row-level isolation depends on
          every service remembering to mix in ``TenantAwareService``);
          under ``SCHEMA``/``DATABASE`` it is often fine (isolation is
          structural).
        - Correct pairings in either direction are silent.
    """
    from varco_core.service.tenant import TenantAwareService

    is_tenant_aware = issubclass(service_cls, TenantAwareService)

    if tenant_scope == TenantScope.GLOBAL and is_tenant_aware:
        raise TenantIsolationError(
            f"{service_cls.__name__} mixes in TenantAwareService but serves "
            f"{entity_cls.__name__}, which is declared TenantScope.GLOBAL. "
            "TenantAwareService._scoped_params would filter on a "
            "non-existent tenant_id field. Drop the TenantAwareService "
            f"mixin from {service_cls.__name__}."
        )

    if tenant_scope == TenantScope.TENANT and not is_tenant_aware:
        logger.warning(
            "%s serves %s (TenantScope.TENANT) without mixing in "
            "TenantAwareService. Under TenantIsolation.SHARED this means "
            "no row-level tenant filtering is applied by this service.",
            service_cls.__name__,
            entity_cls.__name__,
        )
