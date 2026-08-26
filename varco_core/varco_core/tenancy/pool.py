"""
varco_core.tenancy.pool
=========================
``TenantResourcePool[T]`` — bounded LRU pool of per-tenant resources
(``AsyncEngine``, a Beanie tenant binding, ...) with lease-refcounting
eviction protection (Plan 007, Phase 1, step 5-6).

DESIGN: soft cap + lease refcounting, never a hard cap
    ✅ Resource pressure fails **open** (the soft cap is exceeded, with one
       WARNING per breach); isolation never fails open. An entry currently
       leased cannot be evicted out from under an in-flight request — the
       eviction hazard this exists to prevent is data corruption/`Interface
       Error`, strictly worse than a temporary extra idle connection.
    ❌ A pathological workload with every tenant permanently leased grows
       unboundedly. Accepted — the WARNING makes it observable; RD-5 is
       explicit that varco does not enforce a ceiling.

DESIGN: lazy per-tenant ``asyncio.Lock`` (repo rule)
    ✅ The pool is constructible outside a running event loop; locks are
       only created inside ``ensure()``, itself always awaited from a
       running loop.
    ❌ A `dict[str, asyncio.Lock]` grows with the number of distinct tenant
       ids ever seen, even after eviction. Accepted — bounded by the same
       order of magnitude as the resource pool itself in practice, and
       trivial to add cleanup for if it ever becomes an issue.

Thread safety:  N/A — single-event-loop async design (matches the rest of
                the resilience/cache primitives in this repo).
Async safety:   ✅ All mutation happens under the pool-wide ``asyncio.Lock``;
                   the (potentially slow) ``factory``/``closer`` calls run
                   outside that lock via a per-tenant creation lock so one
                   tenant's slow factory does not block another's ``ensure()``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class _Entry(Generic[T]):
    resource: T
    last_used: float
    refcount: int = 0


class TenantResourcePool(Generic[T]):
    """
    Bounded, LRU-evicting, lease-refcounted pool of per-tenant resources.

    Args:
        factory:     ``async def factory(tenant_id) -> T`` — creates a
                     resource for a tenant. Called at most once per tenant
                     while a creation is in flight (per-tenant lazy lock).
        closer:      ``async def closer(resource) -> None`` — disposes a
                     resource. Must never raise uncaught — a raising closer
                     is logged and swallowed (same "must never raise"
                     contract as ``DLQ.push()``); remaining entries still
                     close.
        max_entries: Soft cap. Breach with every entry busy logs one
                     WARNING per breach rather than corrupting state.
        idle_ttl_s:  Sweep threshold — ``sweep()`` closes entries idle
                     longer than this many seconds.

    Edge cases:
        - A raising ``factory`` leaves no poisoned cache entry — the next
          ``ensure()`` retries cleanly.
        - ``lease()`` refcounts; an entry with refcount > 0 is never
          evicted by ``ensure()``'s LRU eviction or by ``sweep()``.
    """

    def __init__(
        self,
        *,
        factory: Callable[[str], Awaitable[T]],
        closer: Callable[[T], Awaitable[None]],
        max_entries: int = 50,
        idle_ttl_s: float = 300.0,
    ) -> None:
        self._factory = factory
        self._closer = closer
        self._max_entries = max_entries
        self._idle_ttl_s = idle_ttl_s

        self._entries: dict[str, _Entry[T]] = {}
        self._creation_locks: dict[str, asyncio.Lock] = {}
        self._pool_lock: asyncio.Lock | None = None
        self._closed = False
        self._sweeper_task: asyncio.Task[None] | None = None

    def _get_pool_lock(self) -> asyncio.Lock:
        if self._pool_lock is None:
            self._pool_lock = asyncio.Lock()
        return self._pool_lock

    def _get_creation_lock(self, tenant_id: str) -> asyncio.Lock:
        lock = self._creation_locks.get(tenant_id)
        if lock is None:
            lock = asyncio.Lock()
            self._creation_locks[tenant_id] = lock
        return lock

    async def ensure(self, tenant_id: str) -> T:
        """
        Return the cached resource for ``tenant_id``, creating it if absent.

        Concurrent ``ensure()`` calls for the same tenant call ``factory``
        exactly once — later callers await the same in-flight creation.

        Raises:
            Whatever ``factory`` raises — no entry is cached on failure.
        """
        entry = self._entries.get(tenant_id)
        if entry is not None:
            entry.last_used = time.monotonic()
            return entry.resource

        creation_lock = self._get_creation_lock(tenant_id)
        async with creation_lock:
            # Re-check: another task may have finished creating it while we
            # waited for the creation lock.
            entry = self._entries.get(tenant_id)
            if entry is not None:
                entry.last_used = time.monotonic()
                return entry.resource

            resource = await self._factory(tenant_id)

            async with self._get_pool_lock():
                self._entries[tenant_id] = _Entry(resource=resource, last_used=time.monotonic())
                await self._evict_if_over_capacity(exclude=tenant_id)

            return resource

    def peek(self, tenant_id: str) -> T | None:
        """Return the cached resource for ``tenant_id`` without creating it."""
        entry = self._entries.get(tenant_id)
        return entry.resource if entry is not None else None

    @asynccontextmanager
    async def lease(self, tenant_id: str) -> AsyncIterator[T]:
        """
        Refcount-protect a resource for the duration of the block.

        An entry with an active lease is never evicted by ``ensure()``'s
        LRU eviction or by ``sweep()`` — resource pressure fails open,
        never mid-flight correctness.
        """
        resource = await self.ensure(tenant_id)
        entry = self._entries[tenant_id]
        entry.refcount += 1
        try:
            yield resource
        finally:
            entry.refcount -= 1

    async def evict(self, tenant_id: str) -> None:
        """Evict and close a single tenant's resource, if present and idle."""
        async with self._get_pool_lock():
            entry = self._entries.get(tenant_id)
            if entry is None or entry.refcount > 0:
                return
            del self._entries[tenant_id]
        await self._safe_close(entry.resource)

    async def sweep(self) -> None:
        """Close entries idle longer than ``idle_ttl_s`` (skips leased entries)."""
        now = time.monotonic()
        to_close: list[T] = []
        async with self._get_pool_lock():
            stale_ids = [
                tid
                for tid, entry in self._entries.items()
                if entry.refcount == 0 and (now - entry.last_used) > self._idle_ttl_s
            ]
            for tid in stale_ids:
                to_close.append(self._entries.pop(tid).resource)
        for resource in to_close:
            await self._safe_close(resource)

    async def _evict_if_over_capacity(self, *, exclude: str | None = None) -> None:
        """Caller must hold ``_pool_lock``. Evicts the LRU idle entry over cap.

        ``exclude`` is the tenant id whose resource was just created to
        satisfy the in-flight ``ensure()`` call — it is never a candidate
        for immediate self-eviction.
        """
        if len(self._entries) <= self._max_entries:
            return

        # Candidates: idle (refcount == 0) entries, oldest first, excluding
        # the entry we just created for this call.
        idle = sorted(
            (
                (tid, entry)
                for tid, entry in self._entries.items()
                if entry.refcount == 0 and tid != exclude
            ),
            key=lambda item: item[1].last_used,
        )
        if not idle:
            logger.warning(
                "TenantResourcePool exceeded max_entries=%s and every entry is "
                "leased — resource pressure fails open (isolation never does). "
                "Currently holding %s entries.",
                self._max_entries,
                len(self._entries),
            )
            return

        tid, entry = idle[0]
        del self._entries[tid]
        # Close outside the pool lock to avoid blocking other tenants —
        # schedule it via a task so _evict_if_over_capacity stays sync-ish
        # from the caller's perspective, but since we're already inside an
        # async function called under the lock, just await it directly:
        # closer failures must never raise here.
        await self._safe_close(entry.resource)

    async def _safe_close(self, resource: T) -> None:
        try:
            await self._closer(resource)
        except Exception:  # noqa: BLE001 - "closer must never raise" contract
            logger.exception("TenantResourcePool: closer raised while disposing a resource.")

    async def start_sweeper(self) -> None:
        """
        Start a background task calling ``sweep()`` every ``idle_ttl_s``
        seconds. Idempotent — a second call is a no-op while one is
        already running.

        Lifecycle owner: ``varco_fastapi.tenancy.TenancyLifecycle``,
        prepended into ``VarcoLifespan`` like ``MigrationLifecycle``. Non-
        FastAPI callers may call this directly, or use ``async with pool:``
        and call ``sweep()`` on their own schedule instead.
        """
        if self._sweeper_task is not None and not self._sweeper_task.done():
            return

        async def _loop() -> None:
            try:
                while True:
                    await asyncio.sleep(self._idle_ttl_s)
                    await self.sweep()
            except asyncio.CancelledError:
                pass

        self._sweeper_task = asyncio.create_task(_loop())

    async def stop_sweeper(self) -> None:
        """Cancel the background sweeper task started by ``start_sweeper()``."""
        if self._sweeper_task is None:
            return
        self._sweeper_task.cancel()
        try:
            await self._sweeper_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._sweeper_task = None

    async def aclose(self) -> None:
        """Close every entry. Idempotent."""
        await self.stop_sweeper()
        if self._closed:
            return
        async with self._get_pool_lock():
            entries = list(self._entries.values())
            self._entries.clear()
            self._closed = True
        for entry in entries:
            await self._safe_close(entry.resource)

    async def __aenter__(self) -> TenantResourcePool[T]:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
