"""
Concurrent-writer coverage for ``adapter="sqlalchemy"`` on real Postgres
(Plan 018 / RT3, Steps 13-14).

Seven real-Postgres persistence tests already exist
(``test_persistence_integration.py``); the residual gap RT3 actually names
is **concurrent writers**. CLAUDE.md's Casbin authorization rules recommend
``adapter="sqlalchemy"`` over ``adapter="file"`` precisely because the
latter *"is durable but single-process only (concurrent writers can corrupt
the CSV)"* — yet nothing verified that the recommended adapter survives
concurrency. These three tests do.

Why ``adapter="file"`` is deliberately NOT tested here (§RT3-scope)
-------------------------------------------------------------------
CLAUDE.md already documents the file adapter as unsafe under concurrency.
A test written against it would have to assert *corruption*, and corruption
from a write race is by definition non-deterministic: the test would either
flake (the race sometimes does not fire) or, worse, would codify a
particular broken interleaving as the expected contract — making the bug
harder to fix rather than easier. Documentation is the correct artifact for
"do not do this"; a test is the correct artifact for "this must hold". The
omission is a decision, not an oversight.

Fixture: the function-scoped ``casbin_db_url`` (``tests/conftest.py:66``),
so every test owns its own freshly-created Postgres **database** — the
SQLAlchemy Casbin adapter has no per-instance table-name override, so
isolation has to happen at the database level.
"""

from __future__ import annotations

import asyncio

import pytest
from varco_casbin.config import CasbinSettings
from varco_casbin.engine import CasbinPolicyEngine
from varco_core.auth.policy import EnforcementRequest as ER

pytestmark = pytest.mark.integration

_N = 10
"""Rules written per engine. Small enough to stay fast, large enough that a
last-writer-wins full-policy rewrite (the failure mode §RT3-scope's Risk
names) loses rules visibly rather than by luck."""


def _settings(db_url: str, preset: str = "rbac") -> CasbinSettings:
    return CasbinSettings(model_preset=preset, adapter="sqlalchemy", db_url=db_url)


async def test_concurrent_add_policy_from_two_engines_all_persist(casbin_db_url: str) -> None:
    """
    Two engines over one database concurrently adding N rules each: a third,
    cold-loaded engine must see all 2N.

    A durable adapter has to serialise these as row-level inserts. If it
    instead rewrites the whole policy set per call (last-writer-wins), the
    two engines clobber each other and the cold reload is short — which is a
    🔴 finding about CLAUDE.md's own ``adapter="sqlalchemy"`` recommendation,
    not a test to weaken (§RT3-scope Risk).
    """
    settings = _settings(casbin_db_url)

    async with (
        CasbinPolicyEngine(settings) as engine_a,
        CasbinPolicyEngine(settings) as engine_b,
    ):
        await asyncio.gather(
            *(engine_a.add_policy(f"role-a-{i}", "posts", "read") for i in range(_N)),
            *(engine_b.add_policy(f"role-b-{i}", "posts", "read") for i in range(_N)),
        )

    async with CasbinPolicyEngine(settings) as reader:
        persisted = set(await reader.list_policies())

    expected = {(f"role-a-{i}", "posts", "read") for i in range(_N)} | {
        (f"role-b-{i}", "posts", "read") for i in range(_N)
    }
    missing = expected - persisted
    assert not missing, (
        f"{len(missing)} of {2 * _N} concurrently-written rules were lost by the "
        f"sqlalchemy adapter: {sorted(missing)}"
    )


async def test_concurrent_add_and_remove_converge(casbin_db_url: str) -> None:
    """
    Interleaved concurrent adds and removes must leave a self-consistent
    final state after a cold reload: no duplicate rows, and no rule that was
    removed still present.

    Edge cases:
        - The removes target rules seeded *before* the concurrent phase, so
          the expected end state is deterministic regardless of interleaving
          — this is a convergence assertion, not a race-outcome assertion.
    """
    settings = _settings(casbin_db_url)

    # Seed: 2N rules, of which the first N will be concurrently removed.
    async with CasbinPolicyEngine(settings) as seeder:
        for i in range(2 * _N):
            await seeder.add_policy(f"seed-{i}", "posts", "read")

    async with (
        CasbinPolicyEngine(settings) as remover,
        CasbinPolicyEngine(settings) as adder,
    ):
        await asyncio.gather(
            *(remover.remove_policy(f"seed-{i}", "posts", "read") for i in range(_N)),
            *(adder.add_policy(f"new-{i}", "posts", "write") for i in range(_N)),
        )

    async with CasbinPolicyEngine(settings) as reader:
        persisted = await reader.list_policies()

    assert len(persisted) == len(set(persisted)), (
        f"duplicate policy rows after concurrent add/remove: {persisted}"
    )
    for i in range(_N):
        assert (f"seed-{i}", "posts", "read") not in persisted, (
            f"phantom rule: seed-{i} was removed but survived the cold reload"
        )
    for i in range(_N, 2 * _N):
        assert (f"seed-{i}", "posts", "read") in persisted, (
            f"untouched rule seed-{i} was lost by a concurrent writer"
        )
    for i in range(_N):
        assert (f"new-{i}", "posts", "write") in persisted, (
            f"concurrently-added rule new-{i} was lost"
        )


async def test_concurrent_writers_do_not_corrupt_rbac_inheritance(casbin_db_url: str) -> None:
    """
    Two engines concurrently writing ``g`` grouping rules: inherited
    permissions must still enforce correctly on a cold-loaded engine.

    Grouping rules are the shape most sensitive to a partial write — a lost
    ``g`` row does not surface as a missing policy, it surfaces as an
    *authorization decision silently flipping to deny* (or, if the wrong row
    survives, to allow). Enforcement is therefore the assertion, not row
    presence.
    """
    settings = _settings(casbin_db_url)

    async with CasbinPolicyEngine(settings) as seeder:
        await seeder.add_policy("admin", "posts", "delete")

    async with (
        CasbinPolicyEngine(settings) as engine_a,
        CasbinPolicyEngine(settings) as engine_b,
    ):
        await asyncio.gather(
            *(engine_a.add_role_for_user(f"user-a-{i}", "admin") for i in range(_N)),
            *(engine_b.add_role_for_user(f"user-b-{i}", "admin") for i in range(_N)),
        )

    async with CasbinPolicyEngine(settings) as reader:
        for i in range(_N):
            assert await reader.enforce(ER(f"user-a-{i}", "posts", "delete")) is True, (
                f"user-a-{i}'s inherited admin grant was lost by a concurrent g-rule write"
            )
            assert await reader.enforce(ER(f"user-b-{i}", "posts", "delete")) is True, (
                f"user-b-{i}'s inherited admin grant was lost by a concurrent g-rule write"
            )
        # Negative control: inheritance was not corrupted into a blanket allow.
        assert await reader.enforce(ER("stranger", "posts", "delete")) is False, (
            "concurrent g-rule writes granted a role to a user that was never assigned one"
        )
