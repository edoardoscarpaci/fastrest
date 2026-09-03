"""
Tests for ``varco_core.reload.ReloadableResource[T]`` (Plan 025 / T2, Step 11).

RED phase: ``varco_core.reload`` does not exist yet.
"""

from __future__ import annotations

import asyncio

import pytest
from varco_core.reload import ReloadableResource, ReloadOutcome, ResourceNotLoadedError


async def test_current_before_start_raises_resource_not_loaded_error() -> None:
    resource = ReloadableResource(loader=lambda: "value")

    with pytest.raises(ResourceNotLoadedError):
        _ = resource.current


async def test_first_load_failure_propagates_out_of_start() -> None:
    def failing_loader() -> str:
        raise RuntimeError("boom")

    resource = ReloadableResource(loader=failing_loader)

    with pytest.raises(RuntimeError, match="boom"):
        await resource.start()


async def test_post_startup_loader_failure_keeps_last_good_value() -> None:
    calls = {"n": 0}

    def loader() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return "good"
        raise RuntimeError("truncated file")

    resource = ReloadableResource(loader=loader)
    await resource.start()
    assert resource.current == "good"
    gen_before = resource.generation

    outcome = await resource.reload()

    assert resource.current == "good"
    assert resource.generation == gen_before
    assert outcome.changed is False
    assert isinstance(outcome.error, RuntimeError)


async def test_successful_reload_bumps_generation_once_and_notifies_subscribers() -> None:
    values = iter(["v1", "v2"])
    resource = ReloadableResource(loader=lambda: next(values))
    await resource.start()

    notified: list[str] = []
    resource.subscribe(notified.append)

    outcome = await resource.reload()

    assert outcome.changed is True
    assert outcome.generation == resource.generation
    assert resource.generation == 2  # 1 from start() + 1 from reload()
    assert notified == ["v2"]


async def test_raising_subscriber_does_not_prevent_others() -> None:
    values = iter(["v1", "v2"])
    resource = ReloadableResource(loader=lambda: next(values))
    await resource.start()

    good_notified: list[str] = []

    def bad_sub(_v: str) -> None:
        raise RuntimeError("bad subscriber")

    resource.subscribe(bad_sub)
    resource.subscribe(good_notified.append)

    await resource.reload()

    assert good_notified == ["v2"]


async def test_unchanged_reload_value_still_counts_as_a_swap() -> None:
    # Edge case: loader returning an equal-but-not-identical value still bumps generation.
    resource = ReloadableResource(loader=lambda: "same")
    await resource.start()
    gen_after_start = resource.generation

    outcome = await resource.reload()

    assert outcome.changed is True
    assert resource.generation == gen_after_start + 1


async def test_subscribe_returns_unsubscribe_callable() -> None:
    values = iter(["v1", "v2", "v3"])
    resource = ReloadableResource(loader=lambda: next(values))
    await resource.start()

    notified: list[str] = []
    unsubscribe = resource.subscribe(notified.append)
    await resource.reload()
    unsubscribe()
    await resource.reload()

    assert notified == ["v2"]


async def test_subscriber_calling_reload_reentrantly_does_not_deadlock() -> None:
    values = iter(["v1", "v2", "v3"])
    resource = ReloadableResource(loader=lambda: next(values))
    await resource.start()

    reentered = {"done": False}

    def reentrant_sub(_v: str) -> None:
        if not reentered["done"]:
            reentered["done"] = True
            asyncio.get_event_loop().create_task(resource.reload())

    resource.subscribe(reentrant_sub)

    await asyncio.wait_for(resource.reload(), timeout=2.0)
    await asyncio.sleep(0.1)  # let the reentrant task run
    assert reentered["done"] is True


async def test_sync_loader_runs_via_to_thread() -> None:
    resource = ReloadableResource(loader=lambda: "sync-value")
    await resource.start()
    assert resource.current == "sync-value"


async def test_async_loader_is_awaited_directly() -> None:
    async def async_loader() -> str:
        return "async-value"

    resource = ReloadableResource(loader=async_loader)
    await resource.start()
    assert resource.current == "async-value"


async def test_reload_outcome_is_frozen_dataclass_with_expected_fields() -> None:
    resource = ReloadableResource(loader=lambda: "v")
    await resource.start()
    outcome = await resource.reload()

    assert isinstance(outcome, ReloadOutcome)
    with pytest.raises(Exception):
        outcome.changed = False  # type: ignore[misc]  # frozen dataclass must reject mutation
