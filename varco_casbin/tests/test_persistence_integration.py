"""
Integration test for the SQLAlchemy policy store against real Postgres.
=======================================================================
Marked ``integration`` — skipped by default; run with::

    uv run pytest varco_casbin/tests/ -m integration

Requires Docker (testcontainers spins up a throw-away Postgres) and the
``varco-casbin[sqlalchemy]`` extra.  Verifies the dynamic-CRUD persistence
contract: a rule written through one engine survives into a fresh engine
backed by the same database.
"""

from __future__ import annotations

import pytest

from varco_casbin.config import CasbinSettings
from varco_casbin.engine import CasbinPolicyEngine
from varco_core.auth.policy import EnforcementRequest as ER

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def postgres_url() -> str:
    """Start a throw-away Postgres container and yield an asyncpg URL."""
    testcontainers = pytest.importorskip("testcontainers.postgres")
    with testcontainers.PostgresContainer("postgres:16-alpine") as pg:
        # testcontainers returns a psycopg2 URL; rewrite to asyncpg.
        sync_url = pg.get_connection_url()
        yield sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")


async def test_policy_persists_across_engines_postgres(postgres_url: str) -> None:
    """A policy written through one engine is enforced by a fresh engine."""
    settings = CasbinSettings(
        model_preset="rbac", adapter="sqlalchemy", db_url=postgres_url
    )

    async with CasbinPolicyEngine(settings) as writer:
        await writer.add_role_for_user("alice", "admin")
        await writer.add_policy("admin", "*", "*")

    async with CasbinPolicyEngine(settings) as reader:
        assert await reader.enforce(ER("alice", "posts", "read")) is True
        assert ("admin", "*", "*") in await reader.list_policies()
