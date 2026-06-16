# Example 13 — Layered Cache (L1 In-Memory + L2 Memcached)

Demonstrates `LayeredCache` from `varco_core.cache` composing an in-process
`InMemoryCache` (L1) with a `MemcachedCache` from `varco_memcached` (L2).

## What it shows

| Concept | Implementation |
|---|---|
| Two-tier cache | `LayeredCache(InMemoryCache, MemcachedCache, write_mode="write-through")` |
| Promote-on-read | L2 hit is written back to L1 with `promote_ttl` to warm local cache |
| Write-through | Writes propagate to both L1 and L2 simultaneously |
| Invalidation | `LayeredCache.delete()` evicts from all layers in one call |
| Test isolation | `NoOpCache` injected in unit tests — no Docker required |

## Architecture

```
GET /v1/products/{id}
        ↓
  L1 InMemoryCache  (30 s TTL, process-local, zero network cost)
        ↓ miss
  L2 MemcachedCache (5 min TTL, shared across processes)
        ↓ miss
  ProductStore      (authoritative in-memory fake DB)
        ↑
  promote back to L1 on L2 hit
```

## Infrastructure

| Service | Purpose |
|---|---|
| Memcached 1.6 | L2 shared cache |

## Run locally

```bash
docker run -d -p 11211:11211 memcached:1.6-alpine
cd examples/13-layered-cache-memcached
uv run uvicorn app:app --reload
```

```bash
# Create a product
curl -X POST http://localhost:8000/v1/products \
     -H "Content-Type: application/json" \
     -d '{"id": "p-1", "name": "Widget", "price": 9.99}'

# First GET — cache miss, falls through to store, warms L1+L2
curl http://localhost:8000/v1/products/p-1

# Second GET — served from L1 (in-process)
curl http://localhost:8000/v1/products/p-1

# Cache stats
curl http://localhost:8000/v1/cache/stats
```

## Run unit tests (no Docker required)

```bash
uv run pytest examples/13-layered-cache-memcached/tests/test_unit.py -v
```

## Run integration tests (requires Docker)

```bash
uv run pytest examples/13-layered-cache-memcached/tests/test_smoke.py -v -m integration
```

## Key design notes

- `LayeredCache` handles promote-on-read automatically — no manual L1 write needed.
- `write_mode="write-through"` (default) writes to all layers on every `set()`.
- `write_mode="write-around"` writes only to L2 — use this when writes are rare
  and you don't want to pollute the L1 on every PUT.
- `NoOpCache` makes unit tests trivially fast — every `get()` returns `None` so
  all reads fall through to the store, testing the full service path without Memcached.
- The `key_prefix` in `MemcachedCacheSettings` provides namespace isolation when
  multiple apps share the same Memcached server.
