"""
varco_sa.tenancy.admin.db_provisioner
========================================
``SADatabaseProvisioner`` — the ``TenantIsolation.DATABASE`` provisioner
(Plan 007, Phase 6, step 6-7 — RD-4).

Required Postgres grants: the control-plane role needs ``CREATEDB`` (and
``CREATEROLE`` only if per-tenant roles are used — out of scope for varco;
see the plan's Non-goals). The app role needs **none** of them — only
``CONNECT`` on its own tenant database, ``USAGE``/DML on its own schema,
and **read-only** on the global schema by default (RD-10).

Supported alternative: ``ExternalTenantProvisioner`` (``varco_core.tenancy.
provisioner``), which records intent and returns, with databases created by
a DBA/Terraform workflow out of band — the ``status`` lifecycle (Phase 4)
handles the asynchrony.
"""

from __future__ import annotations

import inspect
import re
from typing import TYPE_CHECKING, Any

from varco_core.tenancy.provisioner import AbstractTenantProvisioner

from varco_sa.tenancy.admin.engine import SAAdminEngine

if TYPE_CHECKING:
    pass

_VALID_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


async def _apply_autocommit(conn: Any) -> Any:
    """
    ``CREATE DATABASE``/``DROP DATABASE`` cannot run inside a transaction
    block — apply ``isolation_level="AUTOCOMMIT"`` first.

    Supports both a real (async) ``AsyncConnection.execution_options()``
    and a lightweight, synchronous test double — awaits the result only
    when it is actually awaitable.
    """
    result = conn.execution_options(isolation_level="AUTOCOMMIT")
    if inspect.isawaitable(result):
        return await result
    return result


class SADatabaseProvisioner(AbstractTenantProvisioner):
    """
    Provisions/deprovisions one Postgres **database** per tenant via a
    confined ``SAAdminEngine``.

    Args:
        admin_dsn:   The cluster-DDL-privileged DSN
                     (``VARCO_TENANCY_ADMIN_DSN``). **Required** — cluster
                     DDL is unreachable without it (RD-4: this constructor
                     is the enforcement point, not a runtime check).
        app_dsn:     Optional — the request-path ``SAConfig.engine``'s DSN.
                     When it equals ``admin_dsn``, construction refuses: an
                     app pod must not be its own admin.
        db_template: ``{tenant_id}``-templated database name.
        engine_registry: Optional ``SAEngineRegistry``-shaped object with
                     an async ``evict(tenant_id)`` — evicted (disposing the
                     tenant's engine) before destructive ``DROP DATABASE``
                     DDL, so no connection is left pointing at a database
                     about to be dropped.

    Raises:
        ValueError: ``admin_dsn`` is ``None``, or equals ``app_dsn``.
    """

    def __init__(
        self,
        *,
        admin_dsn: str | None,
        app_dsn: str | None = None,
        db_template: str = "db_{tenant_id}",
        engine_registry: Any = None,
    ) -> None:
        if admin_dsn is None:
            raise ValueError(
                "SADatabaseProvisioner requires an explicit admin_dsn "
                "(VARCO_TENANCY_ADMIN_DSN) — cluster DDL is confined to "
                "the control plane (RD-4); an app pod cannot construct "
                "this provisioner without opting in."
            )
        if app_dsn is not None and admin_dsn == app_dsn:
            raise ValueError(
                "SADatabaseProvisioner refuses an admin_dsn equal to the "
                "request-path app_dsn — an app pod must not be its own "
                "admin (RD-4)."
            )

        self._admin_dsn = admin_dsn
        self._db_template = db_template
        self._engine_registry = engine_registry
        self._admin_engine = SAAdminEngine(admin_dsn)

    def _db_name_for(self, tenant_id: str) -> str:
        name = self._db_template.format(tenant_id=tenant_id)
        if not _VALID_IDENTIFIER.match(name):
            raise ValueError(
                f"Database name {name!r} (rendered from tenant_id={tenant_id!r}) "
                "is not a valid SQL identifier — database names cannot be "
                "bound parameters, so this validation is the only "
                "injection defence."
            )
        return name

    async def _create_database(self, conn: Any, db_name: str) -> None:
        """
        Create ``db_name`` if it does not already exist.

        ``CREATE DATABASE`` **cannot** run inside a transaction block —
        ``AUTOCOMMIT`` is applied first. An existence probe precedes it
        (idempotency for RD-1 redelivery).
        """
        import sqlalchemy as sa

        conn = await _apply_autocommit(conn)

        existing = await conn.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": db_name},
        )
        if existing.scalar() is not None:
            return

        await conn.execute(sa.text(f'CREATE DATABASE "{db_name}"'))

    async def _drop_database(
        self, conn: Any, db_name: str, *, force: bool = False
    ) -> None:
        """``DROP DATABASE`` — always ``AUTOCOMMIT``, always ``IF EXISTS``."""
        import sqlalchemy as sa

        conn = await _apply_autocommit(conn)

        if force:
            await conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": db_name},
            )

        await conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{db_name}"'))

    async def provision(self, tenant_id: str, **kwargs: object) -> None:
        db_name = self._db_name_for(tenant_id)
        async with self._admin_engine as engine:
            async with engine.connect() as conn:
                await self._create_database(conn, db_name)

    async def deprovision(
        self, tenant_id: str, *, confirm_destroy: bool = False, force: bool = False
    ) -> None:
        """
        ``DROP DATABASE`` — only with ``confirm_destroy=True``.

        Disposes and evicts the tenant's engine (via ``engine_registry``,
        if given) **before** issuing the drop, then optionally
        ``pg_terminate_backend``s any remaining stragglers with
        ``force=True``.
        """
        await super().deprovision(tenant_id, confirm_destroy=confirm_destroy)

        db_name = self._db_name_for(tenant_id)

        if self._engine_registry is not None:
            await self._engine_registry.evict(tenant_id)

        async with self._admin_engine as engine:
            async with engine.connect() as conn:
                await self._drop_database(conn, db_name, force=force)
