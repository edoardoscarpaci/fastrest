# 12 — Cache Look-Aside (Redis)

Demonstrates **varco_redis** look-aside caching: `RedisCache`, `TTLStrategy`, `TaggedStrategy`, `CompositeStrategy`, and the `@cached` decorator applied to a product catalog service.

## What it shows

| Feature | Where |
|---------|-------|
| `RedisCache` + `RedisCacheSettings` | `cache_layer.py` |
| `TTLStrategy` — time-based eviction | `cache_layer.py` |
| `TaggedStrategy` — explicit per-entity invalidation | `cache_layer.py` |
| `CompositeStrategy` — combines TTL + Tagged | `cache_layer.py` |
| Look-aside `get` / `set` / `invalidate` | `cache_layer.py` |
| `CacheBackend` lifecycle (`start`/`stop`) | `app.py` |
| Hit/miss counter diagnostic endpoint | `router.py` |

## Quick start

```bash
# Start Redis
docker run -d -p 6379:6379 redis:7-alpine

# Run the app
cd examples/12-cache-look-aside-redis
uv run uvicorn app:app --reload
```

Then `POST /v1/products`, `GET /v1/products/{id}`, and watch `/v1/cache/stats` update.

## Run tests

```bash
# From workspace root (requires Docker)
uv run pytest .claude/worktrees/feature+examples-catalog/examples/12-cache-look-aside-redis/tests/ -v -m integration
```

## Invalidation strategy

```
CompositeStrategy(
    TTLStrategy(ttl_seconds=60),   # safety-net: entries expire after 60s
    TaggedStrategy(),              # explicit: PUT /products/{id} evicts immediately
)
```

On `PUT /v1/products/{id}`:
1. Update the in-memory store.
2. Call `cache_layer.invalidate_product(id)` → `TaggedStrategy.invalidate_tag("product:<id>")` + direct `cache.delete`.
3. Next `GET /v1/products/{id}` is a cache miss → re-fetches from store, re-populates cache.
