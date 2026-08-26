"""
varco_core.profiling.decorator
================================
``@profile`` decorator and ``profiled()`` context-manager factory.

Both are the public entry points for on-demand profiling.  They respect the
global kill-switch: when profiling is disabled, ``@profile`` returns the
original function **unchanged** (identity, zero overhead) and ``profiled()``
yields a no-op session.

Usage::

    from varco_core.profiling import profile, profiled, ProfileConfig, set_profiling_enabled

    set_profiling_enabled(True)

    # Decorator — async
    @profile()
    async def fetch_orders() -> list[Order]:
        ...

    # Decorator — sync
    @profile(ProfileConfig(top_n=5), name="heavy-compute")
    def crunch_numbers() -> float:
        ...

    # Context manager — async
    async with profiled("process_batch") as session:
        await do_work()
    print(session.report)

    # Context manager — sync
    with profiled("load_config") as session:
        cfg = load()
    print(session.report)

DESIGN: detection at decoration time, not call time
    ✅ Follows the established resilience module pattern.
    ✅ The wrapper's type signature is correct (async fn → async wrapper).
    ✅ Zero overhead on the call path when profiling is disabled.
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any, ParamSpec, TypeVar
from collections.abc import Callable

from varco_core.profiling.config import ProfileConfig, is_profiling_enabled
from varco_core.profiling.engine import ProfileSession

_P = ParamSpec("_P")
_R = TypeVar("_R")

# ── @profile ──────────────────────────────────────────────────────────────────


def profile(
    config: ProfileConfig | None = None,
    *,
    name: str | None = None,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Decorator that profiles each call to the wrapped function.

    Works on both ``def`` and ``async def`` functions.  Detection happens at
    decoration time — the wrapper is correctly typed as sync or async.

    When profiling is globally disabled (``set_profiling_enabled(False)``), the
    original function is returned **unwrapped** — no overhead on the hot path.

    Each invocation opens a fresh ``ProfileSession``; the completed report is
    logged at ``DEBUG`` level via ``varco_core.profiling`` logger.

    Args:
        config: ``ProfileConfig`` controlling backends, top_n, etc.  Defaults
                to ``ProfileConfig()`` (both CPU + memory, cProfile + tracemalloc).
        name:   Label used in the ``ProfileReport``.  Defaults to the function's
                ``__qualname__``.

    Returns:
        A decorator that wraps the function with profiling.

    Example::

        @profile()
        async def slow_query() -> list[Row]:
            ...

        @profile(ProfileConfig(cpu=False, top_n=20))
        def allocate_big_buffer() -> bytes:
            ...
    """
    _config = config or ProfileConfig()

    def decorator(func: Callable[_P, _R]) -> Callable[_P, _R]:
        # When globally disabled, return the original function untouched.
        if not is_profiling_enabled():
            return func

        op_name = name or func.__qualname__

        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                async with ProfileSession(op_name, _config):
                    return await func(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with ProfileSession(op_name, _config):
                return func(*args, **kwargs)

        return sync_wrapper  # type: ignore[return-value]

    return decorator


# ── profiled() ────────────────────────────────────────────────────────────────


def profiled(
    name: str,
    *,
    config: ProfileConfig | None = None,
) -> ProfileSession:
    """Return a ``ProfileSession`` for use as a ``with`` or ``async with`` block.

    The caller can access ``session.report`` after the block exits for detailed
    introspection.

    When profiling is globally disabled, returns a **no-op** session that
    does nothing and whose ``.report`` remains ``None``.

    Args:
        name:   Label for the profiled operation.
        config: ``ProfileConfig``.  Defaults to ``ProfileConfig()``.

    Returns:
        A ``ProfileSession`` instance (use as a context manager).

    Example::

        async with profiled("load_users") as session:
            users = await db.fetch_all()
        if session.report:
            logger.info(session.report.format())
    """
    if not is_profiling_enabled():
        return _NoopSession(name)  # type: ignore[return-value]
    return ProfileSession(name, config or ProfileConfig())


# ── No-op session ─────────────────────────────────────────────────────────────


class _NoopSession:
    """A no-op context manager returned when profiling is globally disabled.

    Implements both sync and async context manager protocols.  ``.report`` is
    always ``None``.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self.report = None

    def __enter__(self) -> _NoopSession:
        return self

    def __exit__(self, *_: Any) -> None:
        pass

    async def __aenter__(self) -> _NoopSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass
