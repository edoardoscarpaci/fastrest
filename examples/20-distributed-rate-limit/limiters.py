"""
limiters.py
===========
Factory helpers for the rate-limiter instances used by the weather API.

Two limiters are created — one Redis-backed (distributed) and one in-memory
(per-process) — so the example can demonstrate their different semantics
side-by-side.

``build_redis_limiter`` and ``build_in_memory_limiter`` return configured
objects.  ``RedisRateLimiter`` has an async lifecycle (``connect`` / ``disconnect``
or async context manager), so the caller must manage it.

DESIGN: separate factory module over inline construction in app.py
    ✅ Keeps ``app.py`` focused on wiring; ``limiters.py`` owns the config choices.
    ✅ Tests can import only this module to build isolated limiter instances.
    ❌ Slight indirection — follow the import chain if confused about config.

Thread safety:  ❌  Not thread-safe; use within a single asyncio event loop.
Async safety:   ✅  Lifecycle is managed by the caller (app lifespan or test fixture).
"""

from __future__ import annotations

from varco_core.resilience.rate_limit import InMemoryRateLimiter, RateLimitConfig
from varco_redis.config import RedisEventBusSettings
from varco_redis.rate_limit import RedisRateLimiter


def build_redis_limiter(
    redis_url: str,
    *,
    rate: int = 3,
    period: float = 1.0,
) -> RedisRateLimiter:
    """
    Build a ``RedisRateLimiter`` for distributed rate limiting.

    The returned limiter is **not** yet connected.  The caller must call
    ``await limiter.connect()`` (or use it as an async context manager)
    before it can be used.

    Args:
        redis_url: Redis connection URL (e.g. ``"redis://localhost:6379/0"``).
        rate:      Maximum calls allowed per ``period``.  Defaults to 3.
        period:    Rolling window in seconds.  Defaults to 1.0.

    Returns:
        An unconnected ``RedisRateLimiter`` instance.

    DESIGN: pass redis_url as a string, not RedisEventBusSettings
        ✅ Simpler for examples — callers don't need to know about the settings
           class to build a limiter.
        ✅ ``RedisEventBusSettings(url=redis_url)`` picks safe defaults for all
           other settings (timeout, prefix, etc.).
        ❌ Advanced settings (e.g. TLS, auth) require callers to construct
           ``RedisEventBusSettings`` directly.

    Example::

        async with build_redis_limiter("redis://localhost:6379/0") as limiter:
            allowed = await limiter.acquire("weather:default")
    """
    config = RateLimitConfig(rate=rate, period=period)
    settings = RedisEventBusSettings(url=redis_url, channel_prefix="weather:")
    return RedisRateLimiter(config, settings=settings)


def build_in_memory_limiter(
    *,
    rate: int = 3,
    period: float = 1.0,
) -> InMemoryRateLimiter:
    """
    Build an ``InMemoryRateLimiter`` for single-process rate limiting.

    The in-memory limiter has no async lifecycle — it is ready to use
    immediately.  However, each process holds its own counter, so in a
    multi-pod deployment every pod allows ``rate`` calls/``period`` independently,
    giving an effective total of ``N × rate`` across N pods.

    Args:
        rate:   Maximum calls allowed per ``period``.  Defaults to 3.
        period: Rolling window in seconds.  Defaults to 1.0.

    Returns:
        An ``InMemoryRateLimiter`` instance.

    Example::

        limiter = build_in_memory_limiter(rate=5, period=1.0)
        allowed = await limiter.acquire("weather:default")
    """
    config = RateLimitConfig(rate=rate, period=period)
    return InMemoryRateLimiter(config)


__all__ = [
    "build_redis_limiter",
    "build_in_memory_limiter",
]
