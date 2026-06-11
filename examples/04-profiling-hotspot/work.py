"""
work.py
=======
Synthetic "slow" operations that demonstrate varco's profiling primitives.

This module contains three entry points, each illustrating a different
profiling API surface:

- ``cpu_heavy_work()``   — decorated with ``@profile``; hotspot shows up in
                           the cProfile function table as a tight numeric loop.
- ``memory_work()``      — uses the ``profiled()`` context manager so the
                           caller can inspect ``session.report`` directly.
- ``custom_backend_work()`` — same as ``cpu_heavy_work`` but registered under
                              a custom CPU backend name to show the extension
                              point.

No real I/O — all "work" is synthetic (loops, list allocations) so the
example runs without any external service.

DESIGN: global kill-switch call at module import time
    ✅ Tests call ``set_profiling_enabled(True)`` before importing the app,
       so the decorator is applied with profiling ON.
    ✅ ``work.py`` re-enables profiling on each public function entry so tests
       that toggle the flag don't interfere with each other.
    ❌ ``@profile`` evaluates the kill-switch at decoration time, not per-call.
       Toggling the flag after the decorator is applied has no effect on
       already-decorated functions.  Tests that need profiling on must set
       the flag BEFORE this module is imported — handled by the test fixture.

Thread safety:  ✅ No shared mutable state beyond the global kill-switch.
Async safety:   ✅ All functions are ``async def``; profiling is event-loop safe.
"""

from __future__ import annotations

import asyncio

from varco_core.profiling import (
    CpuProfileResult,
    ProfileConfig,
    profile,
    profiled,
    register_cpu_backend,
    set_profiling_enabled,
)

# ── Custom backend registration ───────────────────────────────────────────────

# A minimal custom CPU backend — counts loop iterations rather than using
# cProfile.  Demonstrates the Protocol-based extension point without any
# third-party dependency.
#
# DESIGN: define the class before registering
#   ✅ Makes it obvious the factory callable (__init__) produces a fresh
#      instance per session — backends are stateful and must not be shared.
#   ✅ No import from varco_core internals — structural compatibility is
#      sufficient (Protocol, not ABC).


class _CountingCpuBackend:
    """
    Minimal custom CPU backend that counts synthetic loop iterations.

    Registered under the name ``"counting"`` to demonstrate
    ``register_cpu_backend``.  Does not use ``cProfile`` or any external
    library — suitable as an always-available test fixture.

    Thread safety:  ⚠️ Single-session; do not reuse across threads.
    Async safety:   ✅ ``start()`` / ``collect()`` are synchronous.
    """

    name = "counting"

    def __init__(self) -> None:
        self._iterations: int = 0

    def start(self) -> None:
        """Reset the iteration counter and begin "profiling"."""
        self._iterations = 0

    def collect(self, top_n: int, sort_by: str) -> CpuProfileResult:
        """
        Return a CpuProfileResult encoding the measured iteration count.

        Args:
            top_n:   Maximum number of top functions to return (unused here).
            sort_by: Sort criterion (unused here — only one synthetic entry).

        Returns:
            A ``CpuProfileResult`` with ``cpu_time_ms`` set to the iteration
            count (as a float, for API compatibility) and no ``top_functions``.
        """
        return CpuProfileResult(
            cpu_time_ms=float(self._iterations),
            top_functions=(),
        )

    def record(self, n: int) -> None:
        """Accumulate ``n`` iterations — called by the work function body."""
        self._iterations += n


# Singleton backend instance used by custom_backend_work() to record
# iterations between start() and collect().  ProfileSession creates a fresh
# backend via the factory, so we store a reference to the active instance via
# a module-level slot updated in the factory.
_active_counting_backend: _CountingCpuBackend | None = None


def _counting_backend_factory() -> _CountingCpuBackend:
    """Factory registered with the profiling registry."""
    global _active_counting_backend
    _active_counting_backend = _CountingCpuBackend()
    return _active_counting_backend


# Guard against duplicate registration when the module is reloaded in tests.
try:
    register_cpu_backend("counting", _counting_backend_factory)
except ValueError:
    pass  # already registered — idempotent in test re-imports


# ── Profiled work functions ───────────────────────────────────────────────────

# Enable profiling before the decorator is evaluated so the wrapper is active.
# Tests that disable profiling (via set_profiling_enabled(False)) will see the
# original function invoked directly — correct behaviour for a kill-switch test.
set_profiling_enabled(True)


@profile(ProfileConfig(top_n=5, cpu=True, memory=True))
async def cpu_heavy_work() -> dict[str, object]:
    """
    Perform a CPU-bound numeric loop and return a summary dict.

    Decorated with ``@profile`` so every invocation emits a profiling report
    to the DEBUG logger.  The ``ProfileConfig`` requests both CPU and memory
    tracking with the top 5 hottest functions.

    Returns:
        A dict with ``result`` (sum of loop) and ``iterations`` count.

    Edge cases:
        - If profiling is globally disabled when this module is imported the
          decorator is a no-op and this function runs without instrumentation.

    Async safety:   ✅ Awaits ``asyncio.sleep(0)`` to yield to the event loop
                       mid-computation — demonstrates cProfile capturing frames
                       across an ``await`` boundary.
    """
    total = 0
    for i in range(50_000):
        total += i * i
        if i % 10_000 == 0:
            await asyncio.sleep(0)
    return {"result": total, "iterations": 50_000}


async def memory_work() -> dict[str, object]:
    """
    Allocate a large list and return a summary via the ``profiled()`` context manager.

    The caller can inspect ``session.report`` after the block exits to read
    raw ``wall_time_ms``, ``mem_delta_bytes``, etc.

    Returns:
        A dict with ``items`` (length of allocated list) and
        ``wall_time_ms`` from the profiling report (or ``None`` when disabled).

    Edge cases:
        - When profiling is disabled, ``profiled()`` returns a ``_NoopSession``
          whose ``.report`` is ``None``.  The function guards against this.

    Async safety:   ✅ Context manager supports ``async with``.
    """
    async with profiled("memory_work") as session:
        data = [object() for _ in range(20_000)]
        await asyncio.sleep(0)

    wall_ms: float | None = session.report.wall_time_ms if session.report else None
    return {"items": len(data), "wall_time_ms": wall_ms}


@profile(ProfileConfig(top_n=3, cpu=True, cpu_backend="counting", memory=False))
async def custom_backend_work() -> dict[str, object]:
    """
    Run a synthetic loop using the custom ``"counting"`` CPU backend.

    Demonstrates plugging a third-party (or project-local) CPU backend via
    ``register_cpu_backend`` without changing any varco_core source.

    Returns:
        A dict with ``result`` and ``backend`` name confirming which backend ran.

    Edge cases:
        - If the ``"counting"`` backend was already registered from a prior
          test run, the ``register_cpu_backend`` guard in this module prevents
          a duplicate ``ValueError``.

    Async safety:   ✅ Awaits inside the loop to exercise multi-coroutine capture.
    """
    total = 0
    for i in range(10_000):
        total += i
        if i % 2_000 == 0:
            await asyncio.sleep(0)
    return {"result": total, "backend": "counting"}


__all__ = [
    "cpu_heavy_work",
    "memory_work",
    "custom_backend_work",
]
