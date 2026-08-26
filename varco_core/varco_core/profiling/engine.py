"""
varco_core.profiling.engine
=============================
Backend-agnostic ``ProfileSession`` orchestrator.

``ProfileSession`` drives any registered CPU/memory backend pair through a
start → collect lifecycle and assembles the results into an immutable
``ProfileReport``.  It implements **both** sync and async context-manager
protocols so one object works with ``with`` and ``async with``.

Usage::

    from varco_core.profiling import profiled, ProfileConfig

    # As a context manager
    async with profiled("my_operation") as session:
        result = await some_async_work()
    print(session.report.format())

    # As a sync context manager
    with profiled("sync_op") as session:
        do_cpu_work()

DESIGN: engine knows nothing about specific backends
    ✅ Adding a new backend requires only implementing the protocol and
       registering it — the engine is not modified.
    ✅ Profiling failures are caught here and swallowed so they never break
       the application code being profiled.
    ❌ Errors are silently logged — developers must check logs if a report
       is unexpectedly ``None`` or empty.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from varco_core.profiling.backend import (
    CpuProfilerBackend,
    MemoryProfilerBackend,
    get_cpu_backend,
    get_memory_backend,
)
from varco_core.profiling.config import ProfileConfig
from varco_core.profiling.report import ProfileReport

if TYPE_CHECKING:
    pass

_logger = logging.getLogger(__name__)


class ProfileSession:
    """Dual sync/async context manager that profiles a block of code.

    A fresh ``ProfileSession`` is created per profiling scope — it is **not**
    reusable.  After the ``with`` / ``async with`` block exits, access the
    result via ``.report``.

    Args:
        name:   Human-readable label for the profiled operation.
        config: Profiling configuration.  Defaults to ``ProfileConfig()``.

    Thread safety:  ⚠️ Single-use per scope; do not share across threads.
    Async safety:   ✅ ``__aenter__`` / ``__aexit__`` are safe to ``await``.
                    ⚠️ cProfile captures all coroutines on the event loop thread;
                       see ``cProfile`` backend docs for details.
    """

    def __init__(self, name: str, config: ProfileConfig | None = None) -> None:
        self._name = name
        self._config = config or ProfileConfig()
        self._report: ProfileReport | None = None

        self._cpu_backend: CpuProfilerBackend | None = None
        self._memory_backend: MemoryProfilerBackend | None = None

        self._t_start: float = 0.0
        self._proc_start: float = 0.0
        self._rss_start: int | None = None

    @property
    def report(self) -> ProfileReport | None:
        """The ``ProfileReport`` produced when the session closed.

        Returns:
            ``ProfileReport`` after the context exits; ``None`` if the session
            has not yet completed or profiling failed silently.
        """
        return self._report

    # ── Sync context manager ──────────────────────────────────────────────────

    def __enter__(self) -> ProfileSession:
        self._open()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._close()

    # ── Async context manager ─────────────────────────────────────────────────

    async def __aenter__(self) -> ProfileSession:
        self._open()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._close()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _open(self) -> None:
        """Initialise backends and take baseline measurements."""
        cfg = self._config

        # Resolve backends
        if cfg.cpu:
            self._cpu_backend = self._resolve_backend(cfg.cpu_backend, "cpu")  # type: ignore[assignment]

        if cfg.memory:
            self._memory_backend = self._resolve_backend(cfg.memory_backend, "memory")  # type: ignore[assignment]

        # Baseline wall + CPU time
        self._t_start = time.perf_counter()
        self._proc_start = time.process_time()

        # Baseline RSS (psutil, optional — degrade if it errors)
        if cfg.track_rss:
            with suppress(Exception):
                import psutil  # noqa: PLC0415

                self._rss_start = psutil.Process().memory_info().rss

        # Start backends
        if self._cpu_backend is not None:
            with suppress(Exception):
                self._cpu_backend.start()

        if self._memory_backend is not None:
            with suppress(Exception):
                self._memory_backend.start()

    def _close(self) -> None:
        """Collect results, assemble ``ProfileReport``, dispatch sinks."""
        cfg = self._config

        wall_ms = (time.perf_counter() - self._t_start) * 1000.0
        proc_ms = (time.process_time() - self._proc_start) * 1000.0

        # RSS delta (psutil)
        rss_delta: int | None = None
        if cfg.track_rss and self._rss_start is not None:
            with suppress(Exception):
                import psutil  # noqa: PLC0415

                rss_delta = psutil.Process().memory_info().rss - self._rss_start

        # CPU backend
        from varco_core.profiling.backend import CpuProfileResult  # noqa: PLC0415

        cpu_result: CpuProfileResult | None = None
        cpu_backend_name = "none"
        if self._cpu_backend is not None:
            cpu_backend_name = self._cpu_backend.name
            with suppress(Exception):
                cpu_result = self._cpu_backend.collect(cfg.top_n, cfg.sort_by)

        # Memory backend
        from varco_core.profiling.backend import MemoryProfileResult  # noqa: PLC0415

        mem_result: MemoryProfileResult | None = None
        memory_backend_name = "none"
        if self._memory_backend is not None:
            memory_backend_name = self._memory_backend.name
            with suppress(Exception):
                mem_result = self._memory_backend.collect(cfg.top_n)

        # Collect artifacts from all backends that produced one
        artifacts = []
        if cpu_result is not None and cpu_result.artifact is not None:
            artifacts.append(cpu_result.artifact)
        if mem_result is not None and mem_result.artifact is not None:
            artifacts.append(mem_result.artifact)

        self._report = ProfileReport(
            name=self._name,
            wall_time_ms=wall_ms,
            # Use process_time if CPU backend unavailable
            cpu_time_ms=cpu_result.cpu_time_ms if cpu_result is not None else proc_ms,
            top_functions=cpu_result.top_functions if cpu_result is not None else (),
            mem_current_bytes=mem_result.current_bytes if mem_result is not None else 0,
            mem_peak_bytes=mem_result.peak_bytes if mem_result is not None else 0,
            mem_delta_bytes=mem_result.delta_bytes if mem_result is not None else 0,
            rss_delta_bytes=rss_delta,
            top_allocations=(
                mem_result.top_allocations if mem_result is not None else ()
            ),
            artifacts=tuple(artifacts),
            cpu_backend=cpu_backend_name,
            memory_backend=memory_backend_name,
            captured_at=datetime.now(UTC),
        )

        self._dispatch(self._report, cfg)

    def _dispatch(self, report: ProfileReport, cfg: ProfileConfig) -> None:
        """Log the report and optionally emit to OTel.

        Args:
            report: The completed ``ProfileReport``.
            cfg:    The session configuration.
        """
        logger_name = cfg.logger_name or "varco_core.profiling"
        logger = logging.getLogger(logger_name)
        logger.debug(report.format())

        if cfg.otel:
            with suppress(Exception):
                from varco_core.profiling.otel import emit_to_otel  # noqa: PLC0415

                emit_to_otel(report)

    # ── Backend resolution ────────────────────────────────────────────────────

    @staticmethod
    def _resolve_backend(
        spec: str | Callable[[], Any], kind: str
    ) -> CpuProfilerBackend | MemoryProfilerBackend | None:
        """Resolve a backend name or factory to a fresh instance.

        Args:
            spec: A string name (registry lookup) or zero-arg factory callable.
            kind: ``"cpu"`` or ``"memory"`` for error messages.

        Returns:
            A fresh backend instance, or ``None`` if resolution fails.
        """
        try:
            if callable(spec) and not isinstance(spec, str):
                return spec()
            if kind == "cpu":
                return get_cpu_backend(str(spec))()
            return get_memory_backend(str(spec))()
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Failed to resolve %s backend %r: %s", kind, spec, exc)
            return None
