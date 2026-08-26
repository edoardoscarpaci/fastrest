"""
varco_core.profiling.report
=============================
Immutable result objects for the profiling system.

All dataclasses are ``frozen=True, slots=True`` — safe to cache, hash, and
share across threads.  The public re-exports here are the canonical import
location; the primitive types (``FunctionStat``, ``AllocationStat``,
``ProfileArtifact``) live in ``backend.py`` to avoid circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from varco_core.profiling.backend import (
    AllocationStat,
    FunctionStat,
    ProfileArtifact,
)

if TYPE_CHECKING:
    pass

__all__ = [
    "ProfileReport",
    # re-exported for convenience
    "FunctionStat",
    "AllocationStat",
    "ProfileArtifact",
]


@dataclass(frozen=True, slots=True)
class ProfileReport:
    """Immutable diagnostic report produced by a single profiling session.

    Fields reflect the union of CPU and memory results; fields from disabled
    backends contain zero/empty defaults.

    Args:
        name:             Human-readable label for the profiled operation.
        wall_time_ms:     Wall-clock duration of the session in milliseconds.
        cpu_time_ms:      CPU time measured by the CPU backend (ms).
        top_functions:    Top-N hottest functions from the CPU backend.
        mem_current_bytes: Traced memory currently in use at session end.
        mem_peak_bytes:   Peak traced memory during the session.
        mem_delta_bytes:  Net change in traced memory over the session.
        rss_delta_bytes:  Change in process RSS (psutil), or ``None`` if
                          psutil raised (permission error, container limits).
        top_allocations:  Top-N allocation sites from the memory backend.
        artifacts:        Native artifacts attached by backends (pstats dump,
                          HTML report, flamegraph, …).
        cpu_backend:      Name of the CPU backend that produced the report.
        memory_backend:   Name of the memory backend that produced the report.
        captured_at:      UTC timestamp when the session closed.

    Thread safety:  ✅ Frozen dataclass.
    """

    name: str
    wall_time_ms: float
    cpu_time_ms: float

    top_functions: tuple[FunctionStat, ...]
    mem_current_bytes: int
    mem_peak_bytes: int
    mem_delta_bytes: int
    rss_delta_bytes: int | None

    top_allocations: tuple[AllocationStat, ...]
    artifacts: tuple[ProfileArtifact, ...]

    cpu_backend: str
    memory_backend: str
    captured_at: datetime

    # ── Presentation ──────────────────────────────────────────────────────────

    def format(self) -> str:
        """Return a human-readable multi-line diagnostic report.

        Suitable for structured logging or printing to stderr during debugging.

        Returns:
            A formatted string with CPU timing, function table, and memory summary.
        """
        lines: list[str] = [
            f"── ProfileReport: {self.name} ──",
            f"  wall={self.wall_time_ms:.1f}ms  cpu={self.cpu_time_ms:.1f}ms  "
            f"backends={self.cpu_backend}/{self.memory_backend}",
        ]

        if self.top_functions:
            lines.append("  Top functions (cpu):")
            lines.append(f"    {'ncalls':>8}  {'tottime_ms':>12}  {'cumtime_ms':>12}  function")
            for fn in self.top_functions:
                lines.append(
                    f"    {fn.ncalls:>8}  {fn.tottime_ms:>12.3f}  "
                    f"{fn.cumtime_ms:>12.3f}  {fn.function}"
                )

        rss_str = f"{self.rss_delta_bytes:+,}B" if self.rss_delta_bytes is not None else "n/a"
        lines.append(
            f"  Memory: delta={self.mem_delta_bytes:+,}B  "
            f"peak={self.mem_peak_bytes:,}B  rss_delta={rss_str}"
        )

        if self.top_allocations:
            lines.append("  Top allocations:")
            for alloc in self.top_allocations[:5]:
                lines.append(f"    {alloc.size_bytes:>+12,}B  x{alloc.count}  {alloc.location}")

        if self.artifacts:
            kinds = ", ".join(a.kind for a in self.artifacts)
            lines.append(f"  Artifacts: {kinds}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict representation.

        Returns:
            A plain dict suitable for structured logging or HTTP responses.
        """
        return {
            "name": self.name,
            "wall_time_ms": self.wall_time_ms,
            "cpu_time_ms": self.cpu_time_ms,
            "cpu_backend": self.cpu_backend,
            "memory_backend": self.memory_backend,
            "captured_at": self.captured_at.isoformat(),
            "top_functions": [
                {
                    "function": fn.function,
                    "ncalls": fn.ncalls,
                    "tottime_ms": fn.tottime_ms,
                    "cumtime_ms": fn.cumtime_ms,
                }
                for fn in self.top_functions
            ],
            "memory": {
                "current_bytes": self.mem_current_bytes,
                "peak_bytes": self.mem_peak_bytes,
                "delta_bytes": self.mem_delta_bytes,
                "rss_delta_bytes": self.rss_delta_bytes,
            },
            "top_allocations": [
                {
                    "location": a.location,
                    "size_bytes": a.size_bytes,
                    "count": a.count,
                }
                for a in self.top_allocations
            ],
            "artifacts": [
                {"kind": art.kind, "media_type": art.media_type} for art in self.artifacts
            ],
        }

    def __str__(self) -> str:
        return self.format()


def _empty_report(name: str) -> ProfileReport:
    """Return a zeroed ``ProfileReport`` for disabled/noop sessions.

    Args:
        name: Label for the profiled operation.

    Returns:
        A ``ProfileReport`` with all numeric fields at zero and empty tuples.
    """
    return ProfileReport(
        name=name,
        wall_time_ms=0.0,
        cpu_time_ms=0.0,
        top_functions=(),
        mem_current_bytes=0,
        mem_peak_bytes=0,
        mem_delta_bytes=0,
        rss_delta_bytes=None,
        top_allocations=(),
        artifacts=(),
        cpu_backend="none",
        memory_backend="none",
        captured_at=datetime.now(UTC),
    )
