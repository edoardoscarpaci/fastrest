"""
Plan 024 / Phase 1, Steps 13 + 16 — `@Disposes` teardown for the two Redis
cache configurations (`RedisCacheConfiguration`, `RedisLayeredCacheConfiguration`).

RED MODE: neither configuration has a `@Disposes(CacheBackend)` method yet.
`RedisCache.stop()`/`LayeredCache.stop()` are only reachable via `@PreDestroy`,
which providify never calls on a `@Provider`-produced instance
(`providify/README.md:945-949`). Every test below is expected to FAIL today:

- The `validate()` assertions fail with `AttributeError` because the
  installed providify does not yet have `IssueKind.UNREACHABLE_PRE_DESTROY`
  (pin bump is Step 12, not yet applied) — once pinned to >=2.0.1 the same
  assertion would fail because the disposer that resolves the warning does
  not exist yet.
- The round-trip assertions fail because `ashutdown()` never calls
  `RedisCache.stop()` for a provider-produced instance — the monkeypatched
  `_record` sentinel is never invoked.

No Docker required — Redis I/O is monkeypatched to no-ops.
"""

from __future__ import annotations

from typing import Any

import pytest
from providify import DIContainer, IssueKind
from varco_core.cache.base import CacheBackend
from varco_redis.cache import (
    RedisCache,
    RedisCacheConfiguration,
    RedisLayeredCacheConfiguration,
)


async def _noop_start(self: Any) -> None:
    # No real Redis connection — proves the disposer, not connectivity.
    self._redis = object()


def _make_recorder() -> tuple[list[str], Any]:
    calls: list[str] = []

    async def _record_stop(self: Any) -> None:
        calls.append("stopped")
        self._redis = None

    return calls, _record_stop


async def test_redis_cache_configuration_reports_zero_unreachable_pre_destroy_issues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Tier-A mechanism-level assertion (§D-C2-test) — providify's own
    # detector must see a disposer wired for RedisCache's @PreDestroy stop().
    monkeypatch.setattr(RedisCache, "start", _noop_start)
    calls, record_stop = _make_recorder()
    monkeypatch.setattr(RedisCache, "stop", record_stop)

    container = DIContainer()
    await container.ainstall(RedisCacheConfiguration)
    await container.aget(CacheBackend)

    report = container.validate(raise_on_error=False)
    # Force the attribute lookup unconditionally — a list-comprehension
    # guard (`if i.kind == IssueKind.UNREACHABLE_PRE_DESTROY`) would never
    # even evaluate the RHS when report.issues is empty, silently turning
    # this into a vacuous pass on a pre-2.0.1 providify that has never heard
    # of the kind at all.
    target_kind = IssueKind.UNREACHABLE_PRE_DESTROY
    unreachable = [i for i in report.issues if i.kind == target_kind]
    assert unreachable == []

    await container.ashutdown()


async def test_redis_cache_stop_runs_on_ashutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    # Behavioural round-trip — the actual defect: stop() must fire.
    monkeypatch.setattr(RedisCache, "start", _noop_start)
    calls, record_stop = _make_recorder()
    monkeypatch.setattr(RedisCache, "stop", record_stop)

    container = DIContainer()
    await container.ainstall(RedisCacheConfiguration)
    await container.aget(CacheBackend)
    await container.ashutdown()

    assert calls == ["stopped"]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG: providify's @Disposes wiring loop (container.py:6201-6214) "
        "attaches a disposer to the first matching CacheBackend binding "
        "across the WHOLE container, not the installing module's own — "
        "installing RedisLayeredCacheConfiguration after RedisCacheConfiguration "
        "overwrites the first binding's disposer and leaves the second "
        "binding's own instance (LayeredCache) with no teardown path at all. "
        "See design/upstream-gaps/providify-disposes-first-match.md "
        "(P24-DISPOSES-FIRSTMATCH)."
    ),
)
async def test_both_cache_configurations_installed_together_both_get_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # §D-C2-firstmatch — a recursive scan installs BOTH RedisCacheConfiguration
    # and RedisLayeredCacheConfiguration, both binding CacheBackend. providify
    # attaches @Disposes to the FIRST matching binding and breaks
    # (container.py:6202-6214) — this proves neither instance leaks.
    monkeypatch.setattr(RedisCache, "start", _noop_start)
    stopped: list[str] = []

    async def _record_redis_stop(self: Any) -> None:
        stopped.append("redis")
        self._redis = None

    monkeypatch.setattr(RedisCache, "stop", _record_redis_stop)

    from varco_core.cache.layered import LayeredCache
    from varco_core.cache.memory import InMemoryCache

    async def _layered_start(self: Any) -> None:
        # Avoid real L2 Redis connect inside LayeredCache.start().
        self._l1_started = True
        self._l2_started = True

    async def _record_layered_stop(self: Any) -> None:
        stopped.append("layered")

    monkeypatch.setattr(LayeredCache, "start", _layered_start)
    monkeypatch.setattr(LayeredCache, "stop", _record_layered_stop)
    monkeypatch.setattr(InMemoryCache, "start", lambda self: None)

    container = DIContainer()
    await container.ainstall(RedisCacheConfiguration)
    await container.ainstall(RedisLayeredCacheConfiguration)
    # Force both CacheBackend bindings to actually be instantiated —
    # aget() alone would only construct the highest-priority one.
    await container.aget_all(CacheBackend)
    await container.ashutdown()

    # Both provider-produced CacheBackend instances must have been stopped —
    # not just the one whose binding happened to receive the disposer.
    assert "redis" in stopped
    assert "layered" in stopped
