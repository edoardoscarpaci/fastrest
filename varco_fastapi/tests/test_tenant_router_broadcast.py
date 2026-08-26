"""
Failing tests for Plan 008 Phase 2, step 8 — the three new broadcast/manual-
activation routes on ``build_tenant_router``.
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
        self.request_provision_calls: list[str] = []
        self.request_deprovision_calls: list[tuple[str, bool]] = []
        self.mark_active_calls: list[str] = []
        self.local_ddl_calls = 0

    async def provision(self, tenant_id: str, **kwargs):
        from varco_core.tenancy.catalog import TenantDescriptor
        from varco_core.tenancy.settings import TenantStatus

        self.local_ddl_calls += 1
        descriptor = TenantDescriptor(tenant_id=tenant_id, status=TenantStatus.ACTIVE)
        self._descriptors[tenant_id] = descriptor
        return descriptor

    async def request_provision(self, tenant_id: str) -> None:
        self.request_provision_calls.append(tenant_id)

    async def request_deprovision(
        self, tenant_id: str, *, confirm: bool = False
    ) -> None:
        if not confirm:
            from varco_core.tenancy.provisioner import DestructiveOperationRefused

            raise DestructiveOperationRefused(tenant_id)
        self.request_deprovision_calls.append((tenant_id, confirm))

    async def mark_active(self, tenant_id: str):
        from varco_core.tenancy.catalog import TenantDescriptor
        from varco_core.tenancy.settings import TenantStatus

        self.mark_active_calls.append(tenant_id)
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


def _build_app(role: str | None, control_service: "_FakeControlService | None" = None):
    from varco_fastapi.tenancy.router import build_tenant_router

    ctx = AuthContext(user_id="u1", roles=frozenset({role}) if role else frozenset())
    router = build_tenant_router(
        control_service or _FakeControlService(), server_auth=_StubAuth(ctx)
    )
    app = FastAPI()
    app.include_router(router)
    return app


def test_request_provision_route_returns_202_and_no_local_ddl() -> None:
    control_service = _FakeControlService()
    app = _build_app("tenant-admin", control_service)
    client = TestClient(app)

    response = client.post("/tenancy/tenants/acme/request-provision")

    assert response.status_code == 202
    assert control_service.request_provision_calls == ["acme"]
    assert control_service.local_ddl_calls == 0


def test_activate_route_returns_200_and_flips_active() -> None:
    control_service = _FakeControlService()
    app = _build_app("tenant-admin", control_service)
    client = TestClient(app)

    response = client.post("/tenancy/tenants/acme/activate")

    assert response.status_code == 200
    assert control_service.mark_active_calls == ["acme"]


def test_broadcast_delete_requires_explicit_confirm() -> None:
    control_service = _FakeControlService()
    app = _build_app("tenant-admin", control_service)
    client = TestClient(app)

    response = client.request("DELETE", "/tenancy/tenants/acme?broadcast=true", json={})

    assert response.status_code == 400
    assert control_service.request_deprovision_calls == []


def test_broadcast_delete_with_confirm_calls_request_deprovision() -> None:
    control_service = _FakeControlService()
    app = _build_app("tenant-admin", control_service)
    client = TestClient(app)

    response = client.request(
        "DELETE", "/tenancy/tenants/acme?broadcast=true", json={"confirm": True}
    )

    assert response.status_code == 204
    assert control_service.request_deprovision_calls == [("acme", True)]


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/tenancy/tenants/acme/request-provision"),
        ("POST", "/tenancy/tenants/acme/activate"),
    ],
)
def test_new_routes_are_admin_guarded_non_admin_gets_403(
    method: str, path: str
) -> None:
    app = _build_app("not-an-admin")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.request(method, path)

    assert response.status_code == 403
