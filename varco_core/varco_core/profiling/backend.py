"""
varco_core.profiling.backend
==============================
Backend protocol interfaces and name registry for the profiling system.

This module is the **extensibility seam**.  Adding memray, pyinstrument, or
py-spy support requires:

1. Implement ``CpuProfilerBackend`` or ``MemoryProfilerBackend``.
2. Call ``register_cpu_backend("pyinstrument", MyFactory)`` at import time.
3. Reference the backend by name in ``ProfileConfig(cpu_backend="pyinstrument")``.

No changes to the engine, decorator, or middleware are needed.

DESIGN: Protocol over ABC
    ✅ Third-party backends need not import from varco_core — structural
       compatibility is sufficient.
    ✅ No mandatory base class — easier to wrap existing tools.
    ❌ Type errors on incomplete implementations are caught at runtime, not
       at analysis time.  The engine validates completeness on session open.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    pass

# ── Result primitives ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FunctionStat:
    """Per-function CPU profiling entry.

    Args:
        function: Fully-qualified ``module:lineno(function)`` label from pstats.
        ncalls:   Total number of calls (includes recursive).
        tottime_ms: Time spent in this function alone (excluding callees), ms.
        cumtime_ms: Cumulative time including all callees, ms.
    """

    function: str
    ncalls: int
    tottime_ms: float
    cumtime_ms: float


@dataclass(frozen=True, slots=True)
class AllocationStat:
    """Per-location memory allocation entry.

    Args:
        location: ``filename:lineno`` label from tracemalloc.
        size_bytes: Net bytes allocated at this location.
        count:    Number of allocation blocks.
    """

    location: str
    size_bytes: int
    count: int


@dataclass(frozen=True, slots=True)
class ProfileArtifact:
    """Optional rich native output from a backend (HTML report, flamegraph, …).

    This is the escape hatch that lets richer backends (pyinstrument HTML,
    memray flamegraph) attach their native output without distorting the
    normalized ``top_functions`` / ``top_allocations`` common fields.

    Args:
        kind:       Descriptor string, e.g. ``"html"``, ``"speedscope"``,
                    ``"flamegraph"``, ``"pstats"``.
        media_type: MIME type, e.g. ``"text/html"``, ``"application/json"``.
        payload:    Raw artifact content (str or bytes).
    """

    kind: str
    media_type: str
    payload: str | bytes


@dataclass(frozen=True, slots=True)
class CpuProfileResult:
    """Normalized result returned by a ``CpuProfilerBackend``.

    Args:
        cpu_time_ms:    Total CPU time measured by the backend, in milliseconds.
        top_functions:  Top-N hottest functions (sorted by the backend's policy).
        artifact:       Optional native artifact attached by the backend.
    """

    cpu_time_ms: float
    top_functions: tuple[FunctionStat, ...]
    artifact: ProfileArtifact | None = None


@dataclass(frozen=True, slots=True)
class MemoryProfileResult:
    """Normalized result returned by a ``MemoryProfilerBackend``.

    Args:
        current_bytes:    Traced memory currently in use at session end.
        peak_bytes:       Peak traced memory during the session.
        delta_bytes:      Net change in traced memory over the session (may be negative).
        top_allocations:  Top-N allocation sites by net size.
        artifact:         Optional native artifact attached by the backend.
    """

    current_bytes: int
    peak_bytes: int
    delta_bytes: int
    top_allocations: tuple[AllocationStat, ...]
    artifact: ProfileArtifact | None = None


# ── Backend protocols ─────────────────────────────────────────────────────────


@runtime_checkable
class CpuProfilerBackend(Protocol):
    """Protocol for CPU profiling backends.

    Each ``ProfileSession`` creates a **fresh** instance via the registered
    factory — backends are stateful and must not be shared across sessions.

    Implementations:
        - ``CProfileCpuBackend`` (built-in, registered as ``"cprofile"``)
        - Future: ``PyinstrumentCpuBackend``, ``PySpyCpuBackend``

    Thread safety:  ⚠️ Instances are single-session; do not share across threads.
    Async safety:   ⚠️ ``start()`` / ``collect()`` are synchronous; ``cProfile``
                       captures all coroutines running on the event loop thread.
    """

    def start(self) -> None:
        """Begin CPU profiling.  Called once per session before the target runs."""
        ...

    def collect(self, top_n: int, sort_by: str) -> CpuProfileResult:
        """Stop profiling and return normalized results.

        Args:
            top_n:   How many top functions to include.
            sort_by: Sort key, e.g. ``"cumulative"`` or ``"tottime"``.

        Returns:
            A frozen ``CpuProfileResult`` with normalized stats.
        """
        ...

    @property
    def name(self) -> str:
        """Human-readable backend identifier used in ``ProfileReport.cpu_backend``."""
        ...


@runtime_checkable
class MemoryProfilerBackend(Protocol):
    """Protocol for memory profiling backends.

    Implementations:
        - ``TracemallocMemoryBackend`` (built-in, registered as ``"tracemalloc"``)
        - Future: ``MemrayMemoryBackend``

    Thread safety:  ⚠️ ``tracemalloc`` is process-global — single session at a time.
    Async safety:   ✅ Only called from the engine at session boundaries.
    """

    def start(self) -> None:
        """Begin memory profiling.  Called once per session before the target runs."""
        ...

    def collect(self, top_n: int) -> MemoryProfileResult:
        """Stop profiling and return normalized results.

        Implementations MUST restore any process-global state they modified
        (e.g. tracemalloc enabled state) so the engine leaves the interpreter
        exactly as it found it.

        Args:
            top_n: How many top allocation sites to include.

        Returns:
            A frozen ``MemoryProfileResult`` with normalized stats.
        """
        ...

    @property
    def name(self) -> str:
        """Human-readable backend identifier used in ``ProfileReport.memory_backend``."""
        ...


# ── Registry ─────────────────────────────────────────────────────────────────

_cpu_backends: dict[str, Callable[[], Any]] = {}
_memory_backends: dict[str, Callable[[], Any]] = {}


def register_cpu_backend(name: str, factory: Callable[[], CpuProfilerBackend]) -> None:
    """Register a CPU profiling backend factory under a name.

    Args:
        name:    Lookup key used in ``ProfileConfig(cpu_backend=name)``.
        factory: Zero-arg callable returning a fresh ``CpuProfilerBackend``
                 instance.  Called once per profiling session.

    Raises:
        ValueError: If *name* is already registered (prevents silent shadowing).
    """
    if name in _cpu_backends:
        raise ValueError(f"CPU profiler backend '{name}' is already registered")
    _cpu_backends[name] = factory


def register_memory_backend(
    name: str, factory: Callable[[], MemoryProfilerBackend]
) -> None:
    """Register a memory profiling backend factory under a name.

    Args:
        name:    Lookup key used in ``ProfileConfig(memory_backend=name)``.
        factory: Zero-arg callable returning a fresh ``MemoryProfilerBackend``
                 instance.  Called once per profiling session.

    Raises:
        ValueError: If *name* is already registered.
    """
    if name in _memory_backends:
        raise ValueError(f"Memory profiler backend '{name}' is already registered")
    _memory_backends[name] = factory


def get_cpu_backend(name: str) -> Callable[[], CpuProfilerBackend]:
    """Retrieve a registered CPU backend factory by name.

    Args:
        name: The registered name.

    Returns:
        The factory callable.

    Raises:
        KeyError: If no backend is registered under *name*.
    """
    if name not in _cpu_backends:
        available = list(_cpu_backends)
        raise KeyError(
            f"CPU profiler backend '{name}' is not registered. "
            f"Available: {available}"
        )
    return _cpu_backends[name]


def get_memory_backend(name: str) -> Callable[[], MemoryProfilerBackend]:
    """Retrieve a registered memory backend factory by name.

    Args:
        name: The registered name.

    Returns:
        The factory callable.

    Raises:
        KeyError: If no backend is registered under *name*.
    """
    if name not in _memory_backends:
        available = list(_memory_backends)
        raise KeyError(
            f"Memory profiler backend '{name}' is not registered. "
            f"Available: {available}"
        )
    return _memory_backends[name]


def available_cpu_backends() -> list[str]:
    """Return the list of registered CPU backend names.

    Returns:
        Sorted list of registered CPU backend name strings.
    """
    return sorted(_cpu_backends)


def available_memory_backends() -> list[str]:
    """Return the list of registered memory backend names.

    Returns:
        Sorted list of registered memory backend name strings.
    """
    return sorted(_memory_backends)
