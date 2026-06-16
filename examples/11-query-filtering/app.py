"""
app.py
======
Application factory for the ``11-query-filtering`` example.

Demonstrates varco's query AST pipeline served as a minimal read-only
FastAPI catalog — no database, no broker, no Docker required.

Bootstrap is intentionally simple:
1. Build a plain ``FastAPI`` app.
2. Install ``ErrorMiddleware`` (outermost — catches all unhandled exceptions).
3. Include the ``router`` from ``router.py`` (plain ``APIRouter``).

No DI container — ``router.py`` reads directly from the module-level
``PRODUCTS`` tuple and holds module-level pipeline component singletons.

Run locally::

    cd examples/11-query-filtering
    uv run uvicorn app:app --reload

Then try::

    curl "http://localhost:8000/v1/products?q=price >= 50.0"
    curl "http://localhost:8000/v1/products?q=in_stock = True AND category = 'electronics'"
    curl "http://localhost:8000/v1/products?sort=-price&limit=5"
    curl "http://localhost:8000/v1/products?q=name LIKE 'widget'"
    curl "http://localhost:8000/v1/products?q=category IN ('books', 'home')&sort=+price"

DESIGN: plain ``APIRouter`` + ``FastAPI`` over varco ``GenericRouter``
    The varco ``GenericRouter`` / ``@route`` decorator only injects ``ctx``
    (AuthContext) and path params into handlers.  Since this example needs
    direct ``Query()`` params (``q=``, ``sort=``, ``limit=``, ``offset=``),
    a plain FastAPI ``APIRouter`` is simpler and produces better OpenAPI docs.

    ✅ FastAPI ``Query()`` gives OpenAPI schema and validation for free.
    ✅ No DI machinery needed — keeps the example self-contained.
    ❌ Loses varco middleware / ``RouteGuard`` integration — acceptable since
       the focus is the query AST pipeline, not authorization.

Thread safety:  ✅ ``create_app()`` is called once at startup.
Async safety:   ✅ Synchronous factory — no event loop required at call time.
"""

from __future__ import annotations

from fastapi import FastAPI

from varco_fastapi.exceptions import add_exception_handlers
from varco_fastapi.middleware import ErrorMiddleware, install_middleware_stack

from router import router


def create_app() -> FastAPI:
    """
    Build and return the configured FastAPI application.

    Returns:
        A ``FastAPI`` instance with the product catalog router mounted at
        ``/v1/products`` and ``ErrorMiddleware`` installed.

    Edge cases:
        - Each call produces an independent ``FastAPI`` instance — safe for
          test isolation (tests call ``create_app()`` directly, never import
          the module-level ``app``).
        - The module-level pipeline singletons in ``router.py`` (``_parser``,
          ``_optimizer``, ``_coercer``) are shared across all ``create_app()``
          calls because they are stateless — this is intentional and safe.
    """
    app = FastAPI(
        title="Query Filtering Example",
        version="0.1.0",
        description=(
            "Demonstrates the full varco query AST pipeline on a read-only "
            "product catalog.\n\n"
            "**Filter syntax** (``?q=`` param, Lark grammar):\n"
            "- ``q=price >= 50.0``\n"
            "- ``q=in_stock = True``\n"
            "- ``q=category = 'electronics'``\n"
            "- ``q=name LIKE 'widget'``\n"
            "- ``q=price >= 10.0 AND price <= 50.0``\n"
            "- ``q=category IN ('books', 'home')``\n\n"
            "**Sort syntax**: ``?sort=-price`` (desc), ``?sort=+name`` (asc)\n\n"
            "**Pagination**: ``?limit=5&offset=0``"
        ),
    )

    # ErrorMiddleware converts unhandled exceptions to JSON 500 responses.
    # Outermost middleware — wraps routing and all inner middleware.
    install_middleware_stack(app, [ErrorMiddleware])

    # Register varco service-exception handlers (404, 403, 422, etc.)
    add_exception_handlers(app)

    app.include_router(router)

    return app


# ── Module-level app instance for ``uvicorn app:app`` ─────────────────────────
# DESIGN: module-level app over --factory
#   ✅ Simpler uvicorn command — no --factory flag needed.
#   ✅ Compatible with tools that import ``app`` directly (gunicorn, etc.).
#   ❌ App is created at import time — tests should call create_app() directly
#      and not import this module-level ``app``.
app = create_app()

__all__ = ["app", "create_app"]
