"""
router.py
=========
HTTP router for the ``04-profiling-hotspot`` example.

Exposes three endpoints that trigger the profiled work functions in
``work.py``.  All endpoints are public (no authentication) since this
example is about profiling, not authorization.

Routes
------
GET /v1/compute       — triggers ``cpu_heavy_work()`` (``@profile`` decorator)
GET /v1/allocate      — triggers ``memory_work()`` (``profiled()`` context manager)
GET /v1/custom-backend — triggers ``custom_backend_work()`` (custom CPU backend)

DESIGN: GenericRouter (no service/repository layer)
    ✅ No DI, no DB — the router is a pure function dispatcher.
    ✅ Consistent with the varco_fastapi ``GenericRouter`` pattern for
       service-free endpoints.
    ❌ No auth context — acceptable for a diagnostics / hotspot example.

Thread safety:  ✅ Stateless — no shared mutable state in the router.
Async safety:   ✅ All handlers are ``async def``.
"""

from __future__ import annotations

from varco_fastapi.router.endpoint import route
from varco_fastapi.router.presets import GenericRouter
from work import cpu_heavy_work, custom_backend_work, memory_work


class ProfilingRouter(GenericRouter):
    """
    Router that exposes the profiled work endpoints.

    All routes are public — no ``_auth`` required because this example
    focuses exclusively on the profiling system.
    """

    _prefix = "/v1"

    @route("GET", "/compute")
    async def compute(self) -> dict[str, object]:
        """
        Invoke the CPU-heavy work function wrapped with ``@profile``.

        Returns:
            JSON body from ``cpu_heavy_work()`` — ``result`` and
            ``iterations`` fields.
        """
        return await cpu_heavy_work()

    @route("GET", "/allocate")
    async def allocate(self) -> dict[str, object]:
        """
        Invoke the memory-allocating work function using ``profiled()``.

        Returns:
            JSON body from ``memory_work()`` — ``items`` count and
            ``wall_time_ms`` from the profiling session report.
        """
        return await memory_work()

    @route("GET", "/custom-backend")
    async def custom_backend(self) -> dict[str, object]:
        """
        Invoke the work function using the custom ``"counting"`` CPU backend.

        Returns:
            JSON body from ``custom_backend_work()`` — ``result`` and
            ``backend`` name.
        """
        return await custom_backend_work()


__all__ = ["ProfilingRouter"]
