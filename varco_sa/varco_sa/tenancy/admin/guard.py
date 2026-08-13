"""
varco_sa.tenancy.admin.guard
===============================
``warn_if_admin_dsn_unmounted()`` — RD-9's "the DSN alone grants nothing"
observability note (Plan 007, Phase 6, step 4).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def warn_if_admin_dsn_unmounted(
    *, admin_dsn_present: bool, admin_mounted: bool
) -> None:
    """
    Log one WARNING when a traffic-serving process has an admin DSN
    configured but never mounted the admin surface.

    The DSN alone exposes nothing — this is purely a topology hint,
    recommending the standalone control-plane deployment (RD-9).
    """
    if admin_dsn_present and not admin_mounted:
        logger.warning(
            "VARCO_TENANCY_ADMIN_DSN is set on a process that never called "
            "mount_tenant_admin() — the admin DSN alone grants no route, "
            "but this is the wrong topology: prefer the standalone "
            "control-plane deployment (a small app mounting only the "
            "tenancy admin router, with the admin DSN in its own "
            "environment only) over putting an unused admin credential in "
            "an app pod's environment (RD-9)."
        )
