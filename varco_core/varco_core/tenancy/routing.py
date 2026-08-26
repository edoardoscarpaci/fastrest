"""
varco_core.tenancy.routing
=============================
Tenant status -> routing-decision mapping (Plan 007, Phase 4, step 8).

| status | in list_tenants() default | request routing | migration fan-out |
|---|---|---|---|
| pending | no | rejected (503) | no |
| active | yes | routed normally | yes |
| suspended | no | rejected (403) | yes (kept current) |
| deprovisioning | no | rejected (410) | no |
| deleted | no | rejected (404) | no |

Routing consults the catalog **before** ``pool.ensure()`` — a non-``active``
tenant never causes an engine/binding to be created.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from varco_core.tenancy.catalog import TenantNotFoundError
from varco_core.tenancy.settings import TenantStatus

if TYPE_CHECKING:
    from varco_core.tenancy.catalog import AbstractTenantCatalog, TenantDescriptor

_STATUS_HTTP_CODES: dict[str, int] = {
    TenantStatus.PENDING.value: 503,
    TenantStatus.ACTIVE.value: 200,
    TenantStatus.SUSPENDED.value: 403,
    TenantStatus.DEPROVISIONING.value: 410,
    TenantStatus.DELETED.value: 404,
}


class TenantRoutingRejected(Exception):
    """Raised by ``route_request`` when a non-``active`` tenant is targeted."""

    def __init__(self, tenant_id: str, http_status: int, reason: str) -> None:
        super().__init__(f"Tenant {tenant_id!r} routing rejected ({http_status}): {reason}")
        self.tenant_id = tenant_id
        self.http_status = http_status
        self.reason = reason


@dataclass(frozen=True)
class RoutingDecision:
    """Result of ``routing_decision_for_status`` — an HTTP status + reason."""

    http_status: int
    reason: str
    routable: bool


def routing_decision_for_status(status: str) -> RoutingDecision:
    """
    Map a tenant status string to a ``RoutingDecision``.

    Args:
        status: A ``TenantStatus`` value, or any unrecognised string (e.g.
                for an unknown tenant) — treated the same as ``deleted``.

    Returns:
        ``RoutingDecision(http_status=200, reason="active", routable=True)``
        for ``active``; a rejecting decision for every other status,
        including unknown values (404 — never a default-DB fallback).
    """
    code = _STATUS_HTTP_CODES.get(status, 404)
    if status == TenantStatus.ACTIVE.value:
        return RoutingDecision(http_status=200, reason="active", routable=True)
    reason = {
        TenantStatus.PENDING.value: "provisioning in flight",
        TenantStatus.SUSPENDED.value: "tenant suspended",
        TenantStatus.DEPROVISIONING.value: "tenant is being deprovisioned",
        TenantStatus.DELETED.value: "tenant deleted",
    }.get(status, "unknown tenant")
    return RoutingDecision(http_status=code, reason=reason, routable=False)


async def route_request(*, catalog: AbstractTenantCatalog, pool: Any, tenant_id: str) -> Any:
    """
    Resolve a tenant's pool-backed resource for an incoming request.

    Consults the catalog status **before** calling ``pool.ensure()`` — a
    non-``active`` tenant never causes an engine/binding to be created.

    Raises:
        TenantRoutingRejected: The tenant is not ``active`` (per the
            documented status table).
    """
    try:
        descriptor: TenantDescriptor = await catalog.get(tenant_id)
    except TenantNotFoundError:
        decision = routing_decision_for_status(TenantStatus.DELETED.value)
        raise TenantRoutingRejected(tenant_id, decision.http_status, "unknown tenant") from None

    decision = routing_decision_for_status(descriptor.status.value)
    if not decision.routable:
        raise TenantRoutingRejected(tenant_id, decision.http_status, decision.reason)

    return await pool.ensure(tenant_id)
