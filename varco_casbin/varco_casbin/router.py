"""
varco_casbin.router
===================
REST management API for Casbin policies and role assignments.

``build_policy_router`` returns a standard FastAPI ``APIRouter`` exposing
dynamic, persisted CRUD over a ``PolicyManagement`` backend.  Every route is
guarded by varco's own ``RouteGuard`` (``require_roles(admin_role)``) and
populates ``AuthContext`` through the injected ``AbstractServerAuth`` — so it
integrates with the varco_fastapi auth model exactly like a first-class router.

DESIGN: a FastAPI APIRouter factory rather than a VarcoRouter @route class
    ✅ Policy mutations carry JSON bodies (rule tokens, role assignments).  The
       ``varco_fastapi`` ``@route`` custom-handler injects only ``ctx`` and path
       params — it cannot bind a request body — so a normal FastAPI route is the
       correct tool for a body-driven CRUD API.
    ✅ Still framework-consistent: authentication via ``AbstractServerAuth`` and
       authorization via ``RouteGuard`` are the same primitives varco uses.
    ✅ Mounts with one ``app.include_router(...)`` call, or pass to
       ``create_varco_app(... )`` alongside other routers.
    ❌ Not a ``VarcoRouter`` subclass — it does not gain CRUD mixins / async
       offload.  Those do not apply to policy administration anyway.

This module requires the optional ``varco-casbin[fastapi]`` extra.

Thread safety:  ✅ The router is stateless; the backend serializes writes.
Async safety:   ✅ All handlers are ``async def`` and await the backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from varco_core.auth import AuthContext, PolicyEngine, PolicyManagement
from varco_core.auth.policy import EnforcementRequest
from varco_fastapi.auth import require_roles

if TYPE_CHECKING:
    from varco_fastapi.auth import AbstractServerAuth


# ── Request / response DTOs ───────────────────────────────────────────────────


class PolicyRuleDTO(BaseModel):
    """
    A single Casbin policy rule (``p`` line) as rule-token values.

    Attributes:
        values: Ordered rule tokens, e.g. ``["alice", "posts", "read"]`` for a
                ``p = sub, obj, act`` model.
        ptype:  Policy type — ``"p"`` for a permission rule (default).
    """

    model_config = {"frozen": True}

    values: list[str] = Field(..., min_length=1)
    ptype: str = "p"


class RoleAssignmentDTO(BaseModel):
    """
    A role assignment (``g`` line): grant ``role`` to ``user`` within ``domain``.

    Attributes:
        user:   Subject the role is granted to.
        role:   Role being granted.
        domain: Optional domain / tenant scope for domain-aware RBAC.
    """

    model_config = {"frozen": True}

    user: str
    role: str
    domain: str | None = None


class EnforceCheckDTO(BaseModel):
    """
    A what-if enforcement query mirroring ``EnforcementRequest``.

    Attributes:
        subject:       Principal identifier (Casbin ``sub``).
        object:        Resource identifier (Casbin ``obj``).
        action:        Operation (Casbin ``act``).
        subject_attrs: ABAC subject attribute bag.
        object_attrs:  ABAC object attribute bag.
        domain:        Optional domain / tenant scope.
    """

    model_config = {"frozen": True}

    subject: str
    object: str
    action: str
    subject_attrs: dict[str, object] = Field(default_factory=dict)
    object_attrs: dict[str, object] = Field(default_factory=dict)
    domain: str | None = None


def build_policy_router(
    backend: PolicyManagement,
    *,
    server_auth: AbstractServerAuth,
    admin_role: str = "admin",
    prefix: str = "/authz",
    tags: list[str] | None = None,
) -> APIRouter:
    """
    Build a FastAPI router that administers Casbin policy over REST.

    All routes require the caller to hold ``admin_role``; authentication is
    performed by ``server_auth`` (which yields the request's ``AuthContext``).

    Args:
        backend:     The ``PolicyManagement`` backend (a ``CasbinPolicyEngine``).
                     If it also implements ``PolicyEngine``, the ``/check``
                     route performs real enforcement; otherwise ``/check`` is
                     omitted.
        server_auth: Auth strategy producing an ``AuthContext`` from the request
                     (e.g. ``JwtBearerAuth``).
        admin_role:  Role required on every route.  Defaults to ``"admin"``.
        prefix:      URL prefix for all routes.  Defaults to ``"/authz"``.
        tags:        OpenAPI tags.  Defaults to ``["authz"]``.

    Returns:
        A configured ``fastapi.APIRouter`` ready for ``app.include_router``.

    Raises:
        (per-request) ServiceAuthorizationError: caller lacks ``admin_role`` or
            is anonymous → mapped to HTTP 403 by varco's exception handlers.

    Edge cases:
        - A ``backend`` that is not a ``PolicyEngine`` still exposes the full
          CRUD surface; only ``POST /check`` is skipped.
        - ``DELETE`` routes accept a JSON body (the rule/assignment to remove) —
          unusual but valid HTTP and the natural shape for token-tuple rules.

    Example::

        from varco_casbin.router import build_policy_router
        app.include_router(build_policy_router(engine, server_auth=auth))
    """
    router = APIRouter(prefix=prefix, tags=tags or ["authz"])
    # Reuse varco's own guard so authorization semantics match the rest of the app.
    guard = require_roles(admin_role)

    async def _admin(request: Request) -> AuthContext:
        """FastAPI dependency: authenticate, then enforce the admin guard."""
        # server_auth yields the AuthContext; the guard raises 403 on denial.
        ctx = await server_auth(request)
        await guard.check(ctx)
        return ctx

    admin = Depends(_admin)

    # ── Policy rules (p-lines) ────────────────────────────────────────────────

    @router.get("/policies")
    async def list_policies(
        ptype: str = "p",
        _: AuthContext = admin,
    ) -> list[list[str]]:
        """List policy rules of ``ptype`` (default ``p``)."""
        # Return lists (JSON arrays); tuples would serialize identically but
        # lists are the conventional JSON shape.
        return [list(row) for row in await backend.list_policies(ptype)]

    @router.post("/policies", status_code=201)
    async def add_policy(
        rule: PolicyRuleDTO,
        _: AuthContext = admin,
    ) -> dict[str, bool]:
        """Add a policy rule; ``{"added": false}`` if it already existed."""
        added = await backend.add_policy(*rule.values, ptype=rule.ptype)
        return {"added": added}

    @router.delete("/policies")
    async def remove_policy(
        rule: PolicyRuleDTO,
        _: AuthContext = admin,
    ) -> dict[str, bool]:
        """Remove a policy rule; ``{"removed": false}`` if no match existed."""
        removed = await backend.remove_policy(*rule.values, ptype=rule.ptype)
        return {"removed": removed}

    # ── Role assignments (g-lines) ────────────────────────────────────────────

    @router.get("/roles")
    async def roles_for_user(
        user: str,
        domain: str | None = None,
        _: AuthContext = admin,
    ) -> list[str]:
        """List the roles assigned to ``user`` (optionally within ``domain``)."""
        return await backend.roles_for_user(user, domain)

    @router.post("/roles", status_code=201)
    async def add_role(
        assignment: RoleAssignmentDTO,
        _: AuthContext = admin,
    ) -> dict[str, bool]:
        """Grant a role to a user; ``{"added": false}`` if already assigned."""
        added = await backend.add_role_for_user(
            assignment.user, assignment.role, assignment.domain
        )
        return {"added": added}

    @router.delete("/roles")
    async def remove_role(
        assignment: RoleAssignmentDTO,
        _: AuthContext = admin,
    ) -> dict[str, bool]:
        """Revoke a role from a user; ``{"removed": false}`` if not assigned."""
        removed = await backend.remove_role_for_user(
            assignment.user, assignment.role, assignment.domain
        )
        return {"removed": removed}

    # ── Reload ────────────────────────────────────────────────────────────────

    @router.post("/reload")
    async def reload(_: AuthContext = admin) -> dict[str, str]:
        """Reload policy from the durable store (no-op for in-memory)."""
        await backend.reload()
        return {"status": "reloaded"}

    # ── Enforcement check (only when the backend can enforce) ─────────────────

    if isinstance(backend, PolicyEngine):
        engine = backend  # narrow for the closure below

        @router.post("/check")
        async def check(
            query: EnforceCheckDTO,
            _: AuthContext = admin,
        ) -> dict[str, bool]:
            """Run a what-if enforcement decision for the supplied request."""
            allowed = await engine.enforce(
                EnforcementRequest(
                    subject=query.subject,
                    object=query.object,
                    action=query.action,
                    subject_attrs=query.subject_attrs,
                    object_attrs=query.object_attrs,
                    domain=query.domain,
                )
            )
            return {"allowed": allowed}

    return router


__all__ = [
    "build_policy_router",
    "PolicyRuleDTO",
    "RoleAssignmentDTO",
    "EnforceCheckDTO",
]
