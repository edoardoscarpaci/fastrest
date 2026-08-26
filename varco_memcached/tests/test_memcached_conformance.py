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

import asyncio

import pytest
from varco_conformance.cache import CacheBackendConformance
from varco_memcached.cache import MemcachedCache, MemcachedCacheSettings

pytestmark = pytest.mark.integration


class TestMemcachedCacheConformance(CacheBackendConformance):
    @pytest.fixture
    async def cache(self, memcached_host_port: tuple[str, int]):
        host, port = memcached_host_port
        async with MemcachedCache(MemcachedCacheSettings(host=host, port=port)) as cache:
            yield cache

    async def test_ttl_expiry(self, cache) -> None:  # type: ignore[override]
        """
        KI-5 regression — overrides the shared suite's ttl=0.05/sleep=0.3
        timing instead of inheriting it verbatim.

        Symptom: a sub-second ttl (e.g. 0.05s) never expired the entry at
        all, because MemcachedCache.set() truncated it with int() to
        exptime=0 — which Memcached's protocol treats as "no expiry", not
        "expire almost immediately" (varco_memcached/varco_memcached/cache.py,
        MemcachedCache.set).

        Correct behaviour: the entry expires within its requested window.
        But unlike Redis (PSETEX takes milliseconds), Memcached's exptime is
        genuinely whole-seconds-only at the wire-protocol level — there is no
        finer-grained command to switch to (see the KI-3 fix in
        varco_redis/varco_redis/cache.py for the millisecond-precision
        counterpart, which IS possible there). The fix rounds a positive
        sub-second ttl UP to the smallest expressible non-zero exptime (1s)
        rather than truncating it down to 0s, so a "expire almost
        immediately" request now expires within ~1s instead of never — the
        best this protocol can honor. The shared suite's 0.3s sleep window
        cannot observe a real 1-second-granularity expiry, so this override
        uses timing compatible with that real granularity instead of
        loosening the shared suite for every other backend.
        """
        key = self._key("ttl")
        await cache.set(key, "expires-soon", ttl=0.05)
        assert await cache.get(key) == "expires-soon"
        await asyncio.sleep(1.3)
        assert await cache.get(key) is None
