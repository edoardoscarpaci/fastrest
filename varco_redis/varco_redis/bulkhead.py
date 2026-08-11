"""
varco_redis.bulkhead
=====================
``RedisBulkhead`` — a distributed concurrency limiter, backed by Redis.

Plan 005, Phase 8, Step 88 (U-7's second leg). U-7's first leg — a distributed
*rate* limiter — already shipped as ``RedisRateLimiter``
(``varco_redis.rate_limit``). This module is the sibling primitive: rate
limiting bounds *how often* calls happen; a bulkhead bounds *how many* calls
are in flight at once, across every process talking to the same Redis. They
are genuinely different tools — a service can be well within its rate budget
and still overwhelm a downstream dependency with concurrent in-flight calls
(e.g. a burst of long-running requests arriving within the same second).

Architecture::

    varco_core.resilience.bulkhead.Bulkhead   (single-process semaphore)
        ↔ same public surface, different scope
    RedisBulkhead                              (THIS CLASS — cross-process)
        ↑ configured by
    varco_core.resilience.bulkhead.BulkheadConfig  (reused, not duplicated)
    RedisEventBusSettings                          (reused Redis connection config)

DESIGN: sorted set of holders, scored by acquisition time (mirrors RedisRateLimiter)
    ✅ ``ZCARD`` after pruning expired holders gives an O(log N) atomic
       occupancy check — same shape as ``RedisRateLimiter``'s sliding window,
       so the two modules are easy to read side by side.
    ✅ TTL-based eviction: a crashed holder's slot is reclaimed once its score
       falls outside the TTL window — no heartbeat process, no orphan cleanup
       job required.
    ❌ A holder whose call legitimately runs longer than ``slot_ttl`` has its
       slot reclaimed while still working — set ``slot_ttl`` generously above
       the slowest expected call, mirroring the job-lease TTL guidance
       (``technical_docs/features/job-scheduling-and-leases.md``: TTL should
       comfortably exceed the worst-case call duration).

DESIGN: acquire/release atomic via Lua, mirroring varco_redis.lock
    ✅ ``varco_redis.lock.RedisLock``'s token-guarded Lua release pattern is
       reused: each holder gets a unique token; release only removes that
       holder's own member, so a reclaimed/expired holder cannot accidentally
       remove a different (newer) holder that reused the same slot count.
    ✅ Acquire (prune + count + conditional add) is one round trip — no
       TOCTOU window between checking occupancy and claiming a slot.

DESIGN: same public surface as Bulkhead (call/protect/available_slots), not a subclass
    ✅ Drop-in for call sites already using ``Bulkhead.call()``/``.protect()``
       — no new abstraction to learn.
    ❌ Not literally an ``isinstance`` match with ``Bulkhead`` (there is no
       shared ABC in ``varco_core.resilience.bulkhead`` to implement — the
       existing module ships one concrete class, not an interface). Callers
       that need structural typing should use ``typing.Protocol`` matching
       ``call``/``protect``/``available_slots``.

Usage::

    from varco_redis.bulkhead import RedisBulkhead
    from varco_core.resilience.bulkhead import BulkheadConfig

    db_bulkhead = RedisBulkhead(BulkheadConfig(max_concurrent=10, max_wait=0.5))
    await db_bulkhead.connect()

    result = await db_bulkhead.call(fetch_user, user_id)

    @db_bulkhead.protect
    async def fetch_order(order_id: str) -> Order: ...

Thread safety:  ❌ Not thread-safe — use from a single asyncio event loop.
Async safety:   ✅ All methods are ``async def``.

📚 Docs
- 🔍 https://redis.io/commands/eval/
  EVAL — atomic Lua scripting, reused from ``varco_redis.lock``'s pattern.
- 🔍 https://redis.io/commands/zadd/
  ZADD — sorted set add with score (acquisition timestamp).
- 🔍 https://redis.io/commands/zremrangebyscore/
  ZREMRANGEBYSCORE — prunes holders whose TTL has expired.
"""

from __future__ import annotations

import functools
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import redis.asyncio as aioredis

from providify import Configuration, Inject, Provider
from varco_core.resilience.bulkhead import BulkheadConfig, BulkheadFullError
from varco_redis.config import RedisEventBusSettings

_logger = logging.getLogger(__name__)

_R = TypeVar("_R")

# ── Lua scripts ──────────────────────────────────────────────────────────────
#
# Mirrors varco_redis.lock's token-guarded pattern and varco_redis.rate_limit's
# sliding-window prune-then-check shape.
#
# KEYS[1]  — the Redis sorted set key for this bulkhead's holder set.
# ARGV[1]  — current Unix timestamp (float as string).
# ARGV[2]  — expiry boundary = now - slot_ttl (float as string). Holders scored
#            below this have crashed without releasing — prune them.
# ARGV[3]  — max_concurrent (the slot limit).
# ARGV[4]  — this holder's unique token (sorted set member).
# ARGV[5]  — key TTL in seconds, so an idle bulkhead's key is auto-cleaned.
#
# Returns: 1 if the slot was claimed (added), 0 if the bulkhead is full.
_ACQUIRE_SCRIPT = """
local key        = KEYS[1]
local now        = tonumber(ARGV[1])
local expiry_low = tonumber(ARGV[2])
local max_slots  = tonumber(ARGV[3])
local token      = ARGV[4]
local key_ttl    = tonumber(ARGV[5])

-- Reclaim slots held by holders that crashed without releasing (TTL expired).
redis.call('ZREMRANGEBYSCORE', key, 0, expiry_low)

local count = redis.call('ZCARD', key)

if count < max_slots then
    redis.call('ZADD', key, now, token)
    redis.call('EXPIRE', key, key_ttl)
    return 1
end

return 0
"""

# KEYS[1]  — the holder set key.
# ARGV[1]  — this holder's token to remove.
#
# Returns: number of members removed (0 or 1).
_RELEASE_SCRIPT = """
return redis.call('ZREM', KEYS[1], ARGV[1])
"""


class RedisBulkhead:
    """
    Distributed concurrency limiter — a cross-process ``Bulkhead``.

    Like ``varco_core.resilience.Bulkhead`` and ``CircuitBreaker``, a
    ``RedisBulkhead`` should be instantiated ONCE per external dependency and
    shared across all callers within a process; unlike ``Bulkhead``, the slot
    count itself is shared across every process pointed at the same Redis key
    — so ``max_concurrent`` bounds *fleet-wide* concurrency, not per-pod
    concurrency.

    Args:
        config:      Reused ``varco_core.resilience.bulkhead.BulkheadConfig``
                     — ``max_concurrent`` is the fleet-wide slot count;
                     ``max_wait`` behaves the same as the in-process
                     ``Bulkhead`` (0.0 = fail-fast, > 0 = bounded poll-wait).
        settings:    Redis connection settings. Defaults to env-based
                     ``RedisEventBusSettings()``.
        slot_ttl:    Seconds after which an unreleased slot is reclaimed —
                     the "crashed holder" recovery mechanism. Must comfortably
                     exceed the slowest expected call; too small reclaims a
                     legitimately still-running holder's slot. Defaults to
                     ``60.0``.
        name:        Human-readable name for logging and error messages.

    Thread safety:  ❌ Not thread-safe. Use from a single asyncio event loop.
    Async safety:   ✅ All methods are ``async def``.

    Edge cases:
        - ``call()``/``protect()`` before ``connect()`` raises ``RuntimeError``.
        - A holder that crashes without calling ``release()`` has its slot
          reclaimed once ``slot_ttl`` seconds pass — no manual cleanup needed.
        - ``max_wait > 0`` polls (no Redis blocking-wait primitive exists for
          this pattern) at a fixed interval — see ``_POLL_INTERVAL``. Very
          small ``max_wait`` values may not get a chance to poll at all.

    Example::

        db_bulkhead = RedisBulkhead(BulkheadConfig(max_concurrent=10, max_wait=0.5))
        await db_bulkhead.connect()
        try:
            result = await db_bulkhead.call(fetch_user, user_id)
        except BulkheadFullError:
            raise HTTPException(429, "Service busy — please retry")
        finally:
            await db_bulkhead.disconnect()
    """

    # Poll interval while waiting for a slot under max_wait > 0.
    _POLL_INTERVAL = 0.05

    def __init__(
        self,
        config: BulkheadConfig | None = None,
        *,
        settings: RedisEventBusSettings | None = None,
        slot_ttl: float = 60.0,
        name: str = "redis-bulkhead",
    ) -> None:
        """
        Args:
            config:   Bulkhead configuration. Defaults to
                      ``BulkheadConfig(max_concurrent=10)`` when omitted —
                      matches the DI-resolvable default (providify cannot
                      inject a frozen dataclass positional arg with no
                      registered binding; see the module's DI notes).
            settings: Redis connection settings. ``None`` → env-based
                      ``RedisEventBusSettings()``.
            slot_ttl: Seconds before an unreleased slot is reclaimed.
            name:     Human-readable name for logging/error messages.

        Raises:
            ValueError: ``slot_ttl <= 0``.
        """
        if slot_ttl <= 0:
            raise ValueError(
                f"RedisBulkhead.slot_ttl must be positive; got {slot_ttl}."
            )
        self.config = config or BulkheadConfig(max_concurrent=10)
        self.settings = settings or RedisEventBusSettings()
        self.slot_ttl = slot_ttl
        self.name = name
        self._redis: aioredis.Redis | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """
        Open the Redis connection. Idempotent.

        Raises:
            redis.asyncio.RedisError: If Redis is unreachable.
        """
        if self._redis is not None:
            return
        self._redis = aioredis.from_url(
            self.settings.url,
            decode_responses=False,
            socket_timeout=self.settings.socket_timeout,
            **self.settings.redis_kwargs,
        )
        _logger.debug(
            "RedisBulkhead '%s' connected (max_concurrent=%d, slot_ttl=%.1fs).",
            self.name,
            self.config.max_concurrent,
            self.slot_ttl,
        )

    async def disconnect(self) -> None:
        """Close the Redis connection. Idempotent."""
        if self._redis is None:
            return
        await self._redis.aclose()
        self._redis = None
        _logger.debug("RedisBulkhead '%s' disconnected.", self.name)

    async def __aenter__(self) -> "RedisBulkhead":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.disconnect()

    # ── Public surface — mirrors varco_core.resilience.Bulkhead ────────────────

    async def available_slots(self) -> int:
        """
        Number of concurrency slots currently free, fleet-wide.

        Prunes expired (crashed) holders before counting, so the value
        reflects true current occupancy, not stale holders. A snapshot —
        may change immediately after reading in a concurrent context.

        Raises:
            RuntimeError: ``connect()`` has not been called.
        """
        redis = self._require_redis()
        key = self._key()
        now = time.time()
        expiry_low = now - self.slot_ttl
        await redis.zremrangebyscore(key, 0, expiry_low)
        count = await redis.zcard(key)
        return max(0, self.config.max_concurrent - int(count))

    async def call(
        self,
        func: Callable[..., Awaitable[_R]],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> _R:
        """
        Call ``func`` through the distributed bulkhead.

        Acquires a fleet-wide slot before calling and releases it afterward
        (in a ``finally`` block — a slot is never leaked by a raised exception
        or task cancellation, only by process death, and ``slot_ttl``
        reclaims those).

        Args:
            func:     The async callable to execute within the bulkhead.
            *args:    Positional arguments forwarded to ``func``.
            **kwargs: Keyword arguments forwarded to ``func``.

        Returns:
            The return value of ``func(*args, **kwargs)``.

        Raises:
            RuntimeError:      ``connect()`` has not been called.
            BulkheadFullError: No slot available within ``config.max_wait``.
            Exception:         Any exception raised by ``func`` — propagated
                               after releasing the slot.
        """
        token = await self._acquire()
        try:
            return await func(*args, **kwargs)
        finally:
            await self._release(token)

    def protect(self, func: Callable) -> Callable:
        """
        Decorator that routes all calls to ``func`` through this bulkhead.

        Async-only — applying to a sync function raises ``TypeError``.

        Raises:
            TypeError: ``func`` is not an async function.
        """
        import asyncio  # noqa: PLC0415

        if not asyncio.iscoroutinefunction(func):
            raise TypeError(
                f"RedisBulkhead.protect() only supports async functions; "
                f"'{func.__qualname__}' is synchronous."
            )

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await self.call(func, *args, **kwargs)

        return wrapper

    # ── Internal helpers ───────────────────────────────────────────────────────

    async def _acquire(self) -> str:
        """
        Claim one fleet-wide slot, returning this holder's unique token.

        Raises:
            RuntimeError:      ``connect()`` has not been called.
            BulkheadFullError: No slot available within ``config.max_wait``.
        """
        import asyncio  # noqa: PLC0415

        redis = self._require_redis()
        key = self._key()
        token = uuid.uuid4().hex
        deadline = time.monotonic() + self.config.max_wait

        while True:
            now = time.time()
            expiry_low = now - self.slot_ttl
            key_ttl = int(self.slot_ttl) + 1
            claimed = await redis.eval(  # type: ignore[misc]
                _ACQUIRE_SCRIPT,
                1,
                key,
                str(now),
                str(expiry_low),
                str(self.config.max_concurrent),
                token,
                str(key_ttl),
            )
            if claimed:
                _logger.debug(
                    "RedisBulkhead '%s' slot acquired (token=%s).", self.name, token
                )
                return token

            if self.config.max_wait <= 0.0 or time.monotonic() >= deadline:
                _logger.warning(
                    "RedisBulkhead '%s' full (%d max_concurrent).",
                    self.name,
                    self.config.max_concurrent,
                )
                raise BulkheadFullError(
                    self.name, self.config.max_concurrent, self.config.max_wait
                )

            await asyncio.sleep(
                min(self._POLL_INTERVAL, max(0.0, deadline - time.monotonic()))
            )

    async def _release(self, token: str) -> None:
        """
        Release this holder's slot. Does not raise — logs and swallows any
        Redis error, mirroring ``RedisLock.release()``'s contract, since a
        release failure must never mask the wrapped call's own result.
        """
        try:
            redis = self._require_redis()
            await redis.eval(_RELEASE_SCRIPT, 1, self._key(), token)  # type: ignore[misc]
            _logger.debug(
                "RedisBulkhead '%s' slot released (token=%s).", self.name, token
            )
        except Exception as exc:  # noqa: BLE001
            _logger.error(
                "RedisBulkhead '%s' release failed for token=%s: %s",
                self.name,
                token,
                exc,
            )

    def _key(self) -> str:
        """Full Redis key for this bulkhead's holder set."""
        return f"{self.settings.channel_prefix}bulkhead:{self.name}"

    def _require_redis(self) -> aioredis.Redis:
        if self._redis is None:
            raise RuntimeError(
                "RedisBulkhead is not connected. "
                "Call await bulkhead.connect() or use it as an async context manager."
            )
        return self._redis

    def __repr__(self) -> str:
        connected = self._redis is not None
        return (
            f"RedisBulkhead("
            f"name={self.name!r}, "
            f"max_concurrent={self.config.max_concurrent}, "
            f"connected={connected})"
        )


# ── RedisBulkheadConfiguration ──────────────────────────────────────────────
#
# DESIGN: opt-in @Configuration, not an auto-scanned @Singleton
#   ✅ Mirrors RedisCacheConfiguration — a bulkhead's max_concurrent is a
#      per-external-dependency choice, not a package-wide default that a bare
#      container.scan("varco_redis") should silently activate (CLAUDE.md
#      pitfall: "Policy authorizer silently active" — the same reasoning
#      applies to any resource with app-specific tuning).
#   ✅ The provider connects the instance before returning it, so
#      Inject[RedisBulkhead] is immediately usable — no separate connect()
#      call required at each call site (matches RedisCacheConfiguration's
#      started-cache contract).
#   ❌ Only one shared default instance per container — apps protecting
#      multiple distinct external dependencies with different limits
#      construct additional named instances by hand and register them via
#      ``container.provide()`` (CLAUDE.md: override ordering — before
#      ``ainstall()``, or ``@Provider(priority=100)``).


@Configuration
class RedisBulkheadConfiguration:
    """
    Providify ``@Configuration`` that wires a default ``RedisBulkhead`` into
    the container.

    Provides:
        ``RedisBulkhead`` — a connected instance with
                            ``BulkheadConfig(max_concurrent=10)`` (fail-fast).

    Lifecycle:
        Connected inside the provider; disconnected automatically by
        ``await container.ashutdown()``.

    Example::

        container = DIContainer()
        await container.ainstall(RedisBulkheadConfiguration)
        db_bulkhead = await container.aget(RedisBulkhead)
        result = await db_bulkhead.call(fetch_user, user_id)
        await container.ashutdown()

    Overriding the config::

        @Provider(singleton=True)
        def bulkhead_config() -> BulkheadConfig:
            return BulkheadConfig(max_concurrent=25, max_wait=0.5)

        container.provide(bulkhead_config)          # before ainstall()
        await container.ainstall(RedisBulkheadConfiguration)
    """

    @Provider(singleton=True)
    async def redis_bulkhead(
        self, settings: Inject[RedisEventBusSettings]
    ) -> RedisBulkhead:
        """
        Create and connect the default ``RedisBulkhead`` singleton.

        Args:
            settings: ``RedisEventBusSettings`` — injected from the container.

        Returns:
            A connected ``RedisBulkhead`` bound to the ``RedisBulkhead`` type.
        """
        instance = RedisBulkhead(settings=settings)
        await instance.connect()
        _logger.info(
            "RedisBulkheadConfiguration: RedisBulkhead connected "
            "(max_concurrent=%d).",
            instance.config.max_concurrent,
        )
        return instance


__all__ = ["RedisBulkhead", "RedisBulkheadConfiguration"]
