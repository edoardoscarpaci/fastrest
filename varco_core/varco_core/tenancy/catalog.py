"""
varco_core.tenancy.catalog
============================
``TenantDescriptor`` + ``AbstractTenantCatalog`` + ``StaticTenantCatalog``
(Plan 007, Phase 1, step 3-4).

DESIGN: ``StaticTenantCatalog`` stays a test/bootstrap double
    ✅ The durable catalog (``SATenantCatalog`` / ``BeanieTenantCatalog``,
       Phase 4) is authoritative in production; ``StaticTenantCatalog`` is
       the in-memory implementation used in unit tests and simple
       fixed-tenant-list deployments — same relationship as
       ``InMemoryEventBus`` to ``KafkaEventBus``.
    ❌ Two implementations of the same ABC to maintain. Accepted — the ABC
       is small and every backend is exercised by the same contract suite.

Thread safety:  ✅ ``TenantDescriptor`` is frozen.
Async safety:   ✅ ``StaticTenantCatalog`` guards its dict with a lazily
                   created ``asyncio.Lock``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from varco_core.tenancy.settings import TenantStatus


class TenantIsolationError(Exception):
    """Base error for tenancy isolation violations (Plan 007)."""


class TenantNotFoundError(TenantIsolationError):
    """Raised when a catalog lookup misses for a given tenant id."""

    def __init__(self, tenant_id: str) -> None:
        super().__init__(f"Unknown tenant {tenant_id!r}.")
        self.tenant_id = tenant_id


@dataclass(frozen=True)
class TenantDescriptor:
    """
    Immutable record of one tenant in the catalog.

    Args:
        tenant_id: Stable, unique tenant identifier.
        schema:    Postgres schema name (``TenantIsolation.SCHEMA``).
        database:  Logical database name (``TenantIsolation.DATABASE``).
        dsn_ref:   Secret **reference** — never a literal DSN (RD-2).
        status:    Lifecycle status. Defaults to ``PENDING``.

    Edge cases:
        - ``dsn_ref`` must be a reference, not a literal credential — the
          durable catalog backends (Phase 4) enforce this at the storage
          boundary; ``TenantDescriptor`` itself does not parse it.
    """

    tenant_id: str
    schema: str | None = None
    database: str | None = None
    dsn_ref: str | None = None
    status: TenantStatus = TenantStatus.PENDING


class AbstractTenantCatalog:
    """
    Contract for the durable tenant catalog.

    Backends: ``StaticTenantCatalog`` (in-memory, tests/bootstrap),
    ``SATenantCatalog`` (Postgres, Phase 4), ``BeanieTenantCatalog``
    (MongoDB, Phase 4).
    """

    async def list_tenants(
        self, *, status: TenantStatus | None = TenantStatus.ACTIVE
    ) -> list[TenantDescriptor]:
        """
        Return tenants sorted deterministically by ``tenant_id``.

        Args:
            status: Filter. Defaults to ``TenantStatus.ACTIVE`` (today's
                    "routable tenants" behaviour). Pass ``None`` for every
                    status, including tombstoned/deleted.

        Returns:
            Sorted, deterministic list — fan-out (Phase 8/9) depends on
            stable ordering.
        """
        raise NotImplementedError

    async def get(self, tenant_id: str) -> TenantDescriptor:
        """
        Return the descriptor for ``tenant_id``.

        Raises:
            TenantNotFoundError: No such tenant.
        """
        raise NotImplementedError

    async def add(self, descriptor: TenantDescriptor) -> None:
        """Insert or idempotently re-insert a tenant descriptor."""
        raise NotImplementedError

    async def update_status(self, tenant_id: str, status: TenantStatus) -> None:
        """Transition a tenant's status."""
        raise NotImplementedError

    async def remove(self, tenant_id: str) -> None:
        """Idempotently remove a tenant descriptor."""
        raise NotImplementedError


class StaticTenantCatalog(AbstractTenantCatalog):
    """
    In-memory ``AbstractTenantCatalog`` — the test/bootstrap double.

    Args:
        descriptors: Initial tenant descriptors.

    Async safety: ✅ A lazily created ``asyncio.Lock`` guards the dict
                     (repo rule: never create locks at ``__init__`` time
                     outside a running loop — created on first async use).
    """

    def __init__(self, descriptors: list[TenantDescriptor] | None = None) -> None:
        self._tenants: dict[str, TenantDescriptor] = {
            d.tenant_id: d for d in (descriptors or [])
        }
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def list_tenants(
        self, *, status: TenantStatus | None = TenantStatus.ACTIVE
    ) -> list[TenantDescriptor]:
        async with self._get_lock():
            values = list(self._tenants.values())
        if status is not None:
            values = [d for d in values if d.status == status]
        return sorted(values, key=lambda d: d.tenant_id)

    async def get(self, tenant_id: str) -> TenantDescriptor:
        async with self._get_lock():
            descriptor = self._tenants.get(tenant_id)
        if descriptor is None:
            raise TenantNotFoundError(tenant_id)
        return descriptor

    async def add(self, descriptor: TenantDescriptor) -> None:
        async with self._get_lock():
            self._tenants[descriptor.tenant_id] = descriptor

    async def update_status(self, tenant_id: str, status: TenantStatus) -> None:
        async with self._get_lock():
            existing = self._tenants.get(tenant_id)
            if existing is None:
                raise TenantNotFoundError(tenant_id)
            from dataclasses import replace

            self._tenants[tenant_id] = replace(existing, status=status)

    async def remove(self, tenant_id: str) -> None:
        async with self._get_lock():
            self._tenants.pop(tenant_id, None)
