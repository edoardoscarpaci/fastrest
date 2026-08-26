"""
varco_beanie.tenancy.catalog
===============================
``BeanieTenantCatalog`` — the durable Mongo-backed ``AbstractTenantCatalog``
implementation (Plan 007, Phase 4, step 4-5).

DESIGN: an in-process store by default, a real ``varco_tenants`` collection
when wired
    ✅ Constructible with **zero** arguments and immediately usable — the
       same "test/bootstrap convenience" role ``StaticTenantCatalog`` and
       ``ExternalTenantProvisioner`` play, but as the *durable*-named class
       apps actually wire in production once a collection is supplied.
    ✅ When a real ``pymongo`` async ``AsyncCollection`` is passed, every
       operation is a genuine Mongo round-trip (``find_one``,
       ``replace_one`` with ``upsert=True``, ``delete_one``) against the
       ``varco_tenants`` collection — no Beanie ``Document``/``init_beanie``
       indirection, mirroring ``varco_sa.dlq``'s "raw Core over ORM"
       precedent (raw collection over ODM Document — the tenant catalog is
       infrastructure, not an application entity).
    ❌ Two code paths (in-memory vs. real collection) inside one class.
       Accepted — both share the exact same status-transition/validation
       logic; only the storage read/write calls differ, and unlike a
       Document-based implementation, this needs no ``init_beanie()`` call
       at all — pass a collection and it works.

RD-2 (secret reference, never a literal DSN) and the Phase-4 status
lifecycle (legal transitions) are enforced identically to
``varco_sa.tenancy.catalog.SATenantCatalog`` — see that module's docstring
for the shared contract this satisfies.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from varco_core.tenancy.catalog import (
    AbstractTenantCatalog,
    TenantDescriptor,
    TenantNotFoundError,
)
from varco_core.tenancy.settings import TenantStatus

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

_LEGAL_TRANSITIONS: dict[TenantStatus, frozenset[TenantStatus]] = {
    TenantStatus.PENDING: frozenset({TenantStatus.ACTIVE, TenantStatus.DELETED}),
    TenantStatus.ACTIVE: frozenset(
        {TenantStatus.SUSPENDED, TenantStatus.DEPROVISIONING}
    ),
    TenantStatus.SUSPENDED: frozenset(
        {TenantStatus.ACTIVE, TenantStatus.DEPROVISIONING}
    ),
    TenantStatus.DEPROVISIONING: frozenset({TenantStatus.DELETED}),
    TenantStatus.DELETED: frozenset(),
}

_LITERAL_DSN_SCHEMES = frozenset(
    {
        "postgresql",
        "postgres",
        "mysql",
        "mariadb",
        "sqlite",
        "mongodb",
        "mongodb+srv",
        "oracle",
        "mssql",
        "redis",
    }
)


def _looks_like_literal_dsn(dsn_ref: str) -> bool:
    """Same RD-2 heuristic as ``varco_sa.tenancy.catalog._looks_like_literal_dsn``."""
    if "://" not in dsn_ref:
        return False
    scheme, _, rest = dsn_ref.partition("://")
    if scheme.lower() in _LITERAL_DSN_SCHEMES:
        return True
    return "@" in rest


def _descriptor_to_doc(descriptor: TenantDescriptor) -> dict[str, Any]:
    return {
        "_id": descriptor.tenant_id,
        "schema": descriptor.schema,
        "database": descriptor.database,
        "dsn_ref": descriptor.dsn_ref,
        "status": descriptor.status.value,
    }


def _doc_to_descriptor(doc: dict[str, Any]) -> TenantDescriptor:
    return TenantDescriptor(
        tenant_id=doc["_id"],
        schema=doc.get("schema"),
        database=doc.get("database"),
        dsn_ref=doc.get("dsn_ref"),
        status=TenantStatus(doc["status"]),
    )


class BeanieTenantCatalog(AbstractTenantCatalog):
    """
    ``AbstractTenantCatalog`` backed by the ``varco_tenants`` Mongo
    collection — or, absent one, an in-process dict (see module DESIGN
    note).

    Args:
        collection: Optional ``pymongo`` async ``AsyncCollection`` for
                    ``varco_tenants``. ``None`` (default) uses an
                    in-process store — usable with zero setup in tests and
                    simple bootstrap deployments.
    """

    def __init__(self, *, collection: AsyncCollection | None = None) -> None:
        self._collection = collection
        self._memory: dict[str, TenantDescriptor] = {}
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def list_tenants(
        self, *, status: TenantStatus | None = TenantStatus.ACTIVE
    ) -> list[TenantDescriptor]:
        if self._collection is not None:
            query: dict[str, Any] = {} if status is None else {"status": status.value}
            cursor = self._collection.find(query).sort("_id", 1)
            docs = await cursor.to_list(length=None)
            return [_doc_to_descriptor(d) for d in docs]

        async with self._get_lock():
            values = list(self._memory.values())
        if status is not None:
            values = [d for d in values if d.status == status]
        return sorted(values, key=lambda d: d.tenant_id)

    async def get(self, tenant_id: str) -> TenantDescriptor:
        if self._collection is not None:
            doc = await self._collection.find_one({"_id": tenant_id})
            if doc is None:
                raise TenantNotFoundError(tenant_id)
            return _doc_to_descriptor(doc)

        async with self._get_lock():
            descriptor = self._memory.get(tenant_id)
        if descriptor is None:
            raise TenantNotFoundError(tenant_id)
        return descriptor

    async def add(
        self, descriptor: TenantDescriptor, *, allow_literal_dsn: bool = False
    ) -> None:
        """
        Insert or idempotently re-insert ``descriptor``.

        Raises:
            ValueError: ``descriptor.dsn_ref`` looks like a literal
                connection string rather than a secret reference (RD-2),
                unless ``allow_literal_dsn=True``.
        """
        if (
            descriptor.dsn_ref is not None
            and not allow_literal_dsn
            and _looks_like_literal_dsn(descriptor.dsn_ref)
        ):
            raise ValueError(
                f"TenantDescriptor.dsn_ref for tenant {descriptor.tenant_id!r} "
                "looks like a literal database connection string, not a "
                "secret reference (RD-2). Pass allow_literal_dsn=True to "
                "force this (test/bootstrap only)."
            )

        if self._collection is not None:
            existing = await self._collection.find_one({"_id": descriptor.tenant_id})
            if existing is not None:
                return
            await self._collection.insert_one(_descriptor_to_doc(descriptor))
            return

        async with self._get_lock():
            self._memory.setdefault(descriptor.tenant_id, descriptor)

    async def update_status(self, tenant_id: str, status: TenantStatus) -> None:
        current = await self.get(tenant_id)
        legal = _LEGAL_TRANSITIONS.get(current.status, frozenset())
        if status != current.status and status not in legal:
            raise ValueError(
                f"Illegal tenant status transition for {tenant_id!r}: "
                f"{current.status.value!r} -> {status.value!r}."
            )

        if self._collection is not None:
            await self._collection.update_one(
                {"_id": tenant_id}, {"$set": {"status": status.value}}
            )
            return

        async with self._get_lock():
            self._memory[tenant_id] = replace(current, status=status)

    async def remove(self, tenant_id: str) -> None:
        if self._collection is not None:
            await self._collection.delete_one({"_id": tenant_id})
            return

        async with self._get_lock():
            self._memory.pop(tenant_id, None)
