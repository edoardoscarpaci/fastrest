"""
Real-Memcached conformance opt-in (Plan 012 / RT6, Step 27).

Consumes the session-scoped ``memcached_host_port`` fixture that Phase 0/1
(Steps 3, 7) add to ``varco_memcached/tests/conftest.py`` (backed by
first-party ``testcontainers.memcached.MemcachedContainer``). Until that
fixture exists, this test class errors at fixture-resolution time with
``fixture 'memcached_host_port' not found``.

Also depends on ``pythonpath = ["../testkit"]`` in
``varco_memcached/pyproject.toml`` — until then the import below fails
with ``ModuleNotFoundError: No module named 'varco_conformance'``.
"""

from __future__ import annotations

import pytest

from varco_memcached.cache import MemcachedCache, MemcachedCacheSettings

from varco_conformance.cache import CacheBackendConformance

pytestmark = pytest.mark.integration


class TestMemcachedCacheConformance(CacheBackendConformance):
    @pytest.fixture
    async def cache(self, memcached_host_port: tuple[str, int]):
        host, port = memcached_host_port
        async with MemcachedCache(
            MemcachedCacheSettings(host=host, port=port)
        ) as cache:
            yield cache

    @pytest.mark.xfail(
        reason=(
            "BUG: MemcachedCache.set(ttl=) (varco_memcached/varco_memcached/"
            "cache.py:343) truncates a sub-second float ttl to int() before "
            "passing it as exptime — ttl=0.05 becomes exptime=0, which the "
            "Memcached protocol treats as 'no expiry' rather than 'expire "
            "almost immediately'. The entry is never evicted. Same root cause "
            "as KI-3 (RedisCache), different failure mode (silent no-expiry "
            "instead of a raised error). CacheBackend.set()'s ttl: float | "
            "None contract implies sub-second precision is valid. See "
            "BACKLOG.md. Not fixed here per Plan 012 Non-goals (no production "
            "code changes)."
        ),
        strict=True,
    )
    async def test_ttl_expiry(self, cache) -> None:  # type: ignore[override]
        await super().test_ttl_expiry(cache)
