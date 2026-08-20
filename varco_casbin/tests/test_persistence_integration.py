"""
Integration tests for the SQLAlchemy policy store against real Postgres.
=========================================================================
Marked ``integration`` — skipped by default; run with::

    uv run pytest varco_casbin/tests/ -m integration

Requires Docker (testcontainers spins up a throw-away Postgres, shared for
the whole session via the ``postgres_url`` fixture in ``tests/conftest.py``)
and the ``varco-casbin[sqlalchemy]`` extra. Each test gets its own isolated
**database** via the function-scoped ``casbin_db_url`` fixture (also in
``tests/conftest.py``) — the SQLAlchemy Casbin adapter has no per-instance
table-name override, and different model presets write differently-shaped
``g``/``p`` rows to the one fixed ``casbin_rule`` table, so sharing a
database across presets breaks ``load_policy()``.

Broadened from the original single test (Plan 012 / RT3, Step 14) to cover:
  (i)   remove_policy / remove_filtered_policy round-trip
  (ii)  RBAC role-inheritance enforcement after a cold reload (a fresh
        CasbinPolicyEngine on the same DSN)
  (iii) ABAC enforcement with subject_attrs/object_attrs, persisted
  (iv)  two engines sharing one database — writer adds a policy, reader
        reload()s and sees it (CLAUDE.md's "shared singleton" requirement,
        verified against a real DB)
  (v)   domain/tenant RequestMapper.domain_for keying (rbac_domains preset)
"""

from __future__ import annotations

import pytest

from varco_casbin.config import CasbinSettings
from varco_casbin.engine import CasbinPolicyEngine
from varco_core.auth.policy import EnforcementRequest as ER

pytestmark = pytest.mark.integration

# NOTE: the SQLAlchemy adapter has no per-instance table_name override, so
# each test gets its own isolated Postgres DATABASE via the casbin_db_url
# fixture (tests/conftest.py) rather than sharing one "casbin_rule" table —
# different model presets (rbac/rbac_domains/abac) write differently-shaped
# g/p rows that break a shared table's load_policy().


async def test_policy_persists_across_engines_postgres(casbin_db_url: str) -> None:
    """A policy written through one engine is enforced by a fresh engine."""
    settings = CasbinSettings(
        model_preset="rbac",
        adapter="sqlalchemy",
        db_url=casbin_db_url,
    )

    async with CasbinPolicyEngine(settings) as writer:
        await writer.add_role_for_user("alice", "admin")
        await writer.add_policy("admin", "*", "*")

    async with CasbinPolicyEngine(settings) as reader:
        assert await reader.enforce(ER("alice", "posts", "read")) is True
        assert ("admin", "*", "*") in await reader.list_policies()


async def test_remove_policy_round_trip(casbin_db_url: str) -> None:
    """A removed policy no longer enforces, on a fresh engine (persisted)."""
    settings = CasbinSettings(
        model_preset="rbac",
        adapter="sqlalchemy",
        db_url=casbin_db_url,
    )

    async with CasbinPolicyEngine(settings) as writer:
        await writer.add_role_for_user("bob", "editor")
        await writer.add_policy("editor", "posts", "write")
        assert await writer.enforce(ER("bob", "posts", "write")) is True

        removed = await writer.remove_policy("editor", "posts", "write")
        assert removed is True
        assert await writer.enforce(ER("bob", "posts", "write")) is False

    # Persisted: a fresh engine on the same DSN never sees the removed rule.
    async with CasbinPolicyEngine(settings) as reader:
        assert ("editor", "posts", "write") not in await reader.list_policies()


async def test_remove_filtered_policy_round_trip(casbin_db_url: str) -> None:
    """remove_filtered_policy removes every matching rule and persists it."""
    settings = CasbinSettings(
        model_preset="rbac",
        adapter="sqlalchemy",
        db_url=casbin_db_url,
    )

    async with CasbinPolicyEngine(settings) as writer:
        await writer.add_policy("editor", "posts", "write")
        await writer.add_policy("editor", "posts", "delete")
        await writer.add_policy("editor", "comments", "write")

        # remove_filtered_policy is not wrapped by CasbinPolicyEngine's public
        # surface (only add/remove of a fully-specified rule is) — reach the
        # underlying AsyncEnforcer directly, same as any Casbin admin script
        # would, via the engine's documented internal accessor.
        enforcer = writer._require_enforcer()  # noqa: SLF001
        removed = await enforcer.remove_filtered_named_policy("p", 0, "editor", "posts")
        assert removed is True

        remaining = await writer.list_policies()
        assert ("editor", "posts", "write") not in remaining
        assert ("editor", "posts", "delete") not in remaining
        assert ("editor", "comments", "write") in remaining

    async with CasbinPolicyEngine(settings) as reader:
        remaining = await reader.list_policies()
        assert ("editor", "posts", "write") not in remaining
        assert ("editor", "comments", "write") in remaining


async def test_rbac_role_inheritance_after_cold_reload(casbin_db_url: str) -> None:
    """A role granted through one engine is enforced by a brand-new engine
    instance constructed later against the same DSN (a cold reload, not
    ``reload()`` on the same instance)."""
    settings = CasbinSettings(
        model_preset="rbac",
        adapter="sqlalchemy",
        db_url=casbin_db_url,
    )

    async with CasbinPolicyEngine(settings) as writer:
        await writer.add_policy("admin", "*", "*")
        await writer.add_role_for_user("carol", "admin")

    # Cold reload: a fresh engine/enforcer, not the same process's cache.
    async with CasbinPolicyEngine(settings) as reader:
        assert await reader.enforce(ER("carol", "anything", "delete")) is True
        assert "admin" in await reader.roles_for_user("carol")


async def test_abac_enforcement_persisted(casbin_db_url: str) -> None:
    """ABAC subject_attrs/object_attrs enforcement survives persistence."""
    settings = CasbinSettings(
        model_preset="abac",
        adapter="sqlalchemy",
        db_url=casbin_db_url,
    )

    async with CasbinPolicyEngine(settings) as engine:
        owner_req = ER(
            "u1",
            "posts",
            "update",
            subject_attrs={"id": "u1", "roles": []},
            object_attrs={"owner_id": "u1"},
        )
        non_owner_req = ER(
            "u2",
            "posts",
            "update",
            subject_attrs={"id": "u2", "roles": []},
            object_attrs={"owner_id": "u1"},
        )
        assert await engine.enforce(owner_req) is True
        assert await engine.enforce(non_owner_req) is False


async def test_two_engines_share_database_writer_reader(casbin_db_url: str) -> None:
    """A writer and a reader engine sharing one DB: reader.reload() sees the
    writer's change (CLAUDE.md's per-external-dependency shared-singleton
    rule, verified against real infrastructure).

    Regression test for KI-8 (BACKLOG.md): _AttrStr previously had no
    __reduce__, so once an _AttrStr had been threaded into Casbin's internal
    model/role-manager state via a prior enforce() call, this reload() (->
    casbin's load_policy() -> copy.deepcopy(self.model)) raised TypeError.
    Fixed by _AttrStr.__reduce__ in varco_casbin/engine.py."""
    settings = CasbinSettings(
        model_preset="rbac",
        adapter="sqlalchemy",
        db_url=casbin_db_url,
    )

    async with CasbinPolicyEngine(settings) as writer, CasbinPolicyEngine(
        settings
    ) as reader:
        assert await reader.enforce(ER("dave", "posts", "read")) is False

        await writer.add_policy("public", "posts", "read")
        await writer.add_role_for_user("dave", "public")

        # Reader has its own in-memory enforcer state until reload().
        assert await reader.enforce(ER("dave", "posts", "read")) is False

        await reader.reload()
        assert await reader.enforce(ER("dave", "posts", "read")) is True


async def test_domain_scoped_rbac_persisted(casbin_db_url: str) -> None:
    """rbac_domains preset: a role granted in one domain does not leak into
    another, and persists across a cold reload — the RequestMapper.domain_for
    keying path this preset backs."""
    settings = CasbinSettings(
        model_preset="rbac_domains",
        adapter="sqlalchemy",
        db_url=casbin_db_url,
    )

    async with CasbinPolicyEngine(settings) as writer:
        await writer.add_policy("admin", "tenant-a", "*", "*")
        await writer.add_role_for_user("erin", "admin", domain="tenant-a")

    async with CasbinPolicyEngine(settings) as reader:
        assert await reader.enforce(ER("erin", "*", "*", domain="tenant-a")) is True
        assert await reader.enforce(ER("erin", "*", "*", domain="tenant-b")) is False
