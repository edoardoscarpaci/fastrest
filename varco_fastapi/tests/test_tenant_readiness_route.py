"""
Failing tests for Plan 008 Phase 3, step 4 — ``GET /tenancy/tenants/{id}/
readiness``.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from varco_core.auth.base import AuthContext
from varco_fastapi.auth.server_auth import AbstractServerAuth


class _StubAuth(AbstractServerAuth):
    def __init__(self, ctx: AuthContext) -> None:
        self._ctx = ctx

    async def __call__(self, request: Request) -> AuthContext:
        return self._ctx


class _FakeReadiness:
    def __init__(self, tenant_id: str, seen, expected, missing, complete: bool) -> None:
        self.tenant_id = tenant_id
        self.seen = seen
        self.expected = expected
        self.missing = missing
        self.complete = complete


class _FakeCoordinator:
    def __init__(self) -> None:
        self._by_tenant: dict[str, _FakeReadiness] = {}

    def set_readiness(self, snapshot: "_FakeReadiness") -> None:
        self._by_tenant[snapshot.tenant_id] = snapshot

    def readiness(self, tenant_id: str) -> "_FakeReadiness":
        if tenant_id not in self._by_tenant:
            from varco_core.tenancy.catalog import TenantNotFoundError

            raise TenantNotFoundError(tenant_id)
        return self._by_tenant[tenant_id]


class _FakeControlService:
    async def provision(self, tenant_id: str, **kwargs):
        pass

    async def list_tenants(self, status=None):
        return []

    async def suspend(self, tenant_id: str):
        pass

    async def resume(self, tenant_id: str):
        pass

    async def deprovision(self, tenant_id: str, *, confirm: bool = False):
        pass


def _build_app(role: str, coordinator: "_FakeCoordinator"):
    from varco_fastapi.tenancy.router import build_tenant_router

    ctx = AuthContext(user_id="u1", roles=frozenset({role}))
    router = build_tenant_router(
        _FakeControlService(), server_auth=_StubAuth(ctx), coordinator=coordinator
    )
    app = FastAPI()
    app.include_router(router)
    return app


def test_readiness_route_returns_snapshot() -> None:
    coordinator = _FakeCoordinator()
    coordinator.set_readiness(
        _FakeReadiness(
            tenant_id="acme",
            seen=frozenset({"orders"}),
            expected=frozenset({"orders", "billing"}),
            missing=frozenset({"billing"}),
            complete=False,
        )
    )
    app = _build_app("tenant-admin", coordinator)
    client = TestClient(app)

    response = client.get("/tenancy/tenants/acme/readiness")

    assert response.status_code == 200
    body = response.json()
    assert set(body["seen"]) == {"orders"}
    assert set(body["missing"]) == {"billing"}
    assert body["complete"] is False


def test_readiness_route_404_for_unknown_tenant() -> None:
    coordinator = _FakeCoordinator()
    app = _build_app("tenant-admin", coordinator)
    client = TestClient(app)

    response = client.get("/tenancy/tenants/unknown/readiness")

    assert response.status_code == 404


def test_readiness_route_is_admin_guarded() -> None:
    coordinator = _FakeCoordinator()
    coordinator.set_readiness(
        _FakeReadiness(
            tenant_id="acme",
            seen=frozenset(),
            expected=frozenset({"orders"}),
            missing=frozenset({"orders"}),
            complete=False,
        )
    )
    app = _build_app("not-an-admin", coordinator)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/tenancy/tenants/acme/readiness")

    assert response.status_code == 403


def test_readiness_after_simulated_coordinator_restart_is_incomplete_empty_seen() -> (
    None
):
    """RD-18 caveat made visible: a fresh coordinator (simulating a restart)
    reports complete=false with an empty seen set, rather than hiding the
    reset."""
    coordinator = _FakeCoordinator()
    coordinator.set_readiness(
        _FakeReadiness(
            tenant_id="acme",
            seen=frozenset(),
            expected=frozenset({"orders", "billing"}),
            missing=frozenset({"orders", "billing"}),
            complete=False,
        )
    )
    app = _build_app("tenant-admin", coordinator)
    client = TestClient(app)

    response = client.get("/tenancy/tenants/acme/readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["seen"] == []
    assert body["complete"] is False
