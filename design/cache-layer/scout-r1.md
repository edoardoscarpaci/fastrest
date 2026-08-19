# Scout report — R1 (cache hardening: C1, C2, C3, C4)

Read-only reconnaissance of every part of the codebase the four R1 backlog items touch.

## 1. `varco_core/cache/` inventory

```
varco_core/varco_core/cache/
├── base.py            AsyncCache (runtime_checkable Protocol), CacheBackend (ABC:
│                      start()/stop() lifecycle, warmers, no-op defaults),
│                      InvalidationStrategy (ABC, own start()/stop())
├── invalidation.py    TTLStrategy, ExplicitStrategy, TaggedStrategy,
│                      CompositeStrategy, EventDrivenStrategy
│                      (all stateless except Explicit/Tagged)
├── memory.py          InMemoryCache (dict-backed, max_size FIFO eviction,
│                      per-entry metadata via `_Entry`), NoOpCache
├── layered.py         LayeredCache — L1/L2, write-through default,
│                      read-promote with `promote_ttl`. NO L1 coherence backplane.
├── decorator.py       @cached — async look-aside; no stampede protection;
│                      `None` is not cached (→ no negative caching today)
├── mixin.py/service.py CacheServiceMixin, CacheInvalidated event
└── warming.py/consumer.py CacheWarmer, CacheInvalidationConsumer
```

Anchors: `base.py:66` (Protocol + ABC), `memory.py:88` (InMemoryCache),
`layered.py:81` (LayeredCache), `decorator.py:108` (@cached).

## 2. `varco_redis` cache surface

- `varco_redis/varco_redis/cache.py:135` — `RedisCache` on `redis.asyncio`;
  native `SETEX`/`EXPIRE`; optional `InvalidationStrategy`; pluggable serializer.
- `varco_redis/varco_redis/cache.py:65` — `RedisCacheSettings`
  (`key_prefix`, `decode_responses` must be `False`, `socket_timeout`,
  `env_prefix="VARCO_REDIS_CACHE_"`).
- Pub/sub invalidation exists only at the application level today
  (`EventDrivenStrategy` + `CacheInvalidated` over the event bus).
  No RESP3 client-side-caching integration.

## 3. Distributed-primitive precedents (house style to imitate)

- `varco_redis/varco_redis/rate_limit.py` — `RedisRateLimiter`,
  `SLIDING_WINDOW_SCRIPT` Lua, one shared instance per external dependency,
  async client, idempotent.
- `varco_redis/varco_redis/bulkhead.py` — `RedisBulkhead`, sorted-set semaphore
  with TTL reclaim, same `call()`/`protect()`/`available_slots()` surface as the
  in-process `Bulkhead`.
- `varco_redis/varco_redis/lock.py` — `RedisLock`, Lua compare-and-delete release.
- `varco_sa` — `SAAdvisoryLock` / `SAXactAdvisoryLock` (transaction-scoped variant
  is the pooler-safe one).

**Shape**: shared singleton, Lua for atomicity, async client, in-process twin with
an identical surface so callers swap one for the other.

## 4. Observability integration points (C3)

- `varco_core/varco_core/observability/metric.py:120` — `Metric` (push-based,
  kwargs → attributes, safe to construct at module level).
- `varco_core/varco_core/observability/reliability.py:151` —
  `ReliabilityMetricsConfig` + `install_reliability_metrics()`. **This is the
  "metrics pack" shape a cache pack should mirror**: a plain install function,
  called manually, deliberately NOT a scanned `@Configuration`.
- `varco_core/varco_core/observability/attributes.py` — `wrap_instrument()`,
  the single instrument-creation choke point that stamps global attributes on
  every measurement. Any cache instrument must be created through it.

## 5. Tenancy / scoping

- `varco_core/varco_core/tenancy/cache_key.py:15` — `tenancy_cache_key()`
  returns `tenant:{tenant_id}:entity:key` or `global:entity:key`; **fails closed**
  (RuntimeError) for a `TENANT`-scoped entity outside `tenant_context()`.
- Relevant to C1: a backplane broadcasts key *names* across nodes, so keys are
  already tenant-namespaced — no extra tenant field needed on the message, but the
  message payload becomes cross-tenant-visible metadata on a shared channel.

## 6. Existing tests

- `varco_core/tests/test_cache.py:44` — unit tests; `InMemoryEventBus` + `.drain()`
  for event ordering.
- `varco_redis/tests/test_redis_cache.py:36` — `FakeRedis` mock (dict-backed,
  no TTL enforcement).
- `@pytest.mark.integration` — real Redis via Docker, skipped by default.
- **Absent**: multi-pod L1 coherence tests, any stampede reproduction,
  tenancy isolation under concurrency.

## 7. DI wiring

- `varco_redis/di.py` + `RedisCacheConfiguration` (opt-in `@Configuration` for
  resources needing imperative async setup).
- A new backplane / singleflight component registers here, following the
  "backend `di.py` `bootstrap()` + `container.scan()`" pattern.

## 8. Blockers / landmines

- **C2** — no decision yet on per-pod vs distributed singleflight; must be
  tenant-aware for `TENANT`-scoped entities.
- **C1** — the RESP3 path needs custom negotiation; `redis-py`'s support is basic,
  not a high-level API. Choosing it implies a Redis 6.0+ floor, changing varco's
  dependency story.
- **C3** — L1 vs L2 breakdown (tag-based attribute or separate meters?) and the
  per-operation latency cost are undecided.
- **C4** — SWR × singleflight interaction is the real risk: coalescing a fresh
  recompute while concurrently returning stale is a race that needs an explicit
  design, not two independent features.
- **C4** — `@cached` not caching `None` today means negative caching changes an
  existing, observable behaviour.
- **C1** — `LayeredCache`'s write-through ordering (L2 then L1, or the reverse)
  determines whether a backplane message can arrive before the L2 write lands.

## 9. What research brief 001 already settles (do not re-research)

`design/cache-layer/research/001-mature-async-cache-2026.md`:

- **C2**: singleflight is the standard (Go groupcache, Cloudflare, .NET
  HybridCache, Spring) and is **absent from every Python async cache library** —
  the differentiating gap.
- **C1**: two legitimate paths — event-driven pub/sub (explicit, works on any
  Redis) or RESP3 client-side-caching push (transparent, Redis 6+).
- **C3**: Micrometer's `cache.hits` / `cache.misses` / `cache.puts` /
  `cache.evictions` / `cache.size` is the de-facto shape; **no OTel semconv exists**.
- **C4**: SWR + jitter + negative caching are settled industry practice;
  implementation is straightforward.

## Confidence: high

All four items are implementable within the current architecture with no
refactoring. Research settled the *what*; this maps the *where*. Remaining
decisions are design choices, not unknowns about the codebase.
