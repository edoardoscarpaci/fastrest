"""
Unit tests for ``varco_beanie.tenancy.provisioner.BeanieDatabaseProvisioner``.

No Docker required — the Motor/pymongo async client is a hand-rolled double.

Why this file exists
--------------------
``BeanieDatabaseProvisioner`` had **zero** non-integration coverage, so its
constructor contract (``client=``, keyword-only) drifted away from its only
caller — a ``@pytest.mark.integration`` test that passed ``connection_string=``
and therefore only failed when someone ran the suite with a Docker daemon.
These tests pin the contract at unit level so the same drift cannot recur.

Thread safety:  N/A (unit tests)
Async safety:   the provisioner's methods are ``async def``; the doubles are too.
"""

from __future__ import annotations

import pytest
from varco_beanie.tenancy.provisioner import BeanieDatabaseProvisioner
from varco_core.tenancy.provisioner import DestructiveOperationRefused


class _FakeClient:
    """Minimal stand-in for a Motor/pymongo ``AsyncMongoClient``."""

    def __init__(self) -> None:
        self.dropped: list[str] = []
        self.accessed: list[str] = []

    def __getitem__(self, name: str) -> str:
        self.accessed.append(name)
        return f"db:{name}"

    async def drop_database(self, name: str) -> None:
        self.dropped.append(name)


class TestBeanieDatabaseProvisionerContract:
    def test_regression_constructor_takes_a_client_not_a_connection_string(
        self,
    ) -> None:
        """
        User reports: ``BeanieDatabaseProvisioner(connection_string=...)`` raises
        ``TypeError: unexpected keyword argument 'connection_string'``.  Correct
        behaviour is that the provisioner receives an already-built async
        ``client=``, because client construction and lifetime belong to the
        pool/registry (the same seam ``BeanieTenantPool`` uses) — a provisioner
        that dialled its own connection would leak one per instance.
        """
        client = _FakeClient()

        provisioner = BeanieDatabaseProvisioner(client=client)
        assert provisioner is not None

        with pytest.raises(TypeError):
            BeanieDatabaseProvisioner(connection_string="mongodb://localhost:27017")

    def test_client_is_keyword_only(self) -> None:
        """Positional construction is refused — the kwarg names the seam."""
        with pytest.raises(TypeError):
            BeanieDatabaseProvisioner(_FakeClient())  # type: ignore[misc]


class TestBeanieDatabaseProvisionerBehaviour:
    async def test_provision_without_index_guard_is_a_noop(self) -> None:
        """MongoDB creates databases lazily — nothing to do, and no error."""
        client = _FakeClient()
        provisioner = BeanieDatabaseProvisioner(client=client)

        await provisioner.provision("acme")

        assert client.accessed == []
        assert client.dropped == []

    async def test_deprovision_drops_only_the_named_tenants_database(self) -> None:
        client = _FakeClient()
        provisioner = BeanieDatabaseProvisioner(client=client)

        await provisioner.deprovision("acme", confirm_destroy=True)

        assert client.dropped == ["db_acme"]

    async def test_deprovision_without_confirm_destroy_is_refused(self) -> None:
        """The ABC's destructive-operation guard must not be bypassable."""
        client = _FakeClient()
        provisioner = BeanieDatabaseProvisioner(client=client)

        with pytest.raises(DestructiveOperationRefused):
            await provisioner.deprovision("acme")

        assert client.dropped == []

    async def test_db_template_is_honoured(self) -> None:
        client = _FakeClient()
        provisioner = BeanieDatabaseProvisioner(
            client=client, db_template="tenant-{tenant_id}-db"
        )

        await provisioner.deprovision("globex", confirm_destroy=True)

        assert client.dropped == ["tenant-globex-db"]
