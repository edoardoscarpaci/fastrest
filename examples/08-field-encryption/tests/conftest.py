"""
tests/conftest.py
=================
Pytest configuration for the ``08-field-encryption`` integration tests.

Responsibilities
----------------
1. **sys.path setup** — the example root is not an installed package, so its
   modules (``app``, ``models``, ``service``, ``dtos``, ``assembler``, ``keys``)
   are not importable without a path mutation.  We insert the example root at
   position 0 so imports resolve before any workspace packages.

2. **Shared encryptor** — a single ``FernetFieldEncryptor`` is generated once
   per session and reused across all tests.  This means data written in one
   test can be decrypted in later tests (important for the "DB stores ciphertext"
   and "update encrypted field" tests).

3. **PostgreSQL testcontainer** — a real Postgres 16 container is spun up once
   per test session.  All tests share one container to minimize Docker overhead.

4. **Database URL derivation** — converts the ``psycopg2`` URL produced by
   ``testcontainers`` to the ``asyncpg`` URL required by SQLAlchemy async.

5. **App + client fixtures** — creates the FastAPI app once per session and
   yields a shared ``httpx.AsyncClient``.  Tables are created explicitly
   before the client is yielded because ``ASGITransport`` does NOT trigger the
   FastAPI startup event (FINDINGS.md F06 / F17).

DESIGN: session-scoped encryptor + container + client
    ✅ Single Docker pull/start per ``pytest`` invocation — fastest possible.
    ✅ Same encryptor across all tests — data written in test A can be read in
       test B without re-encrypting.
    ✅ Single ``create_app()`` call — ``Base.metadata`` is module-level so
       calling create_app() twice in the same process would raise.
    ❌ Tests share DB state — each test must be idempotent or clean up.
       Smoke tests use unique values or count relative to their own inserts.

Thread safety:  N/A — pytest fixtures run in a single async task per session.
Async safety:   ✅ All async fixtures use ``asyncio_mode = "auto"`` from the
                   workspace ``pyproject.toml`` — no ``@pytest.mark.asyncio``
                   annotation needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ── 1. sys.path — insert example root before ANY local import ─────────────────
# Must happen first so ``from app import create_app`` resolves to THIS example's
# app.py rather than any installed workspace package with the same name.
_EXAMPLE_ROOT = str(Path(__file__).parent.parent.resolve())
if _EXAMPLE_ROOT not in sys.path:
    sys.path.insert(0, _EXAMPLE_ROOT)

import pytest  # noqa: E402
import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402


# ── 2. Session-scoped encryptor ────────────────────────────────────────────────


@pytest.fixture(scope="session")
def encryptor():
    """
    Generate a single ``FernetFieldEncryptor`` for the test session.

    All tests share this encryptor — data written in one test can be decrypted
    in another.  This mirrors the production pattern where a single stable key
    is loaded from a vault on startup.

    Returns:
        A ``FernetFieldEncryptor`` backed by a fresh random key.

    Edge cases:
        - A new key is generated on every ``pytest`` invocation — ciphertext
          written in a previous run cannot be decrypted in the next run.
          Acceptable for tests; production must use a persistent key store.
    """
    # Import after sys.path is set — this is the example's local module.
    from keys import generate_ephemeral_encryptor

    return generate_ephemeral_encryptor()


# ── 3. Session-scoped PostgreSQL container ────────────────────────────────────


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
        - Requires Docker.  Without Docker, pytest will fail at container start
          with a ``DockerException``.
        - Image pull happens on first run — may be slow on a cold Docker cache.
    """
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


# ── 4. Database URL derivation ─────────────────────────────────────────────────


@pytest.fixture(scope="session")
def db_url(postgres_container: PostgresContainer) -> str:
    """
    Build an ``asyncpg``-compatible connection URL from the testcontainer.

    ``PostgresContainer.get_connection_url()`` returns a ``psycopg2`` URL.
    We replace the driver so SQLAlchemy uses the async ``asyncpg`` driver.

    Args:
        postgres_container: Running ``PostgresContainer`` from the session fixture.

    Returns:
        ``postgresql+asyncpg://user:password@host:port/dbname`` ready for
        ``create_async_engine()``.
    """
    url = postgres_container.get_connection_url()
    # testcontainers may return either "postgresql+psycopg2://..." or
    # "postgresql://..." depending on the version — normalise both.
    url = url.replace("postgresql+psycopg2://", "postgresql://")
    url = url.replace("postgresql://", "postgresql+asyncpg://")
    return url


# ── 5. Session-scoped app + client ────────────────────────────────────────────


@pytest.fixture(scope="session")
async def app_client(db_url: str, encryptor):
    """
    Build the FastAPI app, create the DB schema, and yield an ``AsyncClient``.

    Why tables are created explicitly here
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    ``app.py`` registers ``@app.on_event("startup")`` that calls
    ``create_tables(container)``.  However, ``httpx.ASGITransport`` does NOT
    trigger FastAPI startup events (FINDINGS.md F06 / F17) — the handler never
    fires, so the schema is never created and every INSERT raises
    ``UndefinedTableError``.

    Fix: after ``create_app(db_url, encryptor=enc)`` wires the DI container
    and builds the SA ORM model for Patient (populating ``Base.metadata``),
    we create the tables explicitly via a temporary engine.

    DESIGN: import Base from app.py rather than re-declaring it
        ✅ Single source of truth — ``Base.metadata`` is the same object wired
           into the SAModelFactory inside ``create_app()``.
        ✅ ``create_all`` is idempotent — safe to call even if tables exist.
        ❌ Tight coupling to ``app.py`` internals — acceptable for an example;
           production apps use Alembic migrations.

    Args:
        db_url:    Session-scoped DB URL pointing at the testcontainer.
        encryptor: Session-scoped ``FernetFieldEncryptor`` for the test session.

    Yields:
        A session-scoped ``httpx.AsyncClient`` pointing at the test app.

    Edge cases:
        - ``create_app()`` must only be called ONCE per process because
          ``Base.metadata`` is module-level.  The ``scope="session"`` on this
          fixture guarantees exactly one call per pytest session.
        - Table creation happens BEFORE the AsyncClient is constructed — the
          client constructor does not trigger any app code.
    """
    # Import after sys.path is set — these are the example's local modules.
    from app import create_app, Base  # noqa: E402

    # Build the FastAPI app.  Passing the session-scoped encryptor ensures the
    # same key is used for all data written during this test session — data
    # written in one test can be read back and decrypted correctly in another.
    fast_app = create_app(db_url, encryptor=encryptor)

    # Explicitly create the schema — startup event won't fire via ASGITransport.
    # Use a short-lived engine for DDL only; the app's own engine handles DML.
    engine = create_async_engine(db_url, echo=False)
    try:
        async with engine.begin() as conn:
            # run_sync is the standard async-SA pattern for synchronous DDL.
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()

    # Yield the client — ``async with`` manages the client lifecycle.
    async with httpx.AsyncClient(
        transport=ASGITransport(app=fast_app),
        base_url="http://test",
    ) as client:
        yield client
