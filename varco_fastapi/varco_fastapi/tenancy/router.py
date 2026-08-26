"""
varco_fastapi.tenancy.router
===============================
``build_tenant_router()`` — the REST tenant-provisioning admin surface
(Plan 007, Phase 5, step 6-7).

DESIGN: plain ``APIRouter``, mirroring ``varco_casbin.build_policy_router``
    See the plan's "DESIGN: REST admin surface — plain APIRouter" section.
    Summary: provisioning is orchestration (validate -> create -> migrate
    -> activate), not repository CRUD — ``VarcoCRUDRouter``'s generated
    surface and typed ``D/PK/C/R/U`` args are the wrong shape for a
    control-plane resource that isn't an ``AsyncService``.

This module imports **only** ``varco_core.tenancy`` (+ ``varco_fastapi``'s
own auth primitives) — never ``varco_sa``/``varco_beanie``/``sqlalchemy``/
``pymongo`` (enforced by ``test_tenancy_import_guard.py``).

Unlike ``build_policy_router`` (which relies on the host app's own
``add_exception_handlers()`` to translate ``ServiceAuthorizationError`` into
HTTP 403), this router translates authorization and control-plane errors to
HTTP responses **itself** — the admin surface must behave identically
whether mounted into a full ``create_varco_app()`` or a bare ``FastAPI()``
(the standalone control-plane shape, RD-9).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel
from varco_core.tenancy.provisioner import DestructiveOperationRefused
from varco_core.tenancy.settings import TenantStatus

from varco_fastapi.auth.guard import require_roles

if TYPE_CHECKING:
    from varco_core.auth.base import AuthContext

    from varco_fastapi.auth.server_auth import AbstractServerAuth


class _ProvisionBody(BaseModel):
    tenant_id: str


class _DeleteBody(BaseModel):
    confirm: bool = False


def build_tenant_router(
    control_service: Any,
    *,
    server_auth: AbstractServerAuth | None,
    admin_role: str = "tenant-admin",
    prefix: str = "/tenancy",
    tags: list[str] | None = None,
    coordinator: Any | None = None,
) -> APIRouter:
    """
    Build a FastAPI router exposing tenant provisioning primitives.

    Args:
        control_service: A ``TenantControlService``-shaped object —
                         ``provision``/``deprovision``/``suspend``/
                         ``resume``/``list_tenants``.
        server_auth:     Auth strategy producing an ``AuthContext``.
                         **Required** — a guard that can never be satisfied
                         is a startup error (mirrors the ``requires=``
                         without ``_auth`` rule).
        admin_role:      Role required on every route. Defaults to
                         ``"tenant-admin"`` — deliberately **not** the
                         generic ``"admin"``, so an app's existing admin
                         role does not silently gain tenant provisioning.
        prefix:          URL prefix. Defaults to ``"/tenancy"``.
        tags:            OpenAPI tags. Defaults to ``["tenancy"]``.
        coordinator:     Optional ``TenantReadinessCoordinator``-shaped
                         object exposing ``readiness(tenant_id) ->
                         TenantReadiness``. When provided, ``GET
                         /tenants/{id}/readiness`` is mounted (Plan 008,
                         Phase 3). Omitted (default) — that route is not
                         registered at all.

    Returns:
        A configured ``fastapi.APIRouter`` ready for ``app.include_router``.

    Raises:
        ValueError: ``server_auth`` is ``None``.

    Surface:
        ``POST /tenancy/tenants`` (201, idempotent — 200 on redelivery),
        ``GET /tenancy/tenants`` (``status=`` filter),
        ``GET /tenancy/tenants/{id}``,
        ``PATCH /tenancy/tenants/{id}`` (``{"action": "suspend"|"resume"}``),
        ``DELETE /tenancy/tenants/{id}`` (requires ``{"confirm": true}`` —
        omitted/false is 400 and performs nothing; ``?broadcast=true``
        calls ``request_deprovision()`` instead of the local ``deprovision()``),
        ``POST /tenancy/tenants/{id}/migrate``,
        ``POST /tenancy/tenants/{id}/request-provision`` (202, Plan 008 —
        broadcast-only, no local DDL),
        ``POST /tenancy/tenants/{id}/activate`` (200, Plan 008 — manual
        ``mark_active()`` terminator),
        ``GET /tenancy/tenants/{id}/readiness`` (Plan 008, Phase 3 — only
        mounted when ``coordinator=`` is given).

    Edge cases:
        - Every route requires ``admin_role`` — a non-admin (or
          unauthenticated) caller gets 403, never 500.
        - A duplicate ``POST`` is idempotent (200, not 201) — mirrors
          ``TenantControlService.provision()``'s idempotency.
    """
    if server_auth is None:
        raise ValueError(
            "build_tenant_router() requires server_auth — a guard that can "
            "never be satisfied is a startup error, not a per-request one."
        )

    router = APIRouter(prefix=prefix, tags=tags or ["tenancy"])  # type: ignore[arg-type]
    guard = require_roles(admin_role)

    async def _admin(request: Request) -> AuthContext:
        from varco_core.exception.service import ServiceAuthorizationError

        ctx = await server_auth(request)
        try:
            await guard.check(ctx)
        except ServiceAuthorizationError as exc:
            # Translated here (not left to the host app's exception
            # handlers) so this router behaves identically whether mounted
            # into a full create_varco_app() or a bare FastAPI() — the
            # standalone control-plane shape (RD-9).
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return ctx

    admin = Depends(_admin)

    @router.post("/tenants", status_code=201)
    async def provision_tenant(body: _ProvisionBody, _ctx: AuthContext = admin) -> dict:
        existing_before = None
        try:
            existing_before = [
                d
                for d in await control_service.list_tenants(status=None)
                if d.tenant_id == body.tenant_id
            ]
        except Exception:  # noqa: BLE001 - best-effort idempotency status code
            existing_before = []

        descriptor = await control_service.provision(body.tenant_id)

        # Idempotent POST — a redelivery/duplicate call returns 200, not 201.
        status_code = 200 if existing_before else 201
        return _JSONWithStatus(descriptor, status_code)

    @router.get("/tenants")
    async def list_tenants(status: str | None = None, _ctx: AuthContext = admin) -> list[dict]:
        parsed_status = TenantStatus(status) if status else None
        descriptors = await control_service.list_tenants(status=parsed_status)
        return [_descriptor_to_dict(d) for d in descriptors]

    @router.get("/tenants/{tenant_id}")
    async def get_tenant(tenant_id: str, _ctx: AuthContext = admin) -> dict:
        try:
            descriptors = await control_service.list_tenants(status=None)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        for d in descriptors:
            if d.tenant_id == tenant_id:
                return _descriptor_to_dict(d)
        raise HTTPException(status_code=404, detail=f"Unknown tenant {tenant_id!r}.")

    @router.patch("/tenants/{tenant_id}")
    async def patch_tenant(
        tenant_id: str, body: dict = Body(...), _ctx: AuthContext = admin
    ) -> dict:
        action = body.get("action")
        if action == "suspend":
            await control_service.suspend(tenant_id)
        elif action == "resume":
            await control_service.resume(tenant_id)
        else:
            raise HTTPException(
                status_code=400,
                detail="PATCH body must be {'action': 'suspend'|'resume'}.",
            )
        return {"tenant_id": tenant_id, "action": action}

    @router.delete("/tenants/{tenant_id}", status_code=204)
    async def delete_tenant(
        tenant_id: str,
        broadcast: bool = False,
        body: _DeleteBody = Body(default=_DeleteBody()),
        _ctx: AuthContext = admin,
    ) -> None:
        if not body.confirm:
            raise HTTPException(
                status_code=400,
                detail=(
                    "DELETE requires an explicit confirm=true body field — "
                    "this is a destructive operation."
                ),
            )
        try:
            if broadcast:
                await control_service.request_deprovision(tenant_id, confirm=True)
            else:
                await control_service.deprovision(tenant_id, confirm=True)
        except DestructiveOperationRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/tenants/{tenant_id}/migrate")
    async def migrate_tenant(tenant_id: str, _ctx: AuthContext = admin) -> dict:
        migrate_fn = getattr(control_service, "migrate", None)
        if migrate_fn is None:
            raise HTTPException(
                status_code=501,
                detail="This control service does not support POST .../migrate.",
            )
        await migrate_fn(tenant_id)
        return {"tenant_id": tenant_id, "migrated": True}

    @router.post("/tenants/{tenant_id}/request-provision", status_code=202)
    async def request_provision_tenant(tenant_id: str, _ctx: AuthContext = admin) -> dict:
        """
        Broadcast-only (RD-14): emits ``TenantProvisionRequested`` fleet-wide
        without any local catalog write or provisioner call. Pairs with
        ``provision()``/``POST /tenants`` for a node that must also
        provision itself.
        """
        await control_service.request_provision(tenant_id)
        return {"tenant_id": tenant_id, "broadcast": "provision"}

    @router.post("/tenants/{tenant_id}/activate")
    async def activate_tenant(tenant_id: str, _ctx: AuthContext = admin) -> dict:
        """Manual terminator (Plan 008, Phase 3) — flips ``tenant_id`` to
        ``ACTIVE`` without waiting for a ``TenantReadinessCoordinator``."""
        descriptor = await control_service.mark_active(tenant_id)
        if descriptor is not None:
            return _descriptor_to_dict(descriptor)
        return {"tenant_id": tenant_id, "status": "active"}

    if coordinator is not None:

        @router.get("/tenants/{tenant_id}/readiness")
        async def get_tenant_readiness(tenant_id: str, _ctx: AuthContext = admin) -> dict:
            """
            Readiness snapshot (Plan 008, Phase 3).

            404 means "this coordinator holds no readiness state for that
            tenant" — an unknown tenant, or one whose in-memory state was
            lost to a coordinator restart (RD-18). It does NOT mean the
            tenant is absent from the catalog; check ``GET /tenancy/tenants
            ?status=pending`` for that, and recover with a re-broadcast.
            """
            from varco_core.tenancy.catalog import TenantNotFoundError

            try:
                snapshot = coordinator.readiness(tenant_id)
            except TenantNotFoundError as exc:
                raise HTTPException(
                    status_code=404, detail=f"Unknown tenant {tenant_id!r}."
                ) from exc
            return {
                "tenant_id": snapshot.tenant_id,
                "seen": sorted(snapshot.seen),
                "expected": sorted(snapshot.expected),
                "missing": sorted(snapshot.missing),
                "complete": snapshot.complete,
            }

    return router


def _descriptor_to_dict(descriptor: Any) -> dict:
    return {
        "tenant_id": descriptor.tenant_id,
        "schema": descriptor.schema,
        "database": descriptor.database,
        "status": (
            descriptor.status.value if hasattr(descriptor.status, "value") else descriptor.status
        ),
    }


def _JSONWithStatus(descriptor: Any, status_code: int) -> Any:  # noqa: N802 - internal helper
    # Returns a starlette JSONResponse, not a dict — FastAPI special-cases a
    # returned Response subclass (bypasses normal serialization), which is
    # exactly the point (see below). Typed Any rather than JSONResponse to
    # avoid importing starlette at module scope for one internal helper.
    # FastAPI's `status_code=201` decorator kwarg sets the DEFAULT response
    # code; a successful redelivery must instead answer 200. Returning a
    # JSONResponse with an explicit status overrides the route decorator's
    # default without needing two separate route functions.
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status_code, content=_descriptor_to_dict(descriptor))
