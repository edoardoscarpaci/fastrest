"""
varco_core.migration.fanout
==============================
``TenantFanoutMigrator`` — global-first, then-every-tenant migration
fan-out (Plan 007, Phase 9, step 1-2).

Backend-agnostic — composes ``AbstractTenantCatalog`` with a
``Callable[[TenantDescriptor], AbstractMigrator]`` factory, so it works
unchanged for ``AlembicMigrator`` and ``BeanieMigrator``.

**Required ordering: the global/framework run completes BEFORE the tenant
fan-out** — tenant tables may carry foreign keys to global tables.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from varco_core.tenancy.settings import TenantStatus

if TYPE_CHECKING:
    from varco_core.migration.base import AbstractMigrator
    from varco_core.tenancy.catalog import AbstractTenantCatalog, TenantDescriptor

logger = logging.getLogger(__name__)

# Statuses that participate in fan-out — pending/deleted tenants are skipped
# (see the Phase-4 status routing table: pending has no storage to migrate
# yet, deleted is a tombstone).
_TARGETED_STATUSES = (TenantStatus.ACTIVE, TenantStatus.SUSPENDED)


@dataclass(frozen=True)
class TenantMigrationReport:
    """Aggregate result of a fan-out ``upgrade()``/``plan()`` call."""

    global_report: Any = None
    per_tenant: dict[str, Any] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    not_attempted: list[str] = field(default_factory=list)
    duration_s: float = 0.0


class TenantFanoutMigrationError(Exception):
    """Raised by ``check()`` when one or more tenants are behind."""


class TenantFanoutMigrator:
    """
    Fans a migration run out across every targeted tenant, global-first.

    Args:
        catalog:                The tenant catalog to enumerate.
        global_migrator:        Migrator for the global/framework schema —
                                 always run first.
        tenant_migrator_factory: ``Callable[[TenantDescriptor], AbstractMigrator]``
                                 building a migrator scoped to one tenant.
        fanout_on_failure:      ``"stop"`` (default) halts at the first
                                 tenant failure — later tenants are reported
                                 ``not_attempted``. ``"continue"`` attempts
                                 every tenant and aggregates all failures.
        skip_global:            Explicit opt-out of the global-first run
                                 (``--skip-global``). Defaults ``False``.
    """

    def __init__(
        self,
        *,
        catalog: AbstractTenantCatalog,
        global_migrator: AbstractMigrator | Any,
        tenant_migrator_factory: Callable[[TenantDescriptor], AbstractMigrator | Any],
        fanout_on_failure: Literal["stop", "continue"] = "stop",
        skip_global: bool = False,
    ) -> None:
        self._catalog = catalog
        self._global_migrator = global_migrator
        self._tenant_migrator_factory = tenant_migrator_factory
        self._fanout_on_failure = fanout_on_failure
        self._skip_global = skip_global

    async def _targeted_tenants(self) -> list[TenantDescriptor]:
        all_tenants = await self._catalog.list_tenants(status=None)
        targeted = [d for d in all_tenants if d.status in _TARGETED_STATUSES]
        return sorted(targeted, key=lambda d: d.tenant_id)

    async def plan(self) -> TenantMigrationReport:
        """Aggregate per-tenant plans, keyed by tenant id in sorted order."""
        start = time.monotonic()
        global_plan = None if self._skip_global else await self._global_migrator.plan()

        per_tenant: dict[str, Any] = {}
        for descriptor in await self._targeted_tenants():
            migrator = self._tenant_migrator_factory(descriptor)
            per_tenant[descriptor.tenant_id] = await migrator.plan()

        return TenantMigrationReport(
            global_report=global_plan,
            per_tenant=per_tenant,
            duration_s=time.monotonic() - start,
        )

    async def upgrade(self, target: str = "heads") -> TenantMigrationReport:
        """
        Apply the global/framework run first, then fan out to every
        targeted tenant.

        Edge cases:
            - Empty catalog is a successful no-op, with one WARNING (far
              more likely a misconfiguration than a real deployment state).
            - ``"stop"`` (default): halts at the first tenant failure — the
              report names applied / failed / not-attempted, never a bare
              boolean.
            - ``"continue"``: attempts every tenant and aggregates all
              failures; a per-tenant ``MigrationLockTimeout`` does not abort
              the others.
        """
        start = time.monotonic()
        global_report = None
        if not self._skip_global:
            global_report = await self._global_migrator.upgrade(target)

        tenants = await self._targeted_tenants()
        if not tenants:
            logger.warning(
                "TenantFanoutMigrator.upgrade(): catalog has no active/"
                "suspended tenants — this is a successful no-op, but is "
                "far more likely a misconfiguration than a real deployment "
                "state. Verify the catalog is wired correctly."
            )

        per_tenant: dict[str, Any] = {}
        failures: dict[str, str] = {}
        not_attempted: list[str] = []

        tenant_ids = [d.tenant_id for d in tenants]
        for index, descriptor in enumerate(tenants):
            if failures and self._fanout_on_failure == "stop":
                not_attempted.extend(tenant_ids[index:])
                break
            migrator = self._tenant_migrator_factory(descriptor)
            try:
                per_tenant[descriptor.tenant_id] = await migrator.upgrade(target)
            except Exception as exc:  # noqa: BLE001 - aggregated, not re-raised
                failures[descriptor.tenant_id] = str(exc)
                if self._fanout_on_failure == "stop":
                    not_attempted.extend(tenant_ids[index + 1 :])
                    break

        return TenantMigrationReport(
            global_report=global_report,
            per_tenant=per_tenant,
            failures=failures,
            not_attempted=not_attempted,
            duration_s=time.monotonic() - start,
        )

    async def check(self) -> TenantMigrationReport:
        """
        Raise if any targeted tenant (or the global schema) is behind.

        Raises:
            TenantFanoutMigrationError: Naming every behind tenant.
        """
        behind: list[str] = []

        if not self._skip_global:
            global_ok = await self._global_migrator.check()
            if global_ok is False:
                behind.append("<global>")

        for descriptor in await self._targeted_tenants():
            migrator = self._tenant_migrator_factory(descriptor)
            ok = await migrator.check()
            if ok is False:
                behind.append(descriptor.tenant_id)

        if behind:
            raise TenantFanoutMigrationError(
                f"The following tenants are behind on migrations: {', '.join(behind)}."
            )

        return TenantMigrationReport()
