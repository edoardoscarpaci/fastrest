"""
varco_core.tenancy.control.events
====================================
``TenantProvisionRequested`` / ``TenantDeprovisionRequested`` /
``TenantCatalogChanged`` / ``TenantNodeReady`` — the tenant control plane's
event vocabulary (Plan 007, Phase 5, step 3; extended by Plan 008, Phase 2,
step 4), all on the ``"varco.tenancy"`` channel.

RD-13's command/fact DAG classifies every event here:

- **Commands** (``TenantProvisionRequested``, ``TenantDeprovisionRequested``)
  — may only be produced by ``TenantControlService.request_provision()`` /
  ``request_deprovision()``. No handler may emit a command.
- **Facts** (``TenantCatalogChanged``, ``TenantNodeReady``) — produced by
  command handlers as a side-effect; consumed by cache invalidation
  (``CachedTenantCatalog``) and fleet readiness (``TenantReadinessCoordinator``)
  respectively. Facts may produce nothing further.
"""

from __future__ import annotations

from varco_core.event.base import Event

CHANNEL_TENANCY = "varco.tenancy"


class TenantProvisionRequested(Event, frozen=True):
    """
    Command: requests that ``tenant_id`` be provisioned (event-driven
    onboarding, RD-1).

    Args:
        tenant_id: The tenant to provision.
        origin:    ``TenantControlService.node_id`` of the broadcaster, or
                    ``None`` for an externally-published command (RD-15).
                    A consumer whose own ``node_id`` matches ``origin``
                    skips the event — it already acted synchronously via
                    ``provision()`` before broadcasting.
    """

    __event_type__ = "varco.tenancy.provision_requested"

    tenant_id: str
    origin: str | None = None


class TenantDeprovisionRequested(Event, frozen=True):
    """
    Command: requests that ``tenant_id`` be deprovisioned.

    ``confirm`` mirrors ``AbstractTenantProvisioner.deprovision``'s
    ``confirm_destroy`` gate — an event without it set is rejected (and,
    with a DLQ wired, DLQ'd) rather than silently executed.

    Args:
        tenant_id: The tenant to deprovision.
        confirm:   Destructive-operation confirmation gate.
        origin:    Same RD-15 provenance semantics as
                    ``TenantProvisionRequested.origin``.
    """

    __event_type__ = "varco.tenancy.deprovision_requested"

    tenant_id: str
    confirm: bool = False
    origin: str | None = None


class TenantCatalogChanged(Event, frozen=True):
    """
    Fact: fired whenever a tenant's catalog entry changes (status
    transition, add, remove). Drives cross-pod ``CachedTenantCatalog``
    invalidation. Produces nothing further (RD-13).
    """

    __event_type__ = "varco.tenancy.catalog_changed"

    tenant_id: str


class TenantNodeReady(Event, frozen=True):
    """
    Fact: one node/store has finished provisioning ``tenant_id`` locally.

    RD-19: ``TenantCatalogChanged`` cannot serve this purpose — it is a
    cache-invalidation fact about one shared catalog row, and N nodes
    writing that row emit N *identical* facts about the *same* row.
    Readiness needs a per-node/per-store signal, which is this event.
    Produces nothing further except, eventually,
    ``TenantReadinessCoordinator.on_node_ready`` calling
    ``TenantControlService.mark_active()`` (itself only emitting the fact
    ``TenantCatalogChanged`` — never a command, RD-13).

    Args:
        tenant_id: The tenant that was locally provisioned.
        node_id:   The reporting node's ``TenantControlService.node_id``.
        store_id:  The reporting node's ``TenantControlService.store_id``
                    (``VARCO_TENANCY_STORE_ID``) — the unit
                    ``TenantReadinessCoordinator`` counts (RD-17: a store,
                    not a pod — ten pods of one service share one store).
    """

    __event_type__ = "varco.tenancy.node_ready"

    tenant_id: str
    node_id: str
    store_id: str
