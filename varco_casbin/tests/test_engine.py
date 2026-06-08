"""
Unit tests for varco_casbin.engine.CasbinPolicyEngine
=====================================================
Covers enforcement and management across every shipped model preset
(ACL, RBAC, RBAC-with-domains, ABAC) plus the SQLAlchemy persistence
round-trip via aiosqlite (no Docker required).

All async (auto mode); the in-memory adapter keeps tests fast and isolated.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from varco_casbin.config import CasbinSettings
from varco_casbin.engine import CasbinPolicyEngine, _AttrStr
from varco_core.auth.policy import EnforcementRequest as ER


# ── _AttrStr behaves as both string and attribute holder ──────────────────────


def test_attrstr_is_string_and_carries_attrs() -> None:
    """_AttrStr is a real str (for RBAC/ACL) AND exposes ABAC attributes."""
    s = _AttrStr("alice", {"id": "alice", "roles": ["admin"]})
    assert s == "alice"  # string identity for g()/== matchers
    assert s.id == "alice"  # attribute access for ABAC matchers
    assert "admin" in s.roles


# ── RBAC ──────────────────────────────────────────────────────────────────────


async def test_rbac_role_grants_access() -> None:
    """A user with an admin role inherits the admin's wildcard permission."""
    async with CasbinPolicyEngine(CasbinSettings(model_preset="rbac")) as e:
        await e.add_role_for_user("alice", "admin")
        await e.add_policy("admin", "*", "*")
        assert await e.enforce(ER("alice", "posts", "read")) is True


async def test_rbac_denies_user_without_role() -> None:
    """A user with no matching role/policy is denied (fail-closed)."""
    async with CasbinPolicyEngine(CasbinSettings(model_preset="rbac")) as e:
        await e.add_policy("admin", "*", "*")
        assert await e.enforce(ER("bob", "posts", "read")) is False


async def test_rbac_roles_for_user_and_revoke() -> None:
    """Role assignment is reflected by roles_for_user and reversible."""
    async with CasbinPolicyEngine(CasbinSettings(model_preset="rbac")) as e:
        await e.add_role_for_user("alice", "editor")
        assert await e.roles_for_user("alice") == ["editor"]
        assert await e.remove_role_for_user("alice", "editor") is True
        assert await e.roles_for_user("alice") == []


async def test_add_policy_is_idempotent() -> None:
    """Re-adding an identical rule returns False (no duplicate)."""
    async with CasbinPolicyEngine(CasbinSettings(model_preset="rbac")) as e:
        assert await e.add_policy("admin", "posts", "read") is True
        assert await e.add_policy("admin", "posts", "read") is False


async def test_list_and_remove_policies() -> None:
    """list_policies returns token tuples; remove_policy deletes them."""
    async with CasbinPolicyEngine(CasbinSettings(model_preset="rbac")) as e:
        await e.add_policy("admin", "posts", "read")
        assert ("admin", "posts", "read") in await e.list_policies()
        assert await e.remove_policy("admin", "posts", "read") is True
        assert await e.list_policies() == []


# ── ACL ───────────────────────────────────────────────────────────────────────


async def test_acl_exact_match_only() -> None:
    """ACL allows only an exact (sub, obj, act) rule; other actions denied."""
    async with CasbinPolicyEngine(CasbinSettings(model_preset="acl")) as e:
        await e.add_policy("alice", "posts", "read")
        assert await e.enforce(ER("alice", "posts", "read")) is True
        assert await e.enforce(ER("alice", "posts", "write")) is False
        assert await e.enforce(ER("bob", "posts", "read")) is False


# ── ABAC (Feature 9) ──────────────────────────────────────────────────────────


async def test_abac_owner_allowed_non_owner_denied_admin_override() -> None:
    """ABAC: owner allowed, non-owner denied, admin role overrides ownership."""
    async with CasbinPolicyEngine(CasbinSettings(model_preset="abac")) as e:
        owner = ER(
            "u1",
            "posts:1",
            "update",
            subject_attrs={"id": "u1", "roles": []},
            object_attrs={"owner_id": "u1"},
        )
        non_owner = ER(
            "u2",
            "posts:1",
            "update",
            subject_attrs={"id": "u2", "roles": []},
            object_attrs={"owner_id": "u1"},
        )
        admin = ER(
            "u3",
            "posts:1",
            "update",
            subject_attrs={"id": "u3", "roles": ["admin"]},
            object_attrs={"owner_id": "u1"},
        )
        assert await e.enforce(owner) is True
        assert await e.enforce(non_owner) is False
        assert await e.enforce(admin) is True


# ── RBAC with domains ─────────────────────────────────────────────────────────


async def test_rbac_domains_scopes_roles_per_tenant() -> None:
    """A role granted in one domain does not leak into another."""
    async with CasbinPolicyEngine(CasbinSettings(model_preset="rbac_domains")) as e:
        await e.add_role_for_user("alice", "admin", domain="t1")
        await e.add_policy("admin", "t1", "posts", "read")
        allowed = ER("alice", "posts", "read", domain="t1")
        wrong_domain = ER("alice", "posts", "read", domain="t2")
        assert await e.enforce(allowed) is True
        assert await e.enforce(wrong_domain) is False
        assert await e.roles_for_user("alice", domain="t1") == ["admin"]


# ── Lifecycle / fail-closed ───────────────────────────────────────────────────


async def test_enforce_before_start_raises() -> None:
    """Using the engine before start() fails closed with a clear error."""
    e = CasbinPolicyEngine(CasbinSettings(model_preset="rbac"))
    with pytest.raises(RuntimeError, match="before start"):
        await e.enforce(ER("a", "b", "read"))


async def test_double_start_raises() -> None:
    """Calling start() twice is a programming error."""
    e = CasbinPolicyEngine(CasbinSettings(model_preset="rbac"))
    await e.start()
    with pytest.raises(RuntimeError, match="twice"):
        await e.start()
    await e.stop()


async def test_reload_is_noop_for_memory_adapter() -> None:
    """reload() on the in-memory adapter does nothing and does not raise."""
    async with CasbinPolicyEngine(CasbinSettings(model_preset="rbac")) as e:
        await e.add_policy("admin", "*", "*")
        await e.reload()  # must not clear in-memory policy or raise
        assert await e.list_policies() == [("admin", "*", "*")]


# ── File adapter ──────────────────────────────────────────────────────────────


async def test_file_adapter_loads_existing_policy() -> None:
    """The file adapter loads a CSV policy from disk at start()."""
    tmp = Path(tempfile.mkdtemp()) / "policy.csv"
    tmp.write_text("p, admin, posts, read\ng, alice, admin\n", encoding="utf-8")
    settings = CasbinSettings(model_preset="rbac", adapter="file", policy_path=str(tmp))
    async with CasbinPolicyEngine(settings) as e:
        assert await e.enforce(ER("alice", "posts", "read")) is True


# ── SQLAlchemy persistence round-trip (aiosqlite, no Docker) ──────────────────


async def test_sqlalchemy_adapter_persists_across_engines() -> None:
    """Policies written through one engine survive into a fresh engine."""
    db = Path(tempfile.mkdtemp()) / "policy.db"
    url = f"sqlite+aiosqlite:///{db}"
    settings = CasbinSettings(model_preset="rbac", adapter="sqlalchemy", db_url=url)

    async with CasbinPolicyEngine(settings) as writer:
        await writer.add_role_for_user("alice", "admin")
        await writer.add_policy("admin", "*", "*")

    # Fresh engine instance, same DB — must load the persisted policy.
    async with CasbinPolicyEngine(settings) as reader:
        assert await reader.enforce(ER("alice", "posts", "read")) is True
        assert ("admin", "*", "*") in await reader.list_policies()
        assert await reader.roles_for_user("alice") == ["admin"]
