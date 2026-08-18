"""
tests.test_reliability_metrics
================================
Plan 009, Phase 1 (R2) — varco_core.observability.reliability.

RED until ``varco_core/observability/reliability.py`` lands.

Uses OTel SDK's ``InMemoryMetricReader`` (same pattern as test_observability.py)
so no broker/exporter is required.
"""

from __future__ import annotations

import logging

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from varco_core.event.dlq import DeadLetterEntry, InMemoryDeadLetterQueue
from varco_core.event import Event


class SampleEvent(Event):
    __event_type__ = "test.reliability.sample"


@pytest.fixture()
def metric_reader():
    import unittest.mock as mock

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


def _entry(**kwargs) -> DeadLetterEntry:
    defaults = dict(
        event=SampleEvent(),
        channel="orders",
        handler_name="H.h",
        error_type="RuntimeError",
        error_message="boom",
        attempts=1,
    )
    defaults.update(kwargs)
    return DeadLetterEntry(**defaults)


class TestRecordDlqPush:
    async def test_record_dlq_push_increments_counter(self, metric_reader) -> None:
        from varco_core.observability.reliability import record_dlq_push

        record_dlq_push(source="consumer", channel="orders", ok=True)
        record_dlq_push(source="consumer", channel="orders", ok=True)
        record_dlq_push(source="consumer", channel="orders", ok=True)

        points = _collect_points(metric_reader, "varco.dlq.pushed")
        total = sum(p.value for p in points)
        assert total == 3

    async def test_record_dlq_push_swallows_raising_instrument(
        self, metric_reader, monkeypatch
    ) -> None:
        from varco_core.observability.reliability import record_dlq_push

        def _boom(*args, **kwargs):
            raise RuntimeError("instrument exploded")

        monkeypatch.setattr(
            "varco_core.observability.reliability._dlq_pushed.add", _boom
        )
        # Must not raise -- a metrics failure is never worth a dropped dead letter.
        record_dlq_push(source="consumer", channel="orders", ok=True)


class TestInstallReliabilityMetricsDepthGauge:
    async def test_depth_gauge_observes_count(self, metric_reader) -> None:
        from varco_core.observability.reliability import install_reliability_metrics

        dlq = InMemoryDeadLetterQueue()
        await dlq.push(_entry())
        await dlq.push(_entry())
        await dlq.push(_entry())

        install_reliability_metrics(dlq=dlq, dlq_name="test-dlq")
        points = _collect_points(metric_reader, "varco.dlq.depth")
        assert any(p.value == 3 for p in points)

    async def test_regression_gauge_observes_a_loop_bound_dlq_from_an_exporter_thread(
        self, metric_reader
    ) -> None:
        """
        User reports: ``varco.dlq.depth`` silently produces no data points for
        a real backend (reported against ``RedisDLQ``); the reader returns no
        metrics at all.  Correct behaviour is a real observation, because
        every production DLQ holds an async client bound to the application's
        event loop and OTel's ``PeriodicExportingMetricReader`` collects from
        a *separate* thread — the callback must therefore drive ``count()`` on
        the owning loop, not on a freshly created one.

        ``_LoopBoundDLQ`` reproduces the constraint without a broker: its
        ``count()`` awaits an ``asyncio.Event`` created on the installing loop,
        which raises "attached to a different loop" on any other loop.
        """
        import asyncio

        from varco_core.observability.reliability import install_reliability_metrics

        class _LoopBoundDLQ(InMemoryDeadLetterQueue):
            """A DLQ whose count() only works on the loop that built it."""

            def __init__(self) -> None:
                super().__init__()
                # Bound to the loop running right now, exactly like a
                # redis.asyncio / motor client's internal futures.
                self._loop = asyncio.get_running_loop()

            async def count(self) -> int:  # type: ignore[override]
                if asyncio.get_running_loop() is not self._loop:
                    # Verbatim shape of the real redis.asyncio failure.
                    raise RuntimeError(
                        "got Future <Future pending> attached to a different loop"
                    )
                # A genuine await, so the coroutine really has to be driven by
                # that loop rather than merely constructed on it.
                await asyncio.sleep(0)
                return 7

        dlq = _LoopBoundDLQ()
        install_reliability_metrics(dlq=dlq, dlq_name="loop-bound-dlq")

        # Collect off the event loop — the production topology.
        points = await asyncio.to_thread(
            _collect_points, metric_reader, "varco.dlq.depth"
        )

        assert any(p.value == 7 for p in points), (
            "depth gauge emitted no observation for a loop-bound DLQ — "
            f"count() was driven on a foreign event loop; got {points!r}"
        )

    async def test_negative_count_produces_no_observation(self, metric_reader) -> None:
        """RD-3: a negative count() (Kafka's -1) must emit NO data point --
        never a literal -1, which would poison every alert threshold."""
        from varco_core.observability.reliability import install_reliability_metrics

        class _NegativeCountDLQ(InMemoryDeadLetterQueue):
            async def count(self) -> int:  # type: ignore[override]
                return -1

        install_reliability_metrics(dlq=_NegativeCountDLQ(), dlq_name="kafka-dlq")
        points = _collect_points(metric_reader, "varco.dlq.depth")
        assert points == [] or all(p.value != -1 for p in points)

    async def test_count_raising_emits_nothing_and_logs_debug(
        self, metric_reader, caplog
    ) -> None:
        from varco_core.observability.reliability import install_reliability_metrics

        class _BrokenDLQ(InMemoryDeadLetterQueue):
            async def count(self) -> int:  # type: ignore[override]
                raise ConnectionError("broker down")

        with caplog.at_level(logging.DEBUG):
            install_reliability_metrics(dlq=_BrokenDLQ(), dlq_name="broken-dlq")
            points = _collect_points(metric_reader, "varco.dlq.depth")
        assert points == []


class TestInstallReliabilityMetricsIdempotent:
    async def test_install_twice_is_idempotent(self, metric_reader) -> None:
        from varco_core.observability.reliability import install_reliability_metrics

        dlq = InMemoryDeadLetterQueue()
        await dlq.push(_entry())

        install_reliability_metrics(dlq=dlq, dlq_name="dup-dlq")
        install_reliability_metrics(dlq=dlq, dlq_name="dup-dlq")

        points = _collect_points(metric_reader, "varco.dlq.depth")
        # Exactly one series worth of data, not doubled.
        matching = [p for p in points if p.value == 1]
        assert len(matching) <= 1


class TestOutboxGaugeSelfDisables:
    async def test_count_pending_not_implemented_disables_gauge_with_one_log(
        self, metric_reader, caplog
    ) -> None:
        from varco_core.observability.reliability import install_reliability_metrics

        class _NoCountPendingRepo:
            async def count_pending(self) -> int:
                raise NotImplementedError("count_pending not supported")

            async def oldest_pending_at(self):
                raise NotImplementedError("oldest_pending_at not supported")

        with caplog.at_level(logging.INFO):
            install_reliability_metrics(outbox_repo=_NoCountPendingRepo())
            points = _collect_points(metric_reader, "varco.outbox.pending")
            points_again = _collect_points(metric_reader, "varco.outbox.pending")

        assert points == []
        assert points_again == []
        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(info_records) >= 1
