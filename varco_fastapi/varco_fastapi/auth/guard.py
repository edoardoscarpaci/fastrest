"""
varco_fastapi.auth.guard
========================
Declarative, immutable authorization guards for ``@route``-decorated handlers.

``RouteGuard`` is the *router-layer* authorization primitive.  It evaluates
against the ``AuthContext`` produced by the server's authentication middleware
**before** the handler runs, so the handler never needs to inspect the context
for access control.

Design position in the auth stack
----------------------------------
- ``AbstractServerAuth`` (middleware) → authenticates the request → produces ``AuthContext``
- ``RouteGuard`` (route build-time config) → authorizes the context (no service / entity needed)
- ``AbstractAuthorizer`` (service-layer) → entity-aware authz for domain operations

``RouteGuard`` fills the gap between the two for service-free routers:
any ``@route`` that has no ``AsyncService`` behind it can still declare
"who may call this endpoint" without coupling to a domain entity.

Usage example::

    from varco_fastapi.auth.guard import require_scopes, require_roles, require_grant
    from varco_core.auth import Action

    class ReportRouter(GenericRouter):
        _prefix = "/reports"
        _auth = JwtBearerAuth(...)

        @route("GET", "/summary", requires=require_scopes("reports:read"))
        async def get_summary(self, ctx: AuthContext) -> dict:
            ...

        @route("DELETE", "/cache", requires=require_roles("admin"))
        async def purge_cache(self, ctx: AuthContext) -> None:
            ...

        @route("POST", "/export", requires=require_grant(Action.CREATE, "exports"))
        async def export(self, ctx: AuthContext) -> dict:
            ...

Thread safety:  ✅ ``RouteGuard`` is frozen=True — immutable singleton per route.
Async safety:   ✅ ``check()`` is ``async def``; predicate may be sync or async.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from varco_core.auth import Action, AuthContext
    from varco_core.exception.service import ServiceAuthorizationError  # noqa: F401


# ── RouteGuard ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RouteGuard:
    """
    Immutable, declarative authorization predicate for a single route.

    Evaluated against the request ``AuthContext`` **before** the handler runs.
    Raises ``ServiceAuthorizationError`` on denial — the existing
    ``add_exception_handlers()`` mapping then produces an HTTP 403 response.

    Fields
    ------
    scopes:
        OAuth scopes that must be present on the context.
        Controlled by ``require_all``.
    roles:
        Role names that must be present on the context.
        Controlled by ``require_all``.
    grant:
        ``(action, resource_key)`` pair checked via ``ctx.can(action, key)``.
        Only one grant check is supported per guard; compose guards via
        ``predicate`` if more complex logic is needed.
    token_profiles:
        Named JWT token profile(s) (Plan 002 §B) that must include the
        request's resolved profile — any-of match against
        ``ctx.metadata["token_profile"]``.  Build with
        ``require_token_profile(*names)``.
    require_all:
        When ``True`` (default), ALL declared scopes/roles must match (AND).
        When ``False``, ANY one declared scope or role suffices (OR).
        Has no effect on ``grant`` (always an exact ``ctx.can`` check).
    allow_anonymous:
        When ``True``, anonymous callers (``ctx.is_anonymous()``) bypass all
        other checks.  Use only for truly public routes that still want telemetry
        and correlation — or to keep the middleware in place while relaxing authz.
    predicate:
        Optional callable ``(ctx: AuthContext) → bool | Awaitable[bool]``.
        Called AFTER scope/role/grant checks.  Raise ``ServiceAuthorizationError``
        inside it for domain-specific denial messages; return ``False`` for a
        generic denial.  May close over an injected ``AbstractAuthorizer`` for
        entity-aware routes that still use ``@route``.

    Evaluation order (all conditions must pass unless ``allow_anonymous`` short-circuits)
    --------------------------------------------------------------------------------------
    1. If ``allow_anonymous`` and ``ctx.is_anonymous()`` → allow immediately.
    2. Check ``scopes`` (AND/OR per ``require_all``).
    3. Check ``roles`` (AND/OR per ``require_all``).
    3.5. Check ``token_profiles`` (any-of against
         ``ctx.metadata["token_profile"]`` — Plan 002 §B).
    4. Check ``grant`` via ``ctx.can(action, key)``.
    5. Call ``predicate(ctx)`` — must return truthy or raise.

    Thread safety:  ✅ frozen=True — safe to share across concurrent requests.
    Async safety:   ✅ ``check()`` is ``async def``; predicate may be coroutine.

    Edge cases:
        - All fields empty + ``allow_anonymous=False`` → denies anonymous callers
          but allows any authenticated caller (no scopes/roles/grants required).
        - ``predicate`` returning ``None`` is treated as truthy (falsy check only).
        - ``predicate`` raising any exception other than ``ServiceAuthorizationError``
          propagates as-is (treat it as a 500, not a 403).
        - ``require_all=True`` with empty ``scopes`` / ``roles`` → trivially passes
          those checks (vacuous truth).
    """

    scopes: tuple[str, ...] = field(default_factory=tuple)
    roles: tuple[str, ...] = field(default_factory=tuple)
    # (Action, resource_key) — avoids importing Action at module level
    grant: tuple[object, str] | None = None
    require_all: bool = True
    allow_anonymous: bool = False
    # Token profile names (Plan 002 §B) — checked via
    # ctx.metadata.get("token_profile") between the role check (step 3) and
    # the grant check (step 4). Any-of match, same style as scopes/roles.
    # A dataclass FIELD (not a require_predicate closure) keeps RouteGuard
    # hashable/comparable/introspectable — see decision D-13.
    token_profiles: tuple[str, ...] = field(default_factory=tuple)
    predicate: Callable[[AuthContext], bool | Awaitable[bool]] | None = field(
        default=None,
        # Exclude from hash/eq — callables are not reliably comparable
        hash=False,
        compare=False,
    )

    async def check(self, ctx: AuthContext) -> None:
        """
        Assert that ``ctx`` satisfies this guard.

        Args:
            ctx: The authenticated request context produced by the server's
                 ``AbstractServerAuth`` strategy.

        Returns:
            ``None`` when the caller is authorized.

        Raises:
            ServiceAuthorizationError: The context does not satisfy the guard.
                Maps to HTTP 403 via ``add_exception_handlers()``.

        Edge cases:
            - Anonymous ``ctx`` is allowed only when ``allow_anonymous=True``.
            - Empty guard (no scopes/roles/grant/predicate, ``allow_anonymous=False``)
              → allows any *authenticated* caller.
        """
        # Import here to avoid module-level coupling to varco_core
        from varco_core.exception.service import ServiceAuthorizationError

        # 1a. Anonymous short-circuit — allow_anonymous bypasses all further checks
        if self.allow_anonymous and ctx.is_anonymous():
            return

        # 1b. Deny anonymous callers unless explicitly opted in
        if not self.allow_anonymous and ctx.is_anonymous():
            raise ServiceAuthorizationError(
                "Anonymous access is not permitted on this route",
                str,
            )

        # 2. Scope check
        if self.scopes:
            if self.require_all:
                ok = all(ctx.has_scope(s) for s in self.scopes)
            else:
                ok = any(ctx.has_scope(s) for s in self.scopes)
            if not ok:
                raise ServiceAuthorizationError(
                    f"Missing required scope(s): {', '.join(self.scopes)!r}",
                    str,  # no entity_type for route-level authz
                )

        # 3. Role check
        if self.roles:
            if self.require_all:
                ok = all(ctx.has_role(r) for r in self.roles)
            else:
                ok = any(ctx.has_role(r) for r in self.roles)
            if not ok:
                raise ServiceAuthorizationError(
                    f"Missing required role(s): {', '.join(self.roles)!r}",
                    str,
                )

        # 3.5. Token profile check (Plan 002 §B) — any-of match against
        # ctx.metadata.get("token_profile"), the key populated by
        # varco_core.jwt.profile.resolve_token_profile() when a
        # TokenProfile matched during JWT parsing.
        if self.token_profiles:
            from varco_core.jwt.profile import PROFILE_METADATA_KEY

            actual_profile = ctx.metadata.get(PROFILE_METADATA_KEY)
            if actual_profile not in self.token_profiles:
                raise ServiceAuthorizationError(
                    f"Token profile {'/'.join(self.token_profiles)!r} "
                    f"required; token profile is {actual_profile!r}",
                    str,
                )

        # 4. Grant check (ctx.can is synchronous)
        if self.grant is not None:
            action, resource_key = self.grant
            if not ctx.can(action, resource_key):  # type: ignore[arg-type]
                raise ServiceAuthorizationError(
                    f"Missing grant: {action!r} on {resource_key!r}",
                    str,
                )

        # 5. Custom predicate — may be sync or async
        if self.predicate is not None:
            result = self.predicate(ctx)
            if asyncio.iscoroutine(result):
                result = await result
            if not result:
                raise ServiceAuthorizationError(
                    "Route authorization predicate denied access",
                    str,
                )


# ── Constructor helpers ────────────────────────────────────────────────────────
# These produce RouteGuard instances; they are the ergonomic public API.
# Callers should use these rather than constructing RouteGuard directly.


def require_scopes(*scopes: str, all: bool = True) -> RouteGuard:
    """
    Guard that requires one or more OAuth scopes.

    Args:
        *scopes:  Scope strings that must be present (e.g. ``"reports:read"``).
        all:      When ``True`` (default), ALL scopes required (AND).
                  When ``False``, ANY one scope suffices (OR).

    Returns:
        Frozen ``RouteGuard`` checking the given scopes.

    Example::

        @route("GET", "/admin", requires=require_scopes("admin:read", "admin:write"))
        async def admin_view(self, ctx: AuthContext) -> dict: ...
    """
    return RouteGuard(scopes=tuple(scopes), require_all=all)


def require_roles(*roles: str, all: bool = True) -> RouteGuard:
    """
    Guard that requires one or more named roles.

    Args:
        *roles:  Role names that must be present (e.g. ``"admin"``).
        all:     When ``True`` (default), ALL roles required (AND).
                 When ``False``, ANY one role suffices (OR).

    Returns:
        Frozen ``RouteGuard`` checking the given roles.

    Example::

        @route("DELETE", "/cache", requires=require_roles("admin"))
        async def purge_cache(self, ctx: AuthContext) -> None: ...
    """
    return RouteGuard(roles=tuple(roles), require_all=all)


def require_grant(action: Action, resource_key: str) -> RouteGuard:
    """
    Guard that requires ``ctx.can(action, resource_key)`` to be ``True``.

    Args:
        action:       The ``varco_core.auth.Action`` (or compatible ``StrEnum``
                      value) to check.
        resource_key: Canonical resource key (e.g. ``"reports"`` or
                      ``"reports:abc123"``).

    Returns:
        Frozen ``RouteGuard`` checking a single grant.

    Example::

        @route("POST", "/export", requires=require_grant(Action.CREATE, "exports"))
        async def export_data(self, ctx: AuthContext) -> dict: ...
    """
    return RouteGuard(grant=(action, resource_key))


def require_predicate(
    fn: Callable[[AuthContext], bool | Awaitable[bool]],
) -> RouteGuard:
    """
    Guard backed by an arbitrary callable for custom authorization logic.

    The callable receives the ``AuthContext`` and must return ``True`` to allow
    or ``False`` (or raise ``ServiceAuthorizationError``) to deny.

    Args:
        fn: Sync or async callable ``(ctx: AuthContext) → bool | Awaitable[bool]``.
            May close over an injected ``AbstractAuthorizer`` for entity-aware
            authorization on service-free routes.

    Returns:
        Frozen ``RouteGuard`` delegating to the given predicate.

    Example::

        # Close over a domain-specific authorizer
        @route("POST", "/sensitive", requires=require_predicate(
            lambda ctx: ctx.has_role("superuser")
        ))
        async def sensitive_op(self, ctx: AuthContext) -> None: ...
    """
    return RouteGuard(predicate=fn)


def require_token_profile(*names: str) -> RouteGuard:
    """
    Guard that requires the request's resolved JWT token profile
    (``AuthContext.metadata["token_profile"]``, populated by
    ``varco_core.jwt.profile.resolve_token_profile()`` during JWT parsing)
    to be one of ``names``.

    Args:
        *names: Acceptable profile name(s) — any-of match.

    Returns:
        Frozen ``RouteGuard`` checking ``token_profiles``.

    Edge cases:
        - An anonymous caller (no ``ctx.metadata`` at all, or
          ``allow_anonymous=False`` by default) is denied by the earlier
          anonymous check (step 1b) before the profile check even runs.
        - A context with no ``"token_profile"`` key at all denies with an
          actionable message naming the required profile(s) and ``None``.

    Example::

        @route("GET", "/internal", requires=require_token_profile("internal"))
        async def internal_only(self, ctx: AuthContext) -> dict: ...
    """
    return RouteGuard(token_profiles=tuple(names))


def allow_anonymous() -> RouteGuard:
    """
    Guard that allows anonymous (unauthenticated) callers through unconditionally.

    Use only for truly public endpoints that still need the middleware pipeline
    (telemetry, logging, correlation ID) but impose no authentication requirement.

    Returns:
        Frozen ``RouteGuard`` with ``allow_anonymous=True`` and no other checks.

    Example::

        @route("GET", "/status", requires=allow_anonymous())
        async def public_status(self, ctx: AuthContext) -> dict:
            return {"ok": True}
    """
    return RouteGuard(allow_anonymous=True)
