"""
varco_core.profiling.otel
===========================
Optional OpenTelemetry bridge for profiling summary stats.

Emits three OTel histogram instruments when ``ProfileConfig(otel=True)`` is
set.  The bridge is **fully optional**: if the OTel SDK is unavailable or the
``observability`` helpers are not importable, this module logs a warning and
returns silently.

Instruments emitted:
    ``profiler.wall.duration``   — Histogram[ms]  wall-clock time
    ``profiler.cpu.duration``    — Histogram[ms]  CPU time
    ``profiler.memory.peak``     — Histogram[B]   peak traced memory

Span attributes (added to the current active span if one exists):
    ``profiler.name``         — operation label
    ``profiler.wall_ms``      — wall time float
    ``profiler.mem_delta_b``  — memory delta int
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from varco_core.profiling.report import ProfileReport

_logger = logging.getLogger(__name__)


def emit_to_otel(report: ProfileReport) -> None:
    """Emit a ``ProfileReport`` summary to OpenTelemetry metrics + active span.

    Called automatically by ``ProfileSession`` when ``ProfileConfig(otel=True)``.
    Safe to call directly for custom dispatch.

    Args:
        report: The completed ``ProfileReport`` to emit.
    """
    try:
        from varco_core.observability.helpers import (  # noqa: PLC0415
            create_histogram,
        )
    except ImportError:
        _logger.debug(
            "varco_core.profiling.otel: observability helpers not available; "
            "OTel bridge skipped."
        )
        return

    attrs = {"profiler.name": report.name}

    try:
        wall_hist = create_histogram(
            "profiler.wall.duration",
            unit="ms",
            description="Wall-clock duration of profiled operation",
        )
        wall_hist.record(report.wall_time_ms, attributes=attrs)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("profiler.wall.duration record failed: %s", exc)

    try:
        cpu_hist = create_histogram(
            "profiler.cpu.duration",
            unit="ms",
            description="CPU time of profiled operation",
        )
        cpu_hist.record(report.cpu_time_ms, attributes=attrs)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("profiler.cpu.duration record failed: %s", exc)

    try:
        mem_hist = create_histogram(
            "profiler.memory.peak",
            unit="By",
            description="Peak traced memory during profiled operation",
        )
        mem_hist.record(report.mem_peak_bytes, attributes=attrs)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("profiler.memory.peak record failed: %s", exc)

    # Annotate the current active span
    try:
        from opentelemetry import trace  # noqa: PLC0415

        span = trace.get_current_span()
        if span and span.is_recording():
            span.set_attributes(
                {
                    "profiler.name": report.name,
                    "profiler.wall_ms": report.wall_time_ms,
                    "profiler.mem_delta_b": report.mem_delta_bytes,
                }
            )
    except Exception as exc:  # noqa: BLE001
        _logger.debug("profiler span attribute set failed: %s", exc)
