"""
varco_core.tenancy.fanout
============================
``TenantFanoutSupervisor`` — one supervised child (``OutboxRelay``, job
poller, audit consumer) per active, pool-resident tenant (Plan 007, Phase 8,
RD-8).

See the plan's "DESIGN: TenantFanoutSupervisor — one relay per tenant vs one
loop over tenants" section for the full rationale. Summary: reuses
``OutboxRelay``/``JobPoller``/``AuditConsumer`` verbatim; each child's
supervising task is independent, so one tenant's crash never blocks
another's; children are bounded by the same ``max_entries`` the resource
pool already enforces; first ticks are staggered to avoid a connection/query
thundering herd.

Contract: ``start()``/``stop()`` (lifespan-driven, LIFO), ``on_tenant_
activated(tid)``/``on_tenant_deactivated(tid)`` driven by
``TenantCatalogChanged`` and pool eviction, ``aclose()`` awaiting every
child's ``stop()``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

_INITIAL_BACKOFF_S = 0.05
_MAX_BACKOFF_S = 5.0
_STAGGER_STEP_S = 0.01


class TenantFanoutSupervisor:
    """
    Owns one child (any object exposing async ``start()``/``stop()``) per
    active, pool-resident tenant.

    Args:
        child_factory: ``Callable[[tenant_id], child]`` — builds one child
                       per tenant (e.g. a per-tenant ``OutboxRelay``).
        max_entries:   Soft cap mirroring ``TenantResourcePool.max_entries``
                       — children are bounded by the same knob as the
                       resource pool that backs them (RD-8: "no second
                       bound").
        enabled:       ``TenancySettings.fanout_framework_tables`` — when
                       ``False`` (the default), the supervisor starts
                       nothing at all, even if tenants are activated.

    Edge cases:
        - A child whose ``start()`` raises is restarted with capped
          exponential backoff; other children's supervising tasks are
          entirely independent asyncio Tasks, so one child's failure never
          blocks another's.
        - ``stop()`` awaits every child LIFO and is idempotent.
        - Children are staggered on ``start()`` (tenant *i*'s first attempt
          delayed by ``i * _STAGGER_STEP_S``) to avoid a startup thundering
          herd.
    """

    def __init__(
        self,
        *,
        child_factory: Callable[[str], Any],
        max_entries: int = 50,
        enabled: bool = True,
    ) -> None:
        self._child_factory = child_factory
        self._max_entries = max_entries
        self._enabled = enabled

        self._children: dict[str, Any] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._order: list[str] = []
        self._started = False
        self._stopped = False
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def active_tenant_count(self) -> int:
        """Number of tenants with a currently-owned child."""
        return len(self._children)

    async def on_tenant_activated(self, tenant_id: str) -> None:
        """
        Register a child for ``tenant_id``, subject to ``max_entries``.

        A no-op when the supervisor is disabled, the tenant already has a
        child, or the cap has been reached (logged with one WARNING).
        """
        if not self._enabled:
            return

        async with self._get_lock():
            if tenant_id in self._children:
                return
            if len(self._children) >= self._max_entries:
                logger.warning(
                    "TenantFanoutSupervisor: max_entries=%s reached — "
                    "not starting a fan-out child for tenant %r.",
                    self._max_entries,
                    tenant_id,
                )
                return
            child = self._child_factory(tenant_id)
            self._children[tenant_id] = child
            self._order.append(tenant_id)

        if self._started:
            self._spawn(tenant_id, child, stagger_index=0)

    async def on_tenant_deactivated(self, tenant_id: str) -> None:
        """Stop and remove ``tenant_id``'s child, if any."""
        async with self._get_lock():
            child = self._children.pop(tenant_id, None)
            if tenant_id in self._order:
                self._order.remove(tenant_id)
            task = self._tasks.pop(tenant_id, None)

        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        if child is not None:
            await self._safe_stop(tenant_id, child)

    async def start(self) -> None:
        """Spawn a supervising task for every currently-registered tenant."""
        if self._started or not self._enabled:
            self._started = True
            return
        self._started = True
        for index, tenant_id in enumerate(list(self._order)):
            child = self._children[tenant_id]
            self._spawn(tenant_id, child, stagger_index=index)

    def _spawn(self, tenant_id: str, child: Any, *, stagger_index: int) -> None:
        task = asyncio.create_task(self._run_child(tenant_id, child, stagger_index))
        self._tasks[tenant_id] = task

    async def _run_child(self, tenant_id: str, child: Any, stagger_index: int) -> None:
        if stagger_index:
            await asyncio.sleep(stagger_index * _STAGGER_STEP_S)

        backoff = _INITIAL_BACKOFF_S
        while True:
            try:
                await child.start()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - failure isolation contract
                logger.error(
                    "TenantFanoutSupervisor: child for tenant %r failed to "
                    "start — retrying in %.2fs: %s",
                    tenant_id,
                    backoff,
                    exc,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_S)

    async def _safe_stop(self, tenant_id: str, child: Any) -> None:
        stop = getattr(child, "stop", None)
        if stop is None:
            return
        try:
            await stop()
        except Exception:  # noqa: BLE001 - must never raise
            logger.exception(
                "TenantFanoutSupervisor: child.stop() raised for tenant %r.", tenant_id
            )

    async def stop(self) -> None:
        """Stop every child LIFO. Idempotent."""
        if self._stopped:
            return
        self._stopped = True

        for tenant_id in reversed(self._order):
            task = self._tasks.pop(tenant_id, None)
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            child = self._children.get(tenant_id)
            if child is not None:
                await self._safe_stop(tenant_id, child)

        self._children.clear()
        self._order.clear()
        self._tasks.clear()

    async def aclose(self) -> None:
        """Alias for ``stop()`` — lifecycle-component naming symmetry."""
        await self.stop()
