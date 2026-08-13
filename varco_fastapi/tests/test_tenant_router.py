"""
Failing tests for varco_fastapi.tenancy.router.build_tenant_router (Plan 007,
Phase 5, step 6).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from varco_core.auth.base import AuthContext
from varco_fastapi.auth.server_auth import AbstractServerAuth


class _StubAuth(AbstractServerAuth):
    def __init__(self, ctx: AuthContext) -> None:
        self._ctx = ctx

    async def __call__(self, request: Request) -> AuthContext:
        return self._ctx


class _FakeControlService:
    def __init__(self) -> None:
        self._descriptors: dict[str, object] = {}

    async def provision(self, tenant_id: str, **kwargs):
        from varco_core.tenancy.catalog import TenantDescriptor
        from varco_core.tenancy.settings import TenantStatus

        descriptor = TenantDescriptor(tenant_id=tenant_id, status=TenantStatus.ACTIVE)
        self._descriptors[tenant_id] = descriptor
        return descriptor

    async def list_tenants(self, status=None):
        return list(self._descriptors.values())

    async def suspend(self, tenant_id: str):
        pass

    async def resume(self, tenant_id: str):
        pass

    async def deprovision(self, tenant_id: str, *, confirm: bool = False):
        if not confirm:
            from varco_core.tenancy.provisioner import DestructiveOperationRefused

            raise DestructiveOperationRefused(tenant_id)


def _build_app(role: str | None) -> FastAPI:
    from varco_fastapi.tenancy.router import build_tenant_router

    ctx = AuthContext(user_id="u1", roles=frozenset({role}) if role else frozenset())
    router = build_tenant_router(_FakeControlService(), server_auth=_StubAuth(ctx))
    app = FastAPI()
    app.include_router(router)
    return app


def test_non_admin_gets_403_not_500() -> None:
    app = _build_app(role="not-an-admin")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/tenancy/tenants")

    assert response.status_code == 403


def test_post_tenants_provisions_and_returns_201() -> None:
    app = _build_app(role="tenant-admin")
    client = TestClient(app)

    response = client.post("/tenancy/tenants", json={"tenant_id": "acme"})

    assert response.status_code == 201


def test_delete_without_confirm_is_400_and_performs_nothing() -> None:
    app = _build_app(role="tenant-admin")
    client = TestClient(app)

    # httpx 0.28's TestClient.delete() does not accept a `json=` kwarg
    # (DELETE-with-body is discouraged by httpx's convenience methods) —
    # use the low-level .request() form, which does.
    response = client.request("DELETE", "/tenancy/tenants/acme", json={})

    assert response.status_code == 400


def test_build_tenant_router_refuses_without_server_auth() -> None:
    from varco_fastapi.tenancy.router import build_tenant_router

    with pytest.raises(
        Exception
    ):  # noqa: B017 - a guard that can never be satisfied is a startup error
        build_tenant_router(_FakeControlService(), server_auth=None)
