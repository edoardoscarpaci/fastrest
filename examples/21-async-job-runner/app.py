"""
app.py
======
Application factory for the ``21-async-job-runner`` example.

Demonstrates the varco async job pattern:

1. ``InMemoryJobStore`` — in-process job store; state is lost on restart.
2. ``JobRunner`` — asyncio-Task-per-job execution engine; requires ``start()``
   before jobs can be enqueued.
3. ``POST /v1/reports`` → ``202 Accepted`` + ``job_id`` + ``status_url``.
4. ``GET /v1/jobs/{job_id}`` → poll for status (``pending`` / ``running`` /
   ``completed`` / ``failed``).

No database, no broker, no Docker required.

Run locally::

    cd examples/21-async-job-runner
    uv run uvicorn app:app --reload

DESIGN: plain FastAPI + explicit lifespan over create_varco_app
    ✅ Avoids scanning varco_fastapi DI (which brings in CORS, TrustStore, etc.)
       and keeps the example focused on the job system.
    ✅ Lifespan is explicit — readers see exactly when runner.start() /
       runner.stop() are called.
    ✅ ``InMemoryJobStore`` and ``JobRunner`` are constructed directly — no
       container needed, making the dependency graph visible.
    ❌ Loses automatic lifecycle management from create_varco_app; the example
       shows the low-level wiring intentionally.

Thread safety:  ✅ Called once at startup; no concurrent access to the factory.
Async safety:   ✅ Synchronous factory; lifespan is an async context manager.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from router import build_router
from varco_fastapi.exceptions import add_exception_handlers
from varco_fastapi.job import InMemoryJobStore, JobRunner
from varco_fastapi.middleware import ErrorMiddleware, install_middleware_stack


def create_app() -> FastAPI:
    """
    Build and return the configured FastAPI application.

    Wires together:
    - ``InMemoryJobStore`` — shared between the router and the runner.
    - ``JobRunner`` — lifecycle managed by the ASGI lifespan.
    - Router with ``POST /v1/reports`` and ``GET /v1/jobs/{job_id}``.

    Returns:
        A configured ``FastAPI`` instance ready for an ASGI server.

    Edge cases:
        - ``runner.start()`` must be called before the first request; the
          lifespan context manager handles this automatically.
        - ``InMemoryJobStore`` state is lost on process restart.  For durable
          jobs use ``SaJobStore`` or ``RedisJobStore``.

    Thread safety:  ✅ Called once per process.
    Async safety:   ✅ Synchronous factory; no event loop required at call time.
    """
    # ── Shared infrastructure ─────────────────────────────────────────────────
    store = InMemoryJobStore()
    runner = JobRunner(store=store)

    # ── Lifespan: start runner on startup, stop on shutdown ───────────────────
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        """
        Start the JobRunner before accepting requests; stop it on shutdown.

        DESIGN: lifespan context manager over startup/shutdown event hooks
            ✅ Single-function lifecycle — easier to follow than separate hooks.
            ✅ Guaranteed cleanup even if startup raises after runner.start().
            ✅ Mirrors the pattern used by create_varco_app internally.
        """
        await runner.start()
        try:
            yield
        finally:
            await runner.stop()

    # ── FastAPI app ───────────────────────────────────────────────────────────
    app = FastAPI(
        title="Async Job Runner Example",
        version="0.1.0",
        description=(
            "Demonstrates the varco async job pattern:\n\n"
            "- ``POST /v1/reports`` — enqueue a report job; returns 202 + job_id\n"
            "- ``GET /v1/jobs/{job_id}`` — poll for status and result\n\n"
            "No database, no broker, no Docker required."
        ),
        lifespan=lifespan,
    )

    # Outermost middleware: converts unhandled exceptions to JSON responses
    install_middleware_stack(app, [ErrorMiddleware])
    add_exception_handlers(app)

    # ── Routes ────────────────────────────────────────────────────────────────
    app.include_router(build_router(store=store, runner=runner))

    return app


# Module-level app — lets uvicorn use ``uvicorn app:app`` without ``--factory``.
app = create_app()

__all__ = ["app", "create_app"]
