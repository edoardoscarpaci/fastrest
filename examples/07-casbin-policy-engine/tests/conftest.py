"""
conftest.py
===========
Session-scoped fixtures for the ``07-casbin-policy-engine`` integration tests.

All tests in this suite require Docker (via ``testcontainers``) to spin up a
real PostgreSQL instance.  They are tagged ``@pytest.mark.integration`` and are
skipped by default; run with ``pytest -m integration`` to execute them.

Fixture scoping
---------------
``postgres_container``  — session-scoped: one Postgres instance for the entire run.
``db_url`` / ``sync_db_url``  — session-scoped: derived from the container URL.
``engine``  — session-scoped: one ``CasbinPolicyEngine`` shared across tests.
``client``  — function-scoped: each test gets a fresh HTTP client with a
              fresh in-memory document store.

DESIGN: session-scoped engine + function-scoped client
    Starting a new Postgres container per test would be prohibitively slow.
    The engine is shared across tests (session-scoped), which means its policy
    store accumulates rules across tests.  To avoid test-order dependencies,
    each test that adds rules should use unique subject / object names, OR
    flush the engine's policy before running via ``await engine.reload()``.

    ✅ One Postgres container → fast test suite.
    ✅ ``casbin_rule`` table persists between tests — validating durability.
    ❌ Shared policy state — tests must not rely on a clean-slate policy unless
       they reset it themselves.

Thread safety:  ✅ Session fixtures run once in the main thread.
Async safety:   ✅ Async fixtures use ``asyncio_mode = "auto"`` (no decorator needed).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ── sys.path fix ──────────────────────────────────────────────────────────────
# Add the example root so ``from app import create_app`` etc. resolve correctly
# regardless of the working directory pytest is invoked from.
_EXAMPLE_ROOT = str(Path(__file__).parent.parent.resolve())
if _EXAMPLE_ROOT not in sys.path:
    sys.path.insert(0, _EXAMPLE_ROOT)


# ── Postgres testcontainer ────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def postgres_container():
    """
    Spin up a Postgres 16-alpine container for the test session.

    Yields:
        A running ``testcontainers.postgres.PostgresContainer`` instance.

    Edge cases:
        - Requires Docker to be running on the host.
        - The container is torn down automatically after the session.
    """
    from testcontainers.postgres import PostgresContainer

    # Use alpine for fast pull; 16 matches the production target version.
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def db_url(postgres_container) -> str:
    """
    Return an ``asyncpg`` SQLAlchemy URL pointing at the test Postgres instance.

    Used by ``CasbinPolicyEngine`` (SQLAlchemy async adapter) and for the
    ``app.create_app(db_url=...)`` call.

    Args:
        postgres_container: Session-scoped Postgres testcontainer.

    Returns:
        ``"postgresql+asyncpg://..."`` connection string.

    Edge cases:
        - ``testcontainers`` returns a ``psycopg2`` URL; we replace the driver
          so ``casbin-async-sqlalchemy-adapter`` (asyncpg-based) can use it.
    """
    url = postgres_container.get_connection_url()
    # Replace the sync psycopg2 driver with asyncpg for the async adapter.
    return url.replace("psycopg2", "asyncpg").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


# ── Casbin engine fixture ─────────────────────────────────────────────────────


@pytest.fixture(scope="session")
async def engine(db_url: str):
    """
    Yield a started ``CasbinPolicyEngine`` backed by the test Postgres instance.

    The engine is started once and shared across the entire test session.
    The ``casbin_rule`` table is created on first start (idempotent).

    Args:
        db_url: Async SQLAlchemy URL from the ``db_url`` fixture.

    Yields:
        A running ``CasbinPolicyEngine`` with the ``"rbac"`` preset.

    Edge cases:
        - Policy rules added by tests accumulate in the shared DB; tests
          using unique subject/object names avoid cross-test interference.
        - The engine is stopped (``stop()``) after the session — the DB
          container is torn down at the same time so cleanup is moot.
    """
    from varco_casbin.config import CasbinSettings
    from varco_casbin.engine import CasbinPolicyEngine

    settings = CasbinSettings(
        model_preset="rbac",
        adapter="sqlalchemy",
        db_url=db_url,
        auto_save=True,
    )
    eng = CasbinPolicyEngine(settings)
    await eng.start()
    try:
        yield eng
    finally:
        await eng.stop()


# ── HTTP client fixture ────────────────────────────────────────────────────────


@pytest.fixture
async def client(db_url: str):
    """
    Yield an ``httpx.AsyncClient`` backed by a fresh app instance.

    Each test function gets its own app instance with an isolated in-memory
    document store, but shares the same Postgres policy store (via ``db_url``).

    The ASGI lifespan is triggered via ``httpx``'s ``ASGITransport`` with
    ``app.router.lifespan_context`` so the Casbin engine starts properly.

    Args:
        db_url: Async SQLAlchemy URL from the ``db_url`` fixture.

    Yields:
        An active ``httpx.AsyncClient`` connected to a fresh example app.

    Edge cases:
        - ``ASGITransport`` with ``raise_app_exceptions=False`` converts
          unhandled ASGI exceptions to HTTP responses — required because
          Starlette's ``BaseHTTPMiddleware`` can re-raise ``HTTPException``
          through the stream machinery in test mode.
        - The app's in-memory document store is fresh per test; Casbin
          policies persist in the shared Postgres DB.
    """
    import httpx
    from app import create_app  # imported here so sys.path fix above takes effect
    from httpx import ASGITransport

    # Create a fresh app instance with the test Postgres policy store.
    app = create_app(db_url=db_url, model_preset="rbac")

    # Trigger the ASGI lifespan (starts the engine).
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as c:
            yield c
