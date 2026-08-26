"""
varco_core.profiling.config
=============================
Immutable configuration for the profiling system.

``ProfileConfig`` mirrors the frozen-dataclass pattern used by ``RetryPolicy``
and ``SpanConfig``:  all fields have sensible defaults, ``__post_init__``
validates them, and instances can be shared safely across threads.

Global kill-switch
------------------
``set_profiling_enabled(True/False)`` controls whether the ``@profile``
decorator and ``profiled()`` context manager actually do work.  When disabled,
``@profile`` returns the **original function unwrapped** and ``profiled()`` is a
no-op — callers pay exactly zero overhead.

The flag is seeded from the ``VARCO_PROFILING_ENABLED`` environment variable
(default ``False``) so production deployments are safe by default.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    pass

# ── Global kill-switch ────────────────────────────────────────────────────────

_profiling_enabled: bool = os.environ.get("VARCO_PROFILING_ENABLED", "").lower() in (
    "1",
    "true",
    "yes",
)


def is_profiling_enabled() -> bool:
    """Return whether profiling is globally enabled.

    Returns:
        ``True`` if profiling will actually execute when ``@profile`` or
        ``profiled()`` is used.
    """
    return _profiling_enabled


def set_profiling_enabled(enabled: bool) -> None:
    """Enable or disable profiling globally.

    When disabled, ``@profile`` returns the original function unwrapped
    and ``profiled()`` context managers are no-ops.

    Args:
        enabled: ``True`` to activate profiling, ``False`` to disable it.
    """
    global _profiling_enabled  # noqa: PLW0603
    _profiling_enabled = enabled


# ── ProfileConfig ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProfileConfig:
    """Immutable configuration for a profiling session.

    Backends are specified by **name** (resolved via the registry) or by
    a **zero-arg factory callable** (used directly).  A frozen config stays
    safely reusable; each ``ProfileSession`` builds its own fresh backend
    instances from the factory.

    Example::

        # Default cProfile + tracemalloc
        cfg = ProfileConfig()

        # Memory only, top 20 allocation sites
        cfg = ProfileConfig(cpu=False, top_n=20)

        # Swap in a future pyinstrument backend by name
        cfg = ProfileConfig(cpu_backend="pyinstrument")

        # Or supply an inline factory
        cfg = ProfileConfig(cpu_backend=lambda: MyCustomBackend())

    Args:
        cpu:            Enable CPU profiling (default ``True``).
        memory:         Enable memory profiling (default ``True``).
        track_rss:      Record process RSS delta via psutil (default ``True``).
        cpu_backend:    CPU backend name (registry lookup) or factory callable.
                        Defaults to ``"cprofile"``.
        memory_backend: Memory backend name or factory callable.
                        Defaults to ``"tracemalloc"``.
        top_n:          Number of top functions / allocation sites to include
                        in the report (default ``15``).
        sort_by:        CPU sort key — ``"cumulative"`` (default) or ``"tottime"``.
        otel:           Emit summary stats to OpenTelemetry on session close
                        (default ``False``).
        logger_name:    Logger name for structured report output.  ``None`` uses
                        ``varco_core.profiling``.

    Thread safety:  ✅ Frozen dataclass — safe to share across threads.
    Async safety:   ✅ Read-only after construction.
    """

    cpu: bool = True
    memory: bool = True
    track_rss: bool = True

    cpu_backend: str | Callable[[], Any] = "cprofile"
    memory_backend: str | Callable[[], Any] = "tracemalloc"

    top_n: int = 15
    sort_by: Literal["cumulative", "tottime"] = "cumulative"

    otel: bool = False
    logger_name: str | None = None

    def __post_init__(self) -> None:
        """Validate field values.

        Raises:
            ValueError: If ``top_n`` is not positive, or ``sort_by`` is invalid.
        """
        if self.top_n < 1:
            raise ValueError(f"top_n must be >= 1, got {self.top_n}")
        if self.sort_by not in ("cumulative", "tottime"):
            raise ValueError(
                f"sort_by must be 'cumulative' or 'tottime', got '{self.sort_by}'"
            )
