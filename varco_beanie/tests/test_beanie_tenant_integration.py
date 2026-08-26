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


# mongo_container (module-scoped) was replaced by the session-scoped
# mongo_url fixture in tests/conftest.py (Plan 012 / RT1, Step 6/7).


async def test_two_tenant_databases_full_read_isolation_and_drop_removes_one(
    mongo_url: str,
) -> None:
    """
    User-visible expectation: under ``TenantIsolation.DATABASE`` each tenant's
    data lives in its own Mongo database, and ``deprovision(confirm_destroy=True)``
    (the GDPR erasure primitive) drops exactly one of them.

    ``BeanieDatabaseProvisioner`` takes an already-built Motor/pymongo async
    ``client=``, never a ``connection_string=`` — the pool/registry owns client
    construction and lifetime (same seam as ``BeanieTenantPool``).
    """
    from pymongo import AsyncMongoClient
    from varco_beanie.tenancy.provisioner import BeanieDatabaseProvisioner

    client = AsyncMongoClient(mongo_url)
    provisioner = BeanieDatabaseProvisioner(client=client)

    try:
        await provisioner.provision("acme")
        await provisioner.provision("globex")

        # MongoDB creates databases lazily — write one document per tenant so
        # both databases physically exist and the drop is observable.
        await client["db_acme"]["orders"].insert_one({"_id": "a1", "tenant": "acme"})
        await client["db_globex"]["orders"].insert_one({"_id": "g1", "tenant": "globex"})

        names = await client.list_database_names()
        assert "db_acme" in names
        assert "db_globex" in names

        # Full read isolation — neither tenant's collection sees the other's row.
        assert await client["db_acme"]["orders"].find_one({"_id": "g1"}) is None
        assert await client["db_globex"]["orders"].find_one({"_id": "a1"}) is None

        await provisioner.deprovision("acme", confirm_destroy=True)

        # "globex" database must remain untouched by "acme"'s dropDatabase.
        names_after = await client.list_database_names()
        assert "db_acme" not in names_after
        assert "db_globex" in names_after
        assert await client["db_globex"]["orders"].find_one({"_id": "g1"}) is not None
    finally:
        await client.drop_database("db_acme")
        await client.drop_database("db_globex")
        await client.close()
