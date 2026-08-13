"""
varco_sa.tenancy.guard
=========================
``guard_fanout_configuration()`` — the RD-8 config guard (Plan 007, Phase
6, step 8-9).

Under ``TenantIsolation.DATABASE``, a tenant's ``OutboxEntry``/``Job``/
``AuditEntry`` rows live in *that tenant's* database, which the single
process-wide ``OutboxRelay``/``JobPoller``/``AuditConsumer`` never polls —
events would silently never publish. This guard fires only when db-per-
tenant is configured **without** fan-out enabled while a relay/runner is
actually wired, naming ``VARCO_TENANCY_FANOUT_FRAMEWORK_TABLES`` as the
flag that enables ``TenantFanoutSupervisor`` (Phase 8).
"""

from __future__ import annotations

from varco_core.tenancy.catalog import TenantIsolationError
from varco_core.tenancy.settings import TenantIsolation


def guard_fanout_configuration(
    *, isolation: TenantIsolation, fanout_framework_tables: bool, relay_configured: bool
) -> None:
    """
    Raise when db-per-tenant is configured without fan-out, while a
    relay/runner/audit consumer is actually wired.

    Args:
        isolation:                The configured ``TenantIsolation``.
        fanout_framework_tables:  ``TenancySettings.fanout_framework_tables``.
        relay_configured:         Whether an ``OutboxRelay``/job
                                  poller/audit consumer is wired for this
                                  deployment.

    Raises:
        TenantIsolationError: ``isolation == DATABASE`` and
            ``fanout_framework_tables`` is ``False`` while a relay is
            configured — naming ``VARCO_TENANCY_FANOUT_FRAMEWORK_TABLES``
            as the flag that enables fan-out.
    """
    if (
        isolation == TenantIsolation.DATABASE
        and not fanout_framework_tables
        and relay_configured
    ):
        raise TenantIsolationError(
            "TenantIsolation.DATABASE is configured with an OutboxRelay/"
            "job poller/audit consumer wired, but "
            "TenancySettings.fanout_framework_tables is False. Under "
            "database-per-tenant, a tenant's outbox/job/audit rows live "
            "in that tenant's own database — the process-wide relay never "
            "polls it, so events would never be published. Set "
            "VARCO_TENANCY_FANOUT_FRAMEWORK_TABLES=true (or "
            "TenancySettings(fanout_framework_tables=True)) to enable "
            "TenantFanoutSupervisor."
        )
