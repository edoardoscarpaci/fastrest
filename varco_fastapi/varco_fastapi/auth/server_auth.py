"""
varco_fastapi.auth.server_auth
==============================
Server-side authentication hierarchy for FastAPI.

Each ``AbstractServerAuth`` subclass is a FastAPI-callable dependency that
extracts credentials from the incoming ``Request`` and returns an ``AuthContext``.
Inject it into route handlers via ``Depends()``, or into ``RequestContextMiddleware``
which sets the auth context for the entire request scope.

Hierarchy::

    AbstractServerAuth (ABC)
      ├── JwtBearerAuth          — verify Bearer JWT via TrustedIssuerRegistry
      ├── ApiKeyAuth             — verify X-API-Key header or ?api_key= param
      ├── PassthroughAuth        — decode JWT claims WITHOUT verifying signature
      ├── AnonymousAuth          — always returns anonymous AuthContext
      ├── CompositeServerAuth    — try each strategy in order; first success wins
      └── WebSocketAuth          — extract token from WS upgrade (header, protocol, query)

FastAPI usage::

    auth = JwtBearerAuth(registry=my_registry)

    @app.get("/orders/")
    async def list_orders(ctx: AuthContext = Depends(auth)):
        ...

    # Or via RequestContextMiddleware (sets context for ALL routes):
    app.add_middleware(RequestContextMiddleware, server_auth=auth)

DESIGN: Callable dependency (``__call__``) over FastAPI ``Security()``
    ✅ No FastAPI-specific import needed in route handlers — just ``Depends(auth)``
    ✅ ``AbstractServerAuth`` is a plain ABC; testable without FastAPI
    ✅ Composable via ``CompositeServerAuth`` without framework coupling
    ❌ No built-in OpenAPI security scheme generation — add manually via
       ``app.openapi()`` override if needed

Thread safety:  ✅ Implementations hold no mutable state per-call.
Async safety:   ✅ All ``__call__`` methods are ``async def``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request, status
from varco_core.auth.base import AuthContext

# Sentinel distinguishing "audience kwarg omitted" from "audience=None passed
# explicitly" (Plan 005, Phase 2 / U-13). The two must NOT be treated the
# same: an omitted audience with no VARCO_JWT_AUDIENCE means the caller never
# thought about it (fail closed — raise); an explicit audience=None is the
# pre-Phase-2 idiom for "I am deliberately not enforcing audience" and must
# keep working exactly as before (see
# varco_fastapi/tests/milestone_a/test_server_auth.py::
# test_audience_none_does_not_enforce_either_way, which stays green
# unmodified).
_AUDIENCE_UNSET = object()

_logger = logging.getLogger(__name__)

# Anonymous AuthContext — reused by AnonymousAuth and as the fallback in
# JwtBearerAuth when required=False and no token is present.
_ANONYMOUS: AuthContext = AuthContext()

if TYPE_CHECKING:
    from starlette.datastructures import Headers, QueryParams
    from varco_core.authority import TrustedIssuerRegistry

# ── AbstractServerAuth ────────────────────────────────────────────────────────


class AbstractServerAuth(ABC):
    """
    FastAPI callable dependency that returns an ``AuthContext`` from a request.

    Subclass and implement ``__call__`` to add new authentication strategies.
    The returned ``AuthContext`` is stored in ``auth_context_var`` by
    ``RequestContextMiddleware``.

    Thread safety:  ✅ Implementations must not hold per-request mutable state.
    Async safety:   ✅ ``__call__`` is ``async def``.
    """

    @abstractmethod
    async def __call__(self, request: Request) -> AuthContext:
        """
        Extract credentials from ``request`` and return an ``AuthContext``.

        Args:
            request: The incoming FastAPI/Starlette ``Request`` object.

        Returns:
            An ``AuthContext`` populated from the verified credentials.

        Raises:
            HTTPException: 401 if credentials are invalid or missing (when required).
            HTTPException: 403 if the token is valid but the operation is denied.

        Edge cases:
            - Must not mutate ``request``.
            - For optional auth, return ``_ANONYMOUS`` (user_id=None) rather than
              raising — callers can check ``ctx.is_anonymous()``.
        """


# ── JwtBearerAuth ─────────────────────────────────────────────────────────────


class JwtBearerAuth(AbstractServerAuth):
    """
    Verify a Bearer JWT using ``TrustedIssuerRegistry``.

    Extracts the ``Authorization: Bearer <token>`` header, calls
    ``registry.verify(token)``, and maps the decoded ``JsonWebToken`` to an
    ``AuthContext``.

    The ``JsonWebToken`` produced by varco_core's ``JwtParser`` already includes
    a pre-parsed ``auth_ctx`` field when the token contains ``roles``, ``scopes``,
    or ``grants`` claims.  If ``auth_ctx`` is not present in the token, this auth
    builds one from the ``sub`` claim only.

    Args:
        registry:           ``TrustedIssuerRegistry`` for signature verification.
        required:           If ``True`` (default), missing/invalid tokens raise 401.
                            If ``False``, missing tokens return anonymous auth.
        anonymous_context:  ``AuthContext`` to return when ``required=False`` and
                            no token is present.  Defaults to ``AuthContext()``.
        audience:           Expected ``aud`` claim value(s), threaded into
                            ``registry.verify(audience=...)`` (Plan 002 C-2).
                            Omitted (default) reads ``VARCO_JWT_AUDIENCE``; if
                            that is also unset, construction **raises
                            ``ValueError``** (Plan 005 Phase 2 / U-13 — a
                            BREAKING security-default change: a service that
                            forgets to set an audience used to log one
                            warning and proceed; it now refuses to start).
                            Pass ``audience=None`` **explicitly** to keep the
                            pre-Phase-2 "not enforced" behaviour for this one
                            instance (audience is a deliberate, per-instance
                            opt-out — distinct from ``allow_any_audience``,
                            which is a process-wide policy statement); or use
                            ``allow_any_audience=True`` /
                            ``VARCO_JWT_ALLOW_ANY_AUDIENCE=true`` for the
                            documented, named escape hatch.
        allow_any_audience: Explicit, named opt-out from the ``ValueError``
                            above (Plan 005 Phase 2 / U-13). ``False``
                            (default) reads
                            ``VARCO_JWT_ALLOW_ANY_AUDIENCE``. When ``True``
                            and no audience is configured, construction
                            succeeds but logs a **single** warning — the same
                            shape as the old default, now opt-in only.
        leeway:             Clock-skew leeway in seconds, threaded into
                            ``registry.verify(leeway=...)`` (Plan 002 C-1).
                            ``None`` (default) reads ``VARCO_JWT_LEEWAY_SECONDS``
                            (default ``0.0`` — no leeway, today's behaviour).

    DESIGN: delegates to TrustedIssuerRegistry (not JwtAuthority)
        ✅ Supports multiple issuers — gateway, service-to-service, etc.
        ✅ Key rotation handled by registry; auth layer is rotation-agnostic
        ✅ Same verification path for JWT-based server and client auth
        ❌ Requires at least one issuer to be registered in the registry

    DESIGN: sentinel default for ``audience`` distinguishes "omitted" from
    "explicitly None"
        ✅ A caller who never thought about audience enforcement (omitted the
           kwarg, no env var) gets a startup failure — the whole point of
           Phase 2.
        ✅ A caller who explicitly wrote ``audience=None`` is making a
           deliberate statement and keeps working unmodified — no test in
           this repo asserting that behaviour needs to change.
        ❌ One more sentinel object in the codebase — the alternative
           (treating omitted and explicit-None identically) would silently
           break every existing ``audience=None`` call site on upgrade,
           which is a worse failure mode than "one more sentinel".

    Thread safety:  ✅ ``TrustedIssuerRegistry.verify`` is thread-safe.
    Async safety:   ✅ ``verify`` is ``async def``.

    Edge cases:
        - ``Authorization: Bearer`` with no token value raises 401.
        - Expired tokens raise 401 with "Token expired" detail.
        - Tokens with unknown ``kid`` or ``iss`` raise 401 with "Unknown issuer".
        - Omitted ``audience``, no ``VARCO_JWT_AUDIENCE``, no
          ``allow_any_audience`` → ``ValueError`` at construction.
    """

    def __init__(
        self,
        registry: TrustedIssuerRegistry,
        *,
        required: bool = True,
        anonymous_context: AuthContext | None = None,
        audience: str | list[str] | None = _AUDIENCE_UNSET,  # type: ignore[assignment]
        leeway: float | None = None,
        allow_any_audience: bool | None = None,
    ) -> None:
        from varco_core.jwt.config import JwtVerificationSettings

        settings = JwtVerificationSettings.from_env()

        self._registry = registry
        self._required = required
        self._anonymous = anonymous_context or _ANONYMOUS

        audience_omitted = audience is _AUDIENCE_UNSET
        if audience_omitted:
            self._audience = settings.audience
        else:
            self._audience = audience  # type: ignore[assignment]

        self._leeway = leeway if leeway is not None else settings.leeway_seconds

        effective_allow_any_audience = (
            allow_any_audience if allow_any_audience is not None else settings.allow_any_audience
        )

        if self._audience is None and audience_omitted:
            # Fail closed (Plan 005 Phase 2 / U-13) — only when the caller
            # never configured an audience at all. An explicit
            # audience=None is a deliberate opt-out and never reaches here.
            if not effective_allow_any_audience:
                raise ValueError(
                    "JwtBearerAuth: no audience configured — set audience=..., "
                    "VARCO_JWT_AUDIENCE, or explicitly opt out with "
                    "allow_any_audience=True / VARCO_JWT_ALLOW_ANY_AUDIENCE=true "
                    "if this service must accept tokens minted for any audience."
                )
            _logger.warning(
                "JwtBearerAuth: audience is not enforced (aud claim is never "
                "checked) — allow_any_audience=True. Set audience=... or "
                "VARCO_JWT_AUDIENCE to harden against tokens minted for a "
                "different service."
            )

    async def __call__(self, request: Request) -> AuthContext:
        """
        Args:
            request: Incoming HTTP request.

        Returns:
            Decoded ``AuthContext`` from the JWT, or ``anonymous_context`` when
            ``required=False`` and no token is present.

        Raises:
            HTTPException 401: Token missing (when required), expired,
                invalid, or (when ``audience`` is configured) minted for a
                different ``aud``.
        """
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            if self._required:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing Bearer token",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return self._anonymous

        raw_token = authorization.removeprefix("Bearer ").strip()
        if not raw_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Empty Bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Build verify() kwargs conditionally — omit "audience"/"leeway"
        # entirely when they are at their "not configured" defaults so a
        # zero-config JwtBearerAuth calls registry.verify(raw_token) with no
        # extra kwargs, byte-identical to pre-Plan-002 behaviour (this is
        # asserted by test_jwt_bearer_auth_calls_registry_verify's exact-args
        # mock assertion). Functionally equivalent either way — verify()'s
        # own defaults (audience=None, leeway resolved from env) are the same
        # values — this is purely about not changing the observed call shape.
        verify_kwargs: dict[str, Any] = {}
        if self._audience is not None:
            verify_kwargs["audience"] = self._audience
        if self._leeway:
            verify_kwargs["leeway"] = self._leeway

        try:
            jwt = await self._registry.verify(raw_token, **verify_kwargs)
        except Exception as exc:
            _logger.debug("JwtBearerAuth: token verification failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid or expired token: {exc}",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

        # Use pre-parsed AuthContext from the token if available, otherwise
        # build a minimal one from the sub claim.
        if jwt.auth_ctx is not None:
            return jwt.auth_ctx

        # Fallback: build from sub claim only
        return AuthContext(user_id=jwt.sub)


# ── ApiKeyAuth ────────────────────────────────────────────────────────────────


class ApiKeyAuth(AbstractServerAuth):
    """
    Verify an API key from the ``X-API-Key`` header or ``?api_key=`` query param.

    The ``keys`` mapping maps each API key string to its associated ``AuthContext``
    (which carries identity, roles, and grants for that key).

    Args:
        keys:     Dict mapping API key strings to their ``AuthContext``.
        header:   Header name to check.  Default: ``"X-API-Key"``.
        param:    Query parameter name as fallback.  Default: ``"api_key"``.
        required: Raise 401 if no key is provided.  Default: ``True``.

    DESIGN: static dict over DB lookup per request
        ✅ Zero latency — no I/O per request
        ✅ Trivially testable — inject a plain dict in tests
        ❌ Keys must be loaded at startup; not suitable for dynamic key issuance
           (use JwtBearerAuth for dynamic auth)

    Thread safety:  ✅ ``keys`` dict is read-only after construction.
    Async safety:   ✅ ``__call__`` is ``async def`` but does no I/O.

    Edge cases:
        - API key lookup is case-sensitive.
        - Header check takes priority over query param.
    """

    def __init__(
        self,
        keys: dict[str, AuthContext],
        *,
        header: str = "X-API-Key",
        param: str = "api_key",
        required: bool = True,
    ) -> None:
        self._keys = keys
        self._header = header
        self._param = param
        self._required = required

    async def __call__(self, request: Request) -> AuthContext:
        """
        Args:
            request: Incoming HTTP request.

        Returns:
            ``AuthContext`` for the matched API key.

        Raises:
            HTTPException 401: Key is missing (when required) or not recognized.
        """
        # Header takes priority
        api_key = request.headers.get(self._header) or request.query_params.get(self._param)

        if not api_key:
            if self._required:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Missing API key (header: {self._header!r} or "
                    f"query param: {self._param!r})",
                )
            return _ANONYMOUS

        ctx = self._keys.get(api_key)
        if ctx is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )
        return ctx


# ── PassthroughAuth ───────────────────────────────────────────────────────────


class PassthroughAuth(AbstractServerAuth):
    """
    Decode a Bearer JWT WITHOUT verifying the signature.

    Intended for internal services behind an API gateway that has already
    verified the token.  The gateway strips the token of its signature or
    forwards it as a trusted header; PassthroughAuth just reads the claims.

    WARNING: Never use this on public-facing endpoints.  It bypasses all
    cryptographic verification.

    Args:
        required: Raise 401 if no token is present.  Default: ``False``
                  (internal services often allow requests without tokens for
                  health checks and inter-service calls).

    DESIGN: passthrough over re-verification behind gateway
        ✅ Avoids redundant signature verification for internal routes
        ✅ Works when the gateway strips the signature (JWT format preserved)
        ❌ Completely insecure on public endpoints — document the use case clearly

    Thread safety:  ✅ Stateless.
    Async safety:   ✅ ``__call__`` is ``async def``; no I/O.
    """

    def __init__(self, *, required: bool = False) -> None:
        self._required = required

    async def __call__(self, request: Request) -> AuthContext:
        """
        Returns:
            ``AuthContext`` decoded from claims (no signature check), or
            anonymous if no token is present.

        Raises:
            HTTPException 401: When ``required=True`` and no token present,
                or the token is not well-formed enough to decode at all.

        Edge cases:
            - Delegates to ``JwtParser.parse_unverified()`` (Plan 002 step 35)
              instead of hand-rolling base64/JSON decoding — this is the ONE
              varco_fastapi call site that previously duplicated claim→
              ``AuthContext`` parsing outside ``JwtParser``, so it now
              benefits from the claim-transform pipeline (env-driven or
              explicit) for free, exactly like ``JwtBearerAuth``.
            - ``token.extra_claims`` (raw, non-reserved claims) is merged
              into the resulting ``AuthContext.metadata`` — preserving the
              pre-refactor behaviour where every non-standard claim landed
              in ``metadata``.
        """
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            if self._required:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Missing Bearer token",
                )
            return _ANONYMOUS

        raw_token = authorization.removeprefix("Bearer ").strip()

        from dataclasses import replace

        from varco_core.jwt import JwtParser

        try:
            token = JwtParser.parse_unverified(raw_token)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Could not decode token claims: {exc}",
            ) from exc

        ctx = token.auth_ctx or AuthContext(user_id=token.sub)
        if token.extra_claims:
            ctx = replace(ctx, metadata={**ctx.metadata, **token.extra_claims})
        return ctx


# ── AnonymousAuth ─────────────────────────────────────────────────────────────


class AnonymousAuth(AbstractServerAuth):
    """
    Always return an anonymous ``AuthContext`` (user_id=None).

    Use for fully public endpoints that require no credentials.
    The returned context has no roles, scopes, or grants.

    Thread safety:  ✅ Stateless singleton.
    Async safety:   ✅ ``__call__`` is ``async def``; no I/O.
    """

    async def __call__(self, request: Request) -> AuthContext:
        """Always returns an anonymous ``AuthContext``."""
        return _ANONYMOUS


# ── CompositeServerAuth ───────────────────────────────────────────────────────


class CompositeServerAuth(AbstractServerAuth):
    """
    Try each ``AbstractServerAuth`` strategy in order; first success wins.

    Useful for APIs that accept both JWT Bearer tokens AND API keys, or that
    support optional auth (try JWT, fall back to anonymous).

    Args:
        strategies: List of ``AbstractServerAuth`` instances to try in order.

    DESIGN: composite over subclassing
        ✅ Open/closed — add new strategies without modifying existing ones
        ✅ Mirrors CompositeHealthCheck from varco_core for consistency
        ✅ Order-sensitive — first success short-circuits (most specific first)
        ❌ Error messages can be confusing when all strategies fail — the last
           strategy's 401 is raised

    Thread safety:  ✅ Strategies are read-only after construction.
    Async safety:   ✅ Tries strategies sequentially until one succeeds.

    Edge cases:
        - Empty ``strategies`` list always raises 401.
        - A strategy that returns ``_ANONYMOUS`` counts as success — subsequent
          strategies are NOT tried.  For optional-auth fallback, put ``AnonymousAuth``
          last in the list.
    """

    def __init__(self, strategies: list[AbstractServerAuth]) -> None:
        if not strategies:
            raise ValueError("CompositeServerAuth requires at least one strategy.")
        self._strategies = strategies

    async def __call__(self, request: Request) -> AuthContext:
        """
        Args:
            request: Incoming HTTP request.

        Returns:
            ``AuthContext`` from the first succeeding strategy.

        Raises:
            HTTPException 401: All strategies failed — the last strategy's
                exception is re-raised.
        """
        last_exc: HTTPException | None = None
        for strategy in self._strategies:
            try:
                return await strategy(request)
            except HTTPException as exc:
                last_exc = exc
                continue

        # All strategies failed — raise the last error
        raise last_exc or HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed: no strategy succeeded",
        )


# ── WebSocketAuth ─────────────────────────────────────────────────────────────


class WebSocketAuth(AbstractServerAuth):
    """
    Authentication for WebSocket connections.

    WebSocket connections cannot set custom headers after the initial HTTP
    upgrade request.  This class extracts credentials from one of three sources,
    checked in priority order:

    1. ``Authorization: Bearer <token>`` header on the upgrade request (standard).
    2. ``Sec-WebSocket-Protocol`` sub-protocol with ``protocol_prefix`` (browser
       workaround — JS WebSocket API cannot set custom headers).
       Example: ``Sec-WebSocket-Protocol: bearer.eyJhbGciOi...``
    3. Query parameter ``?token=<jwt>`` (last resort — visible in server logs).

    Once extracted, the raw token is injected as a synthetic ``Authorization:
    Bearer`` header and delegated to the ``inner`` auth strategy for verification.

    Args:
        inner:               The auth strategy to delegate to after extraction.
                             Typically ``JwtBearerAuth`` or ``ApiKeyAuth``.
        token_query_param:   Query param name for the token fallback.
                             Default: ``"token"``.
        protocol_prefix:     Sub-protocol prefix for the token.
                             Default: ``"bearer."``.

    DESIGN: extraction wrapper over a new ABC
        ✅ Reuses existing JwtBearerAuth/ApiKeyAuth for verification — no duplication
        ✅ All three browser-compatible auth patterns in one place
        ✅ Works for both ws:// (dev) and wss:// (prod)
        ❌ Query param token is visible in server access logs — warn in docs

    Thread safety:  ✅ Delegates to inner strategy; no mutable state.
    Async safety:   ✅ Delegates to ``inner.__call__``.

    Edge cases:
        - Only one source is tried per connection (header > protocol > query).
        - The sub-protocol token is NOT included in the ``Sec-WebSocket-Protocol``
          response header — use ``websocket.accept(subprotocol=...)`` if the
          client expects sub-protocol echo.
    """

    def __init__(
        self,
        inner: AbstractServerAuth,
        *,
        token_query_param: str = "token",
        protocol_prefix: str = "bearer.",
    ) -> None:
        self._inner = inner
        self._token_query_param = token_query_param
        self._protocol_prefix = protocol_prefix

    async def __call__(self, request: Request) -> AuthContext:
        """
        Extract credentials and delegate to ``inner`` for verification.

        Args:
            request: The incoming upgrade request.  For WS connections this is
                     the HTTP upgrade ``Request``.

        Returns:
            ``AuthContext`` from the ``inner`` strategy.

        Raises:
            HTTPException 401: No credentials found, or inner verification fails.
        """
        # 1. Standard Authorization header
        if request.headers.get("Authorization", "").startswith("Bearer "):
            return await self._inner(request)

        # 2. Sec-WebSocket-Protocol sub-protocol token
        protocols_header = request.headers.get("Sec-WebSocket-Protocol", "")
        for proto in (p.strip() for p in protocols_header.split(",")):
            if proto.startswith(self._protocol_prefix):
                raw_token = proto.removeprefix(self._protocol_prefix).strip()
                if raw_token:
                    # Inject as synthetic Authorization header via a wrapped request
                    return await self._inner(
                        _RequestWithBearerOverride(request, raw_token)  # type: ignore[arg-type]
                    )

        # 3. Query parameter fallback
        query_token = request.query_params.get(self._token_query_param)
        if query_token:
            _logger.debug(
                "WebSocketAuth: using query param token — visible in logs; "
                "prefer Authorization header or sub-protocol for production."
            )
            return await self._inner(_RequestWithBearerOverride(request, query_token))  # type: ignore[arg-type]

        # All extraction methods failed
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="WebSocket authentication failed: no credentials found "
            "(checked Authorization header, Sec-WebSocket-Protocol, and query param).",
        )


class _RequestWithBearerOverride:
    """
    Thin request wrapper that injects a synthetic ``Authorization: Bearer``
    header, allowing inner auth strategies to work without modification.

    This is an internal implementation detail — not part of the public API.

    Thread safety:  ✅ Wraps an existing immutable Request; no shared state.
    """

    def __init__(self, request: Request, raw_token: str) -> None:
        self._request = request
        self._raw_token = raw_token

    @property
    def headers(self) -> _HeadersWithBearer:
        return _HeadersWithBearer(self._request.headers, self._raw_token)

    @property
    def query_params(self) -> QueryParams:
        return self._request.query_params

    def __getattr__(self, name: str) -> Any:
        return getattr(self._request, name)


class _HeadersWithBearer:
    """Header accessor that overrides the Authorization entry."""

    def __init__(self, original: Headers, raw_token: str) -> None:
        self._original = original
        self._bearer = f"Bearer {raw_token}"

    def get(self, key: str, default: str = "") -> str:
        if key.lower() == "authorization":
            return self._bearer
        return self._original.get(key, default)

    def __getitem__(self, key: str) -> str:
        if key.lower() == "authorization":
            return self._bearer
        return self._original[key]


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    "AbstractServerAuth",
    "JwtBearerAuth",
    "ApiKeyAuth",
    "PassthroughAuth",
    "AnonymousAuth",
    "CompositeServerAuth",
    "WebSocketAuth",
]
