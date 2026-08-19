"""
tests.test_cache_layered_metrics
==================================
Plan 010 Phase 2, step 22 — ``varco_core.cache.layered.LayeredCache`` passes
``layer="l1"``/``"l2"``/... as the bounded layer attribute on hit/miss/
eviction records, gated by ``CacheMetricsConfig.by_layer``.

Covers:
    - A hit served from L1 records ``layer="l1"``; a hit served from L2
      (via read-promote) records ``layer="l2"``.
    - A full miss (key absent from every layer) records one miss per layer
      probed (``l1`` AND ``l2``) — this is what makes a per-layer hit ratio
      derivable.
    - ``CacheMetricsConfig(by_layer=False)`` omits the ``layer`` attribute
      from every hit/miss point.
    - The metrics pack not being installed records nothing at all.
    - A received backplane invalidation records ``varco.cache.evictions``
      with ``reason="backplane"`` for each LOCAL layer evicted — never for
      the authoritative last layer.
"""

from __future__ import annotations

import unittest.mock as mock

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from varco_core.cache.memory import InMemoryCache


@pytest.fixture()
def metric_reader():
    from varco_core.observability.metrics import _instrument_cache

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    _instrument_cache.clear()
    with mock.patch(
        "opentelemetry.metrics._internal.get_meter_provider", return_value=provider
    ):
        yield reader
    _instrument_cache.clear()


@pytest.fixture(autouse=True)
def _reset_cache_metrics_state():
    """
    Reset the module-level enabled/config globals in
    ``varco_core.observability.cache`` before AND after each test — this
    module holds process-global state (see CLAUDE.md's "Be careful with
    global state" instruction for this task), and other suites
    (test_cache_metrics.py etc.) must not observe leakage from here.
    """
    import varco_core.observability.cache as cache_metrics

    prev_enabled = cache_metrics._enabled
    prev_config = cache_metrics._config
    cache_metrics._enabled = False
    cache_metrics._config = cache_metrics.CacheMetricsConfig()
    yield
    cache_metrics._enabled = prev_enabled
    cache_metrics._config = prev_config


def _collect_points(reader: InMemoryMetricReader, metric_name: str) -> list:
    data = reader.get_metrics_data()
    points = []
    if data is None:
        return points
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == metric_name:
                    points.extend(metric.data.data_points)
    return points


async def _make_layered(*layers):
    from varco_core.cache.layered import LayeredCache

    cache = LayeredCache(*layers)
    await cache.start()
    return cache


class TestLayeredCacheHitMissLayerAttribute:
    async def test_hit_served_from_l1_records_layer_l1(self, metric_reader) -> None:
        from varco_core.observability.cache import install_cache_metrics

        install_cache_metrics()
        l1 = InMemoryCache()
        l2 = InMemoryCache()
        await l1.start()
        await l2.start()
        cache = await _make_layered(l1, l2)

        await cache.set("k", "v")
        await cache.get("k")  # served from L1 — no promotion needed

        points = _collect_points(metric_reader, "varco.cache.hits")
        matching = [p for p in points if p.attributes.get("layer") == "l1"]
        assert sum(p.value for p in matching) >= 1

    async def test_hit_served_from_l2_records_layer_l2(self, metric_reader) -> None:
        from varco_core.observability.cache import install_cache_metrics

        install_cache_metrics()
        l1 = InMemoryCache()
        l2 = InMemoryCache()
        await l1.start()
        await l2.start()
        cache = await _make_layered(l1, l2)

        # Write directly to L2 only, bypassing L1, so the get() is an L1
        # miss / L2 hit.
        await l2.set("k", "v")

        await cache.get("k")

        points = _collect_points(metric_reader, "varco.cache.hits")
        matching = [p for p in points if p.attributes.get("layer") == "l2"]
        assert sum(p.value for p in matching) >= 1

    async def test_full_miss_records_miss_per_layer_probed(self, metric_reader) -> None:
        """
        Choice: a full miss (key absent from every layer) records ONE miss
        per layer probed (l1 AND l2) — this is what makes per-layer hit
        ratio derivable (hits_l1 / (hits_l1 + misses_l1)), per the task
        brief's recommendation.
        """
        from varco_core.observability.cache import install_cache_metrics

        install_cache_metrics()
        l1 = InMemoryCache()
        l2 = InMemoryCache()
        await l1.start()
        await l2.start()
        cache = await _make_layered(l1, l2)

        result = await cache.get("absent")
        assert result is None

        points = _collect_points(metric_reader, "varco.cache.misses")
        layers_seen = {p.attributes.get("layer") for p in points}
        assert "l1" in layers_seen
        assert "l2" in layers_seen

    async def test_by_layer_false_omits_layer_attribute(self, metric_reader) -> None:
        from varco_core.observability.cache import (
            CacheMetricsConfig,
            install_cache_metrics,
        )

        install_cache_metrics(config=CacheMetricsConfig(by_layer=False))
        l1 = InMemoryCache()
        l2 = InMemoryCache()
        await l1.start()
        await l2.start()
        cache = await _make_layered(l1, l2)

        await cache.set("k", "v")
        await cache.get("k")
        await cache.get("absent")

        hit_points = _collect_points(metric_reader, "varco.cache.hits")
        miss_points = _collect_points(metric_reader, "varco.cache.misses")
        assert len(hit_points) >= 1
        assert len(miss_points) >= 1
        for p in hit_points + miss_points:
            assert "layer" not in p.attributes

    async def test_metrics_pack_not_installed_records_nothing(
        self, metric_reader
    ) -> None:
        # Pack never installed — _enabled defaults False (see the autouse
        # fixture above, which forces it back to False before this test).
        l1 = InMemoryCache()
        l2 = InMemoryCache()
        await l1.start()
        await l2.start()
        cache = await _make_layered(l1, l2)

        await cache.set("k", "v")
        await cache.get("k")
        await cache.get("absent")

        assert _collect_points(metric_reader, "varco.cache.hits") == []
        assert _collect_points(metric_reader, "varco.cache.misses") == []


class TestLayeredCacheBackplaneEvictionMetric:
    async def test_backplane_receive_records_eviction_backplane_reason(
        self, metric_reader
    ) -> None:
        from varco_core.cache.backplane import InMemoryBackplane
        from varco_core.cache.layered import LayeredCache
        from varco_core.observability.cache import install_cache_metrics

        install_cache_metrics()

        shared_l2 = InMemoryCache()
        await shared_l2.start()
        bp = InMemoryBackplane(bus_name="layered-metrics-pods")

        l1_a = InMemoryCache()
        await l1_a.start()
        node_a = LayeredCache(l1_a, shared_l2, promote_ttl=30, backplane=bp)
        await node_a.start()

        l1_b = InMemoryCache()
        await l1_b.start()
        node_b = LayeredCache(l1_b, shared_l2, promote_ttl=30, backplane=bp)
        await node_b.start()

        # node_b promotes into its own L1 first.
        await node_b.set("k", "old")
        await node_b.get("k")
        assert await l1_b.get("k") == "old"

        # node_a's write publishes an invalidation node_b receives and must
        # record as an eviction with reason="backplane" on its LOCAL layer
        # (l1), never on the authoritative last layer (l2).
        await node_a.set("k", "new")

        import asyncio

        await asyncio.sleep(0.02)

        points = _collect_points(metric_reader, "varco.cache.evictions")
        backplane_points = [
            p for p in points if p.attributes.get("reason") == "backplane"
        ]
        assert len(backplane_points) >= 1
        for p in backplane_points:
            # never the last (authoritative) layer
            assert p.attributes.get("layer") != "l2"
