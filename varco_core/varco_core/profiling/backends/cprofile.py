"""
varco_core.profiling.backends.cprofile
========================================
Built-in CPU profiling backend using the stdlib ``cProfile`` module.

Registered under the name ``"cprofile"`` at package import time.

DESIGN: deterministic profiling via cProfile
    ✅ Zero external dependencies — stdlib only.
    ✅ Per-call statistics with accurate tottime / cumtime.
    ✅ Attaches raw pstats dump as a ``ProfileArtifact`` for offline analysis.
    ❌ Captures *all* Python frames running on the thread, including other
       coroutines that run during ``await`` points.  Not suitable for isolating
       a single coroutine in a busy event loop — use a sampling backend
       (e.g. pyinstrument) for that.
    ❌ Minor (~5–10%) measurement overhead on hot loops.
"""

from __future__ import annotations

import cProfile
import io
import pstats
from typing import TYPE_CHECKING

from varco_core.profiling.backend import (
    CpuProfileResult,
    FunctionStat,
    ProfileArtifact,
)

if TYPE_CHECKING:
    pass


class CProfileCpuBackend:
    """Deterministic CPU profiling backend wrapping ``cProfile.Profile``.

    Thread safety:  ⚠️ One instance per session — do not share.
    Async safety:   ⚠️ ``cProfile`` is process-global; captures all coroutines.
    """

    def __init__(self) -> None:
        self._profiler = cProfile.Profile()

    @property
    def name(self) -> str:
        """Return ``"cprofile"``."""
        return "cprofile"

    def start(self) -> None:
        """Enable the cProfile profiler."""
        self._profiler.enable()

    def collect(self, top_n: int, sort_by: str) -> CpuProfileResult:
        """Disable the profiler and return normalized top-N statistics.

        Args:
            top_n:   Number of top functions to include.
            sort_by: Sort key accepted by ``pstats.Stats.sort_stats``
                     (e.g. ``"cumulative"``, ``"tottime"``).

        Returns:
            A frozen ``CpuProfileResult`` with ``top_functions`` and a raw
            pstats text artifact.
        """
        self._profiler.disable()

        stream = io.StringIO()
        stats = pstats.Stats(self._profiler, stream=stream)
        # pstats tottime is in seconds; we convert to ms
        stats.sort_stats(sort_by)
        stats.print_stats(top_n)
        artifact_text = stream.getvalue()

        # Walk pstats internal dict to extract top_n rows
        top_functions = self._extract_top(stats, top_n, sort_by)

        # Total CPU time = sum of all primitive call tottime
        total_cpu_ms = (
            sum(v[2] for v in stats.stats.values()) * 1000.0 if stats.stats else 0.0
        )

        artifact = ProfileArtifact(
            kind="pstats",
            media_type="text/plain",
            payload=artifact_text,
        )

        return CpuProfileResult(
            cpu_time_ms=total_cpu_ms,
            top_functions=top_functions,
            artifact=artifact,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_top(
        stats: pstats.Stats, top_n: int, sort_by: str
    ) -> tuple[FunctionStat, ...]:
        """Convert pstats internal dict to sorted ``FunctionStat`` tuples.

        Args:
            stats:   A populated ``pstats.Stats`` object.
            top_n:   How many entries to return.
            sort_by: Sort key (``"cumulative"`` → sort by cumtime,
                     ``"tottime"`` → sort by tottime, others → tottime fallback).

        Returns:
            Tuple of up to ``top_n`` ``FunctionStat`` entries, sorted descending.
        """
        if not stats.stats:
            return ()

        # stats.stats keys: (filename, lineno, funcname)
        # values: (cc, nc, tt, ct, callers_dict)  — cc=primitive, nc=total, tt=tot, ct=cum
        rows: list[FunctionStat] = []
        for (filename, lineno, func), (cc, nc, tt, ct, _) in stats.stats.items():
            label = f"{filename}:{lineno}({func})"
            rows.append(
                FunctionStat(
                    function=label,
                    ncalls=nc,
                    tottime_ms=tt * 1000.0,
                    cumtime_ms=ct * 1000.0,
                )
            )

        key_fn = (
            (lambda r: r.cumtime_ms) if "cum" in sort_by else (lambda r: r.tottime_ms)
        )
        rows.sort(key=key_fn, reverse=True)
        return tuple(rows[:top_n])
