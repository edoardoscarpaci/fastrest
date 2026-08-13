"""
varco_sa.tenancy.admin.engine
================================
``SAAdminEngine`` — the short-lived, ``NullPool``, context-managed
maintenance engine cluster DDL runs against (Plan 007, Phase 6, step 5).

DESIGN: ``NullPool`` + context-manager, never a long-lived pooled engine
    ✅ Cluster DDL (``CREATE DATABASE``, ``DROP DATABASE``) is rare and
       short-lived — a pooled connection sitting open between calls is
       both wasted and a bigger attack surface for the admin credential.
    ✅ ``NullPool`` sidesteps entirely the "an admin connection lingers in
       a pool that outlives the DDL that needed it" class of leak.
    ✅ Context-managed disposal (``async with``) guarantees the connection
       is closed even when the DDL body raises (``finally``-equivalent).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool


class SAAdminEngine:
    """
    Short-lived, ``NullPool``-backed maintenance engine.

    Args:
        admin_dsn: The (already-resolved) cluster-DDL-privileged DSN.

    Usage::

        admin_engine = SAAdminEngine(admin_dsn=...)
        async with admin_engine as engine:
            async with engine.begin() as conn:
                await conn.execute(sa.text("CREATE DATABASE ..."))
        # engine.dispose() has already run — admin_engine.disposed is True
    """

    def __init__(self, admin_dsn: str) -> None:
        self._admin_dsn = admin_dsn
        self._engine: AsyncEngine | None = None
        self._disposed = False

    @property
    def disposed(self) -> bool:
        return self._disposed

    async def __aenter__(self) -> AsyncEngine:
        self._engine = create_async_engine(self._admin_dsn, poolclass=NullPool)
        self._disposed = False
        return self._engine

    async def __aexit__(self, *exc_info: object) -> None:
        # Disposed unconditionally — even when the body raised — so the
        # admin connection never lingers past the DDL that needed it.
        if self._engine is not None:
            await self._engine.dispose()
        self._disposed = True
