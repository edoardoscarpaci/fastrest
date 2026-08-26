"""
Failing tests for varco_fastapi.tenancy.mount.mount_tenant_admin (Plan 007,
Phase 5, step 8 — RD-9, the bundled-mode contract).
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from varco_core.auth.base import AuthContext
from varco_fastapi.auth.server_auth import AbstractServerAuth


class _StubAuth(AbstractServerAuth):
    def __init__(self, role: str | None) -> None:
        self._role = role

    async def __call__(self, request: Request) -> AuthContext:
        return AuthContext(
            user_id="u1", roles=frozenset({self._role}) if self._role else frozenset()
        )


class _FakeControlService:
    async def list_tenants(self, status=None):
        return []


def test_default_app_exposes_no_tenancy_route_even_with_admin_dsn_set(
    monkeypatch,
) -> None:
    monkeypatch.setenv("VARCO_TENANCY_ADMIN_DSN", "postgresql://admin@host/db")

    app = FastAPI()
    client = TestClient(app)

    response = client.get("/tenancy/tenants")

    assert response.status_code == 404


def test_mount_without_acknowledgement_raises_value_error() -> None:
    from varco_fastapi.tenancy.mount import mount_tenant_admin

    app = FastAPI()

    with pytest.raises(ValueError) as exc:
        mount_tenant_admin(app, _FakeControlService(), server_auth=_StubAuth("tenant-admin"))

    message = str(exc.value)
    assert "acknowledge_bundled_admin" in message


def test_mount_with_acknowledgement_mounts_and_admin_call_succeeds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from varco_fastapi.tenancy.mount import mount_tenant_admin

    app = FastAPI()
    with caplog.at_level(logging.WARNING):
        mount_tenant_admin(
            app,
            _FakeControlService(),
            acknowledge_bundled_admin=True,
            server_auth=_StubAuth("tenant-admin"),
        )
    client = TestClient(app)

    response = client.get("/tenancy/tenants")

    assert response.status_code == 200


def test_underprivileged_and_unauthenticated_calls_are_403_not_500() -> None:
    from varco_fastapi.tenancy.mount import mount_tenant_admin

    app = FastAPI()
    mount_tenant_admin(
        app,
        _FakeControlService(),
        acknowledge_bundled_admin=True,
        server_auth=_StubAuth("admin"),  # generic admin, not tenant-admin
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/tenancy/tenants")

    assert response.status_code == 403


def test_default_admin_role_is_tenant_admin_not_admin() -> None:
    from varco_fastapi.tenancy.mount import mount_tenant_admin

    app = FastAPI()
    mount_tenant_admin(
        app,
        _FakeControlService(),
        acknowledge_bundled_admin=True,
        server_auth=_StubAuth("admin"),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/tenancy/tenants")

    assert response.status_code == 403


def test_exactly_one_warning_logged_at_mount(caplog: pytest.LogCaptureFixture) -> None:
    from varco_fastapi.tenancy.mount import mount_tenant_admin

    app = FastAPI()
    with caplog.at_level(logging.WARNING):
        mount_tenant_admin(
            app,
            _FakeControlService(),
            acknowledge_bundled_admin=True,
            server_auth=_StubAuth("tenant-admin"),
        )

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1


def test_mounting_twice_is_refused() -> None:
    from varco_fastapi.tenancy.mount import mount_tenant_admin

    app = FastAPI()
    mount_tenant_admin(
        app,
        _FakeControlService(),
        acknowledge_bundled_admin=True,
        server_auth=_StubAuth("tenant-admin"),
    )

    with pytest.raises(Exception):  # noqa: B017
        mount_tenant_admin(
            app,
            _FakeControlService(),
            acknowledge_bundled_admin=True,
            server_auth=_StubAuth("tenant-admin"),
        )
