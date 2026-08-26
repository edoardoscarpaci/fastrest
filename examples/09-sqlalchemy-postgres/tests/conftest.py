"""
tests/conftest.py
=================
Pytest configuration for the ``09-sqlalchemy-postgres`` integration tests.

Responsibilities
----------------
1. **sys.path setup** — the example root is not an installed package, so its
   modules (``app``, ``models``, ``service``, ``dtos``, ``assembler``) are not
   importable without a path mutation.  We insert the example root at position 0
   so imports resolve before any workspace packages with name collisions.

2. **PostgreSQL testcontainer** — a real Postgres 16 container is spun up once
   per test session.  All tests share one container to avoid per-test Docker
   overhead (~2–3 s per container).

3. **Database URL derivation** — converts the ``psycopg2`` URL produced by
   ``testcontainers`` to the ``asyncpg`` URL required by SQLAlchemy async.

4. **App + client fixtures** — creates the FastAPI app once per session and
   yields a shared ``httpx.AsyncClient``.  Tables are created explicitly
   before the client is yielded because ``ASGITransport`` does **not** trigger
   the FastAPI startup event (see FINDINGS.md F06 / F17).

DESIGN: session-scoped container, session-scoped client
    ✅ Single Docker pull/start per ``pytest`` invocation — fastest possible.
    ✅ Shared DB state across tests — create 3 posts in one test, verify in
       the next without re-inserting.
    ❌ Tests must not assume DB is empty — each test should be idempotent or
       clean up after itself.  Smoke tests here use unique values or count
       relative to their own inserts, so no explicit teardown is needed.

Thread safety:  N/A — pytest fixtures run in a single async task per session.
Async safety:   ✅ All async fixtures use ``asyncio_mode = "auto"`` (from the
                   workspace ``pyproject.toml``) — no ``@pytest.mark.asyncio``
                   annotation needed.

Edge cases:
    - ``testcontainers`` pulls the Docker image on first run.  CI must have
      Docker available; local dev requires Docker Desktop or colima.
    - ``asyncio_default_fixture_loop_scope = "session"`` (set in
      ``examples/pyproject.toml``) means the session-scoped async fixtures
      share a single event loop for the entire session.
    - ``create_app()`` must only be called ONCE per process — ``Base`` is
      module-level and ``SAModelFactory.build(Post)`` raises if called twice
      on the same Base.  The session-scoped ``app_client`` fixture guarantees this.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ── 1. sys.path — insert example root so local modules are importable ─────────
# Must happen before ANY local import so that ``from app import create_app``
# resolves to this example's app.py rather than any installed package.
_EXAMPLE_ROOT = str(Path(__file__).parent.parent.resolve())
if _EXAMPLE_ROOT not in sys.path:
    sys.path.insert(0, _EXAMPLE_ROOT)

import httpx  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

# ── 2. Session-scoped PostgreSQL container ────────────────────────────────────


@pytest.fixture(scope="session")
def postgres_container():
    """
    Spin up a PostgreSQL 16 container for the test session.

    Uses ``testcontainers`` which manages the Docker lifecycle.  The container
    is started before any test in the session and stopped after all tests
    complete.

    Yields:
        A running ``PostgresContainer`` instance.

    Edge cases:
        - Requires Docker.  Without Docker, pytest will fail at the
          ``start()`` call with a ``DockerException``.
        - Image pull happens on first run — may be slow on a cold Docker cache.
    """
    # postgres:16-alpine is small (~80 MB) and sufficient for this example.
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


# ── 3. Database URL derivation ─────────────────────────────────────────────────


@pytest.fixture(scope="session")
def db_url(postgres_container: PostgresContainer) -> str:
    """
    Build an ``asyncpg``-compatible connection URL from the testcontainer.

    ``PostgresContainer.get_connection_url()`` returns a ``psycopg2`` URL
    (``postgresql://...``).  We replace the driver and scheme so SQLAlchemy
    uses the async ``asyncpg`` driver instead.

    DESIGN: two string replacements rather than URL parsing
        ✅ Simple and readable — testcontainers always emits the same format.
        ✅ No extra dependency (e.g. ``furl``, ``yarl``).
        ❌ Fragile if testcontainers changes its URL format — acceptable here
           because the format is stable across testcontainers 4.x.

    Args:
        postgres_container: Running ``PostgresContainer`` from the session fixture.

    Returns:
        ``postgresql+asyncpg://user:password@host:port/dbname`` ready for
        ``create_async_engine()``.
    """
    url = postgres_container.get_connection_url()
    # testcontainers returns "postgresql+psycopg2://..." on some versions
    # and plain "postgresql://..." on others — handle both.
    url = url.replace("postgresql+psycopg2://", "postgresql://")
    url = url.replace("postgresql://", "postgresql+asyncpg://")
    return url


# ── 4. Session-scoped app + client ────────────────────────────────────────────


@pytest.fixture(scope="session")
async def app_client(db_url: str):
    """
    Build the FastAPI app, create the DB schema, and yield an ``AsyncClient``.

    Why tables are created explicitly here
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    ``app.py`` registers an ``@app.on_event("startup")`` handler that calls
    ``create_tables(container)``.  However, ``httpx.ASGITransport`` does NOT
    trigger the FastAPI startup event (FINDINGS.md F06 / F17).  The handler
    never fires, so the schema is never created and every INSERT raises
    ``UndefinedTableError``.

    Fix: after ``create_app(db_url)`` wires up the DI container and SA engine,
    we replicate the schema creation here by building a temporary SA engine
    from the same URL and running ``Base.metadata.create_all``.

    DESIGN: import Base from app.py rather than duplicating the declaration
        ✅ Single source of truth for the table schema — ``Base.metadata``
           is the same object as the one wired into SAConfig inside create_app().
        ✅ ``create_all`` is idempotent (``CREATE TABLE IF NOT EXISTS``) —
           safe to call even if another fixture accidentally created tables first.
        ❌ Tight coupling to app.py internals (``Base`` export) — acceptable
           for a self-contained example; production apps use Alembic migrations.

    Yields:
        A session-scoped ``httpx.AsyncClient`` pointing at the test app.

    Edge cases:
        - ``create_app()`` must only be called once per process because ``Base``
          is module-level.  The ``scope="session"`` on this fixture guarantees
          exactly one call per pytest session.
        - Table creation happens BEFORE the AsyncClient is constructed — the
          client constructor does not trigger any app code.
    """
    # Import after sys.path is set — these are the example's local modules.
    from app import Base, create_app  # noqa: E402

    # Build the FastAPI app (sets up DI, SA engine, router).
    # This also stamps the ORM model onto Base.metadata via SAModelFactory.
    fast_app = create_app(db_url)

    # Explicitly create the schema — startup event won't fire via ASGITransport.
    # Use a short-lived engine for DDL only; the app's own engine handles DML.
    engine = create_async_engine(db_url, echo=False)
    try:
        async with engine.begin() as conn:
            # run_sync is the standard async-SA pattern for synchronous DDL.
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()

    # Yield the client — `async with` here just manages the client lifecycle.
    async with httpx.AsyncClient(
        transport=ASGITransport(app=fast_app),
        base_url="http://test",
    ) as client:
        yield client
