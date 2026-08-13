"""
Failing unit tests for varco_sa.tenancy.admin.db_provisioner.SADatabaseProvisioner
(Plan 007, Phase 6, step 6 — unit subset; see
test_database_provisioner_integration.py for the real-Postgres coverage).
"""

from __future__ import annotations

import pytest


class _RecordingConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []
        self.execution_options_calls: list[dict] = []

    def execution_options(self, **kwargs):
        self.execution_options_calls.append(kwargs)
        return self

    async def execute(self, stmt, *args, **kwargs):
        self.executed.append((str(stmt), kwargs))

        class _Result:
            def scalar(self_inner):
                return None

        return _Result()


async def test_create_database_uses_autocommit_isolation_level() -> None:
    from varco_sa.tenancy.admin.db_provisioner import SADatabaseProvisioner

    provisioner = SADatabaseProvisioner(admin_dsn="postgresql+asyncpg://a:b@h/admin")
    conn = _RecordingConnection()

    await provisioner._create_database(conn, "db_acme")  # type: ignore[attr-defined]

    assert any(
        call.get("isolation_level") == "AUTOCOMMIT"
        for call in conn.execution_options_calls
    )


async def test_drop_database_refuses_without_confirm_destroy() -> None:
    from varco_sa.tenancy.admin.db_provisioner import SADatabaseProvisioner

    provisioner = SADatabaseProvisioner(admin_dsn="postgresql+asyncpg://a:b@h/admin")

    with pytest.raises(Exception):  # noqa: B017
        await provisioner.deprovision("acme", confirm_destroy=False)
