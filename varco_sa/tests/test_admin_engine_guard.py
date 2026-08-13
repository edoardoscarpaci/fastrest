"""
Failing tests for varco_sa.tenancy.admin.engine.SAAdminEngine (Plan 007,
Phase 6, step 4 — RD-4 / RD-9).
"""

from __future__ import annotations

import logging

import pytest


def test_provisioner_cannot_be_constructed_without_admin_dsn() -> None:
    from varco_sa.tenancy.admin.db_provisioner import SADatabaseProvisioner

    with pytest.raises(ValueError) as exc:
        SADatabaseProvisioner(admin_dsn=None)

    assert "VARCO_TENANCY_ADMIN_DSN" in str(exc.value)


def test_provisioner_refuses_admin_dsn_equal_to_app_dsn() -> None:
    from varco_sa.tenancy.admin.db_provisioner import SADatabaseProvisioner

    same_dsn = "postgresql+asyncpg://user:pw@host/app_db"

    with pytest.raises(ValueError):
        SADatabaseProvisioner(admin_dsn=same_dsn, app_dsn=same_dsn)


async def test_maintenance_engine_is_nullpool_and_disposed_in_finally() -> None:
    from varco_sa.tenancy.admin.engine import SAAdminEngine
    from sqlalchemy.pool import NullPool

    admin_engine = SAAdminEngine(admin_dsn="sqlite+aiosqlite://")

    async with admin_engine as engine:
        assert isinstance(engine.pool, NullPool)

    assert admin_engine.disposed is True


async def test_admin_engine_disposed_even_if_body_raises() -> None:
    from varco_sa.tenancy.admin.engine import SAAdminEngine

    admin_engine = SAAdminEngine(admin_dsn="sqlite+aiosqlite://")

    with pytest.raises(RuntimeError):
        async with admin_engine:
            raise RuntimeError("boom")

    assert admin_engine.disposed is True


def test_admin_dsn_present_without_mount_logs_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from varco_sa.tenancy.admin.guard import warn_if_admin_dsn_unmounted

    with caplog.at_level(logging.WARNING):
        warn_if_admin_dsn_unmounted(admin_dsn_present=True, admin_mounted=False)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "standalone" in warnings[0].getMessage().lower()
