"""
varco_fastapi.tenancy.lifecycle
==================================
``TenancyLifecycle`` — starts the resource-pool sweeper and (optionally)
the ``TenantFanoutSupervisor``, prepended into ``VarcoLifespan`` like
``MigrationLifecycle`` (Plan 007, Phase 10, step 1-2).

DESIGN: ``stop()`` order — supervisor before pool
    A relay outlives its engine if the pool is closed first — ``stop()``
    always awaits the supervisor **before** ``pool.aclose()`` (asserted by
    call order), the same "consumers stop before the service itself" rule
    ``VarcoLifespan`` already applies at the component level, applied here
    within a single component's own two owned resources.
"""

from __future__ import annotations

from typing import Any


class TenancyLifecycle:
    """
    ``AbstractLifecycle``-shaped component: starts the pool's sweeper and
    (if given) the fan-out supervisor; stops them in reverse order.

    Args:
        pool:       A ``TenantResourcePool``-shaped object — ``start_
                    sweeper()`` and ``aclose()``.
        supervisor: Optional ``TenantFanoutSupervisor``-shaped object —
                    ``start()``/``stop()``. ``None`` (default) — no
                    fan-out wired (``TenancySettings.fanout_framework_
                    tables=False``, the default).
        catalog_subscription: Optional callable invoked at ``start()`` to
                    wire ``TenantCatalogChanged`` -> cache invalidation
                    (e.g. ``consumer.register_to(bus)``). ``None`` — no
                    subscription wired.
    """

    def __init__(
        self,
        *,
        pool: Any,
        supervisor: Any | None = None,
        catalog_subscription: Any | None = None,
    ) -> None:
        self._pool = pool
        self._supervisor = supervisor
        self._catalog_subscription = catalog_subscription

    async def start(self) -> None:
        await self._pool.start_sweeper()
        if self._catalog_subscription is not None:
            result = self._catalog_subscription()
            if hasattr(result, "__await__"):
                await result
        if self._supervisor is not None:
            await self._supervisor.start()

    async def stop(self) -> None:
        # Supervisor stops BEFORE pool.aclose() — a relay must never
        # outlive the engine it polls (asserted on call order).
        if self._supervisor is not None:
            await self._supervisor.stop()
        await self._pool.aclose()
