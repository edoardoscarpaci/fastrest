"""
Failing tests for varco_sa.tenancy.guard (Plan 007, Phase 6, step 8 — RD-8,
re-framed as a config guard).
"""

from __future__ import annotations

import pytest


def test_database_isolation_without_fanout_flag_and_a_relay_raises() -> None:
    from varco_core.tenancy.catalog import TenantIsolationError
    from varco_core.tenancy.settings import TenantIsolation
    from varco_sa.tenancy.guard import guard_fanout_configuration

    with pytest.raises(TenantIsolationError) as exc:
        guard_fanout_configuration(
            isolation=TenantIsolation.DATABASE,
            fanout_framework_tables=False,
            relay_configured=True,
        )

    assert "VARCO_TENANCY_FANOUT_FRAMEWORK_TABLES" in str(exc.value)


def test_database_isolation_with_fanout_flag_and_relay_succeeds() -> None:
    from varco_core.tenancy.settings import TenantIsolation
    from varco_sa.tenancy.guard import guard_fanout_configuration

    guard_fanout_configuration(
        isolation=TenantIsolation.DATABASE,
        fanout_framework_tables=True,
        relay_configured=True,
    )


def test_database_isolation_with_no_relay_configured_no_error() -> None:
    from varco_core.tenancy.settings import TenantIsolation
    from varco_sa.tenancy.guard import guard_fanout_configuration

    guard_fanout_configuration(
        isolation=TenantIsolation.DATABASE,
        fanout_framework_tables=False,
        relay_configured=False,
    )
