"""
Regression tests — Plan 011 / C5, drift item 2.

User reports: ``CacheServiceMixin._use_bulk_cache`` (declared
``varco_core/cache/mixin.py:246``) is read nowhere — ``list()``'s body was
never rewired to call ``read_through_many()`` when the flag is ``True``
(CLAUDE.md's own "Bulk operations" pitfall table documents this as a known
gap). Correct behaviour: when ``_use_bulk_cache = True`` **and** the
injected cache satisfies ``BulkCache``, ``list()`` routes its cache
read/write through ``varco_core.cache.readthrough.read_through_many()``
(the existing C5 batch primitive — no second implementation), instead of
the plain ``cache.get()``/``cache.set()`` pair it uses today. With the flag
left at its default (``False``), or against a cache that does not satisfy
``BulkCache``, behaviour is byte-identical to before (RD-1).
"""

from __future__ import annotations

from typing import Any

from varco_core.cache.base import BulkCache
from varco_core.cache.memory import InMemoryCache
from varco_core.cache.mixin import CacheServiceMixin
from varco_core.cache.policy import CachePolicy
from varco_core.service.base import AsyncService


class _StubService(CacheServiceMixin):
    """Minimal CacheServiceMixin host — AsyncService.list() is monkeypatched
    per test rather than wiring a full UoW/repo/assembler stack, which is
    irrelevant to whether the *cache* half of list() takes the bulk path."""

    _cache_namespace = "widget"

    def __init__(self, cache: Any) -> None:
        self._cache = cache
        self._cache_producer = None

    def _get_repo(self, uow: Any) -> Any:  # pragma: no cover - never reached
        raise NotImplementedError


async def test_regression_use_bulk_cache_false_never_calls_get_many(
    monkeypatch: Any,
) -> None:
    # Symptom (would-be): a True flag having no effect is fine; but a FALSE
    # flag routing through the bulk path would be a real regression. Default
    # off must stay byte-identical (RD-1).
    cache = InMemoryCache()
    await cache.start()
    assert isinstance(cache, BulkCache)

    calls = {"list": 0, "get_many": 0}

    async def fake_list(self, params, ctx=None):
        calls["list"] += 1
        return ["item"]

    orig_get_many = cache.get_many

    async def spy_get_many(*a, **kw):
        calls["get_many"] += 1
        return await orig_get_many(*a, **kw)

    monkeypatch.setattr(AsyncService, "list", fake_list)
    monkeypatch.setattr(cache, "get_many", spy_get_many)

    svc = _StubService(cache)
    assert svc._use_bulk_cache is False

    await svc.list(params="whatever")

    assert calls["list"] == 1
    assert calls["get_many"] == 0


async def test_regression_use_bulk_cache_true_routes_through_read_through_many(
    monkeypatch: Any,
) -> None:
    # Correct behaviour: with the opt-in flag set and a BulkCache-satisfying
    # backend, list() calls cache.get_many()/set_many() (the batch primitives
    # underlying read_through_many()) rather than plain get()/set().
    cache = InMemoryCache()
    await cache.start()

    calls = {"list": 0, "get_many": 0, "set_many": 0, "get": 0, "set": 0}

    async def fake_list(self, params, ctx=None):
        calls["list"] += 1
        return ["item"]

    orig_get_many = cache.get_many
    orig_set_many = cache.set_many
    orig_get = cache.get
    orig_set = cache.set

    async def spy_get_many(*a, **kw):
        calls["get_many"] += 1
        return await orig_get_many(*a, **kw)

    async def spy_set_many(*a, **kw):
        calls["set_many"] += 1
        return await orig_set_many(*a, **kw)

    async def spy_get(*a, **kw):
        calls["get"] += 1
        return await orig_get(*a, **kw)

    async def spy_set(*a, **kw):
        calls["set"] += 1
        return await orig_set(*a, **kw)

    monkeypatch.setattr(AsyncService, "list", fake_list)
    monkeypatch.setattr(cache, "get_many", spy_get_many)
    monkeypatch.setattr(cache, "set_many", spy_set_many)
    monkeypatch.setattr(cache, "get", spy_get)
    monkeypatch.setattr(cache, "set", spy_set)

    svc = _StubService(cache)
    svc._use_bulk_cache = True
    svc._cache_policy = CachePolicy(ttl=60.0)

    result = await svc.list(params="whatever")

    assert result == ["item"]
    assert calls["list"] == 1
    assert calls["get_many"] == 1
    assert calls["set_many"] == 1
    # InMemoryCache's BulkCache default is a portable loop over get()/set()
    # (D-11) — the mixin-level assertion is that it goes through the
    # get_many/set_many *entry points*, not that the backend never loops
    # internally.

    # Second call is a cache hit — loader (AsyncService.list) is not called again.
    result2 = await svc.list(params="whatever")
    assert result2 == ["item"]
    assert calls["list"] == 1
