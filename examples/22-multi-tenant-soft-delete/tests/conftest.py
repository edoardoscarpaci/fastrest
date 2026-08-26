"""
conftest.py
===========
Shared test fixtures for the ``22-multi-tenant-soft-delete`` example.

All tests are integration tests (``@pytest.mark.integration``) — they
require a real PostgreSQL instance managed by ``testcontainers``.

Session scope is used for the container and the FastAPI app so that:
- The Docker container starts once per test session (not per test).
- ``create_app()`` is called once — the module-level ``Base`` object is shared
  across all tests in the session (safe because SA's ``build()`` is idempotent).
- ``create_tables()`` is called once to create the DDL before the first test.

DESIGN: session-scoped ``client`` fixture
    ✅ Single Postgres container = fast test suite (no container restart per test).
    ✅ Single ``create_app()`` call avoids the duplicate-Base SA issue.
    ❌ Tests share database state — each test uses a unique tenant ID to isolate.

DESIGN: ``create_app()`` returns ``(FastAPI, DIContainer)``
    ``ASGITransport`` does not trigger the FastAPI lifespan (Finding F06), so
    ``create_tables`` must be called from the test fixture.  Instead of
    building a second container (which would fail to find the SA bindings from
    the first container), we extract the container from ``create_app()`` and
    pass it directly to ``create_tables``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ── sys.path fix ──────────────────────────────────────────────────────────────
# The example lives outside the installed packages; add its directory to
# sys.path so that ``import app``, ``import service``, etc. work without
# installing the example as a package.
_EXAMPLE_ROOT = str(Path(__file__).parent.parent.resolve())
if _EXAMPLE_ROOT not in sys.path:
    sys.path.insert(0, _EXAMPLE_ROOT)

import httpx  # noqa: E402
import pytest  # noqa: E402
from app import Base, create_app  # noqa: E402
from httpx import ASGITransport  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

# ── PostgreSQL container ──────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def postgres_container():
    """
    Start a PostgreSQL 16 container for the entire test session.

    Session scope ensures the container starts once and is reused across
    all tests — Docker startup overhead is paid only once.

    Yields:
        A running ``PostgresContainer`` instance.
    """
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def db_url(postgres_container: PostgresContainer) -> str:
    """
    Return the asyncpg-compatible connection URL for the test database.

    Converts the psycopg2 URL returned by testcontainers to an asyncpg URL
    by replacing the driver and scheme prefix.

    Args:
        postgres_container: The running ``PostgresContainer`` fixture.

    Returns:
        A ``postgresql+asyncpg://`` connection URL string.

    Edge cases:
        - The returned URL includes credentials and a random port assigned by
          Docker; it is only valid while the container is running.
    """
    # testcontainers returns a psycopg2 URL; SA async requires asyncpg.
    raw = postgres_container.get_connection_url()
    return raw.replace("psycopg2", "asyncpg").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


# ── FastAPI application ───────────────────────────────────────────────────────


@pytest.fixture(scope="session")
async def test_app(db_url: str):
    """
    Build the FastAPI app and create the database schema.

    ``ASGITransport`` does not trigger the FastAPI lifespan (Finding F06),
    so ``create_tables`` is called explicitly before the app is used.

    ``create_app()`` returns ``(FastAPI, DIContainer)``; we use the same
    container to call ``create_tables`` to avoid the double-container issue
    (a second ``_build_container`` call would create a second engine that
    does not share the already-scanned ``SQLAlchemyRepositoryProvider``
    singleton).

    Args:
        db_url: asyncpg URL from the ``db_url`` fixture.

    Returns:
        A configured ``FastAPI`` application with tables already created.
    """
    # create_app returns (FastAPI, DIContainer) — extract both.
    fastapi_app, container = create_app(db_url)

    # Explicitly create tables; the lifespan startup event won't fire via
    # ASGITransport (Finding F06).  Use a short-lived engine for DDL only;
    # provider.register(Note) already populated Base.metadata.
    engine = create_async_engine(db_url, echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()

    return fastapi_app


# ── HTTP client ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
async def client(test_app) -> httpx.AsyncClient:
    """
    Return a session-scoped ``httpx.AsyncClient`` backed by the test app.

    Session scope keeps the client alive for all tests — ``test_app`` is
    also session-scoped, so no per-test teardown is needed.

    Args:
        test_app: The fully initialised FastAPI application.

    Yields:
        An ``httpx.AsyncClient`` configured for in-process ASGI transport.
    """
    async with httpx.AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as c:
        yield c
