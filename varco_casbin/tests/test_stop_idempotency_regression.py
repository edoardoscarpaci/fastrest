"""
Plan 022 / Phase 4 (RL-8a), Step 21 — repurposed as regression tests.

See ``design/api-freeze-and-standards/measurements/predestroy-vs-lifespan.md``
Part 2: all ten ``stop()`` implementations were read and found idempotent, and
the measurement asks for a test before §D-8a2(b) makes the property
load-bearing (``_stop_all()`` then ``container.ashutdown()`` = two ``stop()``
calls for any component on both paths).

``CasbinPolicyEngine`` is table row #10 — a confirmed orphan whose leak is
nominal (its ``stop()`` only nulls two attributes), which is precisely why it
needs a guard: there is no sentinel short-circuit, only unconditional
assignment, and that is easy to lose in a refactor.  The default ``memory``
adapter performs no I/O, so start→stop→stop runs without Docker.

NOTE: may legitimately pass on arrival — a regression guard, not a red test.

Thread safety:  N/A (unit test)
Async safety:   ✅ every stop() is awaited.
"""

from __future__ import annotations

from varco_casbin.config import CasbinSettings
from varco_casbin.engine import CasbinPolicyEngine


async def test_stop_twice_on_never_started_engine_is_a_noop() -> None:
    # A container sweep can reach a singleton the lifespan never started.
    engine = CasbinPolicyEngine(CasbinSettings())

    await engine.stop()
    await engine.stop()  # must not raise


async def test_stop_twice_after_start_is_a_noop() -> None:
    # The §D-8a2(b) double-stop path itself.
    engine = CasbinPolicyEngine(CasbinSettings())
    await engine.start()

    await engine.stop()
    await engine.stop()  # must not raise


async def test_second_stop_leaves_enforcer_and_adapter_cleared() -> None:
    # engine.py:221-222 — idempotent by construction; pin it as the contract.
    engine = CasbinPolicyEngine(CasbinSettings())
    await engine.start()

    await engine.stop()
    await engine.stop()

    assert engine._enforcer is None  # noqa: SLF001
    assert engine._adapter is None  # noqa: SLF001
