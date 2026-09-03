# Conformance suite coverage — audit outcome

**Never packaged** — lives alongside the five conformance base classes it documents, reached only
via `pythonpath = ["../testkit"]` (CLAUDE.md's Test Conventions). This file is the durable, audited
record answering: *for every implementation of one of the five shared ABCs, does it subclass the
matching conformance suite, and if not, why not?*

Produced by Plan 024 (§D-C7, C7). See BACKLOG.md's "C7's audit outcome" open question, answered:
**two** real gaps out of a five-suite × ~24-implementation matrix — the great majority of the
apparent gaps scout tooling flagged were legitimate absences, not real holes.

**Rule (CLAUDE.md's Test Conventions)**: a new implementation of one of the five ABCs either
subclasses its suite or gets a row in this file explaining why not — a future absence must be
argued against a written record, not rediscovered from scratch.

---

## The matrix

| Suite | Implementations | Subclassed | Gap |
|---|---|---|---|
| `channel_manager` | `KafkaChannelManager`, `NatsStreamManager`, `RedisChannelManager` | `varco_kafka/tests/test_kafka_channel_integration.py:111`, `varco_nats/tests/test_nats_channel_integration.py:122`, `varco_redis/tests/test_redis_channel.py:228` | none (no in-process implementation exists — see "Stated absences" below) |
| `cache` | `InMemoryCache`, `NoOpCache`, `RedisCache`, `LayeredCache`, `MemcachedCache` | `varco_core/tests/test_conformance_inmemory.py:59,70`; `varco_redis/tests/test_redis_conformance.py:49,73`; `varco_memcached/tests/test_memcached_conformance.py:26` | none |
| `job_store` | `InMemoryJobStore`, `SAJobStore`, `RedisJobStore`, `BeanieJobStore` | `varco_core/tests/test_conformance_inmemory.py:130` (cross-package import of `varco_fastapi/varco_fastapi/job/store.py:40`, documented); `varco_sa/tests/test_sa_conformance.py:26`; `varco_redis/tests/test_redis_conformance.py:56`; `varco_beanie/tests/test_beanie_conformance.py:34` | none |
| `event_bus` | `InMemoryEventBus`, `NoopEventBus`, `RedisEventBus`, `RedisStreamEventBus`, `KafkaEventBus`, `NatsEventBus` | all but `NoopEventBus` | **`NoopEventBus`** — resolved as a stated reason, not a subclass (see below) |
| `dlq` | `InMemoryDLQ`, `RedisDLQ`, `RedisStreamDLQ`, `KafkaDLQ`, `NatsDLQ`, `SADeadLetterQueue`, `BeanieDeadLetterQueue` | all — `RedisStreamDLQ` filled by Plan 024 Step 32 (`varco_redis/tests/test_redis_conformance.py::TestRedisStreamDLQConformance`) | **none remaining** — was `RedisStreamDLQ`, now closed |

## Stated absences

These are legitimate, permanent absences — not TODOs, not backlog rows.

- **`NoopEventBus`** (`varco_core/varco_core/event/memory.py:639`) — a Null Object: `publish()`
  discards, `subscribe()` returns a **pre-cancelled** `Subscription`
  (`memory.py:665-691`). It deliberately violates `EventBusConformance`'s
  deliver-what-you-publish contract by design — subclassing the suite for it would mean
  xfail-ing most of the suite, which teaches nothing and rots. It is intentionally uncovered by
  the shared suite; `varco_core/tests/test_conformance_inmemory.py`'s module docstring points here.
- **`channel_manager`** — there is no `InMemoryChannelManager`. `ChannelManager` is inherently a
  broker-admin concern (declare/delete/list a real topic/stream/channel on a real broker), so an
  in-process fake would test nothing meaningful. Only the three real-broker backends
  (`varco_kafka`, `varco_redis`, `varco_nats`) subclass it, and all three do.
- **`varco_ws`** — already resolved, not a hole. `varco_ws/tests/test_ws_conformance.py:1-27`
  explains that `WebSocketEventBus`/`SSEEventBus` are push adapters *wrapping* an
  `AbstractEventBus`, not a new bus implementation — they are covered by their own bespoke
  real-server tests instead of the `event_bus` suite, which would not exercise the adapter
  behaviour that actually matters (WS/SSE framing, connection lifecycle).
- **`varco_memcached`** — implements only `CacheBackend`. No event bus, DLQ, job store, or channel
  manager exists in this package, so it legitimately subscribes to only the `cache` suite.
- **`varco_casbin`** — implements **none** of the five ABCs (it is a policy engine, not a
  broker/cache/job-store backend), and therefore does not need `pythonpath = ["../testkit"]` at
  all — the only package of the ten deliberately without one. Confirmed present in all nine
  others.

## What Plan 024 filled

- **`RedisStreamDLQ` → subclassed.** `varco_redis/tests/test_redis_conformance.py` gained
  `TestRedisStreamDLQConformance(DeadLetterQueueConformance)`, mirroring `TestRedisDLQConformance`.
  It is a real, durable DLQ implementation with a real transport; there was no principled reason
  for it to be less proven than `RedisDLQ`.

---

**Audited and written down**: 2026-09-02 (Plan 024, §D-C7). Referenced from CLAUDE.md's Test
Conventions conformance paragraph.
