"""
varco_sa.tenancy.engine_registry
===================================
``SAEngineRegistry`` — one bounded ``AsyncEngine`` per tenant for
``TenantIsolation.DATABASE`` (Plan 007, Phase 6, step 1-2).

Wraps ``TenantResourcePool[AsyncEngine]`` (the bounded LRU/lease pool
``varco_core.tenancy`` already provides) so this module only owns the
Postgres-specific parts: DSN templating, the ``dsn_ref`` override (RD-2 —
already-resolved by the time it reaches this class; the secret *reference*
lives in ``varco_tenants``, resolution happens upstream), per-tenant
``pool_size=1, max_overflow=2`` sizing, and never leaking a credential into
a log/repr.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from varco_core.tenancy.pool import TenantResourcePool

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class SAEngineRegistry:
    """
    Bounded per-tenant ``AsyncEngine`` pool.

    Args:
        db_template: ``{tenant_id}``-templated database-name suffix,
                     appended to ``base_dsn`` (mirrors
                     ``TenancySettings.db_template``).
        base_dsn:    DSN prefix ending in ``/`` — the template's rendered
                     database name is appended directly.
        max_entries: Soft cap forwarded to ``TenantResourcePool``.
        pool_size:   Per-tenant SQLAlchemy pool size. Defaults to ``1`` —
                     per-tenant engines are mostly idle (sizing worksheet,
                     RD-5: informational, no varco-enforced cap).
        max_overflow: Per-tenant overflow connections. Defaults to ``2``.
    """

    def __init__(
        self,
        *,
        db_template: str = "db_{tenant_id}",
        base_dsn: str,
        max_entries: int = 50,
        pool_size: int = 1,
        max_overflow: int = 2,
    ) -> None:
        self._db_template = db_template
        self._base_dsn = base_dsn.rstrip("/") + "/"
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        # Set immediately before each pool.ensure() call — read by the
        # factory closure. Not a race hazard: the pool's per-tenant
        # creation lock (varco_core.tenancy.pool) guarantees the factory
        # for a given tenant_id runs to completion before any other
        # ensure() call for the SAME tenant_id observes a different value,
        # and different tenant_ids never share a dict key.
        self._dsn_overrides: dict[str, str] = {}

        self._pool: TenantResourcePool[AsyncEngine] = TenantResourcePool(
            factory=self._build_engine,
            closer=self._dispose_engine,
            max_entries=max_entries,
        )

    def _dsn_for(self, tenant_id: str) -> str:
        override = self._dsn_overrides.get(tenant_id)
        if override is not None:
            return override
        db_name = self._db_template.format(tenant_id=tenant_id)
        return f"{self._base_dsn}{db_name}"

    async def _build_engine(self, tenant_id: str) -> AsyncEngine:
        dsn = self._dsn_for(tenant_id)
        engine = create_async_engine(
            dsn, pool_size=self._pool_size, max_overflow=self._max_overflow
        )
        # Never log the resolved DSN — it may carry a credential even
        # though varco_tenants itself only stores a reference (RD-2).
        logger.debug("SAEngineRegistry: built engine for tenant_id=%r", tenant_id)
        return engine

    async def _dispose_engine(self, engine: AsyncEngine) -> None:
        await engine.dispose()

    async def ensure(
        self, tenant_id: str, *, dsn_ref: str | None = None
    ) -> AsyncEngine:
        """
        Return the cached engine for ``tenant_id``, creating it if absent.

        Args:
            dsn_ref: An already-resolved DSN overriding the template — the
                     override wins (RD-2). ``None`` uses ``db_template``.
        """
        if dsn_ref is not None:
            self._dsn_overrides[tenant_id] = dsn_ref
        return await self._pool.ensure(tenant_id)

    def peek(self, tenant_id: str) -> AsyncEngine | None:
        """Return the cached engine for ``tenant_id`` without creating it."""
        return self._pool.peek(tenant_id)

    async def evict(self, tenant_id: str) -> None:
        await self._pool.evict(tenant_id)
        self._dsn_overrides.pop(tenant_id, None)

    async def aclose(self) -> None:
        """Dispose every engine. Idempotent."""
        await self._pool.aclose()

    async def __aenter__(self) -> SAEngineRegistry:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
