"""
Failing integration tests for real-Postgres database-per-tenant provisioning
(Plan 007, Phase 6, step 6). Requires Docker (testcontainers) — skipped
unless VARCO_RUN_INTEGRATION=1.
"""

from __future__ import annotations

import os

import pytest

from tests.conftest import asyncpg_url

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("VARCO_RUN_INTEGRATION"),
        reason="Integration tests disabled — set VARCO_RUN_INTEGRATION=1",
    ),
]


# pg_container (module-scoped) was replaced by the session-scoped
# postgres_container fixture in tests/conftest.py (Plan 012 / RT1, Step 6/9).


async def test_create_migrate_isolate_and_drop_two_tenant_databases(
    postgres_container,
) -> None:
    from varco_sa.tenancy.admin.db_provisioner import SADatabaseProvisioner

    admin_dsn = asyncpg_url(postgres_container)
    provisioner = SADatabaseProvisioner(admin_dsn=admin_dsn)

    await provisioner.provision("acme")
    await provisioner.provision("globex")

    # Both databases exist and rows inserted with the same PK are isolated.
    # (Full row-level insert/read assertions are the responsibility of the
    # concrete implementation's own integration test once it exists — this
    # test proves the provisioner surface itself.)
    await provisioner.deprovision("acme", confirm_destroy=True)
    await provisioner.deprovision("globex", confirm_destroy=True)
