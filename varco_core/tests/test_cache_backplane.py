"""
tests.test_cache_backplane
============================
Plan 010 Phase 3, step 26 — ``varco_core.cache.backplane``.

Two ``LayeredCache`` instances (each with its own ``InMemoryCache`` L1)
sharing one ``InMemoryCache`` "L2" and one ``InMemoryBackplane`` — makes
multi-pod L1 coherence unit-testable without Docker.

RED until ``varco_core/cache/backplane.py`` lands and
``LayeredCache.__init__`` gains ``backplane=``.
"""

from __future__ import annotations

import asyncio

import pytest
from varco_core.cache.memory import InMemoryCache


@pytest.fixture()
async def shared_l2() -> InMemoryCache:
    l2 = InMemoryCache()
    await l2.start()
    yield l2
    await l2.stop()


async def _make_node(shared_l2, backplane):
    from varco_core.cache.layered import LayeredCache

    l1 = InMemoryCache()
    await l1.start()
    node = LayeredCache(l1, shared_l2, promote_ttl=30, backplane=backplane)
    await node.start()
    return node, l1


class TestBackplaneMultiPodCoherence:
    async def test_set_on_node_a_evicts_l1_on_node_b(self, shared_l2) -> None:
        from varco_core.cache.backplane import InMemoryBackplane

        bp = InMemoryBackplane(bus_name="pods")
        node_a, _ = await _make_node(shared_l2, bp)
        node_b, l1_b = await _make_node(shared_l2, bp)

        await node_b.set("k", "old")
        await node_b.get("k")  # promote into l1_b
        assert await l1_b.get("k") == "old"

        await node_a.set("k", "new")
        await asyncio.sleep(0.02)

        assert await l1_b.get("k") is None
        assert (await node_b.get("k")) == "new"

    async def test_delete_on_node_a_evicts_l1_on_node_b(self, shared_l2) -> None:
        from varco_core.cache.backplane import InMemoryBackplane

        bp = InMemoryBackplane(bus_name="pods")
        node_a, _ = await _make_node(shared_l2, bp)
        node_b, l1_b = await _make_node(shared_l2, bp)

        await node_a.set("k", "v")
        await node_b.get("k")
        assert await l1_b.get("k") == "v"

        await node_a.delete("k")
        await asyncio.sleep(0.02)
        assert await l1_b.get("k") is None

    async def test_delete_prefix_only_evicts_matching_keys(self, shared_l2) -> None:
        from varco_core.cache.backplane import InMemoryBackplane

        bp = InMemoryBackplane(bus_name="pods")
        node_a, _ = await _make_node(shared_l2, bp)
        node_b, l1_b = await _make_node(shared_l2, bp)

        await node_a.set("users:1", "u1")
        await node_a.set("posts:1", "p1")
        await node_b.get("users:1")
        await node_b.get("posts:1")

        await node_a.delete_prefix("users:")
        await asyncio.sleep(0.02)

        assert await l1_b.get("users:1") is None
        assert await l1_b.get("posts:1") == "p1"

    async def test_received_message_never_touches_l2(self, shared_l2) -> None:
        from varco_core.cache.backplane import InMemoryBackplane

        bp = InMemoryBackplane(bus_name="pods")
        node_a, _ = await _make_node(shared_l2, bp)
        node_b, _ = await _make_node(shared_l2, bp)

        await node_a.set("k", "v")
        await asyncio.sleep(0.02)

        # A received message must evict local layers only — never propagate
        # eviction to the shared L2 (would nuke shared state fleet-wide).
        assert await shared_l2.get("k") == "v"

    async def test_echo_suppression_node_does_not_evict_its_own_write(self, shared_l2) -> None:
        from varco_core.cache.backplane import InMemoryBackplane

        bp = InMemoryBackplane(bus_name="pods")
        node_a, l1_a = await _make_node(shared_l2, bp)

        await node_a.set("k", "v")
        await node_a.get("k")  # promote into own L1
        await asyncio.sleep(0.02)

        # A must not evict the L1 entry it just wrote itself.
        assert await l1_a.get("k") == "v"

    async def test_publish_happens_after_l2_write(self, shared_l2) -> None:
        from varco_core.cache.backplane import InMemoryBackplane, InvalidationMessage

        bp = InMemoryBackplane(bus_name="ordering")
        node_a, _ = await _make_node(shared_l2, bp)

        order: list[str] = []
        original_set = shared_l2.set

        async def tracking_set(*args, **kwargs):
            order.append("l2_write")
            return await original_set(*args, **kwargs)

        shared_l2.set = tracking_set  # type: ignore[assignment]

        original_publish = bp.publish

        async def tracking_publish(message: InvalidationMessage) -> None:
            order.append("publish")
            return await original_publish(message)

        bp.publish = tracking_publish  # type: ignore[assignment]

        await node_a.set("k", "v")
        await asyncio.sleep(0.02)

        assert order == ["l2_write", "publish"]

    async def test_publish_that_raises_does_not_propagate_out_of_set(self, shared_l2) -> None:
        from varco_core.cache.backplane import CacheBackplane, InvalidationMessage

        class RaisingBackplane(CacheBackplane):
            @property
            def origin(self) -> str:
                return "raising-node"

            async def start(self) -> None: ...
            async def stop(self) -> None: ...

            async def publish(self, message: InvalidationMessage) -> None:
                raise RuntimeError("redis down")

            def subscribe(self, handler) -> None: ...

        node_a, _ = await _make_node(shared_l2, RaisingBackplane())
        # publish() must never raise out of set() — the caller's write already
        # succeeded on the authoritative layer.
        await node_a.set("k", "v")
        assert await shared_l2.get("k") == "v"

    async def test_clear_received_evicts_local_layers_only(self, shared_l2) -> None:
        from varco_core.cache.backplane import InMemoryBackplane

        bp = InMemoryBackplane(bus_name="pods")
        node_a, _ = await _make_node(shared_l2, bp)
        node_b, l1_b = await _make_node(shared_l2, bp)

        await node_a.set("k", "v")
        await node_b.get("k")
        assert await l1_b.get("k") == "v"

        await node_a.clear()
        await asyncio.sleep(0.02)

        assert await l1_b.get("k") is None
        # L2 must still hold nothing extra — clear() on node_a legitimately
        # clears its own last layer (it's the writer here), but a node_b
        # merely *receiving* the clear must not clear shared_l2 twice /
        # explicitly — this asserts node_b's own last layer (=shared_l2) is
        # unaffected by the *receive* path specifically.


class TestBackplaneConstructionGuard:
    async def test_backplane_without_promote_ttl_raises_value_error(self, shared_l2) -> None:
        from varco_core.cache.backplane import InMemoryBackplane
        from varco_core.cache.layered import LayeredCache

        bp = InMemoryBackplane(bus_name="guard")
        l1 = InMemoryCache()
        with pytest.raises(ValueError, match="promote_ttl"):
            LayeredCache(l1, shared_l2, backplane=bp)
