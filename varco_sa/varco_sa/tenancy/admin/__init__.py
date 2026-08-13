"""
varco_sa.tenancy.admin
=========================
Cluster-DDL-confined admin path — ``SAAdminEngine``,
``SADatabaseProvisioner``, ``warn_if_admin_dsn_unmounted`` (Plan 007,
Phase 6, RD-4/RD-9).

This subpackage is deliberately separate from ``varco_sa.tenancy``'s
request-path code: nothing here is reachable from an app that only imports
the rest of ``varco_sa.tenancy`` — only from an explicit admin DSN and, in
the FastAPI layer, an explicit ``mount_tenant_admin(...,
acknowledge_bundled_admin=True)`` call.
"""

from __future__ import annotations

from varco_sa.tenancy.admin.db_provisioner import SADatabaseProvisioner
from varco_sa.tenancy.admin.engine import SAAdminEngine
from varco_sa.tenancy.admin.guard import warn_if_admin_dsn_unmounted

__all__ = ["SAAdminEngine", "SADatabaseProvisioner", "warn_if_admin_dsn_unmounted"]
