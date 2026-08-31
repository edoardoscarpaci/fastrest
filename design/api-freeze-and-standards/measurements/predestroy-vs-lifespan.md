# Measurement — `@PreDestroy` singletons vs. `VarcoLifespan` components

**Plan 022 / Phase 0, Steps 5 and 6.** Design section §D-8a1 — *prove the
premise before deciding it*. This file decides §D-8a2 (adopt
`container.ashutdown()`, or decline it) and, via Step 6, whether §D-8a2(b)'s
double-stop model is safe.

---

## Part 0 — the verified half, restated

`varco_fastapi/varco_fastapi/lifespan.py:181-190` — the class's own docstring
asserts it never calls `container.shutdown()`/`ashutdown()`, and `_stop_all()`
(`:221-233`) iterates only `self._components`. Confirmed by reading. So:
**"if an orphan exists, it is never torn down" is proven**. The open question
was only whether one exists.

---

## Part 1 (Step 5) — every path that appends to `lifespan_components`

The plan's Risks section flags this as an ASSUMPTION and specifically names
`app.py:387` as untraced. It is now traced exhaustively. `grep -n
'lifespan_components' varco_fastapi/varco_fastapi/app.py` yields **five**
mutation sites plus the constructor:

| `app.py` line | Path | Adds | Condition |
|---|---|---|---|
| `:319` | `_collect_lifecycle_components(container)` | the four auto-discovered types (below) | always (empty list when `container is None`) |
| `:320-321` | `extra_lifespan_components=` kwarg | caller-supplied objects | caller passes the kwarg |
| `:335-337` | `MigrationLifecycle` (prepended) | 1 | `migrations is not None` **and** `MigrationSettings.mode != "off"` |
| `:355` | `tenancy` kwarg (prepended) | 1 | `tenancy is not None` |
| `:379-381` | `ReliabilityLifecycle` (appended) | 1 | `reliability is not None` |
| `:418` | `I18nLifecycle` (appended) | 1 | `i18n.enabled` **and** `container is not None` **and** a `MessageCatalog` is bound |
| `:420` | `VarcoLifespan(*lifespan_components)` | — | always |

`app.py:387` — the line the Risks section suspected — is **a comment**, part of
the block explaining *why* the i18n resolution at `:418` must happen before
`:420`. It is not a sixth append site. The suspicion is resolved: **there are
no untraced paths.**

`_collect_lifecycle_components()` (`app.py:685-732`) auto-discovers exactly
**four** well-known types, via `_try_resolve_component()`:

1. `varco_core.event.base.AbstractEventBus`
2. `varco_core.job.base.AbstractJobRunner`
3. `varco_ws.websocket.WebSocketEventBus` (`warn_if_missing=False`)
4. `varco_ws.sse.SSEEventBus` (`warn_if_missing=False`)

**Nothing else is ever auto-registered.** In particular: no `ChannelManager`,
no `CacheBackend`, no `PolicyEngine`.

### Resolvability, measured rather than assumed

Every `@PreDestroy` class below is registered with
`@Singleton(priority=-sys.maxsize, qualifier="<backend>")`. Whether a
*qualified* singleton satisfies an *unqualified* `container.get(iface)` is a
providify detail, so it was executed rather than reasoned about — a fresh
`DIContainer()`, `container.scan(pkg, recursive=True)`, then
`container.is_resolvable(iface)`:

| `scan(...)` | `AbstractEventBus` | `AbstractJobRunner` | `ChannelManager` | `CacheBackend` | `PolicyEngine` |
|---|---|---|---|---|---|
| `varco_kafka` | **True** | False | True | False | False |
| `varco_nats` | **True** | False | True | False | False |
| `varco_redis` | **True** | False | True | **True** | False |
| `varco_memcached` | False | False | False | **True** | False |
| `varco_casbin` | False | False | False | False | **True** |

The qualified bindings **are** resolvable unqualified. So the buses genuinely
do become lifecycle components; the `ChannelManager`/`CacheBackend`/
`PolicyEngine` bindings are equally resolvable and are simply **never asked
for**.

### The table §D-8a1 asked for

| # | Class | Site | Registered as a `VarcoLifespan` component? |
|---|---|---|---|
| 1 | `KafkaEventBus` | `varco_kafka/bus.py:321` | **Only under config X** — yes iff the app bound it as `AbstractEventBus` (`varco_kafka.di.bootstrap(container)`); measured resolvable. |
| 2 | `KafkaChannelManager` | `varco_kafka/channel.py:234` | ❌ **No — ORPHAN.** Binds `ChannelManager`, which nothing in `_collect_lifecycle_components()` resolves. |
| 3 | `NatsEventBus` | `varco_nats/bus.py:281` | Only under config X (same as #1, via `varco_nats.di.bootstrap`). |
| 4 | `NatsStreamManager` | `varco_nats/channel.py:319` | ❌ **No — ORPHAN.** Same reason as #2. |
| 5 | `RedisEventBus` | `varco_redis/bus.py:217` | Only under config X — and see the ⚠️ note below. |
| 6 | `RedisStreamEventBus` | `varco_redis/streams.py:318` | Only under config X — selected by `RedisEventBusSelectorConfiguration.bus()` (`bus.py:511-558`) when `VARCO_REDIS_USE_STREAMS=true`. Has **no** `@Singleton` of its own. |
| 7 | `RedisChannelManager` | `varco_redis/channel.py:132` | ❌ **No — ORPHAN.** Same reason as #2. |
| 8 | `RedisCache` | `varco_redis/cache.py:213` | ❌ **No — ORPHAN**, and the worst one: `RedisCacheConfiguration.redis_cache()` (`cache.py:573-597`) **awaits `cache.start()`** inside the provider, so `async_bootstrap(setup_cache=True)` leaves a *started* connection pool that no lifespan path ever closes. |
| 9 | `MemcachedCache` | `varco_memcached/cache.py:248` | ❌ **No — ORPHAN**, same shape as #8, and `varco_memcached.di.async_bootstrap()` defaults `setup_cache=True`, so this is the *default* path for that package. |
| 10 | `CasbinPolicyEngine` | `varco_casbin/engine.py:212` | ❌ **No — ORPHAN.** Binds `PolicyEngine`/`PolicyManagement`. Its `stop()` is a no-op, so the leak is nominal, not resource-bearing. |

⚠️ **Incidental finding on #5/#6, recorded not fixed.** `RedisEventBus` carries
`@Singleton(qualifier="redis")` *and* is constructed a **second time** by
`RedisEventBusSelectorConfiguration.bus()` (`bus.py:558`,
`return RedisEventBus(config=settings)`). Only the selector's instance is bound
to `AbstractEventBus` and therefore only that one becomes a lifecycle
component; anything resolving the qualified `@Singleton` gets a distinct
instance that is a #2-class orphan. Out of scope for Plan 022 — filed here so
it is not re-derived.

### ⛔ Verdict on §D-8a1's unproven half

> **6 confirmed orphans out of 10.**

`KafkaChannelManager`, `NatsStreamManager`, `RedisChannelManager`, `RedisCache`,
`MemcachedCache`, `CasbinPolicyEngine`.

BACKLOG's RL-8a row (*"⚠️ Suspected, not proven"*) is **proven true**, and the
`RedisCache` / `MemcachedCache` cases are stronger than suspected: those two are
not merely un-torn-down, they are un-torn-down **while holding a live,
already-started connection pool**.

Two qualifications that keep this honest:

* An orphan only *leaks* if it was actually instantiated — providify's
  `ashutdown()` runs `@PreDestroy` on constructed singletons only. #2/#4/#7/#10
  are constructed lazily, so an app that never injects a `ChannelManager`
  leaks nothing. #8/#9 are constructed **eagerly** by their `@Configuration`,
  so they always leak once installed.
* The four buses (#1/#3/#5/#6) are only lifecycle components when the app ran
  the package's `bootstrap()`. An app that constructs a bus by hand and passes
  it nowhere is also an orphan — but that is caller error, not a framework gap.

**Consequence for §D-8a2**: the "if zero orphans" branch (plan Step 20 → skip to
Step 24, close RL-8a as *decided: no*) does **not** fire. The recommendation
stands: **adopt `container.ashutdown()`** via the `shutdown=` kwarg, subject to
Part 2 below.

---

## Part 2 (Step 6) — `stop()` idempotency across all ten

§D-8a2(b) keeps LIFO `_stop_all()` first and `await shutdown()` second, so any
component reachable by both paths gets `stop()` called twice. All ten
implementations were read.

| # | Class | Idempotent | The line that makes it so |
|---|---|---|---|
| 1 | `KafkaEventBus` | ✅ yes | `bus.py:333` `if not self._started: return`; `:349` clears the flag. |
| 2 | `KafkaChannelManager` | ✅ yes | `channel.py:237` `if self._admin is None: return`; `:240` sets it to `None`. |
| 3 | `NatsEventBus` | ✅ yes | `bus.py:294` `if not self._started: return`; `:321` clears the flag. |
| 4 | `NatsStreamManager` | ✅ yes | `channel.py:322` `if self._nc is None: return`; `:325-326` set it to `None`. |
| 5 | `RedisEventBus` | ✅ yes | `bus.py:222` `if not self._started: return`; `:238` clears the flag. |
| 6 | `RedisStreamEventBus` | ✅ yes | `streams.py:326` `if not self._started: return`; `:339` clears the flag. |
| 7 | `RedisChannelManager` | ✅ yes | No guard needed — the whole body is `self._started = False` + a DEBUG log (`channel.py:137-138`). Assignment is idempotent by construction. |
| 8 | `RedisCache` | ✅ yes | `cache.py:216` `if self._redis is None: return`; `:221` sets it to `None`. |
| 9 | `MemcachedCache` | ✅ yes | `cache.py:256` `if self._client is None: return`; `:262` sets it to `None`. |
| 10 | `CasbinPolicyEngine` | ✅ yes | No guard needed — the body is two `= None` assignments (`engine.py:221-222`). Idempotent by construction. |

### Tally

> **10 idempotent / 0 non-idempotent.**

Every one either short-circuits on a sentinel it then sets, or performs only
unconditional assignments. Consequences for the plan:

* **Step 21 (fix non-idempotent `stop()`s) has zero work** — it can be marked
  N/A rather than executed.
* The plan's Risks trigger — *"if more than three components turn out
  non-idempotent, stop and reconsider §D-8a2(b)"* — does **not** fire.
* `VarcoLifespan.register()`'s existing docstring expectation
  (`lifespan.py:148-151`, *"components should be idempotent"*) is not merely an
  expectation: it holds today for all ten. Making it load-bearing is safe.

Caveat, stated because Step 6 is a *precondition* and not a proof for all time:
this is a read of the current implementations, not a test. Step 21's budget
should be re-spent as **two double-`stop()` regression tests** (one bus, one
cache) so the property cannot silently regress once §D-8a2(b) makes it
load-bearing.

---

## Summary for the plan's branch points

| Question | Answer |
|---|---|
| Step 5 orphan count | **6 of 10** |
| Does Step 20's "zero orphans" branch fire? | **No** — proceed with adoption |
| Step 6 idempotency tally | **10 yes / 0 no** |
| Does Step 21 have work? | **No** — repurpose as regression tests |
| Does the ">3 non-idempotent → reconsider" trigger fire? | **No** |
