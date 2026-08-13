"""
Failing tests for varco_core.tenancy.pool (Plan 007, Phase 1, step 5).

TenantResourcePool[T] — bounded LRU pool with lease-refcounting eviction
protection. No backend deps (factory/closer are plain callables here).
"""

from __future__ import annotations

import asyncio
import logging

import pytest


def _make_pool(**kwargs):
    from varco_core.tenancy.pool import TenantResourcePool

    created: list[str] = []
    closed: list[str] = []

    async def factory(tenant_id: str) -> str:
        created.append(tenant_id)
        return f"resource-{tenant_id}"

    async def closer(resource: str) -> None:
        closed.append(resource)

    pool = TenantResourcePool(factory=factory, closer=closer, **kwargs)
    return pool, created, closed


async def test_ensure_creates_once_and_caches() -> None:
    pool, created, _closed = _make_pool()

    first = await pool.ensure("acme")
    second = await pool.ensure("acme")

    assert first == second == "resource-acme"
    assert created == ["acme"]


async def test_concurrent_ensure_for_one_tenant_calls_factory_once() -> None:
    from varco_core.tenancy.pool import TenantResourcePool

    calls: list[str] = []

    async def factory(tenant_id: str) -> str:
        calls.append(tenant_id)
        await asyncio.sleep(0.01)
        return f"resource-{tenant_id}"

    async def closer(resource: str) -> None:
        pass

    pool = TenantResourcePool(factory=factory, closer=closer)

    results = await asyncio.gather(*(pool.ensure("acme") for _ in range(10)))

    assert len(calls) == 1
    assert all(r == "resource-acme" for r in results)


async def test_peek_never_creates() -> None:
    pool, created, _closed = _make_pool()

    assert pool.peek("acme") is None
    assert created == []

    await pool.ensure("acme")

    assert pool.peek("acme") == "resource-acme"


async def test_lru_evicts_least_recently_used_entry_at_capacity_plus_one() -> None:
    pool, _created, closed = _make_pool(max_entries=2)

    await pool.ensure("a")
    await pool.ensure("b")
    await pool.ensure("c")  # exceeds cap by one -> evict least-recently-used ("a")

    assert pool.peek("a") is None
    assert "resource-a" in closed


async def test_leased_entry_is_never_evicted_and_cap_breach_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pool, _created, closed = _make_pool(max_entries=1)

    await pool.ensure("a")
    async with pool.lease("a"):
        with caplog.at_level(logging.WARNING):
            await pool.ensure("b")  # would evict "a", but it's leased

        assert "resource-a" not in closed
        assert any(
            "WARNING" in r.levelname or r.levelno >= logging.WARNING
            for r in caplog.records
        )


async def test_idle_ttl_sweep_closes_idle_entries() -> None:
    pool, _created, closed = _make_pool(idle_ttl_s=0.01)

    await pool.ensure("a")
    await asyncio.sleep(0.05)
    await pool.sweep()

    assert "resource-a" in closed
    assert pool.peek("a") is None


async def test_aclose_closes_all_and_is_idempotent() -> None:
    pool, _created, closed = _make_pool()

    await pool.ensure("a")
    await pool.ensure("b")

    await pool.aclose()
    await pool.aclose()  # idempotent

    assert set(closed) == {"resource-a", "resource-b"}


async def test_raising_factory_leaves_no_poisoned_entry() -> None:
    from varco_core.tenancy.pool import TenantResourcePool

    async def factory(tenant_id: str) -> str:
        raise RuntimeError("boom")

    async def closer(resource: str) -> None:
        pass

    pool = TenantResourcePool(factory=factory, closer=closer)

    with pytest.raises(RuntimeError):
        await pool.ensure("acme")

    assert pool.peek("acme") is None


async def test_raising_closer_is_logged_and_swallowed_remaining_entries_close(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from varco_core.tenancy.pool import TenantResourcePool

    closed: list[str] = []

    async def factory(tenant_id: str) -> str:
        return f"resource-{tenant_id}"

    async def closer(resource: str) -> None:
        if resource == "resource-a":
            raise RuntimeError("close failed")
        closed.append(resource)

    pool = TenantResourcePool(factory=factory, closer=closer)
    await pool.ensure("a")
    await pool.ensure("b")

    with caplog.at_level(logging.ERROR):
        await pool.aclose()  # must not raise despite "a"'s closer failing

    assert "resource-b" in closed
