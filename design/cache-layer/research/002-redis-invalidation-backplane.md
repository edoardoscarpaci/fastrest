# Research 002 — Redis C1 invalidation backplane: CLIENT TRACKING vs. pub/sub

Date: 2026-08-19 · Freshness matters: **yes** — CLIENT TRACKING support status in redis-py changes with releases; pub/sub message durability patterns are stable.

## Question

For varco's C1 item — a cross-node L1 invalidation backplane for a two-tier `LayeredCache` (in-process L1 + Redis L2) in async Python — which mechanism should we build on: (a) **application-level pub/sub invalidation messages**, or (b) **Redis server-assisted client-side caching (RESP3 `CLIENT TRACKING` + invalidation push)**?

## Findings

### 1. Redis-py asyncio support for CLIENT TRACKING / server-assisted caching

**Status as of redis-py 5.1.0 (Jan 2026)**: The **async redis-py client does NOT support CLIENT TRACKING or server-assisted client-side caching**. Only the synchronous `redis.Redis` client provides this feature.

— [Async redis-py client does not support client-side caching / key tracking · Issue #3916 · redis/redis-py](https://github.com/redis/redis-py/issues/3916) (open, no timeline)

**Sync client API** (the only path available today):

```python
from redis.cache import CacheConfig

r = redis.Redis(
    host="localhost",
    port=6379,
    protocol=3,  # RESP3 required
    cache_config=CacheConfig(),  # uses invalidation table on server
)
```

The sync client was first introduced in redis-py **5.1.0** and requires `protocol=3` (RESP3). The `CacheConfig()` constructor accepts optional `eviction_policy` and `max_entries` parameters to tune local cache behaviour.

— [RESP3 Features - redis-py 8.1.0 documentation](https://redis.readthedocs.io/en/stable/resp3_features.html) (current, stable)

**Implication for varco**: A varco `LayeredCache` using CLIENT TRACKING would **require a separate non-async wrapper** around the sync `redis.Redis(cache_config=...)` to shield async code from blocking I/O. This defeats the purpose of async Python and introduces thread-pool overhead.

### 2. Constraints of server-assisted caching (CLIENT TRACKING)

**Minimum Redis/Valkey version**: 6.0.0 (released Jul 2019). Both Redis and Valkey implement the identical protocol; no proprietary extensions.

— [CLIENT TRACKING | Docs](https://redis.io/docs/latest/commands/client-tracking/) (current)
— [Valkey Command · CLIENT TRACKING](https://valkey.io/commands/client-tracking/)

**RESP3 protocol requirement**: 
- **Same-connection invalidations** (most efficient, cleanest): Requires RESP3 only. Server pushes `invalidate` messages to the tracking connection immediately.
- **Two-connection mode** (data + invalidation channels): Works with RESP3 (same-connection push) OR RESP2 (Pub/Sub `__redis__:invalidate` channel).

— [Client-side caching reference](https://redis.io/docs/latest/develop/reference/client-side-caching/) (comprehensive technical reference)

**Memory cost (server-side)**:
- **Default tracking mode** (server remembers per-client keys): Memory proportional to (number of tracked keys × number of clients). Server stores an *Invalidation Table* with a configurable max size (`tracking-table-max-keys`, default ~16M slots via CRC64 hashing). When the table fills, Redis evicts older entries and sends spurious invalidations to reclaim space.
- **Broadcasting mode (BCAST)** (`CLIENT TRACKING ON BCAST PREFIX foo:*`): **Zero server memory cost**. Server only matches key patterns; all clients subscribing to `PREFIX foo:*` get notified of all changes to keys matching `foo:*`, regardless of whether they read them. Higher message volume, lower server overhead.

**Proxy/gateway compatibility**: 
- **AWS ElastiCache**: CLIENT TRACKING is **NOT supported in ElastiCache Serverless** (as of 2026). Supported in ElastiCache self-managed Redis 6.0+.
- **Behind HTTP proxies / envoy / twemproxy**: CLIENT TRACKING requires a direct TCP connection to a Redis node. Proxies that multiplex connections typically break per-connection tracking state. Not recommended for client-side caching behind a proxy.
- **Redis Cluster**: CLIENT TRACKING works, but the invalidation table is **per-node**. A write to a key on node A invalidates clients connected to A; clients connected to B (even if reading the same key) are unaffected. Works correctly for clients that pin to a single node (connection pool + consistent hashing).

— [Problem with client caching when using AWS Elasticache redis | AWS re:Post](https://repost.aws/questions/QUpe4ORRjLRkaYXWmFc_lSog/problem-with-client-caching-when-using-aws-elasticache-redis)
— [Best practices: Valkey/Redis OSS clients and Amazon ElastiCache | AWS](https://aws.amazon.com/blogs/database/best-practices-valkey-redis-oss-clients-and-amazon-elasticache/)

**Reconnection and connection pools**:
- **On disconnect**: Local cache is no longer guaranteed to be valid. Redis client libraries document this explicitly: clients must flush their local cache on any connection loss to avoid serving stale data forever.
- **Shared connection pools**: CLIENT TRACKING state is **per-connection**. A single Redis connection can only track keys for one logical client. If a connection pool rotates a connection among multiple code paths, the tracking state is shared — invalidations intended for one consumer may arrive at another. **Workaround**: dedicated non-pooled connections for caching, or two-connection mode where all clients redirect invalidations to a single listener.

— [What to do when losing connection with the server](https://redis.io/docs/latest/develop/reference/client-side-caching/#what-to-do-when-losing-connection-with-the-server)

**BCAST vs. default tracking modes**:

| Mode | Tracking cost | Message volume | Use case |
|------|---|---|---|
| **Default (per-key)** | O(tracked keys × clients) server memory | Only invalidate keys the client read | Dense caching, few keys, many clients |
| **BCAST PREFIX** | O(prefix count) server CPU | All keys matching prefix(es) | Sparse workloads, high client count |
| **OPTIN** | O(tracked keys × clients) | Only keys explicitly marked with `CLIENT CACHING YES` | Selective caching of expensive queries |

### 3. Pub/Sub as the invalidation backplane: pitfalls

Redis Pub/Sub is a fire-and-forget messaging model with these critical limitations for a **durable, cross-node cache invalidation channel**:

**Message loss on disconnect**: When a subscriber disconnects, Redis immediately removes it from all channels. Any invalidation messages published during disconnection are **permanently dropped** — there is no queue, no replay capability, no persistence. A client that reconnects finds a stale L1 cache with no way to know what has changed.

— [How Redis Handles Pub/Sub When Subscriber Disconnects](https://oneuptime.com/blog/post/2026-03-31-redis-how-redis-handles-pubsub-when-subscriber-disconnects/view)

**Self-invalidation echo**: When node A writes a key and publishes an invalidation message, it receives its own message. A cache server that publishes invalidations for its own writes will immediately invalidate its own L1 entry, defeating the optimization. Mitigation: track sender identity and skip self-messages, or use Redis Streams (durable, replay-capable) instead of pub/sub.

— [Using Redis Streams To Implement Near Cache Invalidation](https://medium.com/xebia-engineering/using-redis-streams-to-implement-near-cache-invalidation-ed4136370a19) (Redis Streams as solution for durability)

**Race condition window**: 
```
Node A: SET cache_key v2 (writes to L2 Redis)
        PUBLISH "invalidate:cache_key" (to backplane)
        [network delay here]
Node B: GET cache_key (reads from local L1 — stale v1)
        [Invalidation message finally arrives]
        [But Node B just served stale v1]
```
Mitigation: version the cached values and check the version after fetching from L2, or bundle the new value in the invalidation message instead of just the key name.

— [Avoiding race conditions](https://redis.io/docs/latest/develop/reference/client-side-caching/#avoiding-race-conditions) (CLIENT TRACKING's recommended solution to the same race)

**No message ordering guarantee**: Multiple changes to the same key may arrive out of order (though redis-py Pub/Sub maintains per-channel order for a single subscriber). With concurrent publishers, ordering is undefined.

### 4. What mature production frameworks do

**.NET HybridCache** (Microsoft, .NET 9+): Combines in-process L1 (memory) + distributed L2 (Redis). **No backplane by default** — a write on pod A invalidates only pod A's L1, leaving other pods' L1 stale. A [proposed backplane abstraction](https://github.com/dotnet/runtime/issues/125602) would plug in Redis pub/sub or ServiceBus for cross-pod invalidation; not yet shipped as of Aug 2026.

— [Solving the Distributed Cache Invalidation Problem with Redis and HybridCache](https://milanjovanovic.tech/blog/solving-the-distributed-cache-invalidation-problem-with-redis-and-hybridcache)

**Spring Cache + Spring Data Redis**: Spring's `@Cacheable` is unaware of distributed invalidation. Libraries like **Redisson** provide `RedissonSpringLocalCachedCacheManager`, which layers local + Redis caching and broadcasts invalidations via **Redis pub/sub** to all cluster members. Accepts the message-loss risk in exchange for simplicity; applications are expected to set a short TTL on L1 entries to bound staleness.

— [Designing Cache Invalidation at Scale with Spring Boot, Redis, and AWS ElastiCache - DEV Community](https://dev.to/jessica_patel_472897dd43c/designing-cache-invalidation-at-scale-with-spring-boot-redis-and-aws-elasticache-36cp)

**FusionCache** (.NET, open-source): Offers an optional `IBackplane` abstraction with a Redis pub/sub reference implementation. Acknowledges the message-loss issue explicitly in documentation and recommends pairing with short TTLs or Redis Streams for durability.

— [Using FusionCache's Backplane to synchronize HybridCache instances across multiple instances](https://timdeschryver.dev/blog/hybridcache-sync-with-fusioncache-backplane)

**None of these are using CLIENT TRACKING** because:
1. Most are in languages / frameworks (Java, .NET, Go) with mature async ecosystems that use their own clients.
2. CLIENT TRACKING requires either connection pinning (defeats pooling) or a separate listener connection (architectural overhead).
3. Pub/Sub is "good enough" for L1 with a short TTL; the staleness window is minutes at most.

## Options compared

| Option | ✅ Strengths | ❌ Weaknesses | Evidence |
|---|---|---|---|
| **Redis CLIENT TRACKING (RESP3)** | Per-connection server tracking; only invalid keys you read are notified; zero cross-node chatter when BCAST not used | Async redis-py not supported; sync-only requires thread-pool overhead; breaks on proxy / ElastiCache Serverless / multi-node pinning; memory cost if many clients; must flush cache on reconnect | [Issue #3916](https://github.com/redis/redis-py/issues/3916), [ElastiCache restrictions](https://repost.aws/questions/QUpe4ORRjLRkaYXWmFc_lSog/problem-with-client-caching-when-using-aws-elasticache-redis) |
| **Redis Pub/Sub backplane** | Works in asyncio natively; no client library changes needed; all existing redis-py versions support it; simple to implement; widely used in production | Fire-and-forget message loss on disconnect; no replay; self-invalidation echo unless sender-filtered; race condition window between L2 write and backplane delivery; requires TTL or version-check mitigation | [Pub/Sub message loss on disconnect](https://oneuptime.com/blog/post/2026-03-31-redis-how-redis-handles-pubsub-when-subscriber-disconnects/view), [Spring/Redisson approach](https://dev.to/jessica_patel_472897dd43c/designing-cache-invalidation-at-scale-with-spring-boot-redis-and-aws-elasticache-36cp) |
| **Redis Streams + Pub/Sub (hybrid)** | Durable replay of missed invalidations; fire-and-forget speed of pub/sub; clients can catch up on reconnect; sender identity built-in | Higher complexity (two data structures); more CPU/memory on Redis side; Stream retention policy must be managed; needs consumer group bookkeeping | [Redis Streams for near-cache](https://medium.com/xebia-engineering/using-redis-streams-to-implement-near-cache-invalidation-ed4136370a19) |

## Version/compatibility notes

- **redis-py**: Sync client `CacheConfig` added in 5.1.0 (Aug 2024). Async `redis.asyncio` has NO equivalent (Issue #3916 open, no ETA). Current stable: 5.2.0+ (Aug 2026).
- **redis-py protocol**: Default is RESP3 as of 8.0.0 (May 2024). Earlier versions defaulted to RESP2.
- **Valkey**: Fully compatible with Redis 6.0+ CLIENT TRACKING; no breaking changes in 8.0+.
- **Elasticache**: CLIENT TRACKING **not supported in Serverless** (as of Aug 2026); supported in self-managed 6.0+.
- **Upstash**: Serverless Redis; CLIENT TRACKING likely not supported (behind a proxy).

## Evidence gaps

1. **Real-world performance comparison** — no published benchmarks comparing CLIENT TRACKING vs. pub/sub backplane on the same workload in Python. The `.NET HybridCache` blog posts compare hypothetical architectures, not implementations.
2. **Async redis-py roadmap** — no public statement from redis-py maintainers on when (or if) `redis.asyncio` will gain CLIENT TRACKING support.
3. **Valkey-py client library** — valkey-py (Python) does not appear to exist yet (Valkey is primarily in Go, Ruby, Java). Would need to use `redis-py` with a Valkey backend, which should work but is untested in production.
4. **Connection pool interaction** — no published analysis of whether CLIENT TRACKING state per-connection interacts safely with connection pooling under high concurrency (ThreadLocal? AsyncLocal? Connection identity?).

## Librarian's note

**The evidence favours pub/sub as the backplane for varco**, with explicit caveats.

CLIENT TRACKING is architecturally cleaner (server remembers what each client cached, sends only relevant invalidations) but is **blocked on async redis-py support**. Using the sync client via thread-pool wrapping defeats the async Python value proposition. Waiting for the feature to land (no ETA) creates a cross-version compatibility hazard.

Pub/Sub is imperfect (message loss on disconnect, race condition window) but is **production-proven**, natively async, requires zero library changes, and works everywhere (Valkey, Upstash, ElastiCache self-managed, Redis Cluster). The message-loss risk is **mitigated by a short L1 TTL** (e.g., 5 minutes): stale data is bounded, and the backplane is a "best-effort" optimization, not a durability guarantee. Spring Cache / Redisson and FusionCache both ship pub/sub backlanes today.

**Recommendation**: Build pub/sub backplane for C1 (varco 1.0 / stable release). Add a feature flag `LayeredCache(backplane=RedisPubSubBackplane(...))` so a client using CLIENT TRACKING with the sync client (after redis-py async lands) can opt into it. Monitor Issue #3916 and file a priority request if async support becomes a concrete blocker.

