# Plan 010 — Cache Hardening (R1)

> Executes the **R1 "Hardening"** cut of `BACKLOG.md`: **C2** (singleflight /
> stampede protection), **C1** (`LayeredCache` L1 coherence backplane), **C3**
> (cache observability pack), **C4** (stale-while-revalidate + TTL jitter +
> negative caching).
>
> **Authoritative inputs** (read before implementing any phase):
> - `design/cache-layer/scout-r1.md` — codebase map + landmines
> - `design/cache-layer/research/001-mature-async-cache-2026.md` — brief 001
> - `design/cache-layer/research/002-redis-invalidation-backplane.md` — brief 002
> - `design/cache-layer/research/003-cache-metric-naming.md` — brief 003
>
> **Posture: hardening, not features.** Every default in this plan reproduces
> today's observable behaviour byte-for-byte. Every new capability is reached by
> passing an explicit object or keyword. This is the same posture Plan 005
> (`OutboxRelay(retry_policy=None)` → unchanged), Plan 007 (`TenancySettings()`
> → `SHARED`, nothing constructed) and Plan 009 (`reliability=None` → registers
> nothing) took.

## Goal

After this plan:

1. N concurrent misses on the same cache key produce **one** recompute per
   process (`Singleflight`), and the coalescing is tenant-safe by construction.
2. A `LayeredCache` in a multi-pod deployment no longer silently serves stale L1
   entries after another pod invalidates — a pluggable `CacheBackplane`
   (Redis Pub/Sub implementation) propagates key/prefix/clear invalidations
   across nodes, with bounded staleness enforced at construction.
3. Cache behaviour is measurable: hit/miss/eviction counters, an operation
   latency histogram, and varco-specific stampede/stale/backplane counters,
   installed the same way `install_reliability_metrics()` is.
4. A cache entry can carry a soft TTL (serve stale, refresh once in background),
   randomized TTL (no synchronized expiry cliff), and an opt-in negative-cache
   TTL — all three sharing the **same** read-through core as singleflight, so the
   "coalesce a refresh while concurrently serving stale" race is designed once.

## Non-goals

- **Distributed (cross-pod) singleflight.** R1 ships the per-process coalescer
  and the seam (`SingleflightProtocol`) for a future `RedisSingleflight`. See
  Decision D-3.
- **Redis RESP3 `CLIENT TRACKING`.** Rejected on evidence (Decision D-1); the
  `CacheBackplane` ABC is precisely the feature flag brief 002 recommends
  leaving for it.
- **Durable / replayable invalidation (Redis Streams backplane).** The ABC
  admits one later; R1 ships Pub/Sub only.
- **C5** (`get_many`/`set_many` + pluggable serializer) — explicitly R2 in the
  backlog.
- **The entire R2 track** (X1, I1, I2, T1, T2, T3, C5). BACKLOG's remaining open
  questions — **I2 catalog format** (Babel/gettext vs ICU vs Fluent) and **T2
  migration path** (`Job.run_at` UTC-instant vs wall-clock+zone) — belong to R2
  and are **out of scope here**. Nothing in this plan touches `Job.run_at`, the
  `varco_jobs` table, or any i18n surface.
- **New framework tables / Alembic revisions.** R1 adds none (see "Framework
  table & migration impact").
- **Rewriting `InMemoryCache`'s FIFO eviction, `CacheServiceMixin`'s key scheme,
  `EventDrivenStrategy`, or `CacheInvalidationConsumer`.** They are instrumented
  and reused, not replaced.

---

## Decisions

Two BACKLOG open questions are settled by research; both are recorded here with
their evidence so the implementer does not re-litigate them.

### D-1 — C1 backplane mechanism: **Redis Pub/Sub**, not RESP3 `CLIENT TRACKING`

**Settled.** Build an application-level Pub/Sub invalidation backplane.

- `redis.asyncio` **has no `CLIENT TRACKING` / client-side-caching support**;
  only the synchronous `redis.Redis(cache_config=...)` client does (added
  5.1.0, requires `protocol=3`). redis-py issue **#3916** is open with no ETA
  — brief 002 §1. Using it from async varco code means a thread pool wrapping a
  blocking client, which defeats the point of an async framework — brief 002 §1
  ("Implication for varco").
- `CLIENT TRACKING` state is **per-connection**, so it fights connection
  pooling, and is **not supported on ElastiCache Serverless** or behind
  multiplexing proxies — brief 002 §2.
- Pub/Sub is **production-proven** for exactly this job: Spring/Redisson
  (`RedissonSpringLocalCachedCacheManager`) and .NET FusionCache's `IBackplane`
  both ship Redis Pub/Sub backplanes; .NET `HybridCache` has *no* backplane at
  all and is where varco is today — brief 002 §4.
- Pub/Sub's real costs are named and mitigated, not ignored:
  message loss on subscriber disconnect (brief 002 §3), self-echo (§3), and the
  write→publish→remote-read race window (§3). Mitigations are baked into the
  design below: **bounded L1 TTL enforced at construction**, **origin-based echo
  suppression**, **flush-L1-on-reconnect**, and **publish strictly after the
  authoritative-layer write**.
- Brief 002's Librarian's note recommends exactly
  `LayeredCache(backplane=RedisPubSubBackplane(...))` with the flag left open
  for a `CLIENT TRACKING` implementation later. The `CacheBackplane` ABC **is**
  that flag.

### D-2 — C3 metric names: **separate counters**, `varco.`-prefixed

**Settled.** `varco.cache.hits` / `varco.cache.misses` / `varco.cache.evictions`
(Counters) + `varco.cache.duration` (Histogram, unit `ms`).

- **No OTel semantic convention for application cache metrics exists.** Checked
  against semconv **v1.44.0** (Aug 2026); tracking issue
  `open-telemetry/semantic-conventions#1747` proposes *span attributes*, not
  metrics, and remains open; **no OTEP** has been filed — brief 003 §1.
- Separate counters over one `cache.operations{result=hit|miss}` metric:
  Prometheus (Caffeine/Guava `CacheMetricsCollector`), Micrometer, and the Java
  ecosystem all use the separate-metric shape, and it is OTel-legal because the
  *unit itself* differs (`{hits}` vs `{misses}`) — brief 003 §2, §3 Option A,
  and the Librarian's note.
- **Deviation, deliberately recorded:** brief 003 suggests attribute keys
  `cache.layer` / `cache.operation`. varco's `Metric.add(**attributes: str)`
  (`varco_core/observability/metric.py:208`) takes attributes as Python
  **kwargs**, and `cache.layer` is not a valid identifier. R1 emits `cache=`,
  `layer=`, `operation=`. A dotted rename is a one-line change in the
  `METRIC_NAMES` / attribute-key table (step 21) or an OTel View — recorded in
  the feature doc's migration section.
- **`varco.` prefix, not bare `cache.`**: consistent with every existing varco
  instrument (`varco.dlq.pushed`, `varco.outbox.published`,
  `varco.job.lease_reaps` — `observability/reliability.py:194-216`), *and* it
  deliberately leaves the unprefixed `cache.*` namespace free so a future
  semconv can be adopted additively rather than by renaming varco's series.
  This is brief 003's "semconv migration path clear" made concrete.
- Cardinality: `cache` (instance name), `layer` (`l1`/`l2`/…), `operation`
  (`get`/`set`/`delete`/`clear`), `kind` (`positive`/`negative`/`stale`) only.
  **Never** the cache key, tenant id, user id, or correlation id — brief 003 §4
  (unbounded-cardinality forbidden list), and consistent with
  `ReliabilityMetricsConfig.include_tenant=False` (RD-3).

### D-3 — Singleflight is **per-process** in R1 (scout landmine)

Per-process, with a `SingleflightProtocol` seam for a future distributed one.

- ✅ Takes concurrent recomputes from `N_pods × C_concurrency` to `N_pods` —
  the dominant term for a hot key, at **zero** added infrastructure and zero
  extra network round trips on the read path.
- ✅ No new failure mode: a Redis outage cannot turn a cache miss into an error
  or a hang, because the coalescer is a local dict.
- ❌ Does not deduplicate across pods. A distributed variant needs a
  per-key distributed lock **on the hot read path** (`RedisLock` semantics), plus
  lease renewal for slow recomputes, fencing against a stalled leader, and a
  "leader died" recovery path — a round trip per miss and a whole new class of
  incident, for a `N`→`1` improvement on top of a `N×C`→`N` one already banked.
- The C3 pack (`varco.cache.stampede_suppressed`) is what tells an operator
  whether `N` is still too many — which is why C3 lands before C4 in the phase
  order.

### D-4 — Negative caching is **opt-in** (scout landmine)

`@cached` today explicitly does not cache `None`
(`cache/decorator.py:198` + the module docstring's Caveats). Turning that on by
default would convert "row not found" into a durably cached absence for every
existing caller who never asked for it. `CachePolicy.negative_ttl: float | None
= None` (default `None` = today's behaviour). Setting it implies caching `None`
with that TTL and marking the entry `is_negative`. See also the envelope
argument in D-5 — a cached `None` is *unrepresentable* without the envelope,
because `AsyncCache.get()` returns `None` for a miss.

### D-5 — Envelope, and the rolling-deploy hazard it creates

`CacheEnvelope` (a JSON-round-trippable dict with a `__varco_cache__` version
marker) is written **only** when the active policy needs it — i.e. when any of
`soft_ttl`, `negative_ttl`, or `stale_if_error` is set. Otherwise the raw value
is stored exactly as today.

`read_through()` **tolerates both shapes on read** (a payload without the marker
is treated as a fresh legacy value), so a new pod reading an old pod's entries
is safe. The unsafe direction is the reverse: an **old** pod reading a **new**
pod's envelope returns the wrapper dict to the application.

**Therefore enabling an envelope-requiring policy field against a shared L2 is a
two-step deploy**: (1) roll out the new varco version everywhere with the flags
off; (2) turn the flags on. Documented in the feature doc, the Risks section,
and the CLAUDE.md pitfall table.

- Alternative — **prefix envelope keys with `v2:`** so the two shapes never
  collide: ✅ makes the deploy single-step. ❌ cold-starts the entire cache on
  enable *and* on rollback, permanently doubles the key namespace, and bakes a
  wire version into every application-visible key. Rejected — a documented
  two-step deploy costs one paragraph and no runtime complexity.

---

## Design

### The spine: one read-through algorithm, four features on top

The scout's decisive landmine is that **C2 and C4 interact**: "coalescing a fresh
recompute while concurrently returning stale is a race that needs an explicit
design, not two independent features." This plan resolves it by writing that
algorithm **once**, in `varco_core/cache/readthrough.py`, and having every
feature be a field on the policy object it takes.

```
              CachePolicy (frozen)            CacheEnvelope (wire format)
       ttl · ttl_jitter · soft_ttl            {"__varco_cache__": 1, "v": …,
       negative_ttl · stale_if_error           "sa": stored_at,
       singleflight · refresh_mode             "se": soft_expires_at,
                    │                          "he": hard_expires_at,
                    │                          "neg": bool}
                    ▼                                    ▲
        ┌───────────────────────────────────────────────────────────┐
        │  read_through(cache, key, loader, policy, *, type_hint)   │
        │                                                           │
        │   1. get(key)                                             │
        │   2. unwrap → fresh?      → HIT              (C3 counter) │
        │   3.        → soft-stale? → serve stale NOW              │
        │                             + ONE background refresh      │
        │                               through the SAME            │
        │                               Singleflight slot   (C2×C4) │
        │   4.        → hard-expired / absent →                     │
        │                 Singleflight.do(key, loader)              │
        │                   leader  : compute → wrap → set          │
        │                   follower: await shield(leader_future)   │
        │   5. loader raised + stale within stale_if_error          │
        │                    → serve stale       (C3: reason=error) │
        └───────────────────────────────────────────────────────────┘
                    │                        │
        used by  @cached (decorator.py)   CacheServiceMixin (mixin.py)
```

**Why the race is closed rather than papered over.** The stale-serve path and
the cold-miss path enter the *same* `Singleflight` slot, keyed by the same final
cache key. Therefore:

- Concurrent soft-stale readers → exactly one refresh in flight; every reader
  returns immediately with the stale value. No reader ever blocks on a refresh.
- A cold reader arriving while a soft-stale refresh is already in flight →
  becomes a **follower** of that same future (it has no stale value to serve, so
  it must wait) and gets the refreshed value. It cannot start a second
  recompute.
- A soft-stale reader arriving while a cold-miss recompute is in flight →
  cannot happen for the same key (a cold miss means no entry exists), except
  across a `delete()`; in that case the stale reader also becomes a follower.
  Both orderings converge on "one in-flight recompute per key per process."

Building C4 as an independent feature after C2 was considered and **rejected**:
it would mean either a second coalescer for refreshes (two leaders per key) or
retrofitting `Singleflight` with a "refresh" mode after `@cached` had already
shipped against it. ❌ Guaranteed rework of the exact code path both features
own. ✅ Would let C4 ship on the backlog's stated severity order — bought back
below by sequencing C4's *user-facing knobs* last while its *wire format* is
frozen in Phase 1.

**Wire format is frozen in Phase 1, activated in Phase 4.** `CacheEnvelope`
carries `se` (soft) and `he` (hard) from the very first commit even though only
Phase 4 sets `se`. This is deliberate: the envelope is what production Redis
holds, and adding a field to it later would be a second, avoidable rolling-deploy
hazard (D-5).

### `Singleflight`

```python
class Singleflight:
    def __init__(self, *, name: str = "default") -> None: ...
    async def do(self, key: str, loader: Callable[[], Awaitable[Any]]) -> tuple[Any, bool]: ...
    def spawn_refresh(self, key: str, loader: Callable[[], Awaitable[Any]]) -> None: ...
    async def aclose(self) -> None: ...
    @property
    def in_flight(self) -> int: ...
```

- `asyncio.Lock` guarding the in-flight dict is created **lazily on first use**,
  never in `__init__` and never at module scope (CLAUDE.md rule — locks must be
  created inside a running loop).
- Followers `await asyncio.shield(future)` — **one caller's `@timeout` or
  cancellation must never kill the shared recompute for everybody else.** This
  is the single subtlest correctness point in C2.
- If the *leader* is cancelled, the shared future receives `CancelledError`;
  followers re-raise and the next call re-elects a leader. Documented edge case,
  with a test.
- `spawn_refresh()` (used by C4's SWR path) creates the task, holds a **strong
  reference** in an owned set, and discards it in a done-callback — an untracked
  `asyncio.create_task` can be garbage-collected mid-flight.
- `aclose()` drains outstanding refresh tasks. `CacheServiceMixin` and the
  `@cached` wrapper each expose a way to reach it (`wrapper.aclose()`).
- **Shared-instance rule.** A `Singleflight` is per-cache-namespace state, and a
  per-call instance coalesces nothing — the same defect class as a per-call
  `CircuitBreaker`/`Bulkhead` in CLAUDE.md's pitfall table. `@cached` creates
  exactly one per decorated function at decoration time; `CacheServiceMixin`
  creates one per service instance. Both are documented and pitfall-tabled.
- **Tenant safety (scout landmine).** The coalescing key is the **final cache
  key** — the one that already went through `tenancy_cache_key()` /
  `CacheServiceMixin._cache_key()` and therefore already carries
  `tenant:{tenant_id}:`. Coalescing on a pre-namespaced key would let two
  tenants share one recompute → a cross-tenant leak. `read_through()` never
  builds keys itself; it receives the final key. Asserted by a dedicated test
  (step 12).

### `CacheBackplane` (C1)

Layer split follows CLAUDE.md exactly: **ABC in `varco_core`, concrete backend
in `varco_redis`** — the same seam as `AbstractEventBus`/`RedisEventBus` and
`AbstractDeadLetterQueue`/`RedisDLQ`.

```python
@dataclass(frozen=True)
class InvalidationMessage:
    kind: Literal["key", "prefix", "clear"]
    payload: str          # key name | key prefix | "" for clear
    origin: str           # publisher node id — echo suppression
    ts: float

class CacheBackplane(abc.ABC):
    @property
    @abc.abstractmethod
    def origin(self) -> str: ...
    @abc.abstractmethod
    async def start(self) -> None: ...
    @abc.abstractmethod
    async def stop(self) -> None: ...
    @abc.abstractmethod
    async def publish(self, message: InvalidationMessage) -> None: ...
    @abc.abstractmethod
    def subscribe(self, handler: Callable[[InvalidationMessage], Awaitable[None]]) -> None: ...
```

Five design rules, each closing a named hazard:

1. **`publish()` must never raise.** Identical contract to
   `AbstractDeadLetterQueue.push()` (CLAUDE.md): by the time we publish, the
   authoritative L2 write has already succeeded and the caller's `set()` cannot
   be unwound. A backplane failure logs and increments
   `varco.cache.backplane.dropped`. Implementations swallow.

2. **Publish strictly *after* the authoritative-layer write** (scout landmine —
   `LayeredCache` write-through ordering). Today `set()` is
   `asyncio.gather(*all_layers)` (`cache/layered.py:265`) with **no ordering**.
   If the invalidation can reach node B before node A's L2 write lands, node B
   evicts its L1, immediately re-reads L2, gets the *old* value, and re-promotes
   it — the backplane would make the staleness **permanent**, strictly worse
   than not having one. So when (and only when) a backplane is wired,
   `set()`/`delete()`/`delete_prefix()`/`clear()` become:
   `await last_layer.op(...)` → then `gather(*faster_layers)` **and** `publish()`.
   Cost: write latency goes from `max(L1,L2)` to `L2 + max(faster)`. Documented
   as the price of `backplane=`. **With no backplane the `gather` path is
   untouched** — byte-identical.

3. **A received message evicts local layers only — never the last layer.**
   Propagating a received invalidation back to L2 would nuke shared state and
   amplify one write into a fleet-wide storm. Explicit rule + test.

4. **Echo suppression.** A node skips messages whose `origin` equals its own
   (brief 002 §3, "self-invalidation echo": a publisher that honours its own
   message immediately invalidates the L1 entry it just wrote, defeating the
   whole optimization).

5. **Bounded staleness enforced at construction.**
   `LayeredCache(..., backplane=X)` with `promote_ttl=None` raises `ValueError`
   naming brief 002's mitigation. Pub/Sub is fire-and-forget: a subscriber that
   was disconnected when a message was published never receives it, with no
   queue and no replay (brief 002 §3). The industry answer — Redisson,
   FusionCache — is a **short L1 TTL** bounding how long a missed invalidation
   can hurt (brief 002 §4, Librarian's note). Refusing to construct an unbounded
   L1 behind a best-effort backplane mirrors `OutboxRelay(max_attempts=…)`
   refusing to run without a `dlq=`.

6. **Flush L1 on reconnect.** Redis documents that a client must flush its local
   cache on any connection loss (brief 002 §2). `RedisPubSubBackplane` emits a
   synthetic `kind="clear"` to its local handler after re-subscribing, which
   under rule 3 clears the local layers only.

**The key-name exposure (scout landmine).** A backplane broadcasts key *names*
on a shared channel, and varco keys are already tenant-namespaced
(`tenant:{tenant_id}:Entity:pk` — `tenancy/cache_key.py:49`). So every
subscribing node learns *which tenant touched which entity id*. Under
`TenantIsolation.SHARED` every pod already serves every tenant and nothing new is
exposed. Under `SCHEMA`/`DATABASE` with per-tenant pods it is new cross-tenant
activity metadata. R1 ships the default plus two documented opt-outs:

- `RedisPubSubBackplane(channel_for=lambda key: ...)` — derive the channel from
  the key's `tenant:{id}:` segment so a node subscribes only to the tenants it
  hosts. Cost: one subscription per hosted tenant.
- `RedisPubSubBackplane(hash_keys=True)` — publish `sha256(key)[:16]`; a
  receiver matches by hashing its own L1 keys. ❌ Prefix invalidation is not
  hashable, so `kind="prefix"` degrades to a local `clear` under this mode.
  Stated in the docstring and the feature doc.

Default is one channel with plaintext keys (matching Redisson/FusionCache), with
the exposure stated explicitly in the feature doc and the CLAUDE.md pitfall
table so the choice is informed rather than accidental.

### Cache observability pack (C3)

Mirrors `install_reliability_metrics()` (`observability/reliability.py:370`)
exactly — the scout named it as the shape to copy:

- A **manual install function**, `install_cache_metrics(...)`, deliberately
  **not** a scanned `@Configuration` (a scanned config auto-activates on
  `container.scan()`; CLAUDE.md's "policy authorizer silently active" pitfall).
- Module-level `Metric` instruments — safe to construct before the
  `MeterProvider` exists, and `Metric` already routes through
  `wrap_instrument()` (`observability/metric.py:101`), so global attributes are
  stamped for free. **No cache instrument is created any other way.**
- `record_*` helpers wrapped in a `_safe()` swallow, same as
  `reliability.py:219` — an instrument failure must never break a cache read.
- A `CacheMetricsConfig(enabled=..., meter_name=..., duration_histogram=...,
  by_layer=...)` frozen dataclass, `enabled=False` → whole pack is a no-op.
- Recording is **off unless installed**: `record_*` is a cheap no-op when the
  pack was never installed, so the default hot path is unchanged.

Instruments (D-2):

| Name | Kind | Unit | Attributes |
|---|---|---|---|
| `varco.cache.hits` | counter | `1` | `cache`, `layer`, `kind` (`positive`/`negative`/`stale`) |
| `varco.cache.misses` | counter | `1` | `cache`, `layer` |
| `varco.cache.evictions` | counter | `1` | `cache`, `layer`, `reason` (`capacity`/`ttl`/`explicit`/`backplane`) |
| `varco.cache.duration` | histogram | `ms` | `cache`, `operation` |
| `varco.cache.stampede_suppressed` | counter | `1` | `cache` |
| `varco.cache.stale_served` | counter | `1` | `cache`, `reason` (`soft_ttl`/`error`) |
| `varco.cache.backplane.published` | counter | `1` | `kind` |
| `varco.cache.backplane.received` | counter | `1` | `kind` |
| `varco.cache.backplane.dropped` | counter | `1` | `reason` (`publish_failed`/`decode_failed`) |

Hit **ratio** is derived at query time (`hits / (hits + misses)`) — brief 003
§2 (Uptrace) explicitly recommends deriving it rather than emitting it.

### Alternatives considered

- **RESP3 `CLIENT TRACKING` for C1** — ❌ rejected: no `redis.asyncio` support
  (redis-py #3916, open, no ETA), sync-client-plus-threadpool defeats async,
  breaks under connection pooling / proxies / ElastiCache Serverless (brief 002
  §1–§2). ✅ Would give per-key server-side tracking with zero cross-node
  chatter — revisit when #3916 lands; the `CacheBackplane` ABC is the slot it
  drops into.
- **Redis Streams backplane** — ✅ durable, replayable, sender identity built
  in, fixes the disconnect message-loss hole outright (brief 002 §3, options
  table). ❌ Consumer-group bookkeeping + retention policy + more Redis
  CPU/memory for what is by definition a best-effort optimization, when the
  short-L1-TTL + flush-on-reconnect mitigations recover most of the benefit for
  none of the operational surface. Deferred to R2 behind the same ABC.
- **One `varco.cache.operations{result=hit|miss}` metric for C3** — ✅ more
  composable, aligns with "attributes not names". ❌ Diverges from
  Prometheus/Caffeine/Guava/Micrometer, which is what every operator's existing
  dashboards and mental model are built on (brief 003 §2–§3). Rejected per
  brief 003's Librarian's note.
- **Observable (callback/polled) counters for C3** (Uptrace's suggestion, brief
  003 §2) — ✅ zero hot-path increments. ❌ varco's caches do not maintain a
  `stats()` object to poll, so we would have to build one *and* the callback
  infrastructure; the push-based `Metric` already exists and is what every
  other varco pack uses. Rejected.
- **Distributed singleflight in R1** — see D-3.
- **Negative caching on by default** — see D-4.
- **`v2:` key prefix for envelope entries** — see D-5.
- **Putting `CacheBackplane` in `varco_redis` and having `LayeredCache` import
  it** — ❌ rejected outright: inverts the layer rule (`varco_core` must not
  depend on a backend package) and would make `varco_core`'s cache system
  unusable without Redis.
- **Making the backplane an `InvalidationStrategy`** — ✅ reuses an existing
  ABC with a `start()`/`stop()` lifecycle. ❌ `InvalidationStrategy` answers a
  *synchronous read-time* question (`should_invalidate(key, metadata) -> bool`,
  `cache/base.py:412`); a backplane must *push* evictions into layers it does
  not own and must be shared across a `LayeredCache`'s layers, not owned by one
  of them. Wrong shape. Rejected — but the CLAUDE.md rule still binds: the
  backplane's `start()`/`stop()` are driven **only** by `LayeredCache.start()`/
  `stop()`, never constructed-and-started by application code ad hoc.

---

## Steps

Phases are ordered: **shared core → C2 → C3 → C1 → C4**. Justification for
deviating from the backlog's severity order:

- **Phase 0 before everything** because C2 and C4 share one algorithm (the
  scout's landmine); writing it once is the whole point.
- **C3 (an S) before C1 (an L)** because (a) C1's Pub/Sub backplane is
  fire-and-forget and is *unoperable* without `backplane.published/received/
  dropped` counters — you cannot trust a best-effort mechanism you cannot see;
  (b) C3 is where the backplane's instruments live, so doing C3 second avoids a
  second editing pass over C1's code; (c) D-3's per-process singleflight
  decision is only revisitable with `stampede_suppressed` data in hand.
- **C4 last** because its wire format is already frozen in Phase 1, so what
  remains is activating policy fields — the lowest-risk work in the plan, and
  the right thing to be holding if the release has to be cut short.

### Phase 0 — shared read-through core (no behaviour change)

1. [ ] `varco_core/varco_core/cache/envelope.py` — new. `CacheEnvelope`
   (`@dataclass(frozen=True)`: `value`, `stored_at`, `soft_expires_at`,
   `hard_expires_at`, `is_negative`), `WIRE_VERSION = 1`, `MARKER =
   "__varco_cache__"`, `wrap(envelope) -> dict`, `unwrap(payload) -> CacheEnvelope
   | None` (returns `None` for a non-envelope payload so the caller can treat it
   as a fresh legacy value — D-5), `coerce(value, type_hint)` (pydantic
   `TypeAdapter(...).validate_python` when `type_hint` is a model/dataclass,
   pass-through otherwise; pydantic is already a `varco_core` dependency via
   `VarcoSettings`). Docstring states: envelope mode passes `type_hint=None` to
   the backend and re-applies the hint to the unwrapped payload, and that a
   `NoOpSerializer`-backed cache is incompatible (it fails loudly at the first
   `set()`, since a dict is not `bytes`).
2. [ ] `varco_core/tests/test_cache_envelope.py` — failing tests first:
   round-trip through `JsonSerializer`; `unwrap()` on a legacy raw dict returns
   `None`; `unwrap()` on a dict that merely *contains* a `__varco_cache__` key
   with the wrong version returns `None`; `coerce()` with a pydantic model,
   with a plain dict, and with `type_hint=None`; a negative envelope survives
   round-trip with `value=None` distinguishable from absence.
3. [ ] `varco_core/varco_core/cache/policy.py` — new. `CachePolicy`
   (`@dataclass(frozen=True)`): `ttl: float | None = None`,
   `ttl_jitter: float = 0.0`, `soft_ttl: float | None = None`,
   `negative_ttl: float | None = None`, `stale_if_error: float | None = None`,
   `singleflight: bool = False`, `refresh_mode: Literal["background",
   "blocking"] = "background"`, `name: str = ""` (the bounded `cache=` metric
   attribute). `__post_init__` validation: `0.0 <= ttl_jitter < 1.0`;
   `soft_ttl < ttl` when both set (else `ValueError` — a soft TTL at or beyond
   the hard TTL can never fire); `stale_if_error` requires `ttl`. Properties:
   `requires_envelope` (any of `soft_ttl`/`negative_ttl`/`stale_if_error` set),
   `effective_ttl()` (applies jitter — Phase 4). **Every field defaults to the
   off/today value**; `CachePolicy()` is the identity policy.
4. [ ] `varco_core/tests/test_cache_policy.py` — validation table: each invalid
   combination raises with a message naming the offending fields;
   `CachePolicy().requires_envelope is False`; frozen-ness asserted.
5. [ ] `varco_core/varco_core/cache/singleflight.py` — new.
   `SingleflightProtocol` (`runtime_checkable Protocol`: `do`, `spawn_refresh`,
   `aclose`) + `Singleflight` per the Design section. Lazy `asyncio.Lock`;
   followers `await asyncio.shield(fut)`; owned refresh-task set with
   done-callback discard; `in_flight` property for tests/metrics.
6. [ ] `varco_core/tests/test_cache_singleflight.py` — failing tests first:
   (a) 50 concurrent `do()` calls on one key → loader invoked exactly once, all
   50 get the same value; (b) two different keys → two loader invocations,
   no serialization between them; (c) loader raises → every waiter sees the same
   exception and the slot is cleared (next call re-runs the loader);
   (d) **a follower cancelled mid-wait does not cancel the leader** — the
   remaining followers still get the value (this is the `shield` test);
   (e) leader cancelled → followers see `CancelledError`, slot cleared;
   (f) `spawn_refresh` task is strongly referenced (survives a forced
   `gc.collect()`) and is drained by `aclose()`.
7. [ ] `varco_core/varco_core/cache/readthrough.py` — new. `async def
   read_through(cache, key, loader, policy, *, type_hint=None, singleflight=None)
   -> Any` implementing the 5-step algorithm in the Design section. Phase 0
   ships steps 1/2/4 only (fresh-or-absent + optional singleflight); the
   soft-stale (3) and stale-on-error (5) branches are written as explicit
   `if policy.soft_ttl is not None:` / `if policy.stale_if_error is not None:`
   blocks that are unreachable under Phase 0 defaults and are activated by
   Phase 4's tests. Never constructs a cache key.
8. [ ] `varco_core/tests/test_cache_readthrough.py` — failing tests first, the
   **byte-identical-default** suite: with `CachePolicy()` and no singleflight,
   `read_through` writes the raw value (no envelope in the store), does **not**
   cache a `None` result, and calls `get`/`set` with exactly the arguments
   today's `@cached` wrapper does. Plus: a legacy raw value already in the store
   is served as a hit.
9. [ ] `varco_core/varco_core/cache/__init__.py` — export `CachePolicy`,
   `CacheEnvelope`, `Singleflight`, `SingleflightProtocol`, `read_through`. Add
   them to `__all__` and to the module docstring's layer map.

### Phase 1 — C2: singleflight wired into the two call sites

10. [ ] `varco_core/varco_core/cache/decorator.py` — `cached()` gains
    keyword-only `policy: CachePolicy | None = None` and
    `singleflight: bool = False`. When both are absent the existing wrapper body
    runs **verbatim** (no `read_through` call at all — the cheapest possible
    proof of "unchanged default"). Otherwise the body delegates to
    `read_through(...)` with one `Singleflight` created per decorated function at
    decoration time. Attach `wrapper.aclose()` alongside the existing
    `invalidate` / `invalidate_all`. Update the module docstring's Caveats
    section (the "`None` is NOT cached" note now says "unless
    `policy.negative_ttl` is set — see Plan 010 / D-4").
11. [ ] `varco_core/varco_core/cache/mixin.py` — `CacheServiceMixin` gains
    `_cache_policy: ClassVar[CachePolicy | None] = None`. When `None`, `read()` /
    `list()` keep their current bodies. When set, they route through
    `read_through()` with one `Singleflight` per service instance (created
    lazily on first use — no `asyncio` object in `__init__`). `_cache_ttl` keeps
    working and is folded into the policy when `_cache_policy` is `None` but a
    `singleflight` flag is set.
12. [ ] `varco_core/tests/test_cache_singleflight_tenancy.py` — **the tenant
    landmine test**: two `tenant_context()` blocks issuing concurrent misses for
    the same entity pk through `CacheServiceMixin` produce **two** loader calls
    (one per tenant), never one; and the coalescing key contains the
    `tenant:{id}:` segment. A regression here is a cross-tenant data leak.
13. [ ] `varco_core/tests/test_cache_decorator_singleflight.py` — stampede
    reproduction: 100 concurrent `await get_user(42)` on a cold key with a
    slow loader → loader called once with `singleflight=True`, and (asserting
    the bug this fixes) 100 times with the default.
14. [ ] `technical_docs/features/cache-hardening.md` — **new file**. Sections:
    "What R1 changes and what it does not", "Singleflight" (shared-instance
    rule, per-process scope + D-3's rationale, the `shield` semantics, the
    tenant-key rule). Later phases extend this same file.
15. [ ] `CLAUDE.md` — extend the cache-system section with a
    "Stampede protection" paragraph and add two pitfall rows: *per-call
    `Singleflight`* (→ coalesces nothing, same class as per-call
    `CircuitBreaker`) and *coalescing on a pre-tenant-namespaced key*
    (→ cross-tenant leak).
16. [ ] `ARCHITECTURE.md` — add `policy.py` / `envelope.py` / `singleflight.py`
    / `readthrough.py` to the `varco_core.cache` module map.
17. [ ] `README.md` — one line in the caching feature bullet ("stampede
    protection / singleflight").

### Phase 2 — C3: cache observability pack

18. [ ] `varco_core/varco_core/observability/cache.py` — new.
    `CacheMetricsConfig` (`@dataclass(frozen=True)`: `enabled=True`,
    `meter_name="varco"`, `duration_histogram=True`, `by_layer=True`),
    module-level `Metric` instruments per the D-2 table, `_safe()` swallow,
    `install_cache_metrics(*, config=None) -> None` (idempotent; `enabled=False`
    → no-op), and `record_cache_hit/miss/eviction/duration/
    stampede_suppressed/stale_served/backplane_published/backplane_received/
    backplane_dropped`. A single module-level `METRIC_NAMES` / `ATTR_KEYS`
    mapping so a future semconv rename is one edit (D-2).
19. [ ] `varco_core/tests/test_cache_metrics.py` — failing tests first: an
    in-memory OTel reader sees `varco.cache.hits`/`misses` after a
    `read_through` hit/miss; attribute keys are exactly `cache`/`layer`/
    `operation`/`kind`/`reason` and **never** contain the cache key or a tenant
    id (brief 003 §4 — assert by scanning recorded attribute keys against a
    deny-list); `install_cache_metrics(config=CacheMetricsConfig(enabled=False))`
    records nothing; calling install twice is idempotent; a raising instrument
    does not propagate out of `record_*`.
20. [ ] `varco_core/varco_core/cache/readthrough.py` — instrument: hit
    (`kind=positive|negative|stale`), miss, `duration` around the whole
    read-through, `stampede_suppressed` when `do()` reports follower,
    `stale_served`. All via `record_*`, all no-ops when the pack is not
    installed.
21. [ ] `varco_core/varco_core/cache/memory.py` — `record_cache_eviction(
    reason="capacity")` at the FIFO eviction site and `reason="ttl"` at the
    strategy-driven eager eviction site. No other behaviour change.
22. [ ] `varco_core/varco_core/cache/layered.py` — pass `layer="l1"`/`"l2"`/… as
    the bounded layer attribute on hit/miss records for the layer that answered,
    gated by `CacheMetricsConfig.by_layer`.
23. [ ] `technical_docs/features/cache-hardening.md` — add the "Observability"
    section: the full metric table, the derived-hit-ratio query, the
    cardinality deny-list, the `varco.`-prefix rationale, and the **semconv
    migration path** (brief 003 §1 + Librarian's note: when
    `open-telemetry/semantic-conventions#1747` or a successor OTEP is approved,
    realign via the `METRIC_NAMES` table or an OTel View — do not rename series
    ad hoc).
24. [ ] `CLAUDE.md` — add `install_cache_metrics()` to the observability section
    next to `install_reliability_metrics()`, stating it is a manual install
    function and deliberately **not** a scanned `@Configuration`; add a pitfall
    row for *cache metrics never appear* (→ `install_cache_metrics()` never
    called).

### Phase 3 — C1: L1 coherence backplane

25. [ ] `varco_core/varco_core/cache/backplane.py` — new. `InvalidationMessage`
    (`@dataclass(frozen=True)`), `CacheBackplane` ABC per the Design section
    (with the "`publish()` must never raise" contract stated in the ABC
    docstring, mirroring `AbstractDeadLetterQueue.push()`), and
    `InMemoryBackplane(bus_name="default")` — a process-local fan-out registry
    keyed by `bus_name` so **two `LayeredCache` instances in one test can
    exchange invalidations**, i.e. multi-pod coherence becomes unit-testable
    without Docker (the scout notes such tests are entirely absent today).
26. [ ] `varco_core/tests/test_cache_backplane.py` — failing tests first, the
    multi-pod suite: two `LayeredCache` instances (each with its own
    `InMemoryCache` L1) sharing one `InMemoryCache` "L2" and one
    `InMemoryBackplane`. (a) node A `set()` → node B's L1 no longer serves the
    old value; (b) node A `delete()` → node B's L1 evicted; (c) node A
    `delete_prefix()` → only matching keys evicted on B; (d) **a received
    message never touches L2** (assert the shared L2 still holds the value);
    (e) **echo suppression** — A does not evict its own freshly written L1
    entry; (f) publish happens **after** the L2 write (assert with an
    instrumented L2 whose `set()` records ordering); (g) a `publish()` that
    raises internally does not propagate out of `cache.set()`; (h) `clear`
    received → local layers only.
27. [ ] `varco_core/varco_core/cache/layered.py` — `LayeredCache.__init__`
    gains keyword-only `backplane: CacheBackplane | None = None`. `ValueError`
    when `backplane` is given and `promote_ttl is None`, message naming brief
    002's bounded-staleness mitigation. `start()`/`stop()` drive the
    backplane's lifecycle and register/unregister the receive handler (the
    CLAUDE.md rule: lifecycle-owned, never started ad hoc by app code).
    `set`/`delete`/`delete_prefix`/`clear` take the ordered path **only** when a
    backplane is wired; the no-backplane `asyncio.gather` path is untouched.
    Receive handler evicts `self._layers[:-1]` only, records
    `backplane_received`.
28. [ ] `varco_redis/varco_redis/backplane.py` — new. `RedisBackplaneSettings`
    (pydantic, `env_prefix="VARCO_REDIS_CACHE_BACKPLANE_"`: `url`, `channel`
    default `"varco.cache.invalidate"`, `hash_keys=False`) and
    `RedisPubSubBackplane(settings=None, *, channel_for=None)` on
    `redis.asyncio`. House style per `varco_redis/rate_limit.py`,
    `bulkhead.py`, `lock.py`: shared singleton, async client, `connect()`/
    lifecycle, DESIGN blocks with ✅/❌, `📚 Docs` links. A background listener
    task started in `start()` and cancelled in `stop()`; JSON message codec;
    `origin` = a per-process uuid4; `publish()` swallows and records
    `backplane_dropped(reason="publish_failed")`; a message that fails to decode
    is dropped with `reason="decode_failed"`; **on re-subscribe after a
    connection loss, emit a synthetic `kind="clear"` to the local handler**
    (brief 002 §2). No Lua is required here (Pub/Sub is a single non-atomic
    command) — state that explicitly in the DESIGN block so the deviation from
    the rate-limiter/bulkhead Lua house style is deliberate and legible.
29. [ ] `varco_redis/varco_redis/backplane.py` — register for DI the same way
    the rest of `varco_redis` does (module-level `@Provider` for the settings —
    **never `@Singleton` on a pydantic `BaseSettings`**, per CLAUDE.md's
    `**values` pitfall — and `@Singleton` on the backplane itself), so
    `container.scan("varco_redis", recursive=True)` discovers it.
30. [ ] `varco_redis/tests/test_redis_backplane.py` — unit tests against the
    existing `FakeRedis` mock (extended with a minimal pubsub double): publish
    encodes the expected JSON; a received self-origin message is skipped;
    `hash_keys=True` publishes a hash and degrades `prefix` to `clear`;
    `publish()` never raises when the client errors.
31. [ ] `varco_redis/tests/test_redis_backplane.py` — `@pytest.mark.integration`
    test against real Redis: two `RedisPubSubBackplane` instances (two
    "pods") + two `LayeredCache`s over one real `RedisCache` L2; assert
    cross-node L1 invalidation end to end, and assert the reconnect flush by
    stopping/restarting the subscriber.
32. [ ] `varco_redis/tests/test_redis_di.py` — extend the existing
    `scan` + `validate_bindings()` test to cover the new backplane bindings
    (CLAUDE.md's "package's suite is green but its container won't bootstrap"
    pitfall).
33. [ ] `technical_docs/features/cache-hardening.md` — add the "L1 coherence
    backplane" section: the mechanism decision with brief 002 citations, the
    six design rules, the write-ordering latency cost, the
    `promote_ttl`-required `ValueError`, the **key-name exposure** discussion
    with `channel_for`/`hash_keys` opt-outs, and a "when to prefer Redis
    Streams (R2)" note.
34. [ ] `CLAUDE.md` — extend the cache-system section with the backplane, and
    add pitfall rows: *`LayeredCache` in multi-pod without a backplane* (→ each
    pod's L1 silently serves stale entries — **the shipped bug this closes**);
    *`backplane=` with `promote_ttl=None`* (→ `ValueError`, unbounded staleness
    behind a fire-and-forget channel); *per-call `RedisPubSubBackplane`*
    (→ shared-singleton rule); *backplane key names visible fleet-wide* (→ use
    `channel_for`/`hash_keys` under per-tenant-pod topologies).
35. [ ] `ARCHITECTURE.md` + `README.md` — backplane in the cache layer map and
    one feature-list line.

### Phase 4 — C4: SWR + jitter + negative caching

36. [ ] `varco_core/tests/test_cache_swr.py` — failing tests first:
    (a) a soft-expired entry is returned **immediately** (assert the caller does
    not await the loader) and exactly one refresh runs; (b) 50 concurrent
    soft-stale reads → **one** refresh (the C2×C4 interaction test); (c) a cold
    reader arriving during an in-flight refresh becomes a follower and does not
    start a second recompute; (d) a hard-expired entry blocks and recomputes;
    (e) `refresh_mode="blocking"` awaits the refresh; (f) refresh tasks are
    drained by `aclose()`.
37. [ ] `varco_core/varco_core/cache/readthrough.py` — activate the soft-stale
    branch: serve stale, `record_stale_served(reason="soft_ttl")`,
    `singleflight.spawn_refresh(...)` (or await it under
    `refresh_mode="blocking"`). Activate the stale-on-error branch: loader
    raised **and** a stale value exists within `stale_if_error` → return stale
    and `record_stale_served(reason="error")`; otherwise re-raise.
38. [ ] `varco_core/tests/test_cache_jitter.py` — failing tests first: with
    `ttl_jitter=0.2`, 1000 computed TTLs lie in `[0.8·ttl, 1.2·ttl]`, are not
    all equal, and `ttl_jitter=0.0` returns exactly `ttl` (default is
    deterministic).
39. [ ] `varco_core/varco_core/cache/policy.py` — implement
    `effective_ttl(rng=random)` applying symmetric fractional jitter; used by
    `read_through` for every `set()`.
40. [ ] `varco_core/tests/test_cache_negative.py` — failing tests first:
    (a) default `CachePolicy()` → a `None` result is **not** cached (the
    unchanged-behaviour guard, D-4); (b) `negative_ttl=30` → `None` is cached,
    a second call does not invoke the loader, and the caller still receives
    `None` (not the envelope); (c) the negative entry expires on `negative_ttl`,
    not on `ttl`; (d) `record_cache_hit(kind="negative")` is emitted;
    (e) a negative entry is invalidated by `delete()` and by a backplane
    message like any other.
41. [ ] `varco_core/varco_core/cache/readthrough.py` — activate negative
    caching: a `None` loader result is wrapped with `is_negative=True` and
    `negative_ttl` when `policy.negative_ttl is not None`; on read, a negative
    envelope short-circuits to `None` without calling the loader.
42. [ ] `technical_docs/features/cache-hardening.md` — add the "Stale-while-
    revalidate, jitter, negative caching" section: the soft/hard TTL model, the
    C2×C4 interaction diagram, the **two-step rolling-deploy recipe for enabling
    envelope mode** (D-5), and a "choosing `negative_ttl`" note (shorter than
    the positive TTL).
43. [ ] `CLAUDE.md` — pitfall rows: *`soft_ttl >= ttl`* (→ `ValueError`, the
    soft window can never fire); *enabling envelope mode mid-rolling-deploy*
    (→ old pods return the wrapper dict to the application; two-step deploy);
    *negative caching hiding a fixed row* (→ a `negative_ttl` longer than the
    fix loop keeps serving "not found" after the row exists — keep it short and
    invalidate explicitly on create).
44. [ ] `ARCHITECTURE.md` + `README.md` — final pass: cache section reflects all
    four items.

---

## Edge cases

| Input / state | Expected behaviour |
|---|---|
| `CachePolicy()` + no `singleflight` + no backplane | Every call site behaves byte-identically to pre-plan varco: no envelope written, `None` not cached, `LayeredCache.set()` still one `asyncio.gather`. Guarded by step 8's suite. |
| 100 concurrent misses, one key, `singleflight=True` | Loader invoked once; 99 `stampede_suppressed` recorded; all 100 get the same object. |
| Same pk, two different tenants, concurrent misses | **Two** loader invocations. Coalescing keys carry `tenant:{id}:`. Step 12. |
| Follower cancelled (its own `@timeout` fires) | Leader unaffected (`asyncio.shield`); other followers still get the value. |
| Leader cancelled | Every follower sees `CancelledError`; slot cleared; next call elects a new leader. |
| Loader raises, `stale_if_error` unset | Exception propagates; nothing is cached. |
| Loader raises, `stale_if_error` set, stale entry within window | Stale value returned; `stale_served{reason="error"}`. |
| Soft-expired entry, 50 concurrent readers | All 50 return immediately with the stale value; exactly one background refresh. |
| Cold reader arrives during an in-flight refresh | Becomes a follower of the same future; no second recompute. |
| `soft_ttl >= ttl` | `ValueError` at `CachePolicy` construction. |
| `ttl_jitter` outside `[0.0, 1.0)` | `ValueError` at construction. |
| `negative_ttl` unset, loader returns `None` | Not cached (today's behaviour). |
| `negative_ttl` set, loader returns `None` | Cached as a negative envelope; subsequent reads return `None` without calling the loader; `hits{kind="negative"}`. |
| Legacy raw value already in L2, new code reads it | Treated as a fresh hit (`unwrap()` returns `None` → legacy path). |
| Old pod reads a new pod's envelope | ⚠️ Returns the wrapper dict — the reason envelope mode requires a two-step deploy (D-5). |
| `backplane=` with `promote_ttl=None` | `ValueError` at `LayeredCache` construction naming brief 002's bounded-staleness mitigation. |
| Backplane `publish()` fails (Redis down) | Swallowed; `backplane.dropped{reason="publish_failed"}`; the caller's `set()` still succeeds (L2 write already landed). |
| Node receives its own message | Skipped (origin match); no eviction; one DEBUG log. |
| Node receives `kind="clear"` | Clears `self._layers[:-1]` only. **Never** L2. |
| Backplane subscriber reconnects | Synthetic local `clear` → local layers flushed (brief 002 §2). |
| Backplane message arrives while the L2 write is still in flight | Cannot happen when a backplane is wired: publish is sequenced strictly after the authoritative-layer write. |
| `hash_keys=True` + `delete_prefix()` | Degrades to a local `clear` on receivers (a hash cannot be prefix-matched). Documented, not silent. |
| Metrics pack never installed | Every `record_*` is a cheap no-op; hot path unchanged. |
| `install_cache_metrics()` called twice | Idempotent (same contract as `install_reliability_metrics()`). |
| An instrument raises inside `record_*` | Swallowed by `_safe()`; the cache operation completes. |
| `NoOpSerializer`-backed cache + envelope mode | Fails loudly at the first `set()` (a dict is not `bytes`). Documented in `envelope.py`. |

---

## Verification

```bash
# Unit — the whole cache surface, old and new
uv run pytest varco_core/tests/test_cache.py
uv run pytest varco_core/tests/test_cache_envelope.py varco_core/tests/test_cache_policy.py
uv run pytest varco_core/tests/test_cache_singleflight.py \
              varco_core/tests/test_cache_singleflight_tenancy.py \
              varco_core/tests/test_cache_decorator_singleflight.py
uv run pytest varco_core/tests/test_cache_readthrough.py varco_core/tests/test_cache_metrics.py
uv run pytest varco_core/tests/test_cache_backplane.py
uv run pytest varco_core/tests/test_cache_swr.py varco_core/tests/test_cache_jitter.py \
              varco_core/tests/test_cache_negative.py

# Redis backend — unit (FakeRedis) then real broker
uv run pytest varco_redis/tests/test_redis_cache.py varco_redis/tests/test_redis_backplane.py
uv run pytest varco_redis/tests/test_redis_di.py
uv run pytest varco_redis/tests/ -m integration        # requires Docker Redis

# Regression sweep — nothing else may move
uv run pytest varco_core/tests/
uv run pytest varco_redis/tests/

# Gates
make lint
make type-check
```

Per repo convention every test is `async def` with no `@pytest.mark.asyncio`
(auto mode), `InMemoryEventBus` + `drain()` is used wherever event ordering
matters (the existing `EventDrivenStrategy` / `CacheInvalidationConsumer`
tests), `InMemoryBackplane` is the standard backplane for unit tests, and real
Redis is `@pytest.mark.integration` (skipped by default). If a
timing-sensitive test flakes, increase its sleep margin — never `xfail` it.

---

## Framework table & migration impact

**None.** R1 adds no framework table, no Alembic revision, no
`register_framework_metadata()` call, no Beanie document, and no CLI verb.
The framework table count stays at ten. `varco migrate` is untouched.

The only persistent-shape change is the **value** written into an existing cache
key when an envelope-requiring policy field is enabled — a Redis *value* shape,
not a schema — handled by the two-step deploy recipe in D-5. The Redis key space
gains nothing; the backplane uses a Pub/Sub channel, which stores no keys.

---

## Risks

- ⚠️ **ASSUMPTION** — brief 002 states `redis.asyncio` has no `CLIENT TRACKING`
  support as of redis-py 5.1.0/5.2.0 with issue #3916 open and no ETA. Not
  re-verified against the exact `redis` version pinned in this workspace. If a
  newer pinned redis-py has landed async support, D-1 should be *re-examined*
  before Phase 3 — but the pub/sub backplane remains correct and the ABC keeps
  the door open either way, so this is a "we could have done better" risk, not a
  correctness risk.
- ⚠️ **ASSUMPTION** — brief 003 checked OTel semconv v1.44.0 (Aug 2026) and
  found no cache-metrics convention. Re-check `#1747` at implementation time; if
  a convention has since been approved, adopt its names via the `METRIC_NAMES`
  table (which exists precisely for this).
- ⚠️ **ASSUMPTION** — `varco_redis/tests/test_redis_cache.py`'s `FakeRedis`
  double has no pub/sub surface today (the scout notes it is a dict with no TTL
  enforcement). Step 30 assumes it can be extended with a minimal pubsub double;
  if that proves awkward, write the backplane unit tests against a purpose-built
  fake in `varco_redis/tests/` rather than contorting `FakeRedis`, and lean
  harder on the step-31 integration test.
- ⚠️ **ASSUMPTION** — `CacheServiceMixin.read()`/`list()` are assumed to be the
  only two read paths needing `read_through` wiring (based on the mixin's
  `_cache_key`/`_cache_list_key` structure). Confirm by reading `mixin.py` in
  full before step 11; if there are more cached read paths, wire them the same
  way rather than leaving a half-instrumented mixin.
- **Write latency regression under `backplane=`.** Ordered writes turn
  `max(L1,L2)` into `L2 + max(faster)`. Invariant that must hold: the ordering
  path is entered **only** when a backplane is wired. A benchmark is not
  required, but step 27 must not "simplify" by making the ordered path
  unconditional.
- **Fire-and-forget backplane is best-effort by construction.** A subscriber
  disconnected at publish time never receives the message (brief 002 §3). The
  invariant that bounds the damage is the mandatory `promote_ttl` — if a future
  change relaxes that `ValueError`, C1's correctness argument collapses.
- **Envelope + `type_hint`.** Envelope mode passes `type_hint=None` to the
  backend and re-applies the hint via `envelope.coerce()`. If a caller depends
  on a serializer-specific reconstruction that `TypeAdapter` cannot reproduce,
  typed reads degrade to dicts. Invariant: `coerce()` must never *raise* on an
  unrecognised hint — it returns the payload unchanged and logs at DEBUG.
- **Background refresh tasks.** `spawn_refresh` creates unawaited tasks. The
  invariant is that `Singleflight` holds a strong reference to every one and
  `aclose()` drains them; an untracked task can be GC'd mid-flight (step 6f is
  the guard).
- **Metric cardinality.** Every attribute added to a cache instrument multiplies
  series across every cache in the fleet. Invariant: the attribute keys are
  exactly the deny-list-checked set in step 19; a tenant id or cache key must
  never appear (brief 003 §4).
- **Scope creep into R2.** `CachePolicy` is a tempting home for `get_many`
  batching (C5), a `CachePreset` bundle, and i18n-adjacent knobs. None of those
  are in R1. If a phase starts growing a fifth feature, stop and file it against
  R2.
