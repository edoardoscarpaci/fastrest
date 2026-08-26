"""
varco_core.tenancy.cached_catalog
====================================
``CachedTenantCatalog`` — cross-pod-safe caching wrapper around an
``AbstractTenantCatalog`` (Plan 007, Phase 4, step 6-7).

DESIGN: all three of (a) event invalidation, (b) TTL backstop, (c)
read-through on miss
    See the plan's "DESIGN: cross-pod catalog visibility" section for the
    full rationale — summarized: (a) gives sub-second propagation, (b) is
    the self-healing backstop for a dropped invalidation event (buses do
    drop messages), (c) makes onboarding instant even on a pod that missed
    the event entirely.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from varco_core.tenancy.catalog import (
    AbstractTenantCatalog,
    TenantDescriptor,
    TenantNotFoundError,
)
from varco_core.tenancy.settings import TenantStatus

if TYPE_CHECKING:
    from varco_core.tenancy.control.events import TenantCatalogChanged


class CachedTenantCatalog(AbstractTenantCatalog):
    """
    Caching ``AbstractTenantCatalog`` wrapper.

    Args:
        store:                  The durable, authoritative catalog.
        catalog_ttl_s:          TTL backstop for a per-entry re-read.
        negative_cache_window_s: Rate limit for repeated misses of an
                                 unknown tenant id (avoids hammering the
                                 store for a persistently-unknown id).

    Async safety: ✅ A lazily created ``asyncio.Lock`` guards the cache dict.
    """

    def __init__(
        self,
        *,
        store: AbstractTenantCatalog,
        catalog_ttl_s: float = 60.0,
        negative_cache_window_s: float = 5.0,
    ) -> None:
        self._store = store
        self._catalog_ttl_s = catalog_ttl_s
        self._negative_cache_window_s = negative_cache_window_s

        self._cache: dict[str, tuple[TenantDescriptor, float]] = {}
        self._negative_cache: dict[str, float] = {}
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def get(self, tenant_id: str) -> TenantDescriptor:
        now = time.monotonic()

        async with self._get_lock():
            cached = self._cache.get(tenant_id)
            if cached is not None:
                descriptor, cached_at = cached
                if (now - cached_at) <= self._catalog_ttl_s:
                    return descriptor

            neg_at = self._negative_cache.get(tenant_id)
            if neg_at is not None and (now - neg_at) <= self._negative_cache_window_s:
                raise TenantNotFoundError(tenant_id)

        # Read through outside the lock — the store call may do I/O.
        try:
            descriptor = await self._store.get(tenant_id)
        except TenantNotFoundError:
            async with self._get_lock():
                self._negative_cache[tenant_id] = now
                self._cache.pop(tenant_id, None)
            raise

        async with self._get_lock():
            self._cache[tenant_id] = (descriptor, now)
            self._negative_cache.pop(tenant_id, None)
        return descriptor

    async def list_tenants(
        self, *, status: TenantStatus | None = TenantStatus.ACTIVE
    ) -> list[TenantDescriptor]:
        return await self._store.list_tenants(status=status)

    async def add(self, descriptor: TenantDescriptor) -> None:
        await self._store.add(descriptor)
        async with self._get_lock():
            self._cache[descriptor.tenant_id] = (descriptor, time.monotonic())
            self._negative_cache.pop(descriptor.tenant_id, None)

    async def update_status(self, tenant_id: str, status: TenantStatus) -> None:
        await self._store.update_status(tenant_id, status)
        async with self._get_lock():
            self._cache.pop(tenant_id, None)

    async def remove(self, tenant_id: str) -> None:
        await self._store.remove(tenant_id)
        async with self._get_lock():
            self._cache.pop(tenant_id, None)

    async def on_catalog_changed(self, event: TenantCatalogChanged) -> None:
        """Invalidate the cached entry for ``event.tenant_id`` immediately."""
        async with self._get_lock():
            self._cache.pop(event.tenant_id, None)
            self._negative_cache.pop(event.tenant_id, None)
