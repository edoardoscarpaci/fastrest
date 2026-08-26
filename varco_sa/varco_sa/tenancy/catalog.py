"""
varco_sa.tenancy.catalog
===========================
``SATenantCatalog`` — the durable, SQLAlchemy-backed
``AbstractTenantCatalog`` implementation (Plan 007, Phase 4, step 1-2).

DESIGN: raw Core over the DomainModel/mapper stack
    ``TenantDescriptor`` (``varco_core.tenancy.catalog``) is a plain frozen
    dataclass, not a ``DomainModel`` — there is no ``AbstractMapper`` /
    ``SAModelFactory`` involved, matching ``SADeadLetterQueue``/
    ``SAJobStore``'s "infrastructure table" precedent.

DESIGN: lazy ``ensure_table()`` on first use, not a required explicit call
    ✅ Every method calls the (idempotent, ``checkfirst=True``) table
       creation against the given session's own connection before its
       query — safe with a caller-supplied ``AsyncSession`` whose engine
       this class never sees. Production deployments should still prefer
       an Alembic migration (``varco_tenants`` is on the packaged ``varco``
       branch) — this is the zero-migration convenience path, same as
       ``SADeadLetterQueue.ensure_table()``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from varco_core.tenancy.catalog import (
    AbstractTenantCatalog,
    TenantDescriptor,
    TenantNotFoundError,
)
from varco_core.tenancy.settings import TenantStatus

from varco_sa.tenancy.models import tenants_metadata, tenants_table

# Legal status transitions — mirrors the Phase-4 status lifecycle table.
# `deleted` is a terminal tombstone; nothing transitions out of it.
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

# Known literal-DSN URL schemes (RD-2) — dsn_ref must be a secret
# *reference*, never one of these.
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
    """
    Heuristic RD-2 guard: reject values that look like a literal database
    connection string rather than an opaque secret reference.

    Flags either (a) a known DB-driver URL scheme (``postgresql://...``) or
    (b) any URL carrying embedded userinfo (``scheme://user:pass@host`` —
    the unambiguous "this is a credential" signal), so a bespoke scheme with
    embedded credentials is still caught even if not in the known-scheme list.
    """
    if "://" not in dsn_ref:
        return False
    scheme, _, rest = dsn_ref.partition("://")
    if scheme.lower() in _LITERAL_DSN_SCHEMES:
        return True
    return "@" in rest


class SATenantCatalog(AbstractTenantCatalog):
    """
    ``AbstractTenantCatalog`` backed by the ``varco_tenants`` table.

    Args:
        session: An open ``AsyncSession``. Every call issues its own
                 statements against this session and commits — the catalog
                 is control-plane infrastructure, like ``SADeadLetterQueue``,
                 and does not participate in an application UoW.
    """

    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session

    async def _ensure_table(self) -> None:
        conn = await self._session.connection()
        await conn.run_sync(tenants_metadata.create_all, checkfirst=True)

    @staticmethod
    def _row_to_descriptor(row: sa.Row) -> TenantDescriptor:
        return TenantDescriptor(
            tenant_id=row.tenant_id,
            schema=row.schema_name,
            database=row.database_name,
            dsn_ref=row.dsn_ref,
            status=TenantStatus(row.status),
        )

    async def list_tenants(
        self, *, status: TenantStatus | None = TenantStatus.ACTIVE
    ) -> list[TenantDescriptor]:
        await self._ensure_table()
        stmt = sa.select(tenants_table).order_by(tenants_table.c.tenant_id)
        if status is not None:
            stmt = stmt.where(tenants_table.c.status == status.value)
        result = await self._session.execute(stmt)
        return [self._row_to_descriptor(row) for row in result]

    async def get(self, tenant_id: str) -> TenantDescriptor:
        await self._ensure_table()
        stmt = sa.select(tenants_table).where(tenants_table.c.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        row = result.first()
        if row is None:
            raise TenantNotFoundError(tenant_id)
        return self._row_to_descriptor(row)

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
                "secret reference (RD-2: varco_tenants must never store a "
                "literal credential). Pass allow_literal_dsn=True to force "
                "this (test/bootstrap only), or store a reference your "
                "secret-manager hook resolves at runtime."
            )

        await self._ensure_table()
        now = datetime.now(tz=timezone.utc)

        existing = await self._session.execute(
            sa.select(tenants_table.c.tenant_id).where(
                tenants_table.c.tenant_id == descriptor.tenant_id
            )
        )
        if existing.first() is not None:
            # Idempotent — add() twice must not raise or duplicate.
            await self._session.commit()
            return

        await self._session.execute(
            sa.insert(tenants_table).values(
                tenant_id=descriptor.tenant_id,
                schema_name=descriptor.schema,
                database_name=descriptor.database,
                dsn_ref=descriptor.dsn_ref,
                status=descriptor.status.value,
                created_at=now,
                updated_at=now,
            )
        )
        await self._session.commit()

    async def update_status(self, tenant_id: str, status: TenantStatus) -> None:
        """
        Transition ``tenant_id`` to ``status``.

        Raises:
            TenantNotFoundError: No such tenant.
            ValueError: The transition is not in the legal status graph
                (e.g. ``deleted -> active``).
        """
        current = await self.get(tenant_id)
        legal = _LEGAL_TRANSITIONS.get(current.status, frozenset())
        if status != current.status and status not in legal:
            raise ValueError(
                f"Illegal tenant status transition for {tenant_id!r}: "
                f"{current.status.value!r} -> {status.value!r}."
            )

        await self._session.execute(
            sa.update(tenants_table)
            .where(tenants_table.c.tenant_id == tenant_id)
            .values(status=status.value, updated_at=datetime.now(tz=timezone.utc))
        )
        await self._session.commit()

    async def remove(self, tenant_id: str) -> None:
        await self._ensure_table()
        await self._session.execute(
            sa.delete(tenants_table).where(tenants_table.c.tenant_id == tenant_id)
        )
        await self._session.commit()
