"""
varco_beanie.tenancy.pool
============================
``BeanieTenantPool`` — bounded per-tenant Beanie binding pool (Plan 007,
Phase 7, step 3-4).

Wraps ``TenantResourcePool[_PoolEntry]`` (the same bounded LRU/lease pool
``SAEngineRegistry`` builds on) so this module only owns the Mongo-specific
parts: shared-vs-per-tenant client lifecycle and clone-count observability
(RD-7 — the ``N_active_tenants x N_models`` clone count this pool bounds).

``TenantIsolation.SCHEMA`` has no MongoDB meaning — rejected loudly at
construction rather than silently behaving as ``SHARED``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

from varco_core.tenancy.pool import TenantResourcePool
from varco_core.tenancy.settings import TenantIsolation

from varco_beanie.tenancy.binding import BeanieTenantBinding, build_tenant_binding


@dataclass
class _PoolEntry:
    tenant_id: str
    binding: BeanieTenantBinding
    owned_client: Any | None  # None when sharing the pool-wide client


class BeanieTenantPool:
    """
    Bounded per-tenant ``BeanieTenantBinding`` pool.

    Args:
        client:            A shared Motor/pymongo async client, used by
                           every tenant when ``client_per_tenant=False``.
        client_factory:    ``Callable[[tenant_id], client]`` — builds one
                           client per tenant. Required when
                           ``client_per_tenant=True``.
        client_per_tenant: When ``True``, eviction disposes (``.close()``)
                           the tenant's own client. When ``False``
                           (default), eviction never closes the shared
                           client — it outlives any single tenant's
                           binding.
        isolation:         Must not be ``TenantIsolation.SCHEMA`` — MongoDB
                           has no schema-per-tenant equivalent.
        db_template:       ``{tenant_id}``-templated database name.
        document_models:   Document classes to bind for every tenant.
        max_entries:       Soft cap forwarded to ``TenantResourcePool`` —
                           also the RD-7 clone-count bound
                           (``max_entries x N_models`` clone classes,
                           never more).

    Raises:
        ValueError: ``isolation == TenantIsolation.SCHEMA`` — naming
            MongoDB, so the mistake is caught at construction rather than
            silently behaving as ``SHARED``.
    """

    def __init__(
        self,
        *,
        client: Any = None,
        client_factory: Callable[[str], Any] | None = None,
        client_per_tenant: bool = False,
        isolation: TenantIsolation = TenantIsolation.DATABASE,
        db_template: str = "db_{tenant_id}",
        document_models: list[type] | None = None,
        max_entries: int = 50,
    ) -> None:
        if isolation == TenantIsolation.SCHEMA:
            raise ValueError(
                "TenantIsolation.SCHEMA has no MongoDB equivalent — "
                "MongoDB has no schema-per-tenant primitive. Use "
                "TenantIsolation.SHARED or TenantIsolation.DATABASE."
            )

        self._client = client
        self._client_factory = client_factory
        self._client_per_tenant = client_per_tenant
        self._db_template = db_template
        self._document_models = document_models or []
        self._resident: set[str] = set()

        self._pool: TenantResourcePool[_PoolEntry] = TenantResourcePool(
            factory=self._build, closer=self._close, max_entries=max_entries
        )

    async def _build(self, tenant_id: str) -> _PoolEntry:
        if self._client_per_tenant:
            if self._client_factory is None:
                raise ValueError(
                    "BeanieTenantPool(client_per_tenant=True) requires client_factory."
                )
            owned_client = self._client_factory(tenant_id)
            effective_client = owned_client
        else:
            owned_client = None
            effective_client = self._client

        db_name = self._db_template.format(tenant_id=tenant_id)
        binding = await build_tenant_binding(
            tenant_id,
            database_name=db_name,
            document_models=self._document_models,
            client=effective_client,
        )
        self._resident.add(tenant_id)
        return _PoolEntry(
            tenant_id=tenant_id, binding=binding, owned_client=owned_client
        )

    async def _close(self, entry: _PoolEntry) -> None:
        # Called on every eviction path — explicit evict(), automatic LRU
        # eviction inside TenantResourcePool.ensure(), and aclose() — so
        # this is the single place _resident (the RD-7 clone-count bound)
        # is decremented.
        self._resident.discard(entry.tenant_id)
        # A shared client (client_per_tenant=False) outlives any single
        # tenant's binding — never closed here.
        if entry.owned_client is not None:
            entry.owned_client.close()

    async def ensure(self, tenant_id: str) -> BeanieTenantBinding:
        entry = await self._pool.ensure(tenant_id)
        return entry.binding

    def peek(self, tenant_id: str) -> BeanieTenantBinding | None:
        entry = self._pool.peek(tenant_id)
        return entry.binding if entry is not None else None

    async def evict(self, tenant_id: str) -> None:
        await self._pool.evict(tenant_id)

    def active_clone_count(self) -> int:
        """
        Pool-resident tenant count — bounds the RD-7 clone-class count
        (``active_clone_count() x N_models`` classes, never more).
        """
        return len(self._resident)

    async def aclose(self) -> None:
        await self._pool.aclose()
        self._resident.clear()

    async def __aenter__(self) -> BeanieTenantPool:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
