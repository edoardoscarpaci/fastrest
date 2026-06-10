"""
varco_core.profiling.backends
==============================
Built-in profiling backend implementations.

Registers ``"cprofile"`` and ``"tracemalloc"`` backends on import so they are
always available when ``varco_core.profiling`` is imported.
"""

from __future__ import annotations

from varco_core.profiling.backend import (
    register_cpu_backend,
    register_memory_backend,
)
from varco_core.profiling.backends.cprofile import CProfileCpuBackend
from varco_core.profiling.backends.tracemalloc_backend import TracemallocMemoryBackend

__all__ = [
    "CProfileCpuBackend",
    "TracemallocMemoryBackend",
]

register_cpu_backend("cprofile", CProfileCpuBackend)
register_memory_backend("tracemalloc", TracemallocMemoryBackend)
