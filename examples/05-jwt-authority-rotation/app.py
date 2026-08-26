"""
app.py
======
Application factory for the ``05-jwt-authority-rotation`` example.

Demonstrates ``JwtAuthority``, ``MultiKeyAuthority`` (zero-downtime key
rotation), ``TrustedIssuerRegistry``, and JWT authentication in FastAPI
routes — all without a database or broker.

Endpoints
---------
``POST /auth/token``
    Issue a JWT for any subject.  No password check — demo only.
    Returns ``{"token": "<signed-jwt>", "active_kid": "<kid>"}`` so callers
    can see which key is currently signing.

``GET /me``
    Protected endpoint.  Requires a valid Bearer JWT.  Verifies the token via
    ``TrustedIssuerRegistry`` (wired through ``JwtBearerAuth``), then returns
    ``{"subject": "<sub>", "kid": "<kid>"}`` — the subject from the JWT claims
    and the ``kid`` from the JWT header.

``GET /jwks``
    Serve the current JWKS (all active + still-valid public keys).

Bootstrap sequence
------------------
1. ``authority.py`` constructs ``MultiKeyAuthority`` and ``registry``
   synchronously at import time.
2. ``create_app()`` wires a ``JwtBearerAuth`` from ``registry``, mounts
   middleware, registers routes, and defers ``registry.load_all()`` to the
   ``VarcoLifespan`` hook.

Run locally::

    cd examples/05-jwt-authority-rotation
    uv run uvicorn app:app --reload

DESIGN: module-level ``app = create_app()`` for ASGI server convenience
    ✅ ``uvicorn app:app`` works without ``--factory``.
    ✅ ``create_app()`` factory form is also available for test isolation.
    ✅ All async init deferred to ``VarcoLifespan._setup`` — ``create_app()``
       stays synchronous.
    ❌ Module-level ``app`` makes the factory semi-impure (side effects on
       import).  Acceptable for a quickstart with no DI scanning.

Thread safety:  ✅ Called once at startup.
Async safety:   ✅ Synchronous factory; async init runs inside lifespan.
"""

from __future__ import annotations

from authority import mint_token, multi_authority, registry
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from router import build_protected_router
from varco_fastapi.auth import JwtBearerAuth
from varco_fastapi.exceptions import add_exception_handlers
from varco_fastapi.lifespan import VarcoLifespan
from varco_fastapi.middleware import (
    ErrorMiddleware,
    RequestContextMiddleware,
    install_middleware_stack,
)


def create_app() -> FastAPI:
    """
    Build and return the configured FastAPI application.

    Synchronous — uvicorn can load it without ``--factory``.  All async
    initialisation (``registry.load_all()``) is deferred to a ``_bootstrap``
    closure that runs inside ``VarcoLifespan`` at startup.

    Returns:
        A configured ``FastAPI`` application ready for an ASGI server.

    Edge cases:
        - ``registry`` is already populated synchronously (``register_authority``
          is sync); only ``load_all()`` needs an event loop, hence the deferral.
        - Each call produces an independent ``FastAPI`` instance — safe for test
          isolation.

    Thread safety:  ✅ Intended to be called once per process.
    Async safety:   ✅ Synchronous; no event loop required at call time.
    """
    # ── 1. Auth wiring ────────────────────────────────────────────────────────
    # required=False for the middleware — public routes (/auth/token, /jwks)
    # must not get a 401 just because they carry no Bearer token.
    # The /me route enforces authentication via its own Depends(server_auth_strict).
    server_auth = JwtBearerAuth(registry=registry, required=False)

    # Strict variant used by Depends() on /me — rejects anonymous callers.
    server_auth_strict = JwtBearerAuth(registry=registry, required=True)

    # ── 2. Lifespan ───────────────────────────────────────────────────────────
    lifespan = VarcoLifespan()

    # ── 3. FastAPI app ────────────────────────────────────────────────────────
    app = FastAPI(
        title="JWT Authority Rotation Example",
        version="0.1.0",
        description=(
            "Demonstrates JwtAuthority, MultiKeyAuthority (zero-downtime key rotation),\n"
            "TrustedIssuerRegistry, and JWT authentication in FastAPI routes.\n\n"
            "**Endpoints**:\n"
            "- ``POST /auth/token`` — issue a JWT (demo, no password check)\n"
            "- ``GET /me`` — protected; returns subject + kid from verified token\n"
            "- ``GET /jwks`` — serve current JWKS (all registered public keys)\n"
        ),
        lifespan=lifespan,
    )

    # ── 4. Middleware stack ───────────────────────────────────────────────────
    # Stack (outermost → innermost):
    #   ErrorMiddleware           — catches all exceptions, returns JSON
    #   RequestContextMiddleware  — sets AuthContext ContextVar per request
    install_middleware_stack(
        app,
        [
            ErrorMiddleware,
            (RequestContextMiddleware, {"server_auth": server_auth}),
        ],
    )

    add_exception_handlers(app)

    @app.exception_handler(HTTPException)
    async def _http_exc_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=dict(exc.headers) if exc.headers else {},
        )

    # ── 5. Unprotected routes ─────────────────────────────────────────────────

    @app.post("/auth/token")
    async def issue_token(subject: str = "user:demo") -> dict:
        """
        Issue a JWT for the given subject.

        No credential check — this is a demo endpoint only.  In production,
        verify a password, API key, or client certificate before signing.

        Args:
            subject: The ``sub`` claim value (query param).

        Returns:
            ``{"token": "<jwt>", "active_kid": "<kid>"}``
        """
        token = mint_token(subject)
        return {"token": token, "active_kid": multi_authority.active_kid}

    @app.get("/jwks")
    async def jwks_endpoint() -> dict:
        """
        Serve all currently registered public keys as a JWKS document.

        Returns the merged keyset from ``MultiKeyAuthority.jwks()`` — includes
        both the active key and any still-valid keys retained for verification
        of in-flight tokens after rotation.

        Returns:
            Standard JWKS JSON object (``{"keys": [...]}``)
        """
        return multi_authority.jwks().to_dict()

    # ── 6. Protected route (/me via Depends + build_protected_router) ─────────
    # Using a plain FastAPI APIRouter so the handler can accept ``Request``
    # to extract the ``kid`` from the JWT header — ``GenericRouter`` custom
    # handlers only receive ``ctx`` + path params.
    # Use server_auth_strict so /me returns 401 for missing/invalid tokens.
    protected = build_protected_router(server_auth_strict)
    app.include_router(protected)

    # ── 7. Async bootstrap (deferred to lifespan) ─────────────────────────────
    async def _bootstrap() -> None:
        """
        Populate the registry keyset inside the event loop.

        ``registry.load_all()`` must be called after the event loop starts —
        it sets ``entry._keyset`` so ``get_key()`` can find keys.  For an
        ``AuthoritySource`` (in-memory) this is instant and I/O-free, but
        still required.

        Edge cases:
            - Tests using ``httpx.AsyncClient`` with ``ASGITransport`` do NOT
              trigger the ASGI lifespan.  Those tests must call
              ``registry.load_all()`` explicitly in their fixture.
        """
        await registry.load_all()

    lifespan._setup = _bootstrap

    return app


# Module-level app — lets uvicorn use ``uvicorn app:app`` without ``--factory``.
app = create_app()

__all__ = ["app", "create_app"]
