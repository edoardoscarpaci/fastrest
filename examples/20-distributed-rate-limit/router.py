"""
router.py
=========
FastAPI router for the weather API rate-limiting example.

Endpoints
---------
``GET /v1/weather``          — protected by ``RedisRateLimiter`` (distributed).
``GET /v1/weather/in-mem``   — protected by ``InMemoryRateLimiter`` (per-process).
``GET /v1/rate-limit/stats`` — show current counters for both limiters.
``GET /health``              — health check.

Each endpoint calls ``await limiter.acquire(key)`` directly rather than using
the ``@rate_limit`` decorator so the router can return an HTTP 429 response
with a ``Retry-After`` header and a JSON body.

DESIGN: direct acquire() over @rate_limit decorator
    ✅ Lets us return a proper 429 JSONResponse with ``Retry-After`` header —
       the decorator raises ``RateLimitExceededError`` which the exception handler
       converts, but that requires a registered handler to produce the right body.
    ✅ Clearer for demonstration — the reader sees the rate-limit check inline.
    ❌ A bit more verbose than ``@rate_limit``; production code may prefer the
       decorator if a global exception handler is already in place.

Thread safety:  ❌  Single asyncio event loop.
Async safety:   ✅  All handlers are ``async def``.
"""

from __future__ import annotations

import math

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from varco_core.resilience.rate_limit import InMemoryRateLimiter
from varco_redis.rate_limit import RedisRateLimiter


def build_router(
    redis_limiter: RedisRateLimiter,
    in_mem_limiter: InMemoryRateLimiter,
) -> APIRouter:
    """
    Build and return the FastAPI ``APIRouter`` for the weather API.

    Args:
        redis_limiter:  Connected ``RedisRateLimiter`` (distributed).
        in_mem_limiter: ``InMemoryRateLimiter`` (per-process).

    Returns:
        An ``APIRouter`` with all weather + health endpoints registered.

    DESIGN: closures over limiter instances instead of app.state
        ✅ No dependency on ``Request.app.state`` — limiters are injected at
           build time, making the router composable and testable in isolation.
        ❌ Can't swap limiters at runtime — intentional for this example.
    """
    router = APIRouter()

    # ── Helper ─────────────────────────────────────────────────────────────────

    def _rate_limited_response(retry_after: float) -> JSONResponse:
        """Build a ``429 Too Many Requests`` response."""
        # Round up so the client waits at least the minimum amount.
        retry_ceil = math.ceil(retry_after) if retry_after > 0 else 1
        return JSONResponse(
            status_code=429,
            content={
                "detail": (
                    f"Rate limit exceeded. " f"Retry after {retry_after:.3f} seconds."
                ),
                "retry_after": retry_after,
            },
            headers={"Retry-After": str(retry_ceil)},
        )

    # ── Health ─────────────────────────────────────────────────────────────────

    @router.get("/health")
    async def health() -> dict:
        """
        Health check endpoint.

        Returns:
            ``{"status": "ok"}``
        """
        return {"status": "ok"}

    # ── Redis-backed weather endpoint ──────────────────────────────────────────

    @router.get("/v1/weather")
    async def get_weather() -> JSONResponse:
        """
        Return fake weather data, protected by the distributed Redis rate limiter.

        Rate limit: ``redis_limiter.config.rate`` calls per ``redis_limiter.config.period``
        seconds, shared across **all** processes / pods that connect to the same
        Redis instance.

        Returns:
            200 with fake weather JSON, or 429 if the rate limit is exceeded.
        """
        allowed = await redis_limiter.acquire("weather:redis")
        if not allowed:
            retry_after = await redis_limiter.retry_after("weather:redis")
            return _rate_limited_response(retry_after)

        return JSONResponse(
            status_code=200,
            content={
                "source": "redis-limiter",
                "temperature_c": 22.5,
                "condition": "sunny",
                "note": (
                    "Counter stored in Redis — shared across all pods. "
                    f"Limit: {redis_limiter.config.rate} req/"
                    f"{redis_limiter.config.period}s"
                ),
            },
        )

    # ── In-memory weather endpoint ─────────────────────────────────────────────

    @router.get("/v1/weather/in-mem")
    async def get_weather_in_mem() -> JSONResponse:
        """
        Return fake weather data, protected by the in-memory (per-process) rate limiter.

        Rate limit: ``in_mem_limiter.config.rate`` calls per ``in_mem_limiter.config.period``
        seconds **per process**.  In a multi-pod deploy, each pod allows this
        many calls independently — the effective cluster-wide rate is N × configured
        rate.

        Returns:
            200 with fake weather JSON, or 429 if the rate limit is exceeded.
        """
        allowed = await in_mem_limiter.acquire("weather:in-mem")
        if not allowed:
            retry_after = await in_mem_limiter.retry_after("weather:in-mem")
            return _rate_limited_response(retry_after)

        return JSONResponse(
            status_code=200,
            content={
                "source": "in-memory-limiter",
                "temperature_c": 22.5,
                "condition": "partly cloudy",
                "note": (
                    "Counter stored in-process — NOT shared across pods. "
                    f"Limit: {in_mem_limiter.config.rate} req/"
                    f"{in_mem_limiter.config.period}s per process"
                ),
            },
        )

    # ── Stats endpoint ─────────────────────────────────────────────────────────

    @router.get("/v1/rate-limit/stats")
    async def rate_limit_stats() -> dict:
        """
        Return current rate-limit counters for both limiters.

        Queries ``retry_after()`` on both limiters to report whether the budget
        is currently exhausted and how long until a slot frees up.

        Returns:
            Dict with stats for ``redis`` and ``in_memory`` limiters.
        """
        redis_wait = await redis_limiter.retry_after("weather:redis")
        in_mem_wait = await in_mem_limiter.retry_after("weather:in-mem")

        return {
            "redis_limiter": {
                "rate": redis_limiter.config.rate,
                "period": redis_limiter.config.period,
                "budget_exhausted": redis_wait > 0.0,
                "retry_after_seconds": redis_wait,
            },
            "in_memory_limiter": {
                "rate": in_mem_limiter.config.rate,
                "period": in_mem_limiter.config.period,
                "budget_exhausted": in_mem_wait > 0.0,
                "retry_after_seconds": in_mem_wait,
            },
        }

    return router


__all__ = ["build_router"]
