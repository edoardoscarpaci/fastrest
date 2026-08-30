"""
varco_core.cache.readthrough
==============================
``read_through()`` — the single read-through algorithm shared by C2
(singleflight), C3 (observability), and C4 (stale-while-revalidate / jitter /
negative caching). Written once so the "coalescing a fresh recompute while
concurrently returning stale is a race that needs an explicit design" landmine
(Plan 010's Design section) is solved in one place instead of twice.

Algorithm::

    read_through(cache, key, loader, policy, *, type_hint, singleflight)

    1. get(key) — the raw payload, un-namespaced. read_through NEVER builds
       or namespaces the cache key itself; callers pass the final key.
    2. Try to unwrap an envelope regardless of the CURRENT policy's
       requires_envelope — a payload written under a different (older or
       newer, envelope-requiring) policy must still be read correctly (D-5).
       No envelope marker → treat as a fresh legacy value → HIT.
    3. Envelope present, negative, not hard-expired → HIT (kind="negative"),
       return None WITHOUT calling the loader.
    4. Envelope present, positive, not hard-expired:
         - soft-expired?  → serve the stale value NOW (kind="stale") and
           trigger exactly ONE background refresh through the SAME
           Singleflight slot cold misses use (C2×C4) — unless
           refresh_mode="blocking", which awaits the refresh instead.
         - else → HIT (kind="positive").
    5. Absent / hard-expired / negative-expired → MISS →
       Singleflight.do(key, loader) if a singleflight was passed and
       policy.singleflight is True, else call the loader directly (today's
       behaviour, unchanged default).
    6. loader raised AND a stale envelope existed within
       policy.stale_if_error seconds of its hard expiry → serve stale
       (kind="stale", reason="error") instead of propagating.  Otherwise the
       exception propagates and nothing is cached.

Byte-identical default (CachePolicy(), no singleflight): no envelope is ever
written, a None loader result is never cached, and get()/set() are called
with exactly the arguments today's ``@cached`` wrapper uses.

Thread safety:  ❌ Not thread-safe — inherits from the cache backend and any
                   passed ``Singleflight``.
Async safety:   ✅ Fully ``async def``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from varco_core.cache.envelope import CacheEnvelope, coerce, unwrap, wrap
from varco_core.observability.cache import (
    record_cache_duration,
    record_cache_hit,
    record_cache_miss,
    record_cache_stale_served,
    record_stampede_suppressed,
)

if TYPE_CHECKING:
    from varco_core.cache.base import AsyncCache
    from varco_core.cache.policy import CachePolicy
    from varco_core.cache.singleflight import SingleflightProtocol

Loader = Callable[[], Awaitable[Any]]


async def read_through(
    cache: AsyncCache[str, Any],
    key: str,
    loader: Loader,
    policy: CachePolicy,
    *,
    type_hint: type | None = None,
    singleflight: SingleflightProtocol | None = None,
) -> Any:
    """
    Read ``key`` from ``cache``, recomputing via ``loader`` on a miss.

    See the module docstring for the full algorithm. ``read_through`` never
    constructs or namespaces ``key`` — callers (``@cached``,
    ``CacheServiceMixin``) own key construction, including any
    tenant-namespacing (``tenancy_cache_key()``).

    Args:
        cache:        A started cache backend (or any ``AsyncCache``-shaped
                      object).
        key:          The final, already-namespaced cache key.
        loader:       Zero-argument async callable that recomputes the value
                      on a miss.
        policy:       ``CachePolicy`` controlling TTL, jitter, SWR, negative
                      caching, and singleflight participation.
        type_hint:    Forwarded to ``cache.get()`` for non-envelope reads;
                      re-applied via ``envelope.coerce()`` for envelope reads
                      (envelope mode always fetches with ``type_hint=None``
                      so the wrapper dict itself is not coerced).
        singleflight: Optional coalescer. Required for
                      ``policy.singleflight``/SWR-refresh coalescing to have
                      any effect — without one, every concurrent cold miss
                      independently calls ``loader()`` (today's behaviour),
                      and a soft-stale hit simply serves the stale value
                      with no background refresh triggered.

    Returns:
        The cached or freshly computed value. Never the raw envelope wrapper
        dict — a negative hit returns ``None``, a positive/stale hit returns
        the unwrapped (and re-coerced) value.

    Raises:
        Exception: Whatever ``loader()`` raises, unless
            ``policy.stale_if_error`` is set and a stale envelope within that
            window exists — see the module docstring, step 6.
    """
    start = time.monotonic()
    try:
        return await _read_through(cache, key, loader, policy, type_hint, singleflight)
    finally:
        record_cache_duration(
            cache=policy.name,
            operation="get",
            value_ms=(time.monotonic() - start) * 1000,
        )


async def _read_through(
    cache: AsyncCache[str, Any],
    key: str,
    loader: Loader,
    policy: CachePolicy,
    type_hint: type | None,
    singleflight: SingleflightProtocol | None,
) -> Any:
    now = time.time()
    # Envelope mode fetches the wrapper dict itself — the type hint is
    # re-applied to the unwrapped value below, never to the wrapper.
    get_type_hint = None if policy.requires_envelope else type_hint
    raw = await cache.get(key, type_hint=get_type_hint)

    # D-5: try to unwrap regardless of the CURRENT policy — a payload written
    # under a different policy generation must still read correctly.
    env: CacheEnvelope | None = unwrap(raw) if raw is not None else None

    if raw is not None and env is None:
        # Genuine legacy/raw value — no envelope was ever involved.
        record_cache_hit(cache=policy.name, kind="positive")
        return raw

    if env is not None:
        if env.is_negative:
            if env.hard_expires_at is None or now < env.hard_expires_at:
                record_cache_hit(cache=policy.name, kind="negative")
                return None
            # Negative entry expired — fall through to a real recompute.
        else:
            hard_expired = env.hard_expires_at is not None and now >= env.hard_expires_at
            if not hard_expired:
                value = coerce(env.value, type_hint)
                soft_expired = env.soft_expires_at is not None and now >= env.soft_expires_at
                if soft_expired:
                    return await _serve_stale_and_refresh(
                        cache, key, loader, policy, singleflight, value
                    )
                record_cache_hit(cache=policy.name, kind="positive")
                return value
            # Hard-expired — fall through to recompute, keeping `env` around
            # so a loader failure can still serve it under stale_if_error.

    record_cache_miss(cache=policy.name)
    return await _compute(
        cache, key, loader, policy, singleflight, type_hint, stale_env=env, now=now
    )


async def _serve_stale_and_refresh(
    cache: AsyncCache[str, Any],
    key: str,
    loader: Loader,
    policy: CachePolicy,
    singleflight: SingleflightProtocol | None,
    stale_value: Any,
) -> Any:
    """
    Handle a soft-expired hit: SWR (C2×C4).

    ``refresh_mode="blocking"`` awaits the refresh (through the same
    Singleflight slot a cold miss would use) and returns the fresh value.
    ``refresh_mode="background"`` (default) returns the stale value
    immediately and spawns exactly one background refresh — concurrent
    soft-stale readers all land on the same Singleflight slot, so only one
    refresh ever runs (see the module docstring's C2×C4 note).
    """

    async def _refresh_loader() -> Any:
        return await _load_and_store(cache, key, loader, policy)

    if policy.refresh_mode == "blocking" and singleflight is not None:
        value, is_leader = await singleflight.do(key, _refresh_loader)
        if not is_leader:
            record_stampede_suppressed(cache=policy.name)
        return value

    record_cache_hit(cache=policy.name, kind="stale")
    record_cache_stale_served(cache=policy.name, reason="soft_ttl")
    if singleflight is not None:
        singleflight.spawn_refresh(key, _refresh_loader)
    return stale_value


async def _compute(
    cache: AsyncCache[str, Any],
    key: str,
    loader: Loader,
    policy: CachePolicy,
    singleflight: SingleflightProtocol | None,
    type_hint: type | None,
    *,
    stale_env: CacheEnvelope | None,
    now: float,
) -> Any:
    """Cold-miss / hard-expired recompute, with the stale_if_error fallback."""

    async def _run() -> Any:
        if singleflight is not None and policy.singleflight:
            value, is_leader = await singleflight.do(
                key, lambda: _load_and_store(cache, key, loader, policy)
            )
            if not is_leader:
                record_stampede_suppressed(cache=policy.name)
            return value
        return await _load_and_store(cache, key, loader, policy)

    try:
        return await _run()
    except Exception:
        if (
            policy.stale_if_error is not None
            and stale_env is not None
            and not stale_env.is_negative
            and stale_env.hard_expires_at is not None
            and (now - stale_env.hard_expires_at) <= policy.stale_if_error
        ):
            record_cache_hit(cache=policy.name, kind="stale")
            record_cache_stale_served(cache=policy.name, reason="error")
            return coerce(stale_env.value, type_hint)
        raise


async def _load_and_store(
    cache: AsyncCache[str, Any], key: str, loader: Loader, policy: CachePolicy
) -> Any:
    """Call ``loader()`` and persist the result per ``policy``, then return it."""
    result = await loader()
    await _store(cache, key, result, policy)
    return result


async def _store(cache: AsyncCache[str, Any], key: str, value: Any, policy: CachePolicy) -> None:
    """
    Persist ``value`` under ``key`` per ``policy`` — raw value for the
    identity policy (byte-identical default), envelope otherwise.
    """
    now = time.time()

    if not policy.requires_envelope:
        if value is None:
            # D-4: a None result is never cached under the default policy.
            return
        await cache.set(key, value, ttl=policy.effective_ttl())
        return

    if value is None:
        if policy.negative_ttl is None:
            # D-4: negative caching is opt-in — soft_ttl/stale_if_error alone
            # do not imply caching a None result.
            return
        env = CacheEnvelope(
            value=None,
            stored_at=now,
            soft_expires_at=None,
            hard_expires_at=now + policy.negative_ttl,
            is_negative=True,
        )
        await cache.set(key, wrap(env), ttl=policy.negative_ttl)
        return

    hard_ttl = policy.effective_ttl()
    hard_expires_at = now + hard_ttl if hard_ttl is not None else None
    soft_expires_at = now + policy.soft_ttl if policy.soft_ttl is not None else None
    # Physically keep the entry around past its hard expiry when
    # stale_if_error is set, so a loader failure can still find it.
    backend_ttl = hard_ttl
    if policy.stale_if_error is not None and hard_ttl is not None:
        backend_ttl = hard_ttl + policy.stale_if_error
    env = CacheEnvelope(
        value=value,
        stored_at=now,
        soft_expires_at=soft_expires_at,
        hard_expires_at=hard_expires_at,
        is_negative=False,
    )
    await cache.set(key, wrap(env), ttl=backend_ttl)


# ── read_through_many (Plan 011 C5 / D-12) ────────────────────────────────────

BatchLoader = Callable[["list[str]"], Awaitable["dict[str, Any]"]]


async def read_through_many(
    cache: AsyncCache[str, Any],
    keys: list[str],
    loader: BatchLoader,
    policy: CachePolicy,
    *,
    type_hint: type | None = None,
    singleflight: SingleflightProtocol | None = None,
) -> dict[str, Any]:
    """
    Bulk counterpart of ``read_through`` — one round trip for the cache hits,
    ONE batched ``loader(missing_keys)`` call for the misses this call
    leads, sharing the SAME ``Singleflight`` instance/slots per key with
    plain ``read_through`` (a bulk read and a single read of the same key
    coalesce with each other rather than racing).

    Algorithm:
        1. ``get_many(keys)`` when ``cache`` satisfies ``BulkCache``, else a
           loop over ``get()`` — envelope-aware per key exactly like
           ``read_through``.
        2. fresh / negative-hit keys are resolved immediately.
        3. soft-stale keys are returned NOW and each spawns (at most) one
           background refresh through the SAME per-key ``Singleflight``
           slot ``read_through`` uses.
        4. Remaining (missing / hard-expired) keys are the ``missing`` set.
           With no ``singleflight``, ``loader(missing)`` is called directly,
           once. With a ``singleflight``, each missing key is offered to
           ``singleflight.do(key, ...)`` — a key that is a follower of a
           concurrent ``read_through()``/``read_through_many()`` call for
           the SAME key never triggers the batch loader; the batch loader
           fires (once) lazily, only if at least one key in this call's
           ``missing`` set actually wins leadership.
        5. Fresh values are wrapped + written back with ``set_many`` when
           available, else a loop over ``set()``.

    Args:
        cache: A started cache backend.
        keys: The final, already-namespaced cache keys.
        loader: ``async def loader(missing_keys: list[str]) -> dict[str, Any]``
            — a key ABSENT from the returned dict resolves to ``None`` and is
            negative-cached iff ``policy.negative_ttl`` is set (same per-key
            rule as ``read_through``).
        policy: Same ``CachePolicy`` semantics as ``read_through``.
        type_hint: Forwarded per key, same meaning as ``read_through``.
        singleflight: Optional coalescer — required for cross-call
            coalescing to have any effect; the coalescing key passed to it
            is always the final, already-namespaced key (Plan 010's tenant
            rule, retested for the bulk path).

    Returns:
        ``{key: value_or_None}`` — every requested key is present in the
        result (unlike the loader's own return dict, which may omit
        misses).

    Raises:
        Exception: Whatever ``loader()`` raises, propagated after every
            offered key's ``Singleflight`` slot (if any) has been cleared.
    """
    if not keys:
        return {}

    from varco_core.cache.base import BulkCache

    now = time.time()
    get_type_hint = None if policy.requires_envelope else type_hint

    if isinstance(cache, BulkCache):
        raw_map = await cache.get_many(keys, type_hint=get_type_hint)
    else:
        raw_map = {}
        for k in keys:
            v = await cache.get(k, type_hint=get_type_hint)
            if v is not None:
                raw_map[k] = v

    result: dict[str, Any] = {}
    missing: list[str] = []

    for k in keys:
        raw = raw_map.get(k)
        env = unwrap(raw) if raw is not None else None

        if raw is not None and env is None:
            record_cache_hit(cache=policy.name, kind="positive")
            result[k] = raw
            continue

        if env is not None:
            if env.is_negative:
                if env.hard_expires_at is None or now < env.hard_expires_at:
                    record_cache_hit(cache=policy.name, kind="negative")
                    result[k] = None
                    continue
                # expired negative entry — fall through to recompute below.
            else:
                hard_expired = env.hard_expires_at is not None and now >= env.hard_expires_at
                if not hard_expired:
                    value = coerce(env.value, type_hint)
                    soft_expired = env.soft_expires_at is not None and now >= env.soft_expires_at
                    if soft_expired:
                        record_cache_hit(cache=policy.name, kind="stale")
                        record_cache_stale_served(cache=policy.name, reason="soft_ttl")
                        if singleflight is not None:

                            async def _refresh(k: str = k) -> Any:
                                return await _load_one_and_store(cache, k, loader, policy)

                            singleflight.spawn_refresh(k, _refresh)
                        result[k] = value
                        continue
                    record_cache_hit(cache=policy.name, kind="positive")
                    result[k] = value
                    continue
        missing.append(k)

    if not missing:
        return result

    record_cache_miss(cache=policy.name)

    if singleflight is None or not policy.singleflight:
        batch = await loader(missing)
        to_store: dict[str, Any] = {}
        for k in missing:
            value = batch.get(k)
            result[k] = value
            to_store[k] = value
        await _store_many(cache, to_store, policy)
        return result

    # Singleflight path — the batch call is created LAZILY, and only if at
    # least one key in `missing` actually wins leadership; a key that
    # becomes a follower of a concurrent single-key read_through() call
    # never triggers it. Uses a plain lock + cached-result/-error dict
    # rather than a shared asyncio.Task/Future — deliberately, to avoid
    # asyncio's "exception was never retrieved" warning when multiple led
    # keys await the same batch outcome (a Future/Task's exception must be
    # retrieved exactly via .exception()/await from EVERY holder or asyncio
    # logs a warning at GC time; a manually re-raised cached exception has
    # no such bookkeeping).
    batch_lock = asyncio.Lock()
    shared: dict[str, Any] = {}

    async def _get_batch() -> dict[str, Any]:
        async with batch_lock:
            if "result" not in shared and "error" not in shared:
                try:
                    shared["result"] = await loader(missing)
                except BaseException as exc:  # noqa: BLE001
                    shared["error"] = exc
            if "error" in shared:
                raise shared["error"]
            batch_result: dict[str, Any] = shared["result"]
            return batch_result

    async def _wrapper(k: str) -> Any:
        batch = await _get_batch()
        value = batch.get(k)
        await _store(cache, k, value, policy)
        return value

    outcomes = await asyncio.gather(
        *(singleflight.do(k, (lambda k=k: _wrapper(k))) for k in missing),  # type: ignore[misc]
        return_exceptions=True,
    )
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
    # Every `outcome` that could have been a BaseException was re-raised
    # above — the remaining loop only ever sees `tuple[Any, bool]`, but mypy
    # doesn't narrow `outcomes`'s element type across the two separate loops.
    for k, (value, is_leader) in zip(missing, outcomes):  # type: ignore[misc]
        if not is_leader:
            record_stampede_suppressed(cache=policy.name)
        result[k] = value
    return result


async def _load_one_and_store(
    cache: AsyncCache[str, Any], key: str, loader: BatchLoader, policy: CachePolicy
) -> Any:
    batch = await loader([key])
    value = batch.get(key)
    await _store(cache, key, value, policy)
    return value


async def _store_many(
    cache: AsyncCache[str, Any], items: dict[str, Any], policy: CachePolicy
) -> None:
    """Write every ``(key, value)`` pair per ``policy`` — uses ``set_many``
    when available, else a loop over ``_store`` (per-key ``set``)."""
    from varco_core.cache.base import BulkCache

    if not policy.requires_envelope:
        to_set = {k: v for k, v in items.items() if v is not None}
        if not to_set:
            return
        if isinstance(cache, BulkCache):
            await cache.set_many(to_set, ttl=policy.effective_ttl())
        else:
            for k, v in to_set.items():
                await cache.set(k, v, ttl=policy.effective_ttl())
        return

    # Envelope mode — TTLs differ per key (negative vs positive), so store
    # one at a time even when BulkCache is available.
    for k, v in items.items():
        await _store(cache, k, v, policy)


__all__ = ["read_through", "read_through_many"]
