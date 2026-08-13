"""
Failing integration test for real-Mongo database-per-tenant isolation
(Plan 007, Phase 7, step 5). Requires Docker (testcontainers) — skipped
unless VARCO_RUN_INTEGRATION=1.
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("VARCO_RUN_INTEGRATION"),
        reason="Integration tests disabled — set VARCO_RUN_INTEGRATION=1",
    ),
]


@pytest.fixture(scope="module")
def mongo_container():
    from testcontainers.mongodb import MongoDbContainer

    with MongoDbContainer("mongo:6") as mongo:
        yield mongo


async def test_two_tenant_databases_full_read_isolation_and_drop_removes_one(
    mongo_container,
) -> None:
    from varco_beanie.tenancy.provisioner import BeanieDatabaseProvisioner

    provisioner = BeanieDatabaseProvisioner(
        connection_string=mongo_container.get_connection_url()
    )

    await provisioner.provision("acme")
    await provisioner.provision("globex")

    await provisioner.deprovision("acme", confirm_destroy=True)
    # "globex" database must remain untouched by "acme"'s dropDatabase.
