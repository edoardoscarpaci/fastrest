"""
app.py
======
Application factory for the distributed rate-limiting example.

``create_app(redis_url)`` returns a FastAPI application that demonstrates:

- ``RedisRateLimiter``   — rate counter stored in Redis; shared across all pods.
- ``InMemoryRateLimiter`` — rate counter per-process; each pod has its own.
- ``@rate_limit``-compatible pattern via direct ``acquire()`` in handlers.

Lifecycle
---------
``RedisRateLimiter`` requires ``connect()`` / ``disconnect()`` before and after
use.  The app factory supports two modes:

1. **Production** — pass only ``redis_url``.  The lifespan context manager
   calls ``connect()`` at startup and ``disconnect()`` at shutdown.

2. **Test** — pass a pre-connected ``redis_limiter``.  The lifespan skips the
   lifecycle calls (``ASGITransport`` does not trigger FastAPI lifespan).

``InMemoryRateLimiter`` has no async lifecycle — it is always used as-is.

DESIGN: optional pre-built redis_limiter for test isolation
    ✅ ``ASGITransport`` does NOT trigger FastAPI lifespan.  Accepting a
       pre-connected ``redis_limiter`` lets tests manage lifecycle in a fixture
       while keeping production usage simple.  (See F17 in FINDINGS.md.)
    ❌ Adds an extra parameter; callers must know to use it in tests.

Thread safety:  ❌  Single asyncio event loop.
Async safety:   ✅  ``RedisRateLimiter`` lifecycle is managed by the lifespan
                    context or the caller's fixture.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from limiters import build_in_memory_limiter, build_redis_limiter
from router import build_router
from varco_core.resilience.rate_limit import InMemoryRateLimiter, RateLimitExceededError
from varco_redis.rate_limit import RedisRateLimiter


def create_app(
    redis_url: str,
    *,
    rate: int = 3,
    period: float = 1.0,
    redis_limiter: RedisRateLimiter | None = None,
    in_mem_limiter: InMemoryRateLimiter | None = None,
) -> FastAPI:
    """
    Create and return a configured FastAPI application.

    Args:
        redis_url:      Redis connection URL (e.g. ``"redis://localhost:6379/0"``).
        rate:           Max calls per ``period``.  Applied to both limiters when
                        building defaults.  Defaults to 3.
        period:         Rolling window in seconds.  Defaults to 1.0.
        redis_limiter:  Pre-built (and pre-connected) ``RedisRateLimiter``.
                        If ``None``, one is built from ``redis_url``/``rate``/``period``
                        and managed by the lifespan.  Pass this in tests.
        in_mem_limiter: Pre-built ``InMemoryRateLimiter``.  If ``None``, one is
                        built from ``rate``/``period``.

    Returns:
        A ``FastAPI`` instance ready to serve requests.

    Edge cases:
        - If Redis is unreachable and no ``redis_limiter`` is provided,
          ``redis_limiter.connect()`` will raise during lifespan startup.
        - The in-memory limiter starts with an empty window — first ``rate``
          calls always succeed.

    Example::

        app = create_app("redis://localhost:6379/0", rate=10)
    """
    _redis_limiter = redis_limiter or build_redis_limiter(
        redis_url, rate=rate, period=period
    )
    _in_mem_limiter = in_mem_limiter or build_in_memory_limiter(
        rate=rate, period=period
    )
    # Only manage lifecycle when we built the limiter ourselves.
    _manage_redis = redis_limiter is None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        """Connect / disconnect Redis limiter around the ASGI lifecycle."""
        if _manage_redis:
            await _redis_limiter.connect()
        try:
            yield
        finally:
            if _manage_redis:
                await _redis_limiter.disconnect()

    app = FastAPI(
        title="Distributed Rate Limiting — Weather API",
        description=(
            "Demonstrates RedisRateLimiter (distributed) vs "
            "InMemoryRateLimiter (per-process) using varco_redis + varco_core."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    # Global exception handler: convert RateLimitExceededError → 429.
    # This handles the case where @rate_limit decorator is used elsewhere.
    @app.exception_handler(RateLimitExceededError)
    async def _rate_limit_handler(request, exc: RateLimitExceededError) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={
                "detail": str(exc),
                "retry_after": exc.retry_after,
            },
            headers={"Retry-After": str(max(1, int(exc.retry_after) + 1))},
        )

    app.include_router(build_router(_redis_limiter, _in_mem_limiter))
    return app


__all__ = ["create_app"]
