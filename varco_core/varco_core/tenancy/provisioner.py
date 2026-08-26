"""
varco_core.tenancy.provisioner
================================
``AbstractTenantProvisioner`` + ``ExternalTenantProvisioner``
(Plan 007, Phase 1, step 9).

DESIGN: the ``confirm_destroy`` gate lives in the ABC, not per-backend
    ✅ A subclass that overrides ``deprovision()`` and calls ``super()``
       still cannot bypass the gate — no backend implementation can forget
       it, by construction. Tested directly (a "naive" subclass that
       forgets its own check is still refused).
    ❌ A subclass that overrides ``deprovision()`` **without** calling
       ``super()`` can still bypass it. Accepted — Python offers no way to
       make a method un-overridable without seal machinery this codebase
       does not use elsewhere; the ABC contract is documented and tested.

``ExternalTenantProvisioner`` is the RD-4 "no-op/DBA workflow" — it records
intent and returns; databases/schemas are created by a DBA or Terraform out
of band, and the ``status`` lifecycle (Phase 4) handles the asynchrony.
"""

from __future__ import annotations


class DestructiveOperationRefused(Exception):
    """Raised when a destructive operation is attempted without confirmation."""


class AbstractTenantProvisioner:
    """
    Contract for provisioning/deprovisioning a tenant's storage.

    Backends: ``ExternalTenantProvisioner`` (no-op/DBA workflow, always
    available), ``SASchemaProvisioner`` / ``SADatabaseProvisioner``
    (Phase 3/6), ``BeanieDatabaseProvisioner`` (Phase 7).
    """

    async def provision(self, tenant_id: str, **kwargs: object) -> None:
        """Provision storage for ``tenant_id``. Must be idempotent."""
        raise NotImplementedError

    async def deprovision(self, tenant_id: str, *, confirm_destroy: bool = False) -> None:
        """
        Destroy storage for ``tenant_id``.

        Raises:
            DestructiveOperationRefused: ``confirm_destroy`` is not
                ``True``. This check lives here so no subclass can forget
                it — override and call ``super().deprovision(...)`` to
                inherit the gate.
        """
        if not confirm_destroy:
            raise DestructiveOperationRefused(
                f"Refusing to deprovision tenant {tenant_id!r} without "
                "confirm_destroy=True — this is a destructive operation."
            )


class ExternalTenantProvisioner(AbstractTenantProvisioner):
    """
    No-op provisioner — records intent and returns.

    The supported "cluster DDL stays out of app pods" path (RD-4): a DBA or
    Terraform workflow creates the actual schema/database out of band, and
    the tenant's ``status`` lifecycle (Phase 4) tracks when that external
    work completes.
    """

    async def provision(self, tenant_id: str, **kwargs: object) -> None:
        return None

    async def deprovision(self, tenant_id: str, *, confirm_destroy: bool = False) -> None:
        await super().deprovision(tenant_id, confirm_destroy=confirm_destroy)
