"""
router.py
=========
``GatewayRouter`` — a service-free API gateway built on ``GenericRouter``.

Demonstrates every ``RouteGuard`` variant on a set of stateless endpoints:

    GET  /health                   — anonymous (no token required)
    GET  /v1/echo                  — anonymous; echo query params
    GET  /v1/me                    — any authenticated caller; return identity
    GET  /v1/reports/summary       — ``require_scopes("reports:read")``
    POST /v1/admin/flush-cache     — ``require_roles("admin")``
    GET  /v1/internal/status       — ``require_predicate(...)`` (svc: prefix only)

No database, no broker, no Docker needed.  All responses are plain dicts
produced inline — this router is intentionally trivial so the focus stays on
the auth wiring.

DESIGN: GenericRouter alias over VarcoRouter[D, PK, C, R, U]
    ✅ ``validate_router_class`` skips the D/PK/C/R/U checks for GenericRouter.
    ✅ All cross-cutting features (middleware, guards, telemetry) work identically.
    ✅ Name signals intent — "no service behind this router".
    ❌ No typed CRUD routes — add individual ``@route`` handlers for each endpoint.

Thread safety:  ✅ All ClassVars are read-only after class definition.
Async safety:   ✅ No blocking I/O; handlers are async for consistency with the
                   framework's async handler requirement.
"""

from __future__ import annotations

from varco_core.auth.base import AuthContext

from varco_fastapi.auth.guard import (
    RouteGuard,
    allow_anonymous,
    require_predicate,
    require_roles,
    require_scopes,
)
from varco_fastapi.router.endpoint import route
from varco_fastapi.router.presets import GenericRouter


def _is_service_account(ctx: AuthContext) -> bool:
    """
    Return ``True`` if the caller's subject starts with the service-account prefix.

    Predicate used by ``require_predicate`` on the internal status endpoint.
    Service-to-service callers mint tokens with subjects like ``"svc:my-service"``;
    user tokens use ``"user:alice"`` — this check separates the two.

    Args:
        ctx: The authenticated caller's context.

    Returns:
        ``True`` when ``ctx.user_id`` starts with ``"svc:"``.

    Edge cases:
        - Anonymous callers (``ctx.user_id is None``) return ``False`` — the
          ``RouteGuard`` denies anonymous callers before calling the predicate,
          so this branch is unreachable in normal flow.
    """
    return bool(ctx.user_id and ctx.user_id.startswith("svc:"))


class GatewayRouter(GenericRouter):
    """
    Stateless API gateway router — showcases all ``RouteGuard`` variants.

    Requires a ``_auth`` at class level so that ``AuthContext`` is injected
    by ``RequestContextMiddleware`` on every request.  Public endpoints use
    ``allow_anonymous()`` to let the middleware run without blocking the call.

    ClassVars:
        _prefix:  All routes are mounted under ``""``.
        _auth:    Set by ``create_app()`` after the registry is ready.
        _tags:    OpenAPI tag for all routes in this router.

    DESIGN: _auth set via class attribute after registry construction
        ✅ ``JwtBearerAuth`` requires a live ``TrustedIssuerRegistry``.
        ✅ The registry calls ``register_authority()`` synchronously but
           ``load_all()`` must be awaited — deferred to the lifespan.
        ✅ Setting ``_auth`` on the class before ``build_router()`` is idiomatic;
           ``build_router()`` reads it at call-time, not decoration-time.
        ❌ Mutating a ClassVar at import time is unusual — acceptable here
           because ``create_app()`` constructs the app once per process.

    Thread safety:  ✅ ClassVars are read-only after ``create_app()`` returns.
    Async safety:   ✅ Handlers are async; no blocking I/O.
    """

    _prefix: str = ""
    _tags: list[str] = ["gateway"]
    # _auth is set by create_app() to JwtBearerAuth(registry=..., required=False).
    # required=False allows anonymous callers to reach allow_anonymous() routes.

    # ── Public endpoints ──────────────────────────────────────────────────────

    @route("GET", "/health", requires=allow_anonymous(), summary="Health check")
    async def health(self, ctx: AuthContext) -> dict:
        """
        Return a static 200 response — no token required.

        Returns:
            ``{"status": "ok"}``
        """
        return {"status": "ok"}

    @route("GET", "/v1/echo", requires=allow_anonymous(), summary="Echo query params")
    async def echo(self, ctx: AuthContext) -> dict:
        """
        Echo all query parameters back as a JSON object.

        Returns:
            ``{"echo": {<query-param-name>: <value>, ...}}``

        Edge cases:
            - FastAPI does not inject arbitrary query params into handlers;
              we return a static demo dict.  A real gateway would parse
              the raw query string from the ``Request`` object.
        """
        # DESIGN: static dict instead of parsing Request.query_params
        #   ✅ Keeps the handler signature simple — no FastAPI Request injection.
        #   ✅ Sufficient to demonstrate allow_anonymous() without Request coupling.
        #   ❌ Does not actually echo caller query params — acceptable for a demo.
        return {"echo": {"msg": "hello from the gateway"}}

    # ── Authenticated endpoint (any valid token) ─────────────────────────────

    @route(
        "GET",
        "/v1/me",
        requires=RouteGuard(),
        summary="Caller identity",
    )
    async def me(self, ctx: AuthContext) -> dict:
        """
        Return the caller's identity extracted from the JWT.

        No ``requires=`` means: deny anonymous callers, allow any authenticated
        caller regardless of roles or scopes.

        Returns:
            ``{"subject": <sub>, "roles": [...], "scopes": [...]}``
        """
        return {
            "subject": ctx.user_id,
            "roles": sorted(ctx.roles),
            "scopes": sorted(ctx.scopes),
        }

    # ── Scope-gated endpoint ──────────────────────────────────────────────────

    @route(
        "GET",
        "/v1/reports/summary",
        requires=require_scopes("reports:read"),
        summary="Aggregate report (requires reports:read scope)",
    )
    async def reports_summary(self, ctx: AuthContext) -> dict:
        """
        Return fake aggregate report data.

        Requires the caller's JWT to carry the ``"reports:read"`` scope.

        Returns:
            Mocked aggregate payload.
        """
        return {
            "total_requests": 42_000,
            "error_rate_pct": 0.3,
            "p99_latency_ms": 145,
            "generated_for": ctx.user_id,
        }

    # ── Role-gated endpoint ───────────────────────────────────────────────────

    @route(
        "POST",
        "/v1/admin/flush-cache",
        status_code=204,
        requires=require_roles("admin"),
        summary="Flush cache (admin only)",
    )
    async def admin_flush_cache(self, ctx: AuthContext) -> None:
        """
        Simulate an admin cache flush — returns 204 No Content.

        Requires the caller's JWT to carry the ``"admin"`` role.

        Returns:
            ``None`` (204 No Content).
        """
        # No real cache to flush in this example — the 204 response body is None.

    # ── Predicate-gated endpoint ──────────────────────────────────────────────

    @route(
        "GET",
        "/v1/internal/status",
        requires=require_predicate(_is_service_account),
        summary="Internal service status (service accounts only)",
    )
    async def internal_status(self, ctx: AuthContext) -> dict:
        """
        Return an internal health/status payload.

        Only service-account callers (subject starts with ``"svc:"``) may call
        this endpoint.  User tokens are denied with 403.

        Returns:
            ``{"ok": True, "caller": <subject>}``
        """
        return {"ok": True, "caller": ctx.user_id}


__all__ = ["GatewayRouter"]
