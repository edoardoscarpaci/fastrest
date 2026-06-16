"""
app.py
======
Application factory for the ``02-api-gateway-guards`` example.

Demonstrates a **service-free** FastAPI gateway — no database, no broker,
no Docker needed.  All endpoints are stateless transforms or mocked data.

Bootstrap sequence
------------------
1. Build a local ``JwtAuthority`` (RSA-2048, ephemeral, from ``auth.py``).
2. Construct a ``TrustedIssuerRegistry`` and register the authority.
3. Build a ``JwtBearerAuth`` (``required=False`` so anonymous callers can
   reach ``allow_anonymous()`` routes without getting an immediate 401).
4. Set ``GatewayRouter._auth`` so ``build_router()`` wires the middleware.
5. Create a ``VarcoLifespan`` with an async ``_bootstrap`` that calls
   ``registry.load_all()`` (must happen inside the event loop).
6. Assemble the ``FastAPI`` app with ``ErrorMiddleware``,
   ``RequestContextMiddleware``, and mount the gateway router.

Run locally::

    cd examples/02-api-gateway-guards
    uv run uvicorn app:app --reload

Or use the factory form::

    uv run uvicorn app:create_app --factory --reload

DESIGN: module-level ``app = create_app()`` for ASGI server convenience
    ✅ ``uvicorn app:app`` works without ``--factory``.
    ✅ ``create_app()`` factory form is available for test isolation.
    ✅ All async init deferred to ``VarcoLifespan._setup`` — ``create_app()``
       stays synchronous so uvicorn does not need ``--factory``.
    ❌ Module-level ``app`` makes the factory semi-impure (side effects on
       import).  Acceptable for a quickstart with no DI scanning.

Thread safety:  ✅ Called once at startup.
Async safety:   ✅ Synchronous factory; async init runs inside lifespan.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

from varco_fastapi.auth import JwtBearerAuth
from varco_fastapi.exceptions import add_exception_handlers
from varco_fastapi.lifespan import VarcoLifespan
from varco_fastapi.middleware import (
    ErrorMiddleware,
    RequestContextMiddleware,
    install_middleware_stack,
)

from auth import registry
from router import GatewayRouter


def create_app() -> FastAPI:
    """
    Build and return the configured FastAPI application.

    This function is synchronous — uvicorn can load it without ``--factory``.
    All async initialization (``registry.load_all()``) is deferred to a
    ``_bootstrap`` closure that runs inside ``VarcoLifespan`` at startup.

    Returns:
        A configured ``FastAPI`` application ready for an ASGI server.

    Edge cases:
        - ``registry`` is already populated synchronously (``register_authority``
          is sync); only ``load_all()`` needs an event loop, hence the deferral.
        - Each call to ``create_app()`` produces an independent ``FastAPI``
          instance and a fresh ``VarcoLifespan`` — safe for test isolation.
        - ``GatewayRouter._auth`` is set at the class level inside this function;
          if ``create_app()`` is called multiple times (rare outside tests), each
          call overwrites the class attribute with a fresh ``JwtBearerAuth``
          pointing at the same shared ``registry`` — harmless.

    Thread safety:  ✅ Intended to be called once per process.
    Async safety:   ✅ Synchronous; no event loop required at call time.
    """
    # ── 1. Auth wiring ────────────────────────────────────────────────────────
    # JwtBearerAuth wraps the registry and verifies every Bearer token on
    # incoming requests.  required=False so anonymous callers reach the
    # allow_anonymous() guards without getting an immediate 401.
    server_auth = JwtBearerAuth(registry=registry, required=False)

    # Set _auth at the class level before build_router() is called.
    # build_router() reads _auth at call-time, so this must happen before the
    # app starts accepting requests — here is the right place.
    GatewayRouter._auth = server_auth  # type: ignore[attr-defined]

    # ── 2. Lifespan ───────────────────────────────────────────────────────────
    lifespan = VarcoLifespan()

    # ── 3. FastAPI app + middleware ────────────────────────────────────────────
    app = FastAPI(
        title="API Gateway Guards Example",
        version="0.1.0",
        description=(
            "Service-free API gateway demonstrating all RouteGuard variants.\n\n"
            "**Endpoints**:\n"
            "- ``GET /health`` — anonymous\n"
            "- ``GET /v1/echo`` — anonymous, echoes params\n"
            "- ``GET /v1/me`` — any authenticated caller\n"
            "- ``GET /v1/reports/summary`` — requires ``reports:read`` scope\n"
            "- ``POST /v1/admin/flush-cache`` — requires ``admin`` role\n"
            "- ``GET /v1/internal/status`` — requires ``svc:`` subject prefix\n"
        ),
        lifespan=lifespan,
    )

    # Middleware stack (outermost first, install_middleware_stack handles reversal).
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

    # Register varco service-exception handlers (ServiceAuthorizationError → 403,
    # ServiceNotFoundError → 404, etc.) and an explicit HTTPException handler so
    # that 401s raised inside RequestContextMiddleware reach the client correctly.
    # Without this, HTTPException raised inside BaseHTTPMiddleware escapes the
    # middleware stack without being converted to an HTTP response.
    add_exception_handlers(app)

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=dict(exc.headers) if exc.headers else {},
        )

    # ── 4. Build and mount the router (sync — no lifespan needed) ───────────────
    # Routes are registered now, not inside _bootstrap, so httpx ASGITransport
    # (which does not trigger the ASGI lifespan) can reach them in tests.
    # Only registry.load_all() needs the event loop (deferred to _bootstrap).
    gateway_router = GatewayRouter()
    api_router = gateway_router.build_router()
    app.include_router(api_router)

    # ── 5. Async bootstrap (deferred to lifespan) ─────────────────────────────
    async def _bootstrap() -> None:
        """
        Async initialization deferred from ``create_app()``.

        Runs inside ``VarcoLifespan.__call__`` before any request is served.

        Steps:
        1. ``await registry.load_all()`` — populates the issuer keyset so
           ``JwtBearerAuth.verify()`` can validate incoming tokens.

        Edge cases:
            - ``registry.load_all()`` for an ``AuthoritySource`` (in-process
              key material) is effectively a no-op I/O-wise, but still sets
              ``entry._keyset`` — skipping it causes ``get_key()`` to always
              miss and all JWTs to be rejected as ``UnknownKidError``.
            - In tests that use ``httpx.AsyncClient`` with ``ASGITransport``,
              the ASGI lifespan is NOT triggered — so ``registry.load_all()``
              never runs.  The ``client`` fixture works around this by calling
              ``registry.load_all()`` explicitly in its setup via the
              ``test_app`` helper, or by using ``anyio`` lifespan triggering.
        """
        await registry.load_all()

    lifespan._setup = _bootstrap

    return app


# Module-level app — lets uvicorn use ``uvicorn app:app`` without ``--factory``.
app = create_app()

__all__ = ["app", "create_app"]
