"""
varco_core.profiling
======================
Diagnostic CPU and memory profiler for varco applications.

Fills the gap left by the aggregate OTel observability layer (spans/metrics
answer "how slow on average") by providing deep per-call introspection:
**which function is hot** and **what allocated this memory**.

Quick start::

    from varco_core.profiling import profile, profiled, set_profiling_enabled

    # Enable globally (or set VARCO_PROFILING_ENABLED=true in env)
    set_profiling_enabled(True)

    # Decorator
    @profile()
    async def fetch_data() -> list[Row]:
        ...

    # Context manager
    async with profiled("batch_job") as session:
        await process_batch()
    print(session.report)

Off by default
--------------
When ``is_profiling_enabled()`` returns ``False``, ``@profile`` returns the
original function **untouched** and ``profiled()`` is a no-op — zero overhead.

Extensibility
-------------
CPU and memory collection are backed by pluggable protocols.  To integrate
memray, pyinstrument, or py-spy::

    from varco_core.profiling import (
        CpuProfilerBackend,
        register_cpu_backend,
    )

    class MyBackend:
        name = "pyinstrument"
        def start(self) -> None: ...
        def collect(self, top_n, sort_by): ...

    register_cpu_backend("pyinstrument", MyBackend)

    # Then select it by name:
    cfg = ProfileConfig(cpu_backend="pyinstrument")

Built-in backends registered on import: ``"cprofile"``, ``"tracemalloc"``.
"""

from __future__ import annotations

# Ensure built-in backends are registered when the package is imported
import varco_core.profiling.backends  # noqa: F401
from varco_core.profiling.backend import (
    AllocationStat,
    CpuProfilerBackend,
    CpuProfileResult,
    FunctionStat,
    MemoryProfilerBackend,
    MemoryProfileResult,
    ProfileArtifact,
    available_cpu_backends,
    available_memory_backends,
    get_cpu_backend,
    get_memory_backend,
    register_cpu_backend,
    register_memory_backend,
)
from varco_core.profiling.config import (
    ProfileConfig,
    is_profiling_enabled,
    set_profiling_enabled,
)
from varco_core.profiling.decorator import profile, profiled
from varco_core.profiling.engine import ProfileSession
from varco_core.profiling.otel import emit_to_otel
from varco_core.profiling.report import ProfileReport

__all__ = [
    # ── Core primitives ────────────────────────────────────────────────────────
    "profile",
    "profiled",
    "ProfileSession",
    "ProfileConfig",
    "ProfileReport",
    # ── Report value objects ───────────────────────────────────────────────────
    "FunctionStat",
    "AllocationStat",
    "ProfileArtifact",
    # ── Global kill-switch ─────────────────────────────────────────────────────
    "is_profiling_enabled",
    "set_profiling_enabled",
    # ── OTel bridge ────────────────────────────────────────────────────────────
    "emit_to_otel",
    # ── Extensibility surface ──────────────────────────────────────────────────
    "CpuProfilerBackend",
    "MemoryProfilerBackend",
    "CpuProfileResult",
    "MemoryProfileResult",
    "register_cpu_backend",
    "register_memory_backend",
    "get_cpu_backend",
    "get_memory_backend",
    "available_cpu_backends",
    "available_memory_backends",
]
