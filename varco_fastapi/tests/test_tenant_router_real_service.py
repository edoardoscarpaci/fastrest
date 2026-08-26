"""
Contract tests wiring ``build_tenant_router()`` to a **real**
``TenantControlService`` instead of a hand-written fake.

DESIGN: why this file exists alongside the fake-based router tests
    Every other router test builds a ``_FakeControlService``. Three defects
    hid in exactly that gap — the router called ``control_service.
    list_tenants()`` (a method only the fakes had) and rendered the return
    value of ``provision()``/``mark_active()`` (which the real service did
    not return). Fakes agreed with the router and disagreed with reality, so
    the suite was green and the deployed surface was not.
    ✅ Pins the router against the real collaborator's actual signatures —
       a method rename or a dropped return value fails here immediately.
    ❌ Slower and more wiring than a fake, and it couples two packages'
       tests. Accepted: the fake-based tests keep covering branch/status-code
       logic; this one covers the contract between them.

Seam rule (Plan 007/008): imports only ``varco_core.tenancy`` — never
``varco_sa``/``varco_beanie``/``sqlalchemy``/``pymongo``.
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


class _RecordingProvisioner:
    """Minimal ``AbstractTenantProvisioner``-shaped double — the storage
    layer is the one collaborator that genuinely cannot be real here."""

    def __init__(self) -> None:
        self.provision_calls: list[str] = []
        self.deprovision_calls: list[str] = []

    async def provision(self, tenant_id: str, **kwargs: object) -> None:
        self.provision_calls.append(tenant_id)

    async def deprovision(self, tenant_id: str, *, confirm_destroy: bool = False) -> None:
        if not confirm_destroy:
            from varco_core.tenancy.provisioner import DestructiveOperationRefused

            raise DestructiveOperationRefused(tenant_id)
        self.deprovision_calls.append(tenant_id)


def _build(role: str = "tenant-admin", *, expected_stores: frozenset[str] | None = None):
    """
    Wire a real catalog + real control service + the router under test.

    Args:
        role:            Role stamped on the stub ``AuthContext``.
        expected_stores: When given, a real ``TenantReadinessCoordinator``
                         is built over the same control service and passed
                         to the router, mounting ``GET …/readiness``.
                         ``None`` (default) — that route is not registered.
    """
    from varco_core.event.memory import InMemoryEventBus
    from varco_core.event.producer import BusEventProducer
    from varco_core.tenancy.catalog import StaticTenantCatalog
    from varco_core.tenancy.control.service import TenantControlService
    from varco_fastapi.tenancy.router import build_tenant_router

    catalog = StaticTenantCatalog()
    provisioner = _RecordingProvisioner()
    bus = InMemoryEventBus()
    service = TenantControlService(
        catalog=catalog,
        provisioner=provisioner,
        producer=BusEventProducer(bus),
    )

    coordinator = None
    if expected_stores is not None:
        from varco_core.tenancy.control.readiness import TenantReadinessCoordinator

        coordinator = TenantReadinessCoordinator(
            control_service=service, expected_stores=expected_stores
        )

    ctx = AuthContext(user_id="u1", roles=frozenset({role}))
    router = build_tenant_router(service, server_auth=_StubAuth(ctx), coordinator=coordinator)
    app = FastAPI()
    app.include_router(router)
    return app, service, catalog, provisioner


async def test_post_tenants_renders_the_real_provision_return_value() -> None:
    """``provision()`` must return the post-transition ``TenantDescriptor``
    — the router renders it directly."""
    app, _service, _catalog, provisioner = _build()
    client = TestClient(app)

    response = client.post("/tenancy/tenants", json={"tenant_id": "acme"})

    assert response.status_code == 201
    assert response.json() == {
        "tenant_id": "acme",
        "schema": None,
        "database": None,
        "status": "active",
    }
    assert provisioner.provision_calls == ["acme"]


async def test_post_tenants_is_idempotent_and_answers_200_on_redelivery() -> None:
    app, _service, _catalog, provisioner = _build()
    client = TestClient(app)

    first = client.post("/tenancy/tenants", json={"tenant_id": "acme"})
    second = client.post("/tenancy/tenants", json={"tenant_id": "acme"})

    assert first.status_code == 201
    assert second.status_code == 200
    # Idempotency is the status check, not a new dedup mechanism (RD-1).
    assert provisioner.provision_calls == ["acme"]


async def test_list_and_get_route_through_the_real_control_service() -> None:
    """``list_tenants()`` must exist on ``TenantControlService`` itself —
    the router has exactly one collaborator, not a (service, catalog) pair."""
    app, _service, _catalog, _provisioner = _build()
    client = TestClient(app)
    client.post("/tenancy/tenants", json={"tenant_id": "acme"})

    listed = client.get("/tenancy/tenants")
    fetched = client.get("/tenancy/tenants/acme")
    missing = client.get("/tenancy/tenants/nope")

    assert listed.status_code == 200
    assert [d["tenant_id"] for d in listed.json()] == ["acme"]
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "active"
    assert missing.status_code == 404


async def test_activate_renders_the_real_mark_active_return_value() -> None:
    from varco_core.tenancy.catalog import TenantDescriptor
    from varco_core.tenancy.settings import TenantStatus

    app, _service, catalog, _provisioner = _build()
    await catalog.add(TenantDescriptor(tenant_id="acme", status=TenantStatus.PENDING))
    client = TestClient(app)

    response = client.post("/tenancy/tenants/acme/activate")

    assert response.status_code == 200
    assert response.json()["status"] == "active"
    assert (await catalog.get("acme")).status is TenantStatus.ACTIVE


async def test_request_provision_has_no_local_effect_on_the_real_service() -> None:
    """RD-14: the broadcaster is explicitly NOT included — no catalog write,
    no provisioner call."""
    from varco_core.tenancy.catalog import TenantNotFoundError

    app, _service, catalog, provisioner = _build()
    client = TestClient(app)

    response = client.post("/tenancy/tenants/acme/request-provision")

    assert response.status_code == 202
    assert provisioner.provision_calls == []
    try:
        await catalog.get("acme")
    except TenantNotFoundError:
        pass
    else:  # pragma: no cover - only reached if the broadcast wrote locally
        raise AssertionError("request_provision() must not write the catalog")


async def test_readiness_route_404s_for_a_tenant_the_real_coordinator_never_saw() -> None:
    """The real ``TenantReadinessCoordinator.readiness()`` raises
    ``TenantNotFoundError`` for an unobserved tenant — the router's 404
    branch must be reachable with the real class, not only with a fake."""
    app, _service, _catalog, _provisioner = _build(expected_stores=frozenset({"orders", "billing"}))
    client = TestClient(app)

    response = client.get("/tenancy/tenants/never-seen/readiness")

    assert response.status_code == 404
