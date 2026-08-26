"""
app.py
======
Application factory for the ``04-profiling-hotspot`` example.

Demonstrates the full varco profiling stack:

1. ``set_profiling_enabled(True)`` — global kill-switch activated at startup.
2. ``@profile`` and ``profiled()`` — per-function and block-level profiling
   (defined in ``work.py``).
3. ``ProfilingMiddleware`` with ``attach_headers=True`` — every response
   carries ``X-Profile-Wall-Ms`` and ``X-Profile-Mem-Kb`` headers.
4. Custom CPU backend (``"counting"``) registered via
   ``register_cpu_backend`` — see ``work.py``.

No database, no broker, no Docker required.

Run locally::

    cd examples/04-profiling-hotspot
    uv run uvicorn app:app --reload

DESIGN: bare FastAPI assembly without create_varco_app
    ✅ Avoids pulling in tracing, metrics, and session middleware that would
       inflate the profiling reports with unrelated overhead.
    ✅ ProfilingMiddleware is explicit and visible — readers see exactly what
       middleware is active.
    ✅ Synchronous factory — uvicorn can serve ``app:app`` without
       ``--factory``.
    ❌ Loses the ``enable_profiling=True`` convenience of ``create_varco_app``
       but the manual wiring is more instructive for an example.

Thread safety:  ✅ Called once at startup.
Async safety:   ✅ Synchronous factory; no event loop required at call time.
"""

from __future__ import annotations

from fastapi import FastAPI
from router import ProfilingRouter
from varco_core.profiling import set_profiling_enabled
from varco_fastapi.exceptions import add_exception_handlers
from varco_fastapi.middleware import ErrorMiddleware, install_middleware_stack
from varco_fastapi.middleware.profiling import ProfilingMiddleware, ProfilingSettings


def create_app() -> FastAPI:
    """
    Build and return the configured FastAPI application with profiling enabled.

    Steps:
    1. Enable profiling globally so ``@profile`` wrappers are active.
    2. Build ``ProfilingSettings`` with ``enabled=True``,
       ``attach_headers=True``, and ``slow_threshold_ms=0`` (log all requests).
    3. Assemble ``FastAPI`` with ``ErrorMiddleware`` (outermost) and
       ``ProfilingMiddleware`` (innermost — closest to the route handler).
    4. Mount the ``ProfilingRouter`` at ``/v1``.

    Returns:
        A configured ``FastAPI`` instance ready for an ASGI server.

    Edge cases:
        - ``set_profiling_enabled(True)`` is called here, not at module level,
          so tests that call ``create_app()`` independently each get a fresh
          enable.  Tests that disable profiling must do so explicitly.
        - ``cProfile`` and ``tracemalloc`` are process-global.
          ``ProfilingMiddleware`` serialises concurrent requests with an
          ``asyncio.Lock`` — only one request is profiled at a time; others
          pass through unprofiled.

    Thread safety:  ✅ Intended to be called once per process.
    Async safety:   ✅ Synchronous; no event loop required at call time.
    """
    # ── 1. Global kill-switch ON ──────────────────────────────────────────────
    set_profiling_enabled(True)

    # ── 2. Profiling middleware settings ─────────────────────────────────────
    # slow_threshold_ms=0.0 means "log every request" — good for a demo;
    # in production you would set this to e.g. 200.0 to only report slow ones.
    profiling_settings = ProfilingSettings(
        enabled=True,
        attach_headers=True,
        slow_threshold_ms=0.0,
    )

    # ── 3. FastAPI + middleware stack ─────────────────────────────────────────
    app = FastAPI(
        title="Profiling Hotspot Example",
        version="0.1.0",
        description=(
            "Demonstrates varco's built-in CPU + memory profiling system.\n\n"
            "**Endpoints**:\n"
            "- ``GET /v1/compute`` — CPU loop decorated with ``@profile``\n"
            "- ``GET /v1/allocate`` — memory allocation via ``profiled()``\n"
            "- ``GET /v1/custom-backend`` — custom registered CPU backend\n\n"
            "Every response carries ``X-Profile-Wall-Ms`` and "
            "``X-Profile-Mem-Kb`` headers when middleware profiling is active."
        ),
    )

    # Middleware stack (outermost → innermost after Starlette reversal):
    #   ErrorMiddleware      — catches all exceptions, returns JSON
    #   ProfilingMiddleware  — innermost: attributes cost to route handler only
    #
    # DESIGN: ProfilingMiddleware must be innermost (last in install order)
    #   ✅ Captures only route-handler time — no middleware overhead included.
    #   ✅ Mirrors the placement in create_varco_app's _try_add_profiling_middleware.
    install_middleware_stack(
        app,
        [
            ErrorMiddleware,
            (ProfilingMiddleware, {"settings": profiling_settings}),
        ],
    )

    add_exception_handlers(app)

    # ── 4. Router ─────────────────────────────────────────────────────────────
    profiling_router = ProfilingRouter()
    app.include_router(profiling_router.build_router())

    return app


# Module-level app — lets uvicorn use ``uvicorn app:app`` without ``--factory``.
app = create_app()

__all__ = ["app", "create_app"]
