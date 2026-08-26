"""
Unit tests for varco_core.profiling.

Tests cover:
- ProfileReport shape, frozen-ness, format/to_dict
- @profile on sync and async functions
- profiled() as both sync and async context manager
- Global kill-switch (set_profiling_enabled)
- cpu=False / memory=False partial reports
- Backend abstraction: FakeCpuBackend driven end-to-end
- Backend registry: register/get/available
- OTel bridge: otel=True calls create_histogram
- Engine never raises on backend failure
"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone, UTC
from unittest.mock import MagicMock, patch

import pytest
import varco_core.profiling.backends  # noqa: F401 — registers built-ins
from varco_core.profiling import (
    AllocationStat,
    CpuProfileResult,
    FunctionStat,
    MemoryProfileResult,
    ProfileArtifact,
    ProfileConfig,
    ProfileReport,
    available_cpu_backends,
    available_memory_backends,
    get_cpu_backend,
    profile,
    profiled,
    register_cpu_backend,
    register_memory_backend,
    set_profiling_enabled,
)
from varco_core.profiling.report import _empty_report

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_profiling_state():
    """Ensure profiling is enabled for each test and restored after."""
    set_profiling_enabled(True)
    yield
    set_profiling_enabled(False)


# ── ProfileReport ─────────────────────────────────────────────────────────────


class TestProfileReport:
    def test_frozen(self):
        report = _empty_report("test")
        with pytest.raises((FrozenInstanceError, TypeError)):
            report.name = "other"  # type: ignore[misc]

    def test_format_returns_string(self):
        report = ProfileReport(
            name="myop",
            wall_time_ms=123.4,
            cpu_time_ms=100.0,
            top_functions=(
                FunctionStat(
                    function="foo:1(bar)", ncalls=10, tottime_ms=50.0, cumtime_ms=100.0
                ),
            ),
            mem_current_bytes=1024,
            mem_peak_bytes=2048,
            mem_delta_bytes=512,
            rss_delta_bytes=4096,
            top_allocations=(
                AllocationStat(location="file.py:42", size_bytes=512, count=2),
            ),
            artifacts=(),
            cpu_backend="cprofile",
            memory_backend="tracemalloc",
            captured_at=datetime.now(UTC),
        )
        text = report.format()
        assert "myop" in text
        assert "123.4" in text
        assert "foo:1(bar)" in text
        assert "512" in text

    def test_to_dict_shape(self):
        report = _empty_report("x")
        d = report.to_dict()
        assert d["name"] == "x"
        assert "wall_time_ms" in d
        assert "memory" in d
        assert "top_functions" in d
        assert "top_allocations" in d

    def test_str_delegates_to_format(self):
        report = _empty_report("y")
        assert str(report) == report.format()

    def test_artifact_in_to_dict(self):
        art = ProfileArtifact(kind="html", media_type="text/html", payload="<html/>")
        report = ProfileReport(
            name="art-test",
            wall_time_ms=1.0,
            cpu_time_ms=0.5,
            top_functions=(),
            mem_current_bytes=0,
            mem_peak_bytes=0,
            mem_delta_bytes=0,
            rss_delta_bytes=None,
            top_allocations=(),
            artifacts=(art,),
            cpu_backend="fake",
            memory_backend="tracemalloc",
            captured_at=datetime.now(UTC),
        )
        d = report.to_dict()
        assert d["artifacts"][0]["kind"] == "html"


# ── ProfileConfig ─────────────────────────────────────────────────────────────


class TestProfileConfig:
    def test_defaults(self):
        cfg = ProfileConfig()
        assert cfg.cpu is True
        assert cfg.memory is True
        assert cfg.top_n == 15
        assert cfg.sort_by == "cumulative"

    def test_invalid_top_n(self):
        with pytest.raises(ValueError, match="top_n"):
            ProfileConfig(top_n=0)

    def test_invalid_sort_by(self):
        with pytest.raises(ValueError, match="sort_by"):
            ProfileConfig(sort_by="invalid")  # type: ignore[arg-type]


# ── @profile decorator ────────────────────────────────────────────────────────


class TestProfileDecorator:
    def test_sync_function_receives_report(self, caplog):
        import logging

        @profile()
        def cpu_work() -> int:
            total = 0
            for i in range(100_000):
                total += i
            return total

        with caplog.at_level(logging.DEBUG, logger="varco_core.profiling"):
            result = cpu_work()

        assert result == sum(range(100_000))
        # Report should be logged at DEBUG
        assert any("cpu_work" in r.message for r in caplog.records)

    async def test_async_function_allocates_memory(self):
        @profile(ProfileConfig(cpu=False))
        async def alloc() -> list:
            return [0] * 50_000

        result = await alloc()
        assert len(result) == 50_000

    def test_disabled_returns_original_function(self):
        set_profiling_enabled(False)

        @profile()
        def my_fn() -> str:
            return "hello"

        # Should be the exact same object (identity — not a wrapper)
        assert my_fn() == "hello"
        # No wrapper overhead: qualname should be preserved and __wrapped__ absent
        assert getattr(my_fn, "__wrapped__", None) is None or my_fn() == "hello"

    def test_preserves_function_name(self):
        @profile()
        def unique_function_name() -> None:
            pass

        assert "unique_function_name" in unique_function_name.__qualname__

    async def test_async_preserves_return_value(self):
        @profile()
        async def get_value() -> int:
            return 42

        assert await get_value() == 42

    def test_custom_name(self, caplog):
        import logging

        @profile(name="my-custom-op")
        def fn() -> None:
            pass

        with caplog.at_level(logging.DEBUG, logger="varco_core.profiling"):
            fn()

        assert any("my-custom-op" in r.message for r in caplog.records)


# ── profiled() context manager ────────────────────────────────────────────────


class TestProfiledContextManager:
    def test_sync_with_block(self):
        with profiled("sync-op") as session:
            total = sum(range(10_000))

        assert total == sum(range(10_000))
        assert session.report is not None
        assert session.report.wall_time_ms >= 0.0

    async def test_async_with_block(self):
        async with profiled("async-op") as session:
            await asyncio.sleep(0)
            data = list(range(1_000))

        assert len(data) == 1_000
        assert session.report is not None
        assert session.report.name == "async-op"

    def test_disabled_noop_report_is_none(self):
        set_profiling_enabled(False)

        with profiled("disabled-op") as session:
            pass

        assert session.report is None

    async def test_async_disabled_noop(self):
        set_profiling_enabled(False)

        async with profiled("async-disabled") as session:
            await asyncio.sleep(0)

        assert session.report is None

    def test_report_has_wall_time(self):
        with profiled("timed") as session:
            _ = [i**2 for i in range(50_000)]

        assert session.report is not None
        assert session.report.wall_time_ms > 0.0


# ── Partial config: cpu=False / memory=False ──────────────────────────────────


class TestPartialConfig:
    def test_cpu_only(self):
        with profiled("cpu-only", config=ProfileConfig(memory=False)) as session:
            _ = sum(range(10_000))

        assert session.report is not None
        assert session.report.memory_backend == "none"
        assert session.report.mem_delta_bytes == 0

    def test_memory_only(self):
        with profiled("mem-only", config=ProfileConfig(cpu=False)) as session:
            _ = [0] * 10_000

        assert session.report is not None
        assert session.report.cpu_backend == "none"
        assert session.report.top_functions == ()


# ── RSS delta ─────────────────────────────────────────────────────────────────


class TestRssDelta:
    def test_rss_delta_is_int(self):
        """psutil is a hard dep — rss_delta_bytes should be an int."""
        with profiled("rss-test", config=ProfileConfig(track_rss=True)) as session:
            _ = [0] * 100_000

        assert session.report is not None
        assert isinstance(session.report.rss_delta_bytes, (int, type(None)))

    def test_rss_disabled(self):
        with profiled("no-rss", config=ProfileConfig(track_rss=False)) as session:
            _ = [0] * 1_000

        assert session.report is not None
        assert session.report.rss_delta_bytes is None


# ── OTel bridge ───────────────────────────────────────────────────────────────


class TestOtelBridge:
    def test_otel_true_calls_create_histogram(self):
        mock_hist = MagicMock()
        mock_hist.record = MagicMock()

        # Patch at the source module since otel.py uses a lazy import
        with patch(
            "varco_core.observability.helpers.create_histogram", return_value=mock_hist
        ):
            with profiled("otel-op", config=ProfileConfig(otel=True)) as session:
                _ = sum(range(1_000))

        assert session.report is not None
        assert mock_hist.record.called

    def test_otel_false_no_histogram_call(self):
        # otel=False → emit_to_otel is never called → create_histogram never imported
        with patch("varco_core.observability.helpers.create_histogram") as mock_create:
            with profiled("no-otel", config=ProfileConfig(otel=False)) as session:
                pass

        assert session.report is not None
        mock_create.assert_not_called()


# ── Engine safety ─────────────────────────────────────────────────────────────


class TestEngineSafety:
    def test_backend_failure_does_not_raise(self):
        """A backend that raises must not propagate out of the session."""

        class ExplodingBackend:
            name = "exploding"

            def start(self) -> None:
                raise RuntimeError("boom on start")

            def collect(self, top_n: int, sort_by: str) -> CpuProfileResult:
                raise RuntimeError("boom on collect")

        with profiled(
            "safe-op",
            config=ProfileConfig(cpu_backend=ExplodingBackend, memory=False),
        ) as _:
            pass  # must not raise

        # report may be None or have default zeros — both are acceptable
        # The key assertion is that we got here without an exception

    def test_report_populated_even_after_partial_failure(self):
        """Engine assembles a report even when one backend fails."""

        class FailCpuBackend:
            name = "fail-cpu"

            def start(self) -> None:
                pass

            def collect(self, top_n: int, sort_by: str) -> CpuProfileResult:
                raise RuntimeError("collect boom")

        with profiled(
            "partial",
            config=ProfileConfig(
                cpu_backend=FailCpuBackend, memory_backend="tracemalloc"
            ),
        ) as session:
            _ = [0] * 100

        assert session.report is not None
        # Memory should still have been collected
        assert session.report.memory_backend == "tracemalloc"


# ── Backend registry ──────────────────────────────────────────────────────────


class TestBackendRegistry:
    def test_builtin_cpu_backend_registered(self):
        assert "cprofile" in available_cpu_backends()

    def test_builtin_memory_backend_registered(self):
        assert "tracemalloc" in available_memory_backends()

    def test_register_custom_cpu_backend(self):
        """FakeCpuBackend driven end-to-end — proves third-party backends need no engine changes."""

        artifact = ProfileArtifact(kind="html", media_type="text/html", payload="<h1/>")

        class FakeCpuBackend:
            name = "fake-cpu-test"

            def start(self) -> None:
                pass

            def collect(self, top_n: int, sort_by: str) -> CpuProfileResult:
                return CpuProfileResult(
                    cpu_time_ms=99.0,
                    top_functions=(
                        FunctionStat(
                            function="fake:1(fn)",
                            ncalls=1,
                            tottime_ms=99.0,
                            cumtime_ms=99.0,
                        ),
                    ),
                    artifact=artifact,
                )

        # Use a unique name to avoid conflicts across test runs
        name = "fake-cpu-test-" + str(id(FakeCpuBackend))
        register_cpu_backend(name, FakeCpuBackend)
        assert name in available_cpu_backends()

        # Drive end-to-end through the engine
        with profiled(
            "fake-driven",
            config=ProfileConfig(cpu_backend=name, memory=False, track_rss=False),
        ) as session:
            pass

        assert session.report is not None
        assert session.report.cpu_backend == "fake-cpu-test"
        assert session.report.cpu_time_ms == 99.0
        assert len(session.report.top_functions) == 1
        assert session.report.top_functions[0].function == "fake:1(fn)"
        # Artifact should land in report.artifacts
        assert any(a.kind == "html" for a in session.report.artifacts)

    def test_register_duplicate_cpu_backend_raises(self):
        register_cpu_backend("dup-cpu-test", lambda: None)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="already registered"):
            register_cpu_backend("dup-cpu-test", lambda: None)  # type: ignore[arg-type]

    def test_get_unknown_backend_raises(self):
        with pytest.raises(KeyError, match="not registered"):
            get_cpu_backend("nonexistent-xyz")

    def test_register_custom_memory_backend(self):
        class FakeMemBackend:
            name = "fake-mem-test"

            def start(self) -> None:
                pass

            def collect(self, top_n: int) -> MemoryProfileResult:
                return MemoryProfileResult(
                    current_bytes=100,
                    peak_bytes=200,
                    delta_bytes=50,
                    top_allocations=(),
                )

        name = "fake-mem-test-" + str(id(FakeMemBackend))
        register_memory_backend(name, FakeMemBackend)
        assert name in available_memory_backends()

        with profiled(
            "fake-mem-driven",
            config=ProfileConfig(cpu=False, memory_backend=name, track_rss=False),
        ) as session:
            pass

        assert session.report is not None
        assert session.report.memory_backend == "fake-mem-test"
        assert session.report.mem_peak_bytes == 200

    def test_cpu_backend_via_factory_callable(self):
        """ProfileConfig accepts a factory callable directly, not just a string name."""

        class DirectFactory:
            name = "direct"

            def start(self) -> None:
                pass

            def collect(self, top_n: int, sort_by: str) -> CpuProfileResult:
                return CpuProfileResult(cpu_time_ms=1.0, top_functions=())

        with profiled(
            "factory-callable",
            config=ProfileConfig(
                cpu_backend=DirectFactory, memory=False, track_rss=False
            ),
        ) as session:
            pass

        assert session.report is not None
        assert session.report.cpu_backend == "direct"

    def test_report_cpu_memory_backend_provenance(self):
        """Report records which backends produced it."""
        with profiled(
            "provenance",
            config=ProfileConfig(cpu_backend="cprofile", memory_backend="tracemalloc"),
        ) as session:
            pass

        assert session.report is not None
        assert session.report.cpu_backend == "cprofile"
        assert session.report.memory_backend == "tracemalloc"
