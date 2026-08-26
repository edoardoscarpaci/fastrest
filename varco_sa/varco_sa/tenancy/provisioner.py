"""
varco_sa.tenancy.provisioner
===============================
``SASchemaProvisioner`` — the ``TenantIsolation.SCHEMA`` provisioner (Plan
007, Phase 3, step 5-6).

``CREATE SCHEMA`` **is** transactional in Postgres — unlike ``CREATE
DATABASE`` (Phase 6, ``SADatabaseProvisioner``), a schema provision can run
inside the caller's ordinary transaction and rolls back cleanly on failure.

Note: this provisioner issues schema DDL against whatever connection it is
given — it is **not** the cluster-DDL-confined admin path (RD-4 applies to
``TenantIsolation.DATABASE``'s ``CREATE DATABASE``/``DROP DATABASE``, which
require superuser-adjacent privileges). A Postgres role with ``CREATE`` on
its own database can create schemas within it without elevated privilege,
so ``SASchemaProvisioner`` does not require ``SAAdminEngine`` (Phase 6).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from varco_core.tenancy.provisioner import AbstractTenantProvisioner

from varco_sa.tenancy.router import SASchemaRouter

if TYPE_CHECKING:
    pass


class SASchemaProvisioner(AbstractTenantProvisioner):
    """
    Provisions/deprovisions one Postgres schema per tenant.

    Args:
        connection_factory: ``Callable[[], AsyncConnection]``-like async
                             context manager factory (e.g.
                             ``engine.begin``) — one call per
                             provision/deprovision, so the schema DDL runs
                             in its own short transaction.
        router:              ``SASchemaRouter`` used to render (and
                             validate) the real schema name.
    """

    def __init__(
        self, *, connection_factory, router: SASchemaRouter | None = None
    ) -> None:
        self._connection_factory = connection_factory
        self._router = router or SASchemaRouter()

    async def provision(self, tenant_id: str, **kwargs: object) -> None:
        """
        ``CREATE SCHEMA IF NOT EXISTS "<schema>"`` — idempotent (RD-1
        redelivery safe): a second call for an already-provisioned tenant
        is a no-op.
        """
        import sqlalchemy as sa

        schema_name = self._router.schema_name_for(tenant_id)
        async with self._connection_factory() as conn:  # type: AsyncConnection
            await conn.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))

    async def deprovision(
        self, tenant_id: str, *, confirm_destroy: bool = False
    ) -> None:
        """
        ``DROP SCHEMA "<schema>" CASCADE`` — only with ``confirm_destroy=True``.

        Raises:
            DestructiveOperationRefused: ``confirm_destroy`` is not ``True``
                (enforced by the ABC — see
                ``varco_core.tenancy.provisioner.AbstractTenantProvisioner``).
        """
        await super().deprovision(tenant_id, confirm_destroy=confirm_destroy)

        import sqlalchemy as sa

        schema_name = self._router.schema_name_for(tenant_id)
        async with self._connection_factory() as conn:  # type: AsyncConnection
            await conn.execute(
                sa.text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
            )
