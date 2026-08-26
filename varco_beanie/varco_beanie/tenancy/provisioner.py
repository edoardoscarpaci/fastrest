"""
varco_beanie.tenancy.provisioner
===================================
``BeanieDatabaseProvisioner`` — the ``TenantIsolation.DATABASE`` provisioner
for MongoDB (Plan 007, Phase 7, step 6).

MongoDB creates databases lazily (there is no ``CREATE DATABASE``) — so
provisioning *is* collection/index creation, reusing Plan 006's
``IndexReconciler``. ``deprovision(confirm_destroy=True)`` is the per-tenant
**GDPR erasure primitive** (pairs with crypto-shredding): ``dropDatabase``
is atomic, immediate, and — unlike a Postgres schema/table-row erase —
requires no cross-table `DELETE` fan-out at all.
"""

from __future__ import annotations

from typing import Any

from varco_core.tenancy.provisioner import AbstractTenantProvisioner


class BeanieDatabaseProvisioner(AbstractTenantProvisioner):
    """
    Provisions/deprovisions one Mongo database per tenant.

    Args:
        client:      A Motor/pymongo async client.
        db_template: ``{tenant_id}``-templated database name.
        index_guard: Optional ``BeanieIndexGuard``-shaped object — when
                     given, ``provision()`` reconciles indexes via
                     ``IndexReconciler.apply()`` after the database's
                     collections implicitly exist (MongoDB creates
                     collections lazily too, on first write/index build).
    """

    def __init__(
        self,
        *,
        client: Any,
        db_template: str = "db_{tenant_id}",
        index_guard: Any = None,
    ) -> None:
        self._client = client
        self._db_template = db_template
        self._index_guard = index_guard

    def _db_name_for(self, tenant_id: str) -> str:
        return self._db_template.format(tenant_id=tenant_id)

    async def provision(self, tenant_id: str, **kwargs: object) -> None:
        """
        Idempotent — MongoDB creates the database (and collections)
        lazily; this reconciles indexes if an ``index_guard`` was given.
        """
        if self._index_guard is None:
            return
        from varco_beanie.migration.indexes import IndexReconciler

        db_name = self._db_name_for(tenant_id)
        reconciler = IndexReconciler(self._index_guard, self._client[db_name])
        await reconciler.apply()

    async def deprovision(self, tenant_id: str, *, confirm_destroy: bool = False) -> None:
        """
        ``dropDatabase`` — the per-tenant GDPR erasure primitive.

        Raises:
            DestructiveOperationRefused: ``confirm_destroy`` is not
                ``True`` (enforced by the ABC).
        """
        await super().deprovision(tenant_id, confirm_destroy=confirm_destroy)

        db_name = self._db_name_for(tenant_id)
        await self._client.drop_database(db_name)
