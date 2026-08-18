"""
Unit tests for varco_core.observability
=========================================
Covers all public APIs: @span, @counter, @histogram, TracingServiceMixin,
OtelConfig, and OtelConfiguration.

Test strategy
-------------
- Uses OTel SDK's ``InMemorySpanExporter`` and ``InMemoryMetricReader`` so
  tests are fully self-contained with no external collector required.
- Each test class sets up its own ``TracerProvider`` / ``MeterProvider``
  as the OTel global, then restores the previous provider in teardown to
  avoid cross-test pollution.  (The OTel global is process-wide state.)
- ``TracingServiceMixin`` is tested against a minimal ``AsyncService``
  stub so no DB or DI is needed.
- All async tests use the ``pytest-asyncio`` auto mode — no
  ``@pytest.mark.asyncio`` decorator needed.

Sections
--------
- ``SpanConfig``           — construction, defaults, frozen
- ``@span`` (async)        — creates span, sets name, records exception, sets status
- ``@span`` (sync)         — same as async
- ``@span`` bare form      — no-parens usage, name = qualname
- Correlation ID bridge     — correlation_id attribute set from ContextVar
- ``CounterConfig``         — construction, defaults, frozen
- ``HistogramConfig``       — construction, defaults, frozen
- ``@counter`` (async)     — increments on success, NOT on exception
- ``@counter`` (sync)      — same
- ``@histogram`` (async)   — records duration on success AND exception
- ``@histogram`` (sync)    — same
- ``TracingServiceMixin``  — spans created for create/read/update/delete/list
- ``OtelConfig``           — construction, defaults, frozen
- ``OtelConfiguration``    — providers installed, global updated
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from varco_core.observability import (
    CounterConfig,
    HistogramConfig,
    Metric,
    OtelConfig,
    SpanConfig,
    TracingRepositoryMixin,
    TracingServiceMixin,
    counter,
    create_counter,
    create_histogram,
    create_span,
    histogram,
    register_gauge,
    span,
)
from varco_core.observability.metrics import _instrument_cache
from varco_core.tracing import correlation_context


# ── Test fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture()
def span_exporter():
    """
    Patch ``trace.get_tracer_provider`` to return a fresh in-memory provider.

    Newer OTel SDK versions (≥ 1.20) do not allow overriding the global
    TracerProvider once set via ``trace.set_tracer_provider()``.  Patching
    ``get_tracer_provider`` at the module level sidesteps this restriction and
    gives each test a fully isolated provider without touching global state.

    Yields the ``InMemorySpanExporter`` so tests can assert on recorded spans.
    """
    import unittest.mock as mock

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    # Patch in opentelemetry.trace — that is the module where get_tracer is
    # defined and where it looks up get_tracer_provider() at call time.
    with mock.patch("opentelemetry.trace.get_tracer_provider", return_value=provider):
        yield exporter


@pytest.fixture()
def metric_reader():
    """
    Patch ``otel_metrics.get_meter_provider`` to return a fresh in-memory provider.

    Same motivation as ``span_exporter``: avoids the OTel SDK's single-set
    restriction on the global provider.

    Also clears the lazy ``_instrument_cache`` before and after each test so
    instruments from one test don't bleed into the next.
    """
    import unittest.mock as mock

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])

    # Clear before: remove stale instruments bound to a previous provider.
    _instrument_cache.clear()

    # Patch in opentelemetry.metrics._internal — that is where get_meter is
    # defined and where it resolves get_meter_provider() at call time.
    with mock.patch(
        "opentelemetry.metrics._internal.get_meter_provider", return_value=provider
    ):
        yield reader  # noqa: PT022

    # Clear after: next test starts with a clean instrument cache.
    _instrument_cache.clear()


# ── Global-state hygiene (Plan 004 — mandatory autouse reset) ──────────────


@pytest.fixture(autouse=True)
def _reset_observability_globals():
    """
    Reset the process-wide param-capture defaults and global-attribute
    registry around every test in this file.

    Both ``varco_core.observability.params`` and
    ``varco_core.observability.attributes`` hold process-global mutable
    state (Plan 004). Without this reset, a test that flips the capture
    kill switch or registers a global attribute would leak into every other
    test in this file (and, for the attribute registry, into other span/
    metric assertions that don't expect extra attributes).
    """

    def _reset() -> None:
        try:
            from varco_core.observability.params import reset_param_capture_state

            reset_param_capture_state()
        except ImportError:
            pass
        try:
            from varco_core.observability.attributes import clear_global_attributes

            clear_global_attributes()
        except ImportError:
            pass

    _reset()
    yield
    _reset()


# ── SpanConfig ────────────────────────────────────────────────────────────────


class TestSpanConfig:
    def test_defaults(self) -> None:
        cfg = SpanConfig()
        assert cfg.name is None
        assert cfg.tracer_name == "varco"
        assert cfg.attributes == {}
        assert cfg.record_exception is True
        assert cfg.set_status_on_error is True

    def test_custom_values(self) -> None:
        cfg = SpanConfig(
            name="my.span",
            tracer_name="my-svc",
            attributes={"db": "pg"},
            record_exception=False,
            set_status_on_error=False,
        )
        assert cfg.name == "my.span"
        assert cfg.tracer_name == "my-svc"
        assert cfg.attributes == {"db": "pg"}
        assert cfg.record_exception is False
        assert cfg.set_status_on_error is False

    def test_frozen(self) -> None:
        cfg = SpanConfig()
        with pytest.raises(Exception):  # FrozenInstanceError
            cfg.name = "changed"  # type: ignore[misc]


# ── @span — async ─────────────────────────────────────────────────────────────


class TestSpanAsync:
    async def test_creates_span_with_qualname(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        @span
        async def my_function() -> str:
            return "ok"

        result = await my_function()

        assert result == "ok"
        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        # Bare @span — span name is the function's __qualname__
        assert "my_function" in spans[0].name

    async def test_configured_span_name(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        @span(SpanConfig(name="custom.name"))
        async def my_fn() -> None:
            pass

        await my_fn()

        spans = span_exporter.get_finished_spans()
        assert spans[0].name == "custom.name"

    async def test_static_attributes_set(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        @span(SpanConfig(attributes={"db": "postgresql", "env": "test"}))
        async def my_fn() -> None:
            pass

        await my_fn()

        spans = span_exporter.get_finished_spans()
        assert spans[0].attributes["db"] == "postgresql"
        assert spans[0].attributes["env"] == "test"

    async def test_records_exception(self, span_exporter: InMemorySpanExporter) -> None:
        @span(SpanConfig(record_exception=True))
        async def failing_fn() -> None:
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await failing_fn()

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        # Exception recorded → status is ERROR
        assert spans[0].status.status_code == StatusCode.ERROR
        # Exception event recorded on the span
        events = spans[0].events
        assert any("exception" in e.name for e in events)

    async def test_does_not_swallow_exception(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        @span
        async def failing_fn() -> None:
            raise RuntimeError("must propagate")

        with pytest.raises(RuntimeError, match="must propagate"):
            await failing_fn()

    async def test_record_exception_false_omits_exception_event(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        # When record_exception=False, the span must NOT have an exception event.
        # (The SDK may still mark the span ERROR via its own __exit__ logic —
        # that is SDK behaviour and not something SpanConfig controls.)
        @span(SpanConfig(record_exception=False, set_status_on_error=False))
        async def failing_fn() -> None:
            raise ValueError("quiet")

        with pytest.raises(ValueError):
            await failing_fn()

        spans = span_exporter.get_finished_spans()
        # No exception *event* on the span — record_exception=False respected.
        assert not any("exception" in e.name for e in spans[0].events)

    async def test_correlation_id_set_as_attribute(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        @span
        async def my_fn() -> None:
            pass

        async with correlation_context("test-corr-id"):
            await my_fn()

        spans = span_exporter.get_finished_spans()
        assert spans[0].attributes.get("correlation_id") == "test-corr-id"

    async def test_no_correlation_id_attribute_when_unset(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        @span
        async def my_fn() -> None:
            pass

        # No correlation_context — attribute must NOT be present (not an error)
        await my_fn()

        spans = span_exporter.get_finished_spans()
        assert "correlation_id" not in (spans[0].attributes or {})


# ── @span — sync ──────────────────────────────────────────────────────────────


class TestSpanSync:
    def test_creates_span(self, span_exporter: InMemorySpanExporter) -> None:
        @span
        def my_fn() -> str:
            return "sync"

        result = my_fn()

        assert result == "sync"
        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1

    def test_records_exception(self, span_exporter: InMemorySpanExporter) -> None:
        @span
        def failing_fn() -> None:
            raise KeyError("missing")

        with pytest.raises(KeyError):
            failing_fn()

        spans = span_exporter.get_finished_spans()
        assert spans[0].status.status_code == StatusCode.ERROR


# ── CounterConfig ─────────────────────────────────────────────────────────────


class TestCounterConfig:
    def test_defaults(self) -> None:
        cfg = CounterConfig(name="my.counter")
        assert cfg.meter_name == "varco"
        assert cfg.description == ""
        assert cfg.unit == "1"
        assert cfg.attributes == {}

    def test_frozen(self) -> None:
        cfg = CounterConfig(name="x")
        with pytest.raises(Exception):
            cfg.name = "changed"  # type: ignore[misc]


# ── HistogramConfig ───────────────────────────────────────────────────────────


class TestHistogramConfig:
    def test_defaults(self) -> None:
        cfg = HistogramConfig(name="my.histogram")
        assert cfg.unit == "s"
        assert cfg.meter_name == "varco"

    def test_frozen(self) -> None:
        cfg = HistogramConfig(name="x")
        with pytest.raises(Exception):
            cfg.name = "changed"  # type: ignore[misc]


# ── @counter ──────────────────────────────────────────────────────────────────


class TestCounter:
    async def test_increments_on_success_async(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        cfg = CounterConfig(name="test.counter.async")

        @counter(cfg)
        async def my_fn() -> str:
            return "done"

        await my_fn()
        await my_fn()

        # Force a metric collection cycle.
        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        assert any(m.name == "test.counter.async" for m in metrics)
        counter_metric = next(m for m in metrics if m.name == "test.counter.async")
        # Two successful calls → sum = 2
        assert counter_metric.data.data_points[0].value == 2

    def test_increments_on_success_sync(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        cfg = CounterConfig(name="test.counter.sync")

        @counter(cfg)
        def my_fn() -> str:
            return "done"

        my_fn()

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        counter_metric = next(m for m in metrics if m.name == "test.counter.sync")
        assert counter_metric.data.data_points[0].value == 1

    async def test_does_not_increment_on_exception(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        cfg = CounterConfig(name="test.counter.noerr")

        @counter(cfg)
        async def failing_fn() -> None:
            raise ValueError("fail")

        with pytest.raises(ValueError):
            await failing_fn()

        data = metric_reader.get_metrics_data()
        # No resource metrics recorded — counter was never incremented.
        # get_metrics_data() returns None when no instruments have been
        # created (the counter is created lazily on first successful call,
        # so an exception-only test never creates the instrument at all).
        assert data is None or not data.resource_metrics

    async def test_does_not_swallow_exception(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        @counter(CounterConfig(name="test.counter.exc"))
        async def failing() -> None:
            raise RuntimeError("must propagate")

        with pytest.raises(RuntimeError):
            await failing()


# ── @histogram ────────────────────────────────────────────────────────────────


class TestHistogram:
    async def test_records_duration_on_success(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        cfg = HistogramConfig(name="test.histogram.ok")

        @histogram(cfg)
        async def slow_fn() -> str:
            return "done"

        await slow_fn()

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        hist = next(m for m in metrics if m.name == "test.histogram.ok")
        # One call → count == 1
        assert hist.data.data_points[0].count == 1
        # Duration must be non-negative
        assert hist.data.data_points[0].sum >= 0.0

    async def test_records_duration_on_exception(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        cfg = HistogramConfig(name="test.histogram.exc")

        @histogram(cfg)
        async def failing_fn() -> None:
            raise ValueError("boom")

        with pytest.raises(ValueError):
            await failing_fn()

        # Even though the function failed, duration was recorded.
        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        hist = next(m for m in metrics if m.name == "test.histogram.exc")
        assert hist.data.data_points[0].count == 1

    def test_records_duration_sync(self, metric_reader: InMemoryMetricReader) -> None:
        @histogram(HistogramConfig(name="test.histogram.sync"))
        def my_fn() -> None:
            pass

        my_fn()

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        hist = next(m for m in metrics if m.name == "test.histogram.sync")
        assert hist.data.data_points[0].count == 1


# ── TracingServiceMixin ───────────────────────────────────────────────────────


class TestTracingServiceMixin:
    """
    Test that TracingServiceMixin wraps each CRUD method in a span.

    Tests call ``_run_in_span`` directly with async stubs to avoid
    needing a full DB/DI setup while still exercising the span-creation logic.
    """

    def _make_service(self) -> TracingServiceMixin:
        """Build a minimal TracingServiceMixin instance for testing."""
        from varco_core.service.base import AsyncService

        class _StubService(TracingServiceMixin, AsyncService):  # type: ignore[type-arg]
            _tracing_config = SpanConfig()

            def __init__(self) -> None:
                # Skip AsyncService.__init__ — no DI needed in unit tests.
                pass

            def _get_repo(self, uow: Any) -> Any:
                return MagicMock()

        return _StubService()

    async def _run(self, svc: Any, operation: str) -> None:
        """Call _run_in_span with a no-op stub coroutine."""

        async def _noop(*a: Any, **kw: Any) -> Any:
            return None

        await svc._run_in_span(operation, _noop)

    async def test_create_creates_span(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        svc = self._make_service()
        await self._run(svc, "create")

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        assert "create" in spans[0].name

    async def test_read_creates_span(self, span_exporter: InMemorySpanExporter) -> None:
        svc = self._make_service()
        await self._run(svc, "read")

        spans = span_exporter.get_finished_spans()
        assert any("read" in s.name for s in spans)

    async def test_update_creates_span(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        svc = self._make_service()
        await self._run(svc, "update")

        spans = span_exporter.get_finished_spans()
        assert any("update" in s.name for s in spans)

    async def test_delete_creates_span(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        svc = self._make_service()
        await self._run(svc, "delete")

        spans = span_exporter.get_finished_spans()
        assert any("delete" in s.name for s in spans)

    async def test_list_creates_span(self, span_exporter: InMemorySpanExporter) -> None:
        svc = self._make_service()
        await self._run(svc, "list")

        spans = span_exporter.get_finished_spans()
        assert any("list" in s.name for s in spans)

    async def test_custom_tracing_config(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.service.base import AsyncService

        class _Custom(TracingServiceMixin, AsyncService):  # type: ignore[type-arg]
            _tracing_config = SpanConfig(tracer_name="custom-tracer")

            def __init__(self) -> None:
                pass

            def _get_repo(self, uow: Any) -> Any:
                return MagicMock()

        svc = _Custom()

        async def _stub(*a: Any, **kw: Any) -> str:
            return "ok"

        await svc._run_in_span("create", _stub)

        spans = span_exporter.get_finished_spans()
        # Span still created — tracer name is only a grouping label.
        assert len(spans) == 1


# ── TracingRepositoryMixin ────────────────────────────────────────────────────


class TestTracingRepositoryMixin:
    """
    Test that TracingRepositoryMixin wraps each repository method in a span.

    Uses a minimal stub backend that implements all AsyncRepository abstract
    methods as no-ops.  Tests call ``_run_in_span`` directly or exercise the
    full override path to verify span creation without a real DB.
    """

    def _make_repo(self) -> TracingRepositoryMixin:
        """Build a minimal TracingRepositoryMixin instance for testing."""
        from varco_core.repository import AsyncRepository

        class _StubBackend(AsyncRepository):  # type: ignore[type-arg]
            async def find_by_id(self, pk: Any) -> Any:
                return None

            async def find_all(self) -> list:
                return []

            async def save(self, entity: Any) -> Any:
                return entity

            async def delete(self, entity: Any) -> None:
                pass

            async def find_by_query(self, params: Any) -> list:
                return []

            async def count(self, params: Any = None) -> int:
                return 0

            async def exists(self, pk: Any) -> bool:
                return False

            async def save_many(self, entities: Any) -> list:
                return list(entities)

            async def delete_many(self, entities: Any) -> None:
                pass

            async def update_many_by_query(self, params: Any, update: Any) -> int:
                return 0

            async def stream_by_query(self, params: Any):  # type: ignore[override]
                return
                yield  # make this an async generator

        class _TracedRepo(TracingRepositoryMixin, _StubBackend):  # type: ignore[type-arg]
            _tracing_config = SpanConfig()

        return _TracedRepo()

    async def _run(self, repo: Any, operation: str, *args: Any) -> None:
        """Call _run_in_span with a no-op stub coroutine."""

        async def _noop(*a: Any, **kw: Any) -> Any:
            return None

        await repo._run_in_span(operation, _noop, *args)

    # ── Span name tests ───────────────────────────────────────────────────────

    async def test_find_by_id_creates_span(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        repo = self._make_repo()
        await self._run(repo, "find_by_id")
        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        assert "find_by_id" in spans[0].name

    async def test_save_creates_span(self, span_exporter: InMemorySpanExporter) -> None:
        repo = self._make_repo()
        await self._run(repo, "save")
        spans = span_exporter.get_finished_spans()
        assert any("save" in s.name for s in spans)

    async def test_delete_creates_span(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        repo = self._make_repo()
        await self._run(repo, "delete")
        spans = span_exporter.get_finished_spans()
        assert any("delete" in s.name for s in spans)

    async def test_find_by_query_creates_span(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        repo = self._make_repo()
        await self._run(repo, "find_by_query")
        spans = span_exporter.get_finished_spans()
        assert any("find_by_query" in s.name for s in spans)

    async def test_count_creates_span(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        repo = self._make_repo()
        await self._run(repo, "count")
        spans = span_exporter.get_finished_spans()
        assert any("count" in s.name for s in spans)

    async def test_exists_creates_span(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        repo = self._make_repo()
        await self._run(repo, "exists")
        spans = span_exporter.get_finished_spans()
        assert any("exists" in s.name for s in spans)

    async def test_save_many_creates_span(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        repo = self._make_repo()
        await self._run(repo, "save_many")
        spans = span_exporter.get_finished_spans()
        assert any("save_many" in s.name for s in spans)

    async def test_delete_many_creates_span(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        repo = self._make_repo()
        await self._run(repo, "delete_many")
        spans = span_exporter.get_finished_spans()
        assert any("delete_many" in s.name for s in spans)

    async def test_update_many_by_query_creates_span(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        repo = self._make_repo()
        await self._run(repo, "update_many_by_query")
        spans = span_exporter.get_finished_spans()
        assert any("update_many_by_query" in s.name for s in spans)

    # ── Span name includes class name ─────────────────────────────────────────

    async def test_span_name_includes_class_name(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        repo = self._make_repo()
        await self._run(repo, "save")
        spans = span_exporter.get_finished_spans()
        # Span name is "{ClassName}.{operation}"
        assert any("TracedRepo" in s.name or "_TracedRepo" in s.name for s in spans)

    # ── Exception recording ───────────────────────────────────────────────────

    async def test_exception_recorded_on_span(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        repo = self._make_repo()

        async def _raise(*a: Any, **kw: Any) -> None:
            raise ValueError("db error")

        with pytest.raises(ValueError):
            await repo._run_in_span("save", _raise)

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].status.status_code == StatusCode.ERROR

    async def test_exception_re_raised(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        repo = self._make_repo()

        async def _raise(*a: Any, **kw: Any) -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await repo._run_in_span("find_by_id", _raise)

    # ── No exception recording when disabled ──────────────────────────────────

    async def test_exception_not_recorded_when_disabled(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.repository import AsyncRepository

        class _StubBackend(AsyncRepository):  # type: ignore[type-arg]
            async def find_by_id(self, pk: Any) -> Any:
                return None

            async def find_all(self) -> list:
                return []

            async def save(self, entity: Any) -> Any:
                return entity

            async def delete(self, entity: Any) -> None:
                pass

            async def find_by_query(self, params: Any) -> list:
                return []

            async def count(self, params: Any = None) -> int:
                return 0

            async def exists(self, pk: Any) -> bool:
                return False

            async def save_many(self, entities: Any) -> list:
                return []

            async def delete_many(self, entities: Any) -> None:
                pass

            async def update_many_by_query(self, params: Any, update: Any) -> int:
                return 0

            async def stream_by_query(self, params: Any):  # type: ignore[override]
                return
                yield

        class _NoRecord(TracingRepositoryMixin, _StubBackend):  # type: ignore[type-arg]
            _tracing_config = SpanConfig(
                record_exception=False, set_status_on_error=False
            )

        repo = _NoRecord()

        async def _raise(*a: Any, **kw: Any) -> None:
            raise ValueError("silent")

        with pytest.raises(ValueError):
            await repo._run_in_span("save", _raise)

        spans = span_exporter.get_finished_spans()
        assert spans[0].status.status_code != StatusCode.ERROR

    # ── Correlation ID bridge ─────────────────────────────────────────────────

    async def test_correlation_id_set_as_span_attribute(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        repo = self._make_repo()

        async with correlation_context("test-cid-repo-123"):
            await self._run(repo, "save")

        spans = span_exporter.get_finished_spans()
        assert spans[0].attributes.get("correlation_id") == "test-cid-repo-123"

    async def test_no_correlation_id_attribute_when_not_set(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        repo = self._make_repo()
        await self._run(repo, "save")
        spans = span_exporter.get_finished_spans()
        assert "correlation_id" not in (spans[0].attributes or {})

    # ── stream_by_query ───────────────────────────────────────────────────────

    async def test_stream_by_query_creates_span(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        """stream_by_query span is created and closed after full iteration."""
        repo = self._make_repo()

        async for _ in repo.stream_by_query(MagicMock()):
            pass

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        assert "stream_by_query" in spans[0].name

    async def test_stream_by_query_exception_recorded(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        """Exceptions inside the generator body are recorded on the span."""
        from varco_core.repository import AsyncRepository

        class _StubBackend(AsyncRepository):  # type: ignore[type-arg]
            async def find_by_id(self, pk: Any) -> Any:
                return None

            async def find_all(self) -> list:
                return []

            async def save(self, entity: Any) -> Any:
                return entity

            async def delete(self, entity: Any) -> None:
                pass

            async def find_by_query(self, params: Any) -> list:
                return []

            async def count(self, params: Any = None) -> int:
                return 0

            async def exists(self, pk: Any) -> bool:
                return False

            async def save_many(self, entities: Any) -> list:
                return []

            async def delete_many(self, entities: Any) -> None:
                pass

            async def update_many_by_query(self, params: Any, update: Any) -> int:
                return 0

            async def stream_by_query(self, params: Any):  # type: ignore[override]
                yield MagicMock()
                raise RuntimeError("stream error")

        class _TracedRepo(TracingRepositoryMixin, _StubBackend):  # type: ignore[type-arg]
            _tracing_config = SpanConfig()

        repo = _TracedRepo()

        with pytest.raises(RuntimeError, match="stream error"):
            async for _ in repo.stream_by_query(MagicMock()):
                pass

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].status.status_code == StatusCode.ERROR

    # ── Static attributes ─────────────────────────────────────────────────────

    async def test_static_attributes_set_on_span(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.repository import AsyncRepository

        class _StubBackend(AsyncRepository):  # type: ignore[type-arg]
            async def find_by_id(self, pk: Any) -> Any:
                return None

            async def find_all(self) -> list:
                return []

            async def save(self, entity: Any) -> Any:
                return entity

            async def delete(self, entity: Any) -> None:
                pass

            async def find_by_query(self, params: Any) -> list:
                return []

            async def count(self, params: Any = None) -> int:
                return 0

            async def exists(self, pk: Any) -> bool:
                return False

            async def save_many(self, entities: Any) -> list:
                return []

            async def delete_many(self, entities: Any) -> None:
                pass

            async def update_many_by_query(self, params: Any, update: Any) -> int:
                return 0

            async def stream_by_query(self, params: Any):  # type: ignore[override]
                return
                yield

        class _WithAttrs(TracingRepositoryMixin, _StubBackend):  # type: ignore[type-arg]
            _tracing_config = SpanConfig(attributes={"db.system": "postgresql"})

        repo = _WithAttrs()

        async def _noop(*a: Any, **kw: Any) -> Any:
            return None

        await repo._run_in_span("save", _noop)

        spans = span_exporter.get_finished_spans()
        assert spans[0].attributes.get("db.system") == "postgresql"


# ── OtelConfig ────────────────────────────────────────────────────────────────


class TestOtelConfig:
    def test_required_service_name(self) -> None:
        cfg = OtelConfig(service_name="my-svc")
        assert cfg.service_name == "my-svc"

    def test_defaults(self) -> None:
        cfg = OtelConfig(service_name="svc")
        assert cfg.service_version == "0.0.0"
        assert cfg.otlp_endpoint is None
        assert cfg.tracer_name == "varco"
        assert cfg.meter_name == "varco"
        assert cfg.export_interval_ms == 60_000
        assert cfg.extra_resource_attrs == {}

    def test_extra_resource_attrs(self) -> None:
        cfg = OtelConfig(
            service_name="svc",
            extra_resource_attrs={"k8s.pod.name": "pod-abc"},
        )
        assert cfg.extra_resource_attrs["k8s.pod.name"] == "pod-abc"

    def test_frozen(self) -> None:
        cfg = OtelConfig(service_name="svc")
        with pytest.raises(Exception):
            cfg.service_name = "changed"  # type: ignore[misc]


# ── OtelConfiguration DI ──────────────────────────────────────────────────────


class TestOtelConfiguration:
    def test_tracer_provider_registered_globally(self) -> None:
        from varco_core.observability.di import _build_resource

        cfg = OtelConfig(service_name="test-svc")

        # Build the provider manually (same as the @Provider method does).
        resource = _build_resource(cfg)
        provider = TracerProvider(resource=resource)

        old = trace.get_tracer_provider()
        trace.set_tracer_provider(provider)

        # After setting, get_tracer() returns a tracer from our provider.
        tracer = trace.get_tracer("test")
        assert tracer is not None

        trace.set_tracer_provider(old)

    def test_meter_provider_registered_globally(self) -> None:
        from varco_core.observability.di import _build_resource

        cfg = OtelConfig(service_name="test-svc")
        resource = _build_resource(cfg)
        reader = InMemoryMetricReader()
        provider = MeterProvider(resource=resource, metric_readers=[reader])

        old = otel_metrics.get_meter_provider()
        otel_metrics.set_meter_provider(provider)

        meter = otel_metrics.get_meter("test")
        assert meter is not None

        otel_metrics.set_meter_provider(old)


# ── create_span context manager ───────────────────────────────────────────────


class TestCreateSpan:
    def test_creates_span_with_name(self, span_exporter: InMemorySpanExporter) -> None:
        with create_span("my.block"):
            pass

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "my.block"

    def test_yields_span_for_dynamic_attributes(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        with create_span("my.block") as s:
            s.set_attribute("entity.id", "order-42")

        spans = span_exporter.get_finished_spans()
        assert spans[0].attributes["entity.id"] == "order-42"

    def test_static_attributes(self, span_exporter: InMemorySpanExporter) -> None:
        with create_span("my.block", attributes={"queue": "orders"}):
            pass

        spans = span_exporter.get_finished_spans()
        assert spans[0].attributes["queue"] == "orders"

    def test_records_exception(self, span_exporter: InMemorySpanExporter) -> None:
        with pytest.raises(ValueError):
            with create_span("my.block"):
                raise ValueError("boom")

        spans = span_exporter.get_finished_spans()
        assert spans[0].status.status_code == StatusCode.ERROR

    def test_does_not_swallow_exception(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        with pytest.raises(RuntimeError):
            with create_span("my.block"):
                raise RuntimeError("must propagate")

    def test_correlation_id_bridged(self, span_exporter: InMemorySpanExporter) -> None:
        import asyncio

        async def _run() -> None:
            async with correlation_context("ctx-123"):
                with create_span("my.block"):
                    pass

        asyncio.run(_run())

        spans = span_exporter.get_finished_spans()
        assert spans[0].attributes.get("correlation_id") == "ctx-123"

    def test_nested_creates_parent_child(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        with create_span("outer"):
            with create_span("inner"):
                pass

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 2
        # inner finishes first
        inner = next(s for s in spans if s.name == "inner")
        outer = next(s for s in spans if s.name == "outer")
        # inner's parent should be outer
        assert inner.parent is not None
        assert inner.parent.span_id == outer.context.span_id


# ── create_counter / create_histogram helpers ─────────────────────────────────


class TestCreateMetricHelpers:
    def test_create_counter_returns_instrument(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        c = create_counter("helpers.counter", description="test counter")
        c.add(5)

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        metric = next(m for m in metrics if m.name == "helpers.counter")
        assert metric.data.data_points[0].value == 5

    def test_create_histogram_returns_instrument(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        h = create_histogram("helpers.histogram", description="test histogram")
        h.record(0.123)

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        hist = next(m for m in metrics if m.name == "helpers.histogram")
        assert hist.data.data_points[0].count == 1

    def test_same_name_returns_cached_instrument(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        # Two calls with the same name → same object (cached).
        c1 = create_counter("cache.test.counter")
        c2 = create_counter("cache.test.counter")
        assert c1 is c2

    def test_build_resource_includes_service_name(self) -> None:
        from varco_core.observability.di import _build_resource

        cfg = OtelConfig(
            service_name="orders-svc",
            service_version="2.0.0",
            extra_resource_attrs={"k8s.pod.name": "pod-x"},
        )
        resource = _build_resource(cfg)
        attrs = resource.attributes
        assert attrs["service.name"] == "orders-svc"
        assert attrs["service.version"] == "2.0.0"
        assert attrs["k8s.pod.name"] == "pod-x"


# ── Metric ────────────────────────────────────────────────────────────────────


class TestMetric:
    """
    Tests for the ``Metric`` wrapper class.

    Uses the ``metric_reader`` fixture so each test gets a fresh
    ``MeterProvider`` and clean ``_instrument_cache``.
    """

    def test_counter_add_increments(self, metric_reader: InMemoryMetricReader) -> None:
        """add() on a counter kind records the value via OTel Counter."""
        m = Metric("test.counter", kind="counter", description="test")
        m.add(3)

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(m for m in metrics if m.name == "test.counter")
        assert point.data.data_points[0].value == 3

    def test_counter_add_default_amount(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        """add() with no argument defaults to incrementing by 1."""
        m = Metric("test.counter.default", kind="counter")
        m.add()  # default amount = 1

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(m for m in metrics if m.name == "test.counter.default")
        assert point.data.data_points[0].value == 1

    def test_updown_counter_add_and_sub(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        """sub() decrements an updown_counter — net result is the sum."""
        m = Metric("test.updown", kind="updown_counter")
        m.add(5)
        m.sub(2)

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(met for met in metrics if met.name == "test.updown")
        # OTel updown counter reports the cumulative sum: 5 - 2 = 3
        assert point.data.data_points[0].value == 3

    def test_sub_default_amount_decrements_by_one(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        """sub() with no argument defaults to decrementing by 1."""
        m = Metric("test.sub.default", kind="updown_counter")
        m.add(10)
        m.sub()  # default = 1

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(met for met in metrics if met.name == "test.sub.default")
        assert point.data.data_points[0].value == 9

    def test_histogram_record_captures_value(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        """record() on a histogram kind records the observation."""
        m = Metric("test.histogram", kind="histogram", unit="s")
        m.record(0.75)

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(met for met in metrics if met.name == "test.histogram")
        dp = point.data.data_points[0]
        # Histogram records count and sum — verify both
        assert dp.count == 1
        assert dp.sum == pytest.approx(0.75)

    def test_add_on_histogram_routes_to_record(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        """add() on histogram kind internally calls .record() — same result."""
        m = Metric("test.histogram.add", kind="histogram", unit="1")
        m.add(42)

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(met for met in metrics if met.name == "test.histogram.add")
        assert point.data.data_points[0].sum == 42

    def test_kwargs_forwarded_as_attributes(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        """Keyword arguments to add() become OTel attribute dimensions."""
        m = Metric("test.attrs", kind="counter")
        m.add(1, model="gpt-4o", tenant="acme")

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(met for met in metrics if met.name == "test.attrs")
        dp = point.data.data_points[0]
        assert dp.attributes.get("model") == "gpt-4o"
        assert dp.attributes.get("tenant") == "acme"

    def test_instrument_is_cached_across_instances(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        """Two Metric objects with the same name share the same instrument."""
        m1 = Metric("test.cached", kind="counter")
        m2 = Metric("test.cached", kind="counter")
        # Touch both to trigger lazy creation
        m1.add(1)
        m2.add(1)

        # Same instrument means cumulative value is 2, not 1+1 separate points
        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(met for met in metrics if met.name == "test.cached")
        assert point.data.data_points[0].value == 2

    def test_repr_includes_name_and_kind(self) -> None:
        """__repr__ is descriptive for debugging."""
        m = Metric("agent.tool_calls", kind="counter", meter_name="my-svc")
        r = repr(m)
        assert "agent.tool_calls" in r
        assert "counter" in r
        assert "my-svc" in r

    def test_invalid_kind_raises_value_error(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        """An unsupported kind string raises ValueError on first use."""
        m = Metric("bad.kind", kind="gauge")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="not supported"):
            m.add(1)


# ── register_gauge ─────────────────────────────────────────────────────────────


class TestRegisterGauge:
    """
    Tests for the ``register_gauge`` observable-gauge helper.

    Observable gauges are pull-based — the callback is invoked by the
    SDK when ``metric_reader.get_metrics_data()`` is called.
    """

    def test_callback_invoked_on_collection(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        """The callback's return value appears as the gauge observation."""
        register_gauge(
            "test.gauge",
            callback=lambda: 42,
            description="test gauge",
            unit="1",
        )

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(m for m in metrics if m.name == "test.gauge")
        assert point.data.data_points[0].value == 42

    def test_static_attributes_attached_to_observation(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        """static_attributes dict is forwarded to every Observation."""
        register_gauge(
            "test.gauge.attrs",
            callback=lambda: 7,
            static_attributes={"pod": "worker-1"},
        )

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(m for m in metrics if m.name == "test.gauge.attrs")
        assert point.data.data_points[0].attributes.get("pod") == "worker-1"

    def test_callback_can_return_dynamic_value(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        """Callback is re-evaluated on each collection — returns current value."""
        counter_box = [0]

        def _gauge_cb() -> int:
            counter_box[0] += 1
            return counter_box[0]

        register_gauge("test.gauge.dynamic", callback=_gauge_cb)

        # First collection
        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(m for m in metrics if m.name == "test.gauge.dynamic")
        # callback was called at least once → counter_box[0] >= 1
        assert point.data.data_points[0].value >= 1


# ── Plan 004 (A) — automatic parameter capture on @span ─────────────────────


class TestSpanParamCapture:
    """
    Step 7: bare ``@span`` auto-captures decorated-function arguments as
    ``param.<name>`` span attributes.  These tests must FAIL until
    ``varco_core.observability.params`` exists and ``span.py`` is wired to it
    (Plan 004, phase 3).
    """

    async def test_bare_span_captures_positional_and_default_args(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        @span
        async def f(a, b=2):
            pass

        await f(1)

        spans = span_exporter.get_finished_spans()
        assert spans[0].attributes.get("param.a") == 1
        # b was NOT passed by the caller — defaults are not applied (plan design)
        assert "param.b" not in spans[0].attributes

    async def test_method_call_does_not_capture_self(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        class Svc:
            @span
            async def do(self, x):
                pass

        await Svc().do(5)

        spans = span_exporter.get_finished_spans()
        assert "param.self" not in spans[0].attributes
        assert spans[0].attributes.get("param.x") == 5

    async def test_capture_params_false_on_span_config_disables_capture(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        @span(SpanConfig(capture_params=False))
        async def f(a):
            pass

        await f(1)

        spans = span_exporter.get_finished_spans()
        assert not any(k.startswith("param.") for k in spans[0].attributes)

    async def test_set_capture_enabled_false_disables_globally(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.observability.params import set_capture_enabled

        set_capture_enabled(False)

        @span
        async def f(a):
            pass

        await f(1)

        spans = span_exporter.get_finished_spans()
        assert not any(k.startswith("param.") for k in spans[0].attributes)

    async def test_password_kwarg_is_redacted(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        @span
        async def login(username, password):
            pass

        await login("bob", password="hunter2")

        spans = span_exporter.get_finished_spans()
        assert spans[0].attributes.get("param.password") == "[REDACTED]"

    async def test_global_attribute_appears_on_span(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.observability.attributes import set_global_attributes

        set_global_attributes(pod="p1")

        @span
        async def f() -> None:
            pass

        await f()

        spans = span_exporter.get_finished_spans()
        assert spans[0].attributes.get("pod") == "p1"

    async def test_configure_global_attributes_apply_to_spans_false_removes_it(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.observability.attributes import (
            configure_global_attributes,
            set_global_attributes,
        )

        set_global_attributes(pod="p1")
        configure_global_attributes(apply_to_spans=False)

        @span
        async def f() -> None:
            pass

        await f()

        spans = span_exporter.get_finished_spans()
        assert "pod" not in spans[0].attributes

    async def test_span_config_attributes_wins_over_same_named_global_attr(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.observability.attributes import set_global_attributes

        set_global_attributes(env="global-env")

        @span(SpanConfig(attributes={"env": "explicit-env"}))
        async def f() -> None:
            pass

        await f()

        spans = span_exporter.get_finished_spans()
        assert spans[0].attributes.get("env") == "explicit-env"

    async def test_correlation_id_still_present_regression(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        """Regression: adding param-capture/global-attr merge must not shadow correlation_id."""

        @span
        async def f() -> None:
            pass

        async with correlation_context("still-here"):
            await f()

        spans = span_exporter.get_finished_spans()
        assert spans[0].attributes.get("correlation_id") == "still-here"


class TestTracingMixinParamCapture:
    """Step 10 (test half): TracingServiceMixin/TracingRepositoryMixin spans
    also get params + global attributes routed through the shared merge
    helper."""

    async def test_service_mixin_span_gets_global_attribute(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.observability.attributes import set_global_attributes
        from varco_core.service.base import AsyncService

        set_global_attributes(pod="p1")

        class _StubService(TracingServiceMixin, AsyncService):  # type: ignore[type-arg]
            _tracing_config = SpanConfig()

            def __init__(self) -> None:
                pass

            def _get_repo(self, uow: Any) -> Any:
                return MagicMock()

        svc = _StubService()

        async def _noop(*a: Any, **kw: Any) -> Any:
            return None

        await svc._run_in_span("create", _noop)

        spans = span_exporter.get_finished_spans()
        assert spans[0].attributes.get("pod") == "p1"

    async def test_repository_mixin_span_gets_global_attribute(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.observability.attributes import set_global_attributes
        from varco_core.repository import AsyncRepository

        set_global_attributes(pod="p1")

        class _StubBackend(AsyncRepository):  # type: ignore[type-arg]
            async def find_by_id(self, pk: Any) -> Any:
                return None

            async def find_all(self) -> list:
                return []

            async def save(self, entity: Any) -> Any:
                return entity

            async def delete(self, entity: Any) -> None:
                pass

            async def find_by_query(self, params: Any) -> list:
                return []

            async def count(self, params: Any = None) -> int:
                return 0

            async def exists(self, pk: Any) -> bool:
                return False

            async def save_many(self, entities: Any) -> list:
                return []

            async def delete_many(self, entities: Any) -> None:
                pass

            async def update_many_by_query(self, params: Any, update: Any) -> int:
                return 0

            async def stream_by_query(self, params: Any):  # type: ignore[override]
                return
                yield

        class _TracedRepo(TracingRepositoryMixin, _StubBackend):  # type: ignore[type-arg]
            _tracing_config = SpanConfig()

        repo = _TracedRepo()

        async def _noop(*a: Any, **kw: Any) -> Any:
            return None

        await repo._run_in_span("save", _noop)

        spans = span_exporter.get_finished_spans()
        assert spans[0].attributes.get("pod") == "p1"


# ── Plan 004 (B) — global attributes on metrics ──────────────────────────────


class TestGlobalAttributesOnMetrics:
    """Step 11: global attrs land on counters/histograms/Metric/gauges."""

    def test_counter_decorator_gets_global_attribute(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        from varco_core.observability.attributes import set_global_attributes

        set_global_attributes(pod="p1")

        @counter(CounterConfig(name="test.ga.counter"))
        def my_fn() -> None:
            pass

        my_fn()

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(m for m in metrics if m.name == "test.ga.counter")
        assert point.data.data_points[0].attributes.get("pod") == "p1"

    def test_histogram_decorator_gets_global_attribute(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        from varco_core.observability.attributes import set_global_attributes

        set_global_attributes(pod="p1")

        @histogram(HistogramConfig(name="test.ga.histogram"))
        def my_fn() -> None:
            pass

        my_fn()

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(m for m in metrics if m.name == "test.ga.histogram")
        assert point.data.data_points[0].attributes.get("pod") == "p1"

    def test_metric_add_gets_global_attribute(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        from varco_core.observability.attributes import set_global_attributes

        set_global_attributes(pod="p1")

        m = Metric("test.ga.metric.counter", kind="counter")
        m.add(1)

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(mm for mm in metrics if mm.name == "test.ga.metric.counter")
        assert point.data.data_points[0].attributes.get("pod") == "p1"

    def test_metric_sub_gets_global_attribute(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        from varco_core.observability.attributes import set_global_attributes

        set_global_attributes(pod="p1")

        m = Metric("test.ga.metric.updown", kind="updown_counter")
        m.sub(1)

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(mm for mm in metrics if mm.name == "test.ga.metric.updown")
        assert point.data.data_points[0].attributes.get("pod") == "p1"

    def test_metric_record_gets_global_attribute(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        from varco_core.observability.attributes import set_global_attributes

        set_global_attributes(pod="p1")

        m = Metric("test.ga.metric.hist", kind="histogram")
        m.record(1.0)

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(mm for mm in metrics if mm.name == "test.ga.metric.hist")
        assert point.data.data_points[0].attributes.get("pod") == "p1"

    def test_register_gauge_observation_gets_global_attribute(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        from varco_core.observability.attributes import set_global_attributes

        set_global_attributes(pod="p1")

        register_gauge("test.ga.gauge", callback=lambda: 42)

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(m for m in metrics if m.name == "test.ga.gauge")
        assert point.data.data_points[0].attributes.get("pod") == "p1"

    def test_caller_attribute_wins_on_conflict(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        from varco_core.observability.attributes import set_global_attributes

        set_global_attributes(tenant="global-tenant")

        m = Metric("test.ga.conflict", kind="counter")
        m.add(1, tenant="caller-tenant")

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(mm for mm in metrics if mm.name == "test.ga.conflict")
        assert point.data.data_points[0].attributes.get("tenant") == "caller-tenant"

    def test_configure_apply_to_metrics_false_removes_global_attrs(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        from varco_core.observability.attributes import (
            configure_global_attributes,
            set_global_attributes,
        )

        set_global_attributes(pod="p1")
        configure_global_attributes(apply_to_metrics=False)

        m = Metric("test.ga.disabled", kind="counter")
        m.add(1)

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(mm for mm in metrics if mm.name == "test.ga.disabled")
        assert "pod" not in point.data.data_points[0].attributes

    def test_late_registered_global_attribute_still_appears(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        """Attributes registered AFTER the instrument was first created still appear."""
        from varco_core.observability.attributes import set_global_attributes

        m = Metric("test.ga.late", kind="counter")
        m.add(1)  # instrument created + cached here, no global attrs yet

        set_global_attributes(pod="late-pod")
        m.add(1)  # second measurement — registry mutated after creation

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(mm for mm in metrics if mm.name == "test.ga.late")
        # Each distinct attribute set is its own data point, and the SDK keeps them in
        # insertion order — so the pre-registration measurement is always data_points[0].
        # The contract under test is that the LATER measurement picked the attribute up.
        assert any(
            dp.attributes.get("pod") == "late-pod" for dp in point.data.data_points
        )


# ── Plan 004 (Phase 5) — config, DI, import-surface ──────────────────────────


class TestOtelConfigNewFields:
    """Step 15: OtelConfig gains capture/global-attr fields with documented defaults."""

    def test_new_fields_have_documented_defaults(self) -> None:
        cfg = OtelConfig(service_name="svc")
        assert cfg.capture_params is None
        assert cfg.param_capture is None
        assert cfg.global_attributes == {}
        assert cfg.global_attributes_on_spans is True
        assert cfg.global_attributes_on_metrics is True
        assert cfg.promote_global_attrs_to_resource is False

    def test_capture_params_false_disables_process_wide(self) -> None:
        from varco_core.observability.params import capture_enabled
        from varco_core.observability.di import _apply_observability_config

        cfg = OtelConfig(service_name="svc", capture_params=False)
        _apply_observability_config(cfg)

        assert capture_enabled() is False


class TestOtelConfigurationGlobalAttrsBootstrap:
    def test_config_global_attributes_appear_on_span(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.observability.di import _apply_observability_config

        cfg = OtelConfig(service_name="svc", global_attributes={"k8s.pod.name": "p"})
        _apply_observability_config(cfg)

        @span
        async def f() -> None:
            pass

        import asyncio

        asyncio.run(f())

        spans = span_exporter.get_finished_spans()
        assert spans[0].attributes.get("k8s.pod.name") == "p"

    def test_config_global_attributes_appear_on_counter(
        self, metric_reader: InMemoryMetricReader
    ) -> None:
        from varco_core.observability.di import _apply_observability_config

        cfg = OtelConfig(service_name="svc", global_attributes={"k8s.pod.name": "p"})
        _apply_observability_config(cfg)

        m = Metric("test.di.ga.counter", kind="counter")
        m.add(1)

        data = metric_reader.get_metrics_data()
        metrics = data.resource_metrics[0].scope_metrics[0].metrics
        point = next(mm for mm in metrics if mm.name == "test.di.ga.counter")
        assert point.data.data_points[0].attributes.get("k8s.pod.name") == "p"

    def test_promote_global_attrs_to_resource_puts_it_on_exported_resource(
        self,
    ) -> None:
        from varco_core.observability.attributes import set_global_attributes
        from varco_core.observability.di import _build_resource

        set_global_attributes(**{"k8s.pod.name": "p"})
        cfg = OtelConfig(
            service_name="svc",
            promote_global_attrs_to_resource=True,
        )
        resource = _build_resource(cfg)
        assert resource.attributes["k8s.pod.name"] == "p"


class TestObservabilityPublicImportSurface:
    """Step 19: assert every new name is importable from varco_core.observability."""

    @pytest.mark.parametrize(
        "name",
        [
            "ParamCaptureConfig",
            "set_capture_enabled",
            "set_param_capture_defaults",
            "GlobalAttributes",
            "set_global_attributes",
            "register_global_attribute_provider",
            "current_global_attributes",
            "clear_global_attributes",
            "configure_global_attributes",
        ],
    )
    def test_name_importable_from_observability_package(self, name: str) -> None:
        import varco_core.observability as obs

        assert hasattr(obs, name), f"{name} not exported from varco_core.observability"


# ── TracingEventMiddleware ─────────────────────────────────────────────────────


class TestTracingEventMiddleware:
    """
    Tests for ``TracingEventMiddleware``.

    Uses the same ``span_exporter`` fixture as the rest of the file — each
    test gets a fresh ``InMemorySpanExporter`` with no cross-test pollution.

    The middleware is exercised by wiring it into ``InMemoryEventBus`` and
    publishing events, then inspecting the recorded spans.
    """

    async def test_span_created_on_dispatch(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.event import InMemoryEventBus
        from varco_core.event.base import Event
        from varco_core.event.middleware import TracingEventMiddleware

        class PingEvent(Event):
            __event_type__ = "test.ping"

        bus = InMemoryEventBus(middleware=[TracingEventMiddleware()])
        bus.subscribe(PingEvent, lambda e: None)
        await bus.publish(PingEvent())

        spans = span_exporter.get_finished_spans()
        assert len(spans) == 1

    async def test_span_name_is_event_type_name(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.event import InMemoryEventBus
        from varco_core.event.base import Event
        from varco_core.event.middleware import TracingEventMiddleware

        class MySpecialEvent(Event):
            __event_type__ = "test.special"

        bus = InMemoryEventBus(middleware=[TracingEventMiddleware()])
        bus.subscribe(MySpecialEvent, lambda e: None)
        await bus.publish(MySpecialEvent())

        spans = span_exporter.get_finished_spans()
        assert spans[0].name == "event.MySpecialEvent"

    async def test_channel_attribute_set(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.event import InMemoryEventBus
        from varco_core.event.base import Event
        from varco_core.event.middleware import TracingEventMiddleware

        class OrderEvent(Event):
            __event_type__ = "test.order"

        bus = InMemoryEventBus(middleware=[TracingEventMiddleware()])
        bus.subscribe(OrderEvent, lambda e: None, channel="orders")
        await bus.publish(OrderEvent(), channel="orders")

        spans = span_exporter.get_finished_spans()
        assert spans[0].attributes.get("messaging.channel") == "orders"

    async def test_event_type_attribute_set(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.event import InMemoryEventBus
        from varco_core.event.base import Event
        from varco_core.event.middleware import TracingEventMiddleware

        class SomeEvent(Event):
            __event_type__ = "test.some"

        bus = InMemoryEventBus(middleware=[TracingEventMiddleware()])
        bus.subscribe(SomeEvent, lambda e: None)
        await bus.publish(SomeEvent())

        spans = span_exporter.get_finished_spans()
        assert spans[0].attributes.get("event.type") == "SomeEvent"

    async def test_correlation_id_from_correlation_middleware(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.event import InMemoryEventBus
        from varco_core.event.base import Event
        from varco_core.event.middleware import (
            CorrelationMiddleware,
            TracingEventMiddleware,
        )

        class PongEvent(Event):
            __event_type__ = "test.pong"

        # CorrelationMiddleware sets the ContextVar before TracingEventMiddleware reads it
        bus = InMemoryEventBus(
            middleware=[CorrelationMiddleware(), TracingEventMiddleware()]
        )
        bus.subscribe(PongEvent, lambda e: None)
        await bus.publish(PongEvent())

        spans = span_exporter.get_finished_spans()
        cid = spans[0].attributes.get("correlation_id")
        assert cid is not None
        assert isinstance(cid, str)

    async def test_correlation_id_from_event_attribute(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.event import InMemoryEventBus
        from varco_core.event.domain import EntityCreatedEvent
        from varco_core.event.middleware import TracingEventMiddleware

        bus = InMemoryEventBus(middleware=[TracingEventMiddleware()])
        bus.subscribe(EntityCreatedEvent, lambda e: None)
        await bus.publish(
            EntityCreatedEvent(
                entity_type="Order",
                pk="o-1",
                correlation_id="cid-xyz",
                payload={},
            )
        )

        spans = span_exporter.get_finished_spans()
        assert spans[0].attributes.get("correlation_id") == "cid-xyz"

    async def test_exception_recorded_on_handler_failure(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.event import InMemoryEventBus
        from varco_core.event.base import Event
        from varco_core.event.middleware import TracingEventMiddleware

        class FailEvent(Event):
            __event_type__ = "test.fail"

        async def bad_handler(event: Event) -> None:
            raise RuntimeError("boom")

        bus = InMemoryEventBus(
            middleware=[TracingEventMiddleware(record_exception=True)]
        )
        bus.subscribe(FailEvent, bad_handler)

        with pytest.raises(RuntimeError):
            await bus.publish(FailEvent())

        spans = span_exporter.get_finished_spans()
        assert len(spans[0].events) > 0
        assert any("exception" in e.name.lower() for e in spans[0].events)

    async def test_exception_re_raised_after_span(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.event import InMemoryEventBus
        from varco_core.event.base import Event
        from varco_core.event.middleware import TracingEventMiddleware

        class BombEvent(Event):
            __event_type__ = "test.bomb"

        async def explode(event: Event) -> None:
            raise ValueError("detonated")

        bus = InMemoryEventBus(middleware=[TracingEventMiddleware()])
        bus.subscribe(BombEvent, explode)

        with pytest.raises(ValueError, match="detonated"):
            await bus.publish(BombEvent())

    async def test_error_status_set_on_failure(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from opentelemetry.trace import StatusCode as OTelStatusCode

        from varco_core.event import InMemoryEventBus
        from varco_core.event.base import Event
        from varco_core.event.middleware import TracingEventMiddleware

        class ErrorEvent(Event):
            __event_type__ = "test.error_event"

        async def bad(e: Event) -> None:
            raise RuntimeError("bad")

        bus = InMemoryEventBus(
            middleware=[TracingEventMiddleware(set_status_on_error=True)]
        )
        bus.subscribe(ErrorEvent, bad)

        with pytest.raises(RuntimeError):
            await bus.publish(ErrorEvent())

        spans = span_exporter.get_finished_spans()
        assert spans[0].status.status_code == OTelStatusCode.ERROR

    async def test_no_error_status_when_disabled(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from opentelemetry.trace import StatusCode as OTelStatusCode

        from varco_core.event import InMemoryEventBus
        from varco_core.event.base import Event
        from varco_core.event.middleware import TracingEventMiddleware

        class SilentError(Event):
            __event_type__ = "test.silent_error"

        async def bad(e: Event) -> None:
            raise RuntimeError("silent")

        bus = InMemoryEventBus(
            middleware=[
                TracingEventMiddleware(
                    set_status_on_error=False, record_exception=False
                )
            ]
        )
        bus.subscribe(SilentError, bad)

        with pytest.raises(RuntimeError):
            await bus.publish(SilentError())

        spans = span_exporter.get_finished_spans()
        assert spans[0].status.status_code != OTelStatusCode.ERROR

    async def test_static_attributes_applied(
        self, span_exporter: InMemorySpanExporter
    ) -> None:
        from varco_core.event import InMemoryEventBus
        from varco_core.event.base import Event
        from varco_core.event.middleware import TracingEventMiddleware

        class AttrEvent(Event):
            __event_type__ = "test.attr"

        bus = InMemoryEventBus(
            middleware=[
                TracingEventMiddleware(attributes={"messaging.system": "internal"})
            ]
        )
        bus.subscribe(AttrEvent, lambda e: None)
        await bus.publish(AttrEvent())

        spans = span_exporter.get_finished_spans()
        assert spans[0].attributes.get("messaging.system") == "internal"

    def test_repr_contains_class_name(self) -> None:
        from varco_core.event.middleware import TracingEventMiddleware

        mw = TracingEventMiddleware()
        assert "TracingEventMiddleware" in repr(mw)
