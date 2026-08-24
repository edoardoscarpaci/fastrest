"""
Integration tests for varco_redis.dlq
=======================================
These tests spin up a real Redis instance via testcontainers and verify
end-to-end push/pop/ack/count behaviour of ``RedisDLQ``.

DISABLED BY DEFAULT — requires Docker.  Run with::

    pytest -m integration tests/test_redis_dlq_integration.py

Or set the ``VARCO_RUN_INTEGRATION`` env var::

    VARCO_RUN_INTEGRATION=1 pytest tests/test_redis_dlq_integration.py

Prerequisites:
    - Docker daemon running
    - testcontainers[redis] installed (see pyproject.toml dev dependencies)
"""

from __future__ import annotations

import os
import uuid

import pytest

from varco_core.event import Event
from varco_core.event.dlq import DeadLetterEntry

pytestmark = pytest.mark.integration

if not os.environ.get("VARCO_RUN_INTEGRATION"):
    pytest.skip(
        "Integration tests disabled — set VARCO_RUN_INTEGRATION=1 or use -m integration",
        allow_module_level=True,
    )


# ── Test event types ────────────────────────────────────────────────────────────


class OrderPlacedEvent(Event):
    __event_type__ = "test.order.placed.redis_dlq_integration"
    order_id: str = "ord-1"


# ── Fixtures ───────────────────────────────────────────────────────────────────


# redis_container (module-scoped) was replaced by the session-scoped
# redis_url fixture in tests/conftest.py (Plan 012 / RT1, Step 6).


@pytest.fixture
async def dlq(redis_url: str):
    """Connected ``RedisDLQ`` backed by the shared session-scoped Redis
    container."""
    from varco_redis.config import RedisEventBusSettings
    from varco_redis.dlq import RedisDLQ

    # Use a unique key prefix per test run to avoid cross-test interference.
    prefix = f"test:{uuid.uuid4().hex[:8]}:"

    settings = RedisEventBusSettings(url=redis_url, channel_prefix=prefix)
    async with RedisDLQ(settings) as d:
        yield d


def _make_entry(handler_name: str = "H.handle") -> DeadLetterEntry:
    return DeadLetterEntry(
        event=OrderPlacedEvent(),
        channel="orders",
        handler_name=handler_name,
        error_type="ValueError",
        error_message="integration test error",
        attempts=1,
    )


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestRedisDLQIntegration:
    async def test_push_increases_count(self, dlq) -> None:
        assert await dlq.count() == 0
        await dlq.push(_make_entry())
        assert await dlq.count() == 1

    async def test_push_multiple_increases_count(self, dlq) -> None:
        for _ in range(3):
            await dlq.push(_make_entry())
        assert await dlq.count() == 3

    async def test_pop_batch_returns_pushed_entry(self, dlq) -> None:
        entry = _make_entry("OrderConsumer.on_order")
        await dlq.push(entry)

        result = await dlq.pop_batch(limit=10)
        assert any(e.handler_name == "OrderConsumer.on_order" for e in result)

    async def test_pop_batch_does_not_remove_entries(self, dlq) -> None:
        await dlq.push(_make_entry())
        count_before = await dlq.count()
        await dlq.pop_batch(limit=10)
        count_after = await dlq.count()
        assert count_after == count_before  # entries remain until ack()

    async def test_ack_removes_entry(self, dlq) -> None:
        entry = _make_entry()
        await dlq.push(entry)
        assert await dlq.count() == 1

        await dlq.ack(entry.entry_id)
        assert await dlq.count() == 0

    async def test_ack_unknown_id_is_noop(self, dlq) -> None:
        await dlq.ack(uuid.uuid4())  # must not raise

    async def test_roundtrip_event_type_preserved(self, dlq) -> None:
        """The event nested in DeadLetterEntry must survive Redis serialization."""
        entry = _make_entry()
        await dlq.push(entry)

        result = await dlq.pop_batch(limit=1)
        assert len(result) == 1
        assert isinstance(result[0].event, OrderPlacedEvent)

    async def test_fifo_order(self, dlq) -> None:
        """Oldest entries (lowest score) must be returned first."""
        import asyncio

        e1 = _make_entry("first")
        await asyncio.sleep(0.01)
        e2 = _make_entry("second")

        await dlq.push(e1)
        await dlq.push(e2)

        result = await dlq.pop_batch(limit=10)
        handler_names = [e.handler_name for e in result]
        assert handler_names.index("first") < handler_names.index("second")


# ════════════════════════════════════════════════════════════════════════════
# Plan 009, Phase 1 (R2 metrics) / Phase 2 (R3 retention) — integration
# ════════════════════════════════════════════════════════════════════════════


class TestRedisDLQRetentionSweepIntegration:
    async def test_chunked_delete_where_sweep_drains_matching_entries(
        self, dlq
    ) -> None:
        """Loop delete_where(..., limit=chunk) until 0 -- the chunked-sweep
        recipe from technical_docs/features/job-scheduling-and-leases.md's
        retention pitfall."""
        import asyncio
        from datetime import datetime, timedelta, timezone

        for _ in range(5):
            await dlq.push(_make_entry())

        cutoff = datetime.now(timezone.utc) + timedelta(seconds=1)
        await asyncio.sleep(1.1)

        total_deleted = 0
        while True:
            deleted = await dlq.delete_where(older_than=cutoff, limit=2)
            total_deleted += deleted
            if deleted == 0:
                break

        assert total_deleted == 5
        assert await dlq.count() == 0


class TestRedisDLQDepthGaugeIntegration:
    async def test_depth_gauge_observes_real_redis_count(self, dlq) -> None:
        """
        The ``varco.dlq.depth`` gauge must report the real Redis entry count
        (Plan 009, RD-3 — "depth gauge against a real Redis").

        Collection is driven from a worker thread via ``asyncio.to_thread``
        rather than inline, because that is the production topology: OTel's
        ``PeriodicExportingMetricReader`` collects on its own thread while the
        application's event loop — which owns the ``RedisDLQ``'s async client —
        keeps running.  Collecting inline would instead block the very loop the
        gauge callback needs, which no synchronous callback can ever do.
        """
        import asyncio
        from unittest import mock

        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader
        from varco_core.observability.reliability import install_reliability_metrics

        await dlq.push(_make_entry())
        await dlq.push(_make_entry())

        reader = InMemoryMetricReader()
        provider = MeterProvider(metric_readers=[reader])
        with mock.patch(
            "opentelemetry.metrics._internal.get_meter_provider", return_value=provider
        ):
            install_reliability_metrics(dlq=dlq, dlq_name="redis-integration")
            data = await asyncio.to_thread(reader.get_metrics_data)

        assert data is not None, (
            "no instrument produced data — the depth gauge emitted zero "
            "observations, i.e. count() failed inside the callback"
        )
        points = []
        for rm in data.resource_metrics:
            for sm in rm.scope_metrics:
                for metric in sm.metrics:
                    if metric.name == "varco.dlq.depth":
                        points.extend(metric.data.data_points)
        assert any(p.value == 2 for p in points), points
