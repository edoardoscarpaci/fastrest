"""
Shared fixtures for varco_casbin tests.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from varco_casbin.config import CasbinSettings
from varco_casbin.engine import CasbinPolicyEngine
from varco_casbin.router import build_policy_router
from varco_core.auth import AuthContext
from varco_core.exception.service import ServiceAuthorizationError
from varco_fastapi.auth import AbstractServerAuth

# ── Session-scoped Postgres container (Plan 012 / RT1, Steps 7-8) ─────────────
#
# ``postgres_url`` starts a single Postgres container ONCE per test session,
# shared by every integration test in this package (RT3's persistence suite).
#
# Per-test namespacing rule: the container is shared, so tests that need
# isolated storage must use a fresh Casbin policy table name / adapter
# instance per test rather than assuming an empty database.
#
# ``VARCO_TEST_POSTGRES_URL`` overrides the container entirely (Open Question
# 1) — when set, no container is started; the value is used as-is and
# reported via ``request.config.stash``.


@pytest.fixture(scope="session")
def postgres_url(request: pytest.FixtureRequest) -> str:
    """
    Session-scoped asyncpg DSN for the shared Postgres container.

    Yields:
        A DSN beginning with ``postgresql+asyncpg://``.
    """
    if not os.environ.get("VARCO_RUN_INTEGRATION"):
        pytest.skip(
            "Integration tests disabled — set VARCO_RUN_INTEGRATION=1 or use -m integration"
        )

    override = os.environ.get("VARCO_TEST_POSTGRES_URL")
    if override:
        request.config.stash.setdefault("varco_test_overrides", []).append(("postgres", override))
        yield override
        return

    from testcontainers.postgres import PostgresContainer  # noqa: PLC0415

    with PostgresContainer("postgres:16-alpine") as container:
        url = container.get_connection_url(driver="asyncpg")
        assert url.startswith("postgresql+asyncpg://"), (
            f"expected an asyncpg DSN from the container, got: {url}"
        )
        yield url


@pytest_asyncio.fixture
async def casbin_db_url(postgres_url: str) -> AsyncIterator[str]:
    """
    Function-scoped, isolated Postgres **database** for one test.

    The SQLAlchemy Casbin adapter has no per-instance table-name override —
    every ``CasbinPolicyEngine(adapter="sqlalchemy")`` writes to the fixed
    ``casbin_rule`` table. Different Casbin model presets (``rbac`` vs
    ``rbac_domains`` vs ``abac``) write ``g``/``p`` rows of different shapes
    (different column counts) to that same table; sharing the session-scoped
    ``postgres_url`` database across tests using different presets makes
    ``load_policy()`` fail with "grouping policy elements do not meet role
    definition" once a later preset reads an earlier preset's rows. A fresh
    database per test sidesteps this without needing per-instance table
    naming.

    Yields:
        An asyncpg DSN pointing at a freshly created, empty database.
    """
    import uuid  # noqa: PLC0415

    import sqlalchemy as sa
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine

    db_name = f"casbin_it_{uuid.uuid4().hex[:8]}"
    admin_engine = create_async_engine(postgres_url, echo=False)
    try:
        async with admin_engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(sa.text(f'CREATE DATABASE "{db_name}"'))
    finally:
        await admin_engine.dispose()

    isolated_url = (
        make_url(postgres_url).set(database=db_name).render_as_string(hide_password=False)
    )
    yield isolated_url

    # Best-effort cleanup: CasbinPolicyEngine's underlying SQLAlchemy engine
    # may still hold a pooled connection open briefly after the test's own
    # `async with` exits — DROP DATABASE fails while any connection remains.
    # Force-disconnect other sessions first, then drop; never fail the test
    # itself over cleanup (a leftover throwaway database is harmless).
    admin_engine = create_async_engine(postgres_url, echo=False)
    try:
        async with admin_engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :db AND pid <> pg_backend_pid()"
                ),
                {"db": db_name},
            )
            await conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    except Exception:  # noqa: BLE001 — cleanup must never fail the test
        pass
    finally:
        await admin_engine.dispose()


class StaticAuth(AbstractServerAuth):
    """Test auth strategy that always yields a fixed AuthContext."""

    def __init__(self, ctx: AuthContext) -> None:
        self._ctx = ctx

    async def __call__(self, request: Request) -> AuthContext:
        return self._ctx


def make_app(engine: CasbinPolicyEngine, ctx: AuthContext) -> FastAPI:
    """
    Build a FastAPI app mounting the policy router with a fixed identity.

    Registers the ServiceAuthorizationError → 403 handler that a real varco app
    installs, so guard denials surface as HTTP 403 in tests.
    """
    app = FastAPI()
    app.include_router(build_policy_router(engine, server_auth=StaticAuth(ctx)))

    @app.exception_handler(ServiceAuthorizationError)
    async def _forbidden(request: Request, exc: ServiceAuthorizationError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": "forbidden"})

    return app


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[CasbinPolicyEngine]:
    """A started in-memory RBAC engine, stopped after the test."""
    eng = CasbinPolicyEngine(CasbinSettings(model_preset="rbac"))
    await eng.start()
    try:
        yield eng
    finally:
        await eng.stop()


def client_for(app: FastAPI) -> AsyncClient:
    """An httpx AsyncClient bound to the app via ASGI transport."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── Session-scoped MongoDB container (Plan 012 / RT1, Steps 7-8) ──────────────
#
# ``mongo_url`` starts a single MongoDB container ONCE per test session,
# shared by the Beanie Casbin adapter integration test.
#
# ``VARCO_TEST_MONGO_URL`` overrides the container entirely (Open Question
# 1) — when set, no container is started; the value is used as-is and
# reported via ``request.config.stash``.


@pytest.fixture(scope="session")
def mongo_url(request: pytest.FixtureRequest) -> str:
    """
    Session-scoped MongoDB connection URL — real container or override.

    Yields:
        A ``mongodb://`` connection URL for the shared server (no
        ``authSource`` suffix — callers needing that append it themselves,
        since it is a per-adapter connection detail, not a generic one).
    """
    if not os.environ.get("VARCO_RUN_INTEGRATION"):
        pytest.skip(
            "Integration tests disabled — set VARCO_RUN_INTEGRATION=1 or use -m integration"
        )

    override = os.environ.get("VARCO_TEST_MONGO_URL")
    if override:
        request.config.stash.setdefault("varco_test_overrides", []).append(("mongo", override))
        yield override
        return

    from testcontainers.mongodb import MongoDbContainer  # noqa: PLC0415

    with MongoDbContainer("mongo:7") as container:
        yield container.get_connection_url()
