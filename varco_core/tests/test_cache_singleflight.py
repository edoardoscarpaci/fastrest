"""
tests.test_cache_singleflight
===============================
Plan 010 Phase 0, step 6 — ``varco_core.cache.singleflight.Singleflight``.

RED until ``varco_core/cache/singleflight.py`` lands.
"""

from __future__ import annotations

import asyncio
import gc

import pytest


class TestSingleflightCoalescing:
    async def test_concurrent_calls_on_one_key_invoke_loader_once(self) -> None:
        from varco_core.cache.singleflight import Singleflight

        sf = Singleflight()
        calls = 0

        async def loader() -> int:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            return 42

        results = await asyncio.gather(*[sf.do("k", loader) for _ in range(50)])

        assert calls == 1
        assert all(value == 42 for value, _ in results)

    async def test_different_keys_do_not_serialize(self) -> None:
        from varco_core.cache.singleflight import Singleflight

        sf = Singleflight()
        calls: dict[str, int] = {"a": 0, "b": 0}

        async def loader_a() -> str:
            calls["a"] += 1
            await asyncio.sleep(0.02)
            return "a-val"

        async def loader_b() -> str:
            calls["b"] += 1
            await asyncio.sleep(0.02)
            return "b-val"

        (val_a, _), (val_b, _) = await asyncio.gather(sf.do("a", loader_a), sf.do("b", loader_b))

        assert calls == {"a": 1, "b": 1}
        assert val_a == "a-val"
        assert val_b == "b-val"

    async def test_loader_raises_every_waiter_sees_same_exception(self) -> None:
        from varco_core.cache.singleflight import Singleflight

        sf = Singleflight()

        async def failing_loader() -> None:
            await asyncio.sleep(0.02)
            raise RuntimeError("boom")

        results = await asyncio.gather(
            *[sf.do("k", failing_loader) for _ in range(5)], return_exceptions=True
        )
        assert all(isinstance(r, RuntimeError) for r in results)

    async def test_loader_raises_slot_cleared_next_call_reruns_loader(self) -> None:
        from varco_core.cache.singleflight import Singleflight

        sf = Singleflight()
        calls = 0

        async def loader() -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("boom")
            return 7

        with pytest.raises(RuntimeError):
            await sf.do("k", loader)

        value, is_leader = await sf.do("k", loader)
        assert value == 7
        assert calls == 2

    async def test_follower_cancelled_mid_wait_does_not_cancel_leader(self) -> None:
        from varco_core.cache.singleflight import Singleflight

        sf = Singleflight()
        leader_finished = asyncio.Event()

        async def loader() -> int:
            await asyncio.sleep(0.1)
            leader_finished.set()
            return 99

        leader_task = asyncio.create_task(sf.do("k", loader))
        await asyncio.sleep(0.01)
        follower_task = asyncio.create_task(sf.do("k", loader))
        await asyncio.sleep(0.01)

        follower_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await follower_task

        # The leader must be unaffected by the follower's own cancellation —
        # this is the single subtlest correctness point in C2 (asyncio.shield).
        value, _ = await leader_task
        assert value == 99
        assert leader_finished.is_set()

    async def test_leader_cancelled_followers_see_cancelled_error(self) -> None:
        from varco_core.cache.singleflight import Singleflight

        sf = Singleflight()

        async def loader() -> int:
            await asyncio.sleep(1.0)
            return 1

        leader_task = asyncio.create_task(sf.do("k", loader))
        await asyncio.sleep(0.01)
        follower_task = asyncio.create_task(sf.do("k", loader))
        await asyncio.sleep(0.01)

        leader_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await leader_task
        with pytest.raises(asyncio.CancelledError):
            await follower_task

        # Slot must be cleared — next call re-elects a leader instead of
        # awaiting a dead future forever.
        async def fresh_loader() -> int:
            return 5

        value, _ = await sf.do("k", fresh_loader)
        assert value == 5

    async def test_spawn_refresh_task_survives_gc_and_is_drained_by_aclose(
        self,
    ) -> None:
        from varco_core.cache.singleflight import Singleflight

        sf = Singleflight()
        completed = asyncio.Event()

        async def refresh_loader() -> int:
            await asyncio.sleep(0.05)
            completed.set()
            return 1

        sf.spawn_refresh("k", refresh_loader)
        # Force a collection cycle — an untracked create_task() result can be
        # garbage-collected mid-flight; a strongly-referenced one survives.
        gc.collect()
        await asyncio.sleep(0.1)
        assert completed.is_set()

        await sf.aclose()  # must not raise, drains any outstanding refreshes

    async def test_in_flight_property_reflects_active_slots(self) -> None:
        from varco_core.cache.singleflight import Singleflight

        sf = Singleflight()
        assert sf.in_flight == 0

        started = asyncio.Event()
        release = asyncio.Event()

        async def loader() -> int:
            started.set()
            await release.wait()
            return 1

        task = asyncio.create_task(sf.do("k", loader))
        await started.wait()
        assert sf.in_flight == 1

        release.set()
        await task
        assert sf.in_flight == 0
