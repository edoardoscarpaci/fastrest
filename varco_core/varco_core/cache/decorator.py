"""
varco_core.cache.decorator
===========================
``@cached`` — async look-aside cache decorator.

Analogous to ``functools.lru_cache`` / ``functools.cache`` but for coroutines
and backed by any ``CacheBackend`` (``InMemoryCache``, ``LayeredCache``,
``RedisCache``, …).

Usage — module-level cache (simplest)
--------------------------------------
::

    from varco_core.cache import InMemoryCache, LayeredCache, TTLStrategy, cached
    from varco_redis.cache import RedisCache, RedisCacheSettings

    # Declare once at module level (or in application startup)
    _cache = LayeredCache(
        InMemoryCache(strategy=TTLStrategy(60)),
        RedisCache(RedisCacheSettings(key_prefix="users:")),
        promote_ttl=60,
    )

    @cached(_cache, ttl=300, namespace="users")
    async def get_user(user_id: int) -> dict:
        return await db.fetch_one("SELECT * FROM users WHERE id = $1", user_id)

    # Cache is checked transparently; DB is hit only on a miss.
    user = await get_user(42)

    # Invalidate a specific call's result:
    await get_user.invalidate(42)

    # Invalidate all entries cached by this function:
    await get_user.invalidate_all()

Usage — instance method with ``self._cache``
----------------------------------------------
::

    class PostRepository:
        def __init__(self, cache: CacheBackend) -> None:
            self._cache = cache

        @cached(lambda self: self._cache, ttl=120, namespace="posts")
        async def find_by_id(self, post_id: int) -> dict | None:
            return await db.fetch_one("SELECT * FROM posts WHERE id = $1", post_id)

Key generation
--------------
By default the cache key is::

    "<namespace>:<md5(repr(args[1:]) + repr(sorted(kwargs.items())))[:12]>"

where ``args[0]`` (``self`` / ``cls``) is excluded from the hash.

Override with a custom key callable::

    @cached(_cache, key=lambda post_id: f"post:{post_id}", namespace="posts")
    async def get_post(post_id: int) -> dict:
        ...

    # For methods:
    @cached(lambda self: self._cache, key=lambda self, post_id: f"post:{post_id}")
    async def get_post(self, post_id: int) -> dict:
        ...

Invalidation helpers
---------------------
Both helpers are attached to the wrapper function:

- ``wrapper.invalidate(*args, **kwargs)`` — evict the entry for these
  specific arguments (uses the same key function as the decorator).
- ``wrapper.invalidate_all()`` — call ``cache.clear()`` to flush all entries
  managed by this backend.

Caveats
-------
- ``None`` return values are NOT cached — a ``None`` result always triggers
  a fresh call on the next access, unless ``policy.negative_ttl`` is set
  — see Plan 010 / D-4.
- The cache must be started before the decorated function is called.  The
  decorator itself never calls ``cache.start()``.
- Thread / async safety inherits from the underlying ``CacheBackend``.

Stampede protection (Plan 010 / C2)
------------------------------------
Pass ``policy=`` and/or ``singleflight=True`` to coalesce concurrent misses
for the same key into one recompute per process::

    from varco_core.cache import CachePolicy, cached

    @cached(_cache, policy=CachePolicy(ttl=300.0), singleflight=True, namespace="users")
    async def get_user(user_id: int) -> dict:
        return await db.fetch_one("SELECT * FROM users WHERE id = $1", user_id)

When neither ``policy`` nor ``singleflight`` is given, the wrapper body runs
**exactly** as it did before this plan — no ``read_through()`` call, no
``Singleflight`` allocation.  A ``Singleflight`` is created **once per
decorated function** at decoration time — never per call (the same
shared-instance rule as ``CircuitBreaker``/``Bulkhead``, see CLAUDE.md).
``wrapper.aclose()`` drains any outstanding background SWR refreshes.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import logging
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from varco_core.cache.base import CacheBackend
from varco_core.cache.policy import CachePolicy
from varco_core.cache.readthrough import read_through
from varco_core.cache.singleflight import Singleflight

_logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Coroutine[Any, Any, Any]])

# Sentinel — distinguishes "no cache argument provided" from ``None``.
_MISSING = object()


# ── Public decorator ──────────────────────────────────────────────────────────


def cached(
    cache: CacheBackend | Callable[..., CacheBackend],
    *,
    key: str | Callable[..., str] | None = None,
    ttl: float | None = None,
    namespace: str = "",
    policy: CachePolicy | None = None,
    singleflight: bool = False,
) -> Callable[[F], F]:
    """
    Async look-aside cache decorator.

    Args:
        cache:     A started ``CacheBackend`` instance **or** a callable that
                   accepts the decorated function's first argument (``self`` /
                   ``cls``) and returns the backend.  Use the callable form for
                   instance methods whose cache is stored on ``self``.
        key:       Cache key strategy.  Three options:

                   - ``None`` *(default)* — auto-generated from ``namespace``
                     (or function ``__qualname__``) plus an MD5 hash of the
                     call arguments (excluding ``self`` / ``cls``).
                   - A plain ``str`` — the *same* key is used for every call.
                     Only useful when the function is called with identical
                     arguments every time.
                   - A ``callable`` with the *same signature as the decorated
                     function* — return the desired key string.

        ttl:       Per-entry TTL in seconds passed to ``cache.set()``.
                   ``None`` → fallback to the cache backend's own default.
        namespace: Key prefix.  If omitted the function's ``__qualname__``
                   (``module.ClassName.method``) is used so keys from
                   different functions never collide.
        policy:    Optional ``CachePolicy`` (Plan 010 / C2-C4).  When
                   ``None`` (default) and ``singleflight=False``, the wrapper
                   body is byte-identical to pre-Plan-010 ``@cached`` — no
                   ``read_through()`` call at all.  When either is set, the
                   decorator delegates to ``read_through()`` with one
                   ``Singleflight`` created per decorated function at
                   decoration time.  ``ttl`` is folded into
                   ``CachePolicy(ttl=ttl)`` when ``policy`` is ``None`` but
                   ``singleflight=True`` was requested.
        singleflight: Coalesce concurrent misses for the same key into one
                   recompute per process.  Requires the effective policy to
                   have ``singleflight=True`` — passing ``singleflight=True``
                   here sets that on the fly if ``policy`` didn't already.

    Returns:
        A decorated coroutine function with two extra attributes:
        ``invalidate(*args, **kwargs)`` evicts the entry for specific call
        arguments; ``invalidate_all()`` flushes all entries managed by this
        backend (calls ``cache.clear()``).

    Example::

        @cached(_cache, ttl=300, namespace="users")
        async def get_user(user_id: int) -> dict:
            return await db.fetch_user(user_id)

        user = await get_user(42)
        await get_user.invalidate(42)   # evict user 42
        await get_user.invalidate_all() # flush entire cache
    """

    def decorator(func: F) -> F:
        ns = namespace or f"{func.__module__}.{func.__qualname__}"

        # Resolve the effective policy ONCE at decoration time — folding
        # `ttl`/`singleflight` in here means the hot path (wrapper body)
        # never has to re-derive it per call.
        effective_policy: CachePolicy | None = policy
        if effective_policy is None and singleflight:
            effective_policy = CachePolicy(ttl=ttl, singleflight=True)
        elif effective_policy is not None and singleflight and not effective_policy.singleflight:
            effective_policy = dataclasses.replace(effective_policy, singleflight=True)

        # One Singleflight per decorated function, created at decoration
        # time — NEVER per call (shared-instance rule, CLAUDE.md).
        sf = Singleflight(name=ns) if effective_policy is not None else None

        # ── Cache resolver ────────────────────────────────────────────────────

        def _resolve_cache(args: tuple[Any, ...]) -> CacheBackend:
            if callable(cache) and not isinstance(cache, CacheBackend):
                # Factory form — pass first arg (self/cls) to get the backend
                return cache(args[0]) if args else cache()
            return cache

        # ── Key builder ───────────────────────────────────────────────────────

        def _build_key(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
            if key is None:
                # Auto: hash args (skip self/cls) + sorted kwargs
                call_args = args[1:] if _looks_like_method(func) else args
                raw = repr(call_args) + repr(sorted(kwargs.items()))
                h = hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()[:12]
                return f"{ns}:{h}"
            if callable(key):
                return key(*args, **kwargs)
            # Plain string — same key for every call
            return key

        # ── Wrapper ───────────────────────────────────────────────────────────

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            _cache = _resolve_cache(args)
            cache_key = _build_key(args, kwargs)

            if effective_policy is None:
                # Byte-identical to pre-Plan-010 @cached — no read_through()
                # call at all when neither policy= nor singleflight= is given.
                cached_val = await _cache.get(cache_key)
                if cached_val is not None:
                    _logger.debug("@cached[%s]: hit for key %r.", ns, cache_key)
                    return cached_val

                _logger.debug("@cached[%s]: miss for key %r, calling function.", ns, cache_key)
                result = await func(*args, **kwargs)
                if result is not None:
                    await _cache.set(cache_key, result, ttl=ttl)
                return result

            async def loader() -> Any:
                return await func(*args, **kwargs)

            return await read_through(
                _cache,
                cache_key,
                loader,
                dataclasses.replace(effective_policy, name=effective_policy.name or ns),
                singleflight=sf,
            )

        # ── Invalidation helpers (attached to wrapper) ────────────────────────

        async def invalidate(*args: Any, **kwargs: Any) -> None:
            """Evict the cached result for these specific call arguments."""
            _cache = _resolve_cache(args)
            cache_key = _build_key(args, kwargs)
            await _cache.delete(cache_key)
            _logger.debug("@cached[%s]: invalidated key %r.", ns, cache_key)

        async def invalidate_all() -> None:
            """Flush all entries managed by this cache backend."""
            # Resolve cache without arguments — must be module-level backend
            _cache = cache if isinstance(cache, CacheBackend) else cache(None)
            await _cache.clear()
            _logger.debug("@cached[%s]: invalidate_all() called.", ns)

        async def aclose() -> None:
            """Drain any outstanding background SWR refresh tasks (no-op if none)."""
            if sf is not None:
                await sf.aclose()

        wrapper.invalidate = invalidate  # type: ignore[attr-defined]
        wrapper.invalidate_all = invalidate_all  # type: ignore[attr-defined]
        wrapper.aclose = aclose  # type: ignore[attr-defined]
        wrapper.__cache__ = cache  # type: ignore[attr-defined]

        return wrapper  # type: ignore[return-value]

    return decorator


# ── Helpers ───────────────────────────────────────────────────────────────────


def _looks_like_method(func: Callable[..., Any]) -> bool:
    """
    Heuristic: return True when ``func`` looks like an instance/class method.

    Checks whether the first parameter of ``func`` is named ``self`` or ``cls``.
    This is used to exclude ``self`` / ``cls`` from the auto-generated cache key
    so that ``get_user(42)`` and ``other_instance.get_user(42)`` share the same
    key when the backend is the same object.

    Returns ``False`` for plain functions and static methods.
    """
    import inspect

    try:
        params = list(inspect.signature(func).parameters)
    except (ValueError, TypeError):
        return False
    return bool(params) and params[0] in ("self", "cls")


__all__ = ["cached"]
