"""
Plan 022 / Phase 4 (RL-8a), Step 25 — the orphan is *actually* torn down.

Steps 22–23 proved with unit tests that ``VarcoLifespan`` awaits its new
``shutdown=`` hook.  That is not the claim RL-8a makes.  The claim is that a
``@PreDestroy``-bearing singleton which is **not** a registered lifecycle
component — and which therefore leaked before this plan — now really releases
its resources.  Asserting "a hook was called" would restate Step 22; this file
asserts the *effect*, against a real Redis container.

``RedisCache`` is the worst confirmed orphan of the six measured in
``design/api-freeze-and-standards/measurements/predestroy-vs-lifespan.md``:
``RedisCacheConfiguration.redis_cache()`` (``varco_redis/cache.py:573-597``)
**awaits ``cache.start()``** inside the provider, so
``async_bootstrap(setup_cache=True)`` leaves a *started* connection pool bound
as ``CacheBackend`` that no lifespan path ever closed.

⛔ **And the RL-8a adoption does not fix it** — measured, not assumed.  providify
teardown dispatches per *binding kind* (``providify/container.py:4567-4576``,
``_adispose``): a ``ProviderBinding`` runs its ``@Disposes`` disposer and
**returns**, so the ``@PreDestroy`` on the *instance a provider returned* is
never reached; only a ``ClassBinding`` consults ``binding.pre_destroy``.
``RedisCache`` carries no ``@Singleton`` of its own (``cache.py:133``) — it
exists solely as the return value of the ``@Provider`` above — and therefore
stays orphaned after ``container.ashutdown()``.  Same shape for
``MemcachedCache``.

This test is kept as a **strict xfail** rather than deleted or "fixed": per
CLAUDE.md's conformance rule, a genuine upstream contract gap becomes a strict
xfail plus a BACKLOG row, never an in-place workaround.  ``strict=True`` means
it fails loudly the day providify (or a varco-side ``@Disposes``) closes the
gap.  See BACKLOG.md's "Findings from Plan 022 (Phase 4 / RL-8a)" row.  The
*positive* proof that the adoption works lives in
``varco_nats/tests/test_nats_lifespan_shutdown_integration.py``, against
``NatsStreamManager`` — a ``@Singleton`` (``ClassBinding``) orphan holding a
real connection.

⚠️ Cross-package direction.  ``varco_redis`` does not (and must not) depend on
``varco_fastapi``; the import below is guarded by ``importorskip`` and the test
lives here only because the session-scoped ``redis_url`` fixture and the real
container do (CLAUDE.md's Test Conventions).  Nothing in ``varco_redis``'s
shipped code is involved in that import.

Per-test namespacing: the shared session container means this test confines
itself to a ``uuid4().hex[:8]`` key prefix it owns exclusively.

Thread safety:  N/A (integration test)
Async safety:   ✅ the lifespan is driven as an async context manager.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.xfail(
    reason=(
        "BUG(upstream, providify 2.0.0): container.ashutdown() runs @Disposes for a "
        "ProviderBinding and never the @PreDestroy of the instance the provider "
        "returned (container.py:4567-4576), so the RedisCache orphan survives the "
        "Plan 022 / RL-8a adoption. See BACKLOG.md."
    ),
    strict=True,
)
async def test_container_shutdown_closes_orphaned_redis_cache_pool(
    redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A started-but-unregistered ``RedisCache`` has its socket closed at shutdown.

    Edge cases covered here on purpose:
        - The cache is asserted to be an *orphan* first (not in
          ``_collect_lifecycle_components()``), so the test cannot silently
          degrade into "a registered component was stopped".
        - The assertion is on the live ``redis.asyncio`` connection object, not
          on ``RedisCache._redis`` alone: a hook that nulled the attribute but
          leaked the socket would still pass a weaker check.
    """
    providify = pytest.importorskip("providify")
    pytest.importorskip("varco_fastapi")

    from varco_core.cache.base import CacheBackend
    from varco_fastapi.app import _collect_lifecycle_components
    from varco_fastapi.lifespan import VarcoLifespan
    from varco_redis.di import async_bootstrap

    run_id = uuid4().hex[:8]
    monkeypatch.setenv("VARCO_REDIS_CACHE_URL", redis_url)
    monkeypatch.setenv("VARCO_REDIS_CACHE_KEY_PREFIX", f"test:{run_id}:")

    container = providify.DIContainer()
    await async_bootstrap(container, setup_cache=True)

    cache: Any = await container.aget(CacheBackend)
    # Force a real round-trip so the pool holds a genuinely connected socket.
    await cache.set(f"{run_id}:probe", "v", ttl=30)
    assert await cache.get(f"{run_id}:probe") == "v"

    client = cache._redis
    assert client is not None
    pool = client.connection_pool
    connections = list(pool._available_connections)
    assert connections, "expected the pool to hold a released connection after a round-trip"
    assert any(conn.is_connected for conn in connections)

    # Pre-condition: this cache is exactly the orphan RL-8a is about.
    assert cache not in _collect_lifecycle_components(container)

    async def _shutdown() -> None:
        await container.ashutdown()

    async with VarcoLifespan(shutdown=_shutdown)(object()):
        pass

    assert cache._redis is None
    assert not any(conn.is_connected for conn in connections)
