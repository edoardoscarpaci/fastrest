"""
varco_core.cache.singleflight
================================
``Singleflight`` — per-process request coalescer (Plan 010 / C2).

N concurrent misses on the same key produce **one** recompute per process;
every other caller ("follower") awaits the same in-flight future.  This is
the standard mitigation for cache stampedes (Go's ``groupcache``, Cloudflare,
.NET ``HybridCache``, Spring) and is, per research brief 001, absent from
every mature Python async cache library — the gap this closes.

Scope: **per-process only** (D-3).  A ``SingleflightProtocol`` seam is left
for a future distributed (cross-pod) implementation; see Plan 010's Decision
D-3 for the full cost/benefit analysis of why R1 does not ship one.

Shared-instance rule (same class of pitfall as ``CircuitBreaker``/
``Bulkhead`` in CLAUDE.md's pitfall table):  a ``Singleflight`` is
per-cache-namespace state.  A per-call instance coalesces **nothing** — the
in-flight dict is empty on every call.  ``@cached`` creates exactly one
``Singleflight`` per decorated function at decoration time;
``CacheServiceMixin`` creates one per service instance.

Tenant safety (scout landmine):  the coalescing key passed to ``do()`` /
``spawn_refresh()`` MUST already be the final, tenant-namespaced cache key
(the one that went through ``tenancy_cache_key()`` /
``CacheServiceMixin._cache_key()``).  ``Singleflight`` never builds keys
itself — coalescing on a pre-namespaced key would let two tenants share one
recompute, a cross-tenant data leak.

DESIGN: asyncio.Lock created lazily, never in __init__
    ✅ CLAUDE.md rule — locks must be created inside a running event loop.
    ✅ Safe to construct ``Singleflight()`` at module/class-definition time
       (e.g. ``@cached`` decoration), long before any event loop exists.

DESIGN: followers await asyncio.shield(future)
    ✅ The single subtlest correctness point in C2: one caller's own
       ``@timeout``/cancellation must never cancel the shared recompute for
       every other follower.  ``asyncio.shield()`` decouples the follower's
       cancellation from the underlying future.
    ❌ If the *leader* itself is cancelled, the shared future still receives
       ``CancelledError`` and every follower sees it too — documented edge
       case, with a dedicated test.  The slot is cleared so the next caller
       re-elects a leader instead of awaiting a dead future forever.

DESIGN: spawn_refresh() holds a strong reference to its task
    ✅ An untracked ``asyncio.create_task()`` result can be garbage
       collected mid-flight (a well-known asyncio footgun) — the owned
       ``_refresh_tasks`` set plus a done-callback discard prevents this.

Thread safety:  ❌ Not thread-safe — use from a single event loop.
Async safety:   ✅ All public methods are coroutines or trivial properties.

📚 Docs
- 🐍 asyncio.shield — https://docs.python.org/3/library/asyncio-task.html#asyncio.shield
- 🐍 Task GC footgun — https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

_logger = logging.getLogger(__name__)


@runtime_checkable
class SingleflightProtocol(Protocol):
    """
    Structural protocol for a request coalescer.

    Exists so a future distributed implementation (e.g. ``RedisSingleflight``,
    explicitly out of scope for R1 — see Plan 010 Decision D-3) can be
    substituted for the per-process ``Singleflight`` without changing
    ``read_through()``'s call sites.
    """

    async def do(self, key: str, loader: Callable[[], Awaitable[Any]]) -> tuple[Any, bool]: ...

    def spawn_refresh(self, key: str, loader: Callable[[], Awaitable[Any]]) -> None: ...

    async def aclose(self) -> None: ...


class Singleflight:
    """
    Per-process in-flight-call coalescer.

    Args:
        name: Diagnostic label (e.g. surfaced in ``__repr__`` / logs).  Not
              used as a cache namespace — callers must already pass a final,
              tenant-namespaced key to ``do()``/``spawn_refresh()``.

    Thread safety:  ❌ Not thread-safe — one event loop only.
    Async safety:   ✅ ``do()``/``spawn_refresh()``/``aclose()`` are safe to
                       call concurrently from many coroutines on the same
                       loop.
    """

    def __init__(self, *, name: str = "default") -> None:
        self._name = name
        # Lazy — created on first use inside a running loop (CLAUDE.md rule).
        self._lock: asyncio.Lock | None = None
        self._in_flight: dict[str, asyncio.Future[Any]] = {}
        # Strong references to spawn_refresh() tasks — see module DESIGN note.
        self._refresh_tasks: set[asyncio.Task[Any]] = set()

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def in_flight(self) -> int:
        """Number of keys with an active (leader-owned) recompute in flight."""
        return len(self._in_flight)

    async def do(self, key: str, loader: Callable[[], Awaitable[Any]]) -> tuple[Any, bool]:
        """
        Coalesce concurrent calls for ``key``.

        The first caller for a given ``key`` becomes the **leader** — it runs
        ``loader()`` and populates a shared future.  Every subsequent caller
        for the same ``key`` while the leader is still running becomes a
        **follower** — it awaits the same future via ``asyncio.shield()``.

        Args:
            key:    Final, already-namespaced cache key (see the module
                    docstring's tenant-safety note).
            loader: Zero-argument async callable that recomputes the value.

        Returns:
            ``(value, is_leader)`` — ``is_leader`` is ``True`` for the caller
            that actually ran ``loader()``, ``False`` for every follower.
            Callers use this to decide whether to record a
            ``stampede_suppressed`` metric.

        Raises:
            Exception: Whatever ``loader()`` raises — propagated to every
                waiter (leader and followers alike). The in-flight slot is
                cleared so the next call re-elects a leader.
            asyncio.CancelledError: If the leader is cancelled, every
                follower observes it too; the slot is cleared. If a
                *follower* is cancelled, the leader and every other follower
                are unaffected (``asyncio.shield``).
        """
        lock = self._get_lock()
        async with lock:
            future = self._in_flight.get(key)
            is_leader = future is None
            if future is None:
                future = asyncio.get_running_loop().create_future()
                self._in_flight[key] = future

        if is_leader:
            try:
                result = await loader()
            except BaseException as exc:
                async with lock:
                    self._in_flight.pop(key, None)
                if not future.done():
                    future.set_exception(exc)
                raise
            else:
                async with lock:
                    self._in_flight.pop(key, None)
                if not future.done():
                    future.set_result(result)
                return result, True

        # Follower: shield our wait from our OWN cancellation so cancelling
        # this caller never cancels the leader's shared future.
        value = await asyncio.shield(future)
        return value, False

    def spawn_refresh(self, key: str, loader: Callable[[], Awaitable[Any]]) -> None:
        """
        Fire-and-forget a background refresh for ``key`` through the same
        coalescing slot ``do()`` uses — used by the SWR (stale-while-
        revalidate) path so a soft-stale hit and an in-flight refresh close
        the C2×C4 race (see Plan 010's Design section).

        The created task is strongly referenced in ``self._refresh_tasks``
        (discarded via a done-callback) so it cannot be garbage collected
        mid-flight, and is drained by ``aclose()``.

        Args:
            key:    Final, already-namespaced cache key.
            loader: Zero-argument async callable that recomputes the value.

        Edge cases:
            - If a recompute for ``key`` is already in flight (e.g. this is
              itself a follower's arrival), ``do()`` transparently makes this
              call a follower of the same slot — no second recompute starts.
        """

        async def _runner() -> None:
            try:
                await self.do(key, loader)
            except Exception as exc:  # noqa: BLE001 - background task must not crash the loop
                _logger.debug(
                    "Singleflight[%s]: background refresh for %r failed: %s",
                    self._name,
                    key,
                    exc,
                )

        task = asyncio.create_task(_runner())
        self._refresh_tasks.add(task)
        task.add_done_callback(self._refresh_tasks.discard)

    async def aclose(self) -> None:
        """
        Drain all outstanding ``spawn_refresh()`` tasks.

        Waits for every currently-tracked refresh task to finish (success or
        failure — failures are already swallowed and logged by
        ``spawn_refresh()``'s runner). Safe to call with zero outstanding
        tasks. Idempotent.
        """
        tasks = list(self._refresh_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def __repr__(self) -> str:
        return f"Singleflight(name={self._name!r}, in_flight={self.in_flight})"


__all__ = ["Singleflight", "SingleflightProtocol"]
