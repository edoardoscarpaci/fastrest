"""
tests.test_cache_metrics
==========================
Plan 010 Phase 2, step 19 — ``varco_core.observability.cache``.

Uses OTel SDK's ``InMemoryMetricReader`` (same pattern as
test_reliability_metrics.py) so no broker/exporter is required.

RED until ``varco_core/observability/cache.py`` lands.
"""

from __future__ import annotations

import unittest.mock as mock

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


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


# Deny-list per brief 003 §4 — a cache instrument must never carry a bare
# cache key, tenant id, user id, or correlation id as an attribute.
_FORBIDDEN_ATTR_KEYS = {"key", "cache_key", "tenant_id", "user_id", "correlation_id"}
_ALLOWED_ATTR_KEYS = {"cache", "layer", "operation", "kind", "reason"}


class TestInstallCacheMetrics:
    async def test_record_hit_increments_hits_counter(self, metric_reader) -> None:
        from varco_core.observability.cache import (
            install_cache_metrics,
            record_cache_hit,
        )

        install_cache_metrics()
        record_cache_hit(cache="users", layer="l1", kind="positive")

        points = _collect_points(metric_reader, "varco.cache.hits")
        assert sum(p.value for p in points) == 1

    async def test_record_miss_increments_misses_counter(self, metric_reader) -> None:
        from varco_core.observability.cache import (
            install_cache_metrics,
            record_cache_miss,
        )

        install_cache_metrics()
        record_cache_miss(cache="users", layer="l1")

        points = _collect_points(metric_reader, "varco.cache.misses")
        assert sum(p.value for p in points) == 1

    async def test_attribute_keys_never_contain_forbidden_names(
        self, metric_reader
    ) -> None:
        from varco_core.observability.cache import (
            install_cache_metrics,
            record_cache_hit,
        )

        install_cache_metrics()
        record_cache_hit(cache="users", layer="l1", kind="positive")

        points = _collect_points(metric_reader, "varco.cache.hits")
        for point in points:
            keys = set(point.attributes.keys())
            assert keys.isdisjoint(_FORBIDDEN_ATTR_KEYS)
            assert keys.issubset(_ALLOWED_ATTR_KEYS)

    async def test_disabled_config_records_nothing(self, metric_reader) -> None:
        from varco_core.observability.cache import (
            CacheMetricsConfig,
            install_cache_metrics,
            record_cache_hit,
        )

        install_cache_metrics(config=CacheMetricsConfig(enabled=False))
        record_cache_hit(cache="users", layer="l1", kind="positive")

        points = _collect_points(metric_reader, "varco.cache.hits")
        assert sum(p.value for p in points) == 0

    async def test_install_twice_is_idempotent(self, metric_reader) -> None:
        from varco_core.observability.cache import (
            install_cache_metrics,
            record_cache_hit,
        )

        install_cache_metrics()
        install_cache_metrics()
        record_cache_hit(cache="users", layer="l1", kind="positive")

        points = _collect_points(metric_reader, "varco.cache.hits")
        assert sum(p.value for p in points) == 1

    async def test_raising_instrument_does_not_propagate_out_of_record(
        self, metric_reader
    ) -> None:
        from varco_core.observability.cache import (
            install_cache_metrics,
            record_cache_hit,
        )

        install_cache_metrics()
        with mock.patch(
            "varco_core.observability.cache._CACHE_HITS.add",
            side_effect=RuntimeError("boom"),
        ):
            record_cache_hit(
                cache="users", layer="l1", kind="positive"
            )  # must not raise

    async def test_stampede_suppressed_and_stale_served_and_backplane_counters(
        self, metric_reader
    ) -> None:
        from varco_core.observability.cache import (
            install_cache_metrics,
            record_backplane_dropped,
            record_backplane_published,
            record_backplane_received,
            record_cache_stale_served,
            record_stampede_suppressed,
        )

        install_cache_metrics()
        record_stampede_suppressed(cache="users")
        record_cache_stale_served(cache="users", reason="soft_ttl")
        record_backplane_published(kind="key")
        record_backplane_received(kind="key")
        record_backplane_dropped(reason="publish_failed")

        assert (
            sum(
                p.value
                for p in _collect_points(
                    metric_reader, "varco.cache.stampede_suppressed"
                )
            )
            == 1
        )
        assert (
            sum(
                p.value
                for p in _collect_points(metric_reader, "varco.cache.stale_served")
            )
            == 1
        )
        assert (
            sum(
                p.value
                for p in _collect_points(
                    metric_reader, "varco.cache.backplane.published"
                )
            )
            == 1
        )
        assert (
            sum(
                p.value
                for p in _collect_points(
                    metric_reader, "varco.cache.backplane.received"
                )
            )
            == 1
        )
        assert (
            sum(
                p.value
                for p in _collect_points(metric_reader, "varco.cache.backplane.dropped")
            )
            == 1
        )
