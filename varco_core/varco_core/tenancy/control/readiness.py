"""
varco_core.tenancy.control.readiness
=======================================
``TenantReadinessCoordinator`` — flips a tenant to ``ACTIVE`` only once
every declared store has reported ``TenantNodeReady`` (Plan 008, Phase 3).

Included rather than deferred: RD-16's ``catalog_authority=False`` has no
terminator without it, and shipping a mode that strands tenants in
``PENDING`` forever would repeat exactly the class of gap Plan 008 exists to
close. Kept deliberately minimal (RD-18 — no durable, restart-surviving
readiness state).

DESIGN: RD-17 — the expected fleet is a declared set of *stores*, not a
count of pods, and a timeout never activates
    ``expected_stores: frozenset[str]`` is static deploy-time config: ten
    ``orders`` pods provision the same ``orders`` database, so the first one
    makes ``orders`` ready and the other nine are idempotent no-ops. Pod
    count, which changes on every autoscale event, never enters the
    calculation. No default, no auto-discovery — constructing without
    ``expected_stores`` raises ``ValueError``.
    ✅ Under ``TenantIsolation.SHARED`` the expected set is a singleton and
       this coordinator is unnecessary (documented, not wired).
    ❌ Adding a service means updating ``expected_stores``; forgetting it
       activates tenants one store early — mitigated by logging the full
       expected/received set at every partial step and exposing
       ``GET …/readiness``.
    A timeout logs at ERROR, emits nothing, and leaves the tenant
    ``PENDING`` (503) — it never flips ``ACTIVE`` on a fleet known to be
    incomplete.

DESIGN: RD-18 — readiness state is in-memory; recovery is re-broadcast, not
persistence
    A coordinator restart loses partial readiness. Recovery is
    ``request_provision(tenant_id)`` again: every layer is idempotent, and
    each worker re-emits ``TenantNodeReady``. No eleventh framework table,
    no migration, no new durable contract.

DESIGN: RD-13 — this coordinator's fact handler never emits a command
    ``on_node_ready`` only ever calls ``TenantControlService.mark_active()``,
    which itself only emits the fact ``TenantCatalogChanged`` — never a
    command event (asserted by ``test_tenant_command_fact_dag.py``).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from varco_core.event.consumer import EventConsumer, listen
from varco_core.resilience import RetryPolicy
from varco_core.tenancy.control.events import CHANNEL_TENANCY, TenantNodeReady

if TYPE_CHECKING:
    from varco_core.event.base import AbstractEventBus, Event, Subscription
    from varco_core.event.dlq import AbstractDeadLetterQueue
    from varco_core.tenancy.control.service import TenantControlService

logger = logging.getLogger(__name__)

# Sentinel distinguishing "omitted" from an explicit None override — same
# pattern AuditConsumer/TenantProvisionConsumer use for register_to() kwargs.
_UNSET = object()


@dataclass(frozen=True)
class TenantReadiness:
    """
    Immutable snapshot of one tenant's fleet-readiness state.

    Args:
        tenant_id: The tenant this snapshot describes.
        seen:      Store ids that have reported ``TenantNodeReady``.
        expected:  The coordinator's full ``expected_stores`` set.
        missing:   ``expected - seen``.
        complete:  ``True`` iff ``missing`` is empty.
    """

    tenant_id: str
    seen: frozenset[str]
    expected: frozenset[str]
    missing: frozenset[str]
    complete: bool


class TenantReadinessCoordinator(EventConsumer):
    """
    ``EventConsumer`` that aggregates per-store ``TenantNodeReady`` facts
    and calls ``TenantControlService.mark_active()`` once every store in
    ``expected_stores`` has reported for a given tenant.

    Args:
        control_service: The authority ``TenantControlService`` used to
                         call ``mark_active()``. Must have
                         ``catalog_authority=True``.
        expected_stores: The declared, static set of stores this
                         deployment expects to hear from (RD-17). Required
                         — ``ValueError`` if falsy.
        timeout_s:       Seconds to wait for the remaining stores after the
                         first ``TenantNodeReady`` for a tenant, before
                         logging an ERROR naming the missing stores.
                         ``None`` disables the timeout watchdog entirely.
                         Defaults to ``900.0``.
        dlq:             Optional default DLQ applied by ``register_to()``
                         unless overridden there — mirrors
                         ``TenantProvisionConsumer``.

    Raises:
        ValueError: ``expected_stores`` is falsy, or ``control_service.
            catalog_authority`` is ``False``.

    Async safety: ✅ ``asyncio.Lock`` guarding ``_seen`` is created lazily
                     (never at ``__init__`` time, per repo rule).
    """

    _default_retry_policy: RetryPolicy = RetryPolicy.durable_delivery()

    def __init__(
        self,
        *,
        control_service: TenantControlService,
        expected_stores: frozenset[str],
        timeout_s: float | None = 900.0,
        dlq: AbstractDeadLetterQueue | None = None,
    ) -> None:
        if not expected_stores:
            raise ValueError(
                "TenantReadinessCoordinator requires expected_stores — a "
                "non-empty, statically-declared set of store ids (RD-17). "
                "No default, no auto-discovery."
            )
        if not getattr(control_service, "catalog_authority", True):
            raise ValueError(
                "TenantReadinessCoordinator requires a control_service with "
                "catalog_authority=True — a worker-mode service must not "
                "write the catalog (RD-16)."
            )

        self._control = control_service
        self._expected_stores: frozenset[str] = frozenset(expected_stores)
        self._timeout_s = timeout_s
        self._dlq = dlq

        self._seen: dict[str, set[str]] = {}
        self._lock: asyncio.Lock | None = None
        self._timeout_tasks: dict[str, asyncio.Task] = {}

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def register_to(
        self,
        bus: AbstractEventBus,
        *,
        retry_policy: RetryPolicy | None = _UNSET,  # type: ignore[assignment]
        dlq: AbstractDeadLetterQueue | None = _UNSET,  # type: ignore[assignment]
    ) -> list[Subscription]:
        """Wire this coordinator to ``bus``. Call from the host's
        ``@PostConstruct`` — never ``__init__``."""
        effective_dlq = self._dlq if dlq is _UNSET else dlq
        effective_retry_policy = (
            self._default_retry_policy if retry_policy is _UNSET else retry_policy
        )
        return super().register_to(bus, retry_policy=effective_retry_policy, dlq=effective_dlq)

    @listen(TenantNodeReady, channel=CHANNEL_TENANCY)
    async def on_node_ready(self, event: Event) -> None:
        """
        Record one store's readiness for one tenant. Calls
        ``control_service.mark_active()`` exactly once, on the delivery
        that completes the expected set.
        """
        assert isinstance(event, TenantNodeReady)
        tenant_id = event.tenant_id
        store_id = event.store_id

        # Record the tenant as *observed* before the expected-store check:
        # readiness() answers only for tenants this coordinator has heard
        # about at all (see its docstring), and an unexpected-store report
        # still proves the tenant is mid-onboarding somewhere in the fleet.
        async with self._get_lock():
            self._seen.setdefault(tenant_id, set())

        if store_id not in self._expected_stores:
            logger.warning(
                "TenantReadinessCoordinator: unexpected store_id=%r for "
                "tenant=%r (expected=%s) — ignored, does not count toward "
                "completion.",
                store_id,
                tenant_id,
                sorted(self._expected_stores),
            )
            return

        async with self._get_lock():
            seen = self._seen.setdefault(tenant_id, set())
            if store_id in seen:
                # Duplicate report (e.g. ten pods of one service) — a no-op.
                return
            seen.add(store_id)
            complete = seen >= self._expected_stores
            snapshot = set(seen)

        logger.info(
            "TenantReadinessCoordinator: tenant=%r seen=%s expected=%s",
            tenant_id,
            sorted(snapshot),
            sorted(self._expected_stores),
        )

        if complete:
            self._cancel_timeout(tenant_id)
            await self._control.mark_active(tenant_id)
        else:
            self._ensure_timeout_task(tenant_id)

    def _ensure_timeout_task(self, tenant_id: str) -> None:
        if self._timeout_s is None or tenant_id in self._timeout_tasks:
            return
        self._timeout_tasks[tenant_id] = asyncio.create_task(self._watch_timeout(tenant_id))

    def _cancel_timeout(self, tenant_id: str) -> None:
        task = self._timeout_tasks.pop(tenant_id, None)
        if task is not None:
            task.cancel()

    async def _watch_timeout(self, tenant_id: str) -> None:
        assert self._timeout_s is not None
        try:
            await asyncio.sleep(self._timeout_s)
            async with self._get_lock():
                seen = set(self._seen.get(tenant_id, set()))
            missing = self._expected_stores - seen
            if missing:
                logger.error(
                    "TenantReadinessCoordinator: timeout waiting for tenant "
                    "%r — missing stores=%s (seen=%s, expected=%s). Tenant "
                    "stays PENDING; never auto-activated. Recovery: "
                    "re-broadcast request_provision(%r).",
                    tenant_id,
                    sorted(missing),
                    sorted(seen),
                    sorted(self._expected_stores),
                    tenant_id,
                )
        finally:
            self._timeout_tasks.pop(tenant_id, None)

    def readiness(self, tenant_id: str) -> TenantReadiness:
        """
        Return the current readiness snapshot for ``tenant_id``.

        Args:
            tenant_id: The tenant to report on. Must have been *observed* —
                       i.e. at least one ``TenantNodeReady`` for it reached
                       this coordinator (an unexpected ``store_id`` counts:
                       it still proves the tenant is being onboarded).

        Returns:
            A frozen ``TenantReadiness`` snapshot.

        Raises:
            TenantNotFoundError: This coordinator holds no readiness state
                for ``tenant_id``. Rendered as HTTP 404 by
                ``GET /tenancy/tenants/{id}/readiness``.

        Edge cases:
            - Tenant observed but no *expected* store has reported yet →
              empty ``seen``, ``complete=False``. Not an error.
            - After a coordinator restart (RD-18) in-memory state is gone,
              so a tenant that was mid-onboarding now raises rather than
              reporting a misleading "0 of 3 stores ready". A 404 is the
              visible signal that readiness state was lost; the recovery is
              the same single verb as the happy path —
              ``request_provision(tenant_id)`` again, after which every
              worker re-emits ``TenantNodeReady``.

        Thread safety: ⚠️ Reads ``_seen`` without the lock — a snapshot may
                       race with a concurrent in-flight ``on_node_ready``
                       and observe the set one store early or late. This is
                       a diagnostic view, never the activation decision
                       (which is made under the lock in ``on_node_ready``).
        """
        if tenant_id not in self._seen:
            from varco_core.tenancy.catalog import TenantNotFoundError

            raise TenantNotFoundError(tenant_id)

        seen = frozenset(self._seen.get(tenant_id, set()))
        missing = self._expected_stores - seen
        return TenantReadiness(
            tenant_id=tenant_id,
            seen=seen,
            expected=self._expected_stores,
            missing=frozenset(missing),
            complete=not missing,
        )
