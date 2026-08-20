"""
Red-mode tests for Plan 011 Phase 0 — ``varco_core.context.ambient.AmbientVar``.

These tests encode the plan's step 2 requirements: nested scope() restores
outer value, ascope() works across an await, a value set inside a spawned
asyncio.Task is invisible to the parent (copy-on-spawn semantics), an
exception inside scope() still resets, get() with no value returns the
constructor default, and two same-named AmbientVars are independent.

The whole module (``varco_core.context.ambient``) does not exist yet, so
every test is expected to fail at import/collection time with ImportError.
"""

from __future__ import annotations

import asyncio

import pytest

# Module under test does not exist yet (Plan 011 Phase 0, step 1) — this
# import is expected to raise ImportError, which is the "right" failure
# reason for red-mode.
from varco_core.context.ambient import AmbientVar


def test_get_returns_none_when_no_default_and_never_set() -> None:
    # Baseline: an AmbientVar with no constructor default and no scope active.
    var: AmbientVar[str] = AmbientVar("test.no_default")
    assert var.get() is None


def test_get_returns_constructor_default_when_unset() -> None:
    # The constructor `default=` kwarg must be honoured until a scope sets one.
    var: AmbientVar[str] = AmbientVar("test.with_default", default="fallback")
    assert var.get() == "fallback"


def test_scope_sets_value_for_duration_of_block() -> None:
    var: AmbientVar[str] = AmbientVar("test.scope_basic")
    with var.scope("inner"):
        assert var.get() == "inner"
    assert var.get() is None


def test_nested_scope_restores_outer_value_on_exit() -> None:
    # Core AmbientVar contract: nesting must restore the exact previous value,
    # not just clear to the default — this is what token-based reset gives you
    # over a naive "set on enter, clear on exit" implementation.
    var: AmbientVar[str] = AmbientVar("test.nested")
    with var.scope("outer"):
        assert var.get() == "outer"
        with var.scope("inner"):
            assert var.get() == "inner"
        assert var.get() == "outer"
    assert var.get() is None


def test_scope_resets_even_when_exception_raised_inside_block() -> None:
    # `finally`-based reset must run on the exceptional path too, or a failed
    # request would leak ambient state into whatever runs next on that task.
    var: AmbientVar[str] = AmbientVar("test.exception_reset")
    with pytest.raises(ValueError, match="boom"), var.scope("inner"):
        assert var.get() == "inner"
        raise ValueError("boom")
    assert var.get() is None


async def test_ascope_sets_value_across_an_await() -> None:
    var: AmbientVar[str] = AmbientVar("test.ascope")

    async def read_after_await() -> str | None:
        await asyncio.sleep(0)
        return var.get()

    async with var.ascope("async-inner"):
        assert var.get() == "async-inner"
        assert await read_after_await() == "async-inner"
    assert var.get() is None


async def test_ascope_resets_even_when_exception_raised_inside_block() -> None:
    var: AmbientVar[str] = AmbientVar("test.ascope_exception")
    with pytest.raises(ValueError, match="boom"):
        async with var.ascope("inner"):
            raise ValueError("boom")
    assert var.get() is None


async def test_value_set_in_spawned_task_is_invisible_to_parent() -> None:
    # asyncio.Task copies the context at spawn time (copy-on-spawn) — a value
    # set inside the child task must NOT leak back to the parent. This is
    # explicitly asserted per the plan so nobody "fixes" it later.
    var: AmbientVar[str] = AmbientVar("test.task_isolation")

    async def child() -> None:
        with var.scope("child-value"):
            await asyncio.sleep(0)

    task = asyncio.create_task(child())
    await asyncio.sleep(0)
    # Parent must never observe the child's scoped value.
    assert var.get() is None
    await task
    assert var.get() is None


def test_two_same_named_ambient_vars_are_independent() -> None:
    # Two AmbientVar instances constructed with the identical name string must
    # not share state — each wraps its own ContextVar.
    var_a: AmbientVar[str] = AmbientVar("test.duplicate_name")
    var_b: AmbientVar[str] = AmbientVar("test.duplicate_name")

    with var_a.scope("a-value"):
        assert var_a.get() == "a-value"
        assert var_b.get() is None


def test_set_for_task_returns_token_usable_for_manual_reset() -> None:
    # Explicit Token API for callers that need manual set/reset outside a
    # context-manager shape (e.g. framework middleware wiring).
    var: AmbientVar[str] = AmbientVar("test.manual_token")
    token = var.set_for_task("manual")
    assert var.get() == "manual"
    var.reset(token)
    assert var.get() is None
