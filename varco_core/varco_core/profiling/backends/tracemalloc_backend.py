"""
varco_core.profiling.backends.tracemalloc
==========================================
Built-in memory profiling backend using the stdlib ``tracemalloc`` module.

Registered under the name ``"tracemalloc"`` at package import time.

DESIGN: tracemalloc snapshot diffing
    ✅ Zero external dependencies — stdlib only.
    ✅ Per-allocation-site statistics with file/line granularity.
    ✅ Restores prior tracemalloc state on exit — safe to use inside apps
       that already enable tracemalloc for their own purposes.
    ❌ Only tracks Python-level allocations; C extensions allocating memory
       directly (e.g. numpy) are invisible.
    ❌ 20–100% memory overhead when tracemalloc is running — not for prod.
"""

from __future__ import annotations

import tracemalloc
from typing import TYPE_CHECKING

from varco_core.profiling.backend import (
    AllocationStat,
    MemoryProfileResult,
)

if TYPE_CHECKING:
    pass


class TracemallocMemoryBackend:
    """Memory profiling backend wrapping ``tracemalloc``.

    Restores the prior tracemalloc enabled/disabled state on ``collect()``
    so the session leaves the interpreter exactly as it found it.

    Thread safety:  ⚠️ ``tracemalloc`` is process-global — one session at a time.
    Async safety:   ✅ Only called from the engine at session boundaries.
    """

    def __init__(self) -> None:
        self._was_tracing: bool = False
        self._snapshot_before: tracemalloc.Snapshot | None = None

    @property
    def name(self) -> str:
        """Return ``"tracemalloc"``."""
        return "tracemalloc"

    def start(self) -> None:
        """Take a baseline snapshot, starting tracemalloc if not already running."""
        self._was_tracing = tracemalloc.is_tracing()
        if not self._was_tracing:
            tracemalloc.start()
        self._snapshot_before = tracemalloc.take_snapshot()

    def collect(self, top_n: int) -> MemoryProfileResult:
        """Take a final snapshot, compute diff, and restore tracemalloc state.

        Args:
            top_n: Number of top allocation sites to include.

        Returns:
            A frozen ``MemoryProfileResult`` with delta, peak, and top sites.
        """
        snapshot_after = tracemalloc.take_snapshot()
        current_bytes, peak_bytes = tracemalloc.get_traced_memory()

        if not self._was_tracing:
            tracemalloc.stop()

        # Diff the two snapshots at file:lineno granularity
        top_allocations = self._diff_snapshots(
            self._snapshot_before, snapshot_after, top_n
        )

        delta_bytes = sum(s.size_bytes for s in top_allocations)

        return MemoryProfileResult(
            current_bytes=current_bytes,
            peak_bytes=peak_bytes,
            delta_bytes=delta_bytes,
            top_allocations=top_allocations,
            artifact=None,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _diff_snapshots(
        before: tracemalloc.Snapshot | None,
        after: tracemalloc.Snapshot,
        top_n: int,
    ) -> tuple[AllocationStat, ...]:
        """Compute per-location allocation delta between two snapshots.

        Args:
            before: Baseline snapshot (may be None if tracemalloc was just started).
            after:  Final snapshot.
            top_n:  How many top sites to return.

        Returns:
            Tuple of up to ``top_n`` ``AllocationStat`` entries sorted by size desc.
        """
        stats: list[tracemalloc.Statistic] | list[tracemalloc.StatisticDiff]
        if before is None:
            stats = after.statistics("lineno")
        else:
            stats = after.compare_to(before, "lineno")

        rows: list[AllocationStat] = []
        for stat in stats[:top_n]:
            frame = stat.traceback[0] if stat.traceback else None
            if frame:
                location = f"{frame.filename}:{frame.lineno}"
            else:
                location = "<unknown>"

            size = getattr(stat, "size_diff", None)
            if size is None:
                size = stat.size
            count = getattr(stat, "count_diff", None)
            if count is None:
                count = stat.count

            rows.append(
                AllocationStat(
                    location=location,
                    size_bytes=size,
                    count=count,
                )
            )

        return tuple(rows)
