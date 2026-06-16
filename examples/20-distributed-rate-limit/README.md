# Example 20 — Distributed Rate Limiting

Demonstrates `varco_redis.RedisRateLimiter` for distributed (multi-pod) rate limiting
versus `varco_core.resilience.InMemoryRateLimiter` (per-process only).

## What you will learn

| Concept | Where |
|---------|-------|
| `RedisRateLimiter` — sliding-window counter in Redis, shared across all pods | `limiters.py`, `app.py` |
| `InMemoryRateLimiter` — local counter, one per process | `limiters.py`, `router.py` |
| Why `InMemoryRateLimiter` gives N× the configured rate in a multi-pod deploy | `router.py` endpoint comments |
| Async lifecycle of `RedisRateLimiter` (`connect` / `disconnect`) | `app.py` lifespan |
| Pre-injected limiter for test isolation (F17 pattern) | `tests/test_smoke.py` |
| Mapping a denied request to HTTP 429 with `Retry-After` | `router.py` |

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/v1/weather` | Weather data protected by Redis rate limiter (distributed) |
| `GET` | `/v1/weather/in-mem` | Weather data protected by in-memory rate limiter (per-process) |
| `GET` | `/v1/rate-limit/stats` | Current counters and exhaustion state for both limiters |
| `GET` | `/health` | Health check |

## Running

```bash
# Start Redis
docker run -d -p 6379:6379 redis:7-alpine

# From the workspace root
uv run uvicorn examples.20-distributed-rate-limit.app:app --reload
# → http://localhost:8000
```

## Running tests

```bash
# From the workspace root — requires Docker for Redis via testcontainers
uv run pytest examples/20-distributed-rate-limit/tests/ -v -m integration
```

## Key insight: per-pod vs. distributed counters

With `InMemoryRateLimiter(rate=3, period=1.0)` and **two pods**:

```
Pod A: allows 3 calls/second
Pod B: allows 3 calls/second
Total: 6 calls/second reach the backend  ← 2× the intended limit
```

With `RedisRateLimiter(rate=3, period=1.0)` and **two pods**:

```
Pod A + Pod B share a Redis sorted set
Total: exactly 3 calls/second reach the backend  ← correct
```

## Rate limiter comparison

| Feature | `InMemoryRateLimiter` | `RedisRateLimiter` |
|---------|-----------------------|--------------------|
| Storage | Per-process `deque` | Redis sorted set |
| Async lifecycle | None | `connect()` / `disconnect()` |
| Multi-pod safe | No — N× rate | Yes — shared counter |
| Latency | Zero (local) | Redis round-trip |
| Dependency | None | Redis |
| Package | `varco_core.resilience` | `varco_redis` |

## File layout

```
20-distributed-rate-limit/
├── app.py        # create_app(redis_url) — wires limiters + exception handler
├── limiters.py   # build_redis_limiter() + build_in_memory_limiter() factories
├── router.py     # Weather endpoints with inline acquire() + 429 response
├── tests/
│   ├── __init__.py
│   ├── conftest.py   # sys.path setup
│   └── test_smoke.py # Integration tests (7 scenarios)
└── README.md
```
