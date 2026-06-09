---
name: project-gaps-scan-2026-06-09
description: Cross-backend parity gaps and missing implementations found in full scan of all 10 varco packages, June 2026
metadata:
  type: project
---

Full scan of varco workspace (10 packages: varco_core, varco_kafka, varco_redis, varco_sa, varco_beanie, varco_casbin, varco_fastapi, varco_nats, varco_ws, varco_memcached).

**Why:** Release planning scan, June 9 2026.
**How to apply:** Use as the ground truth for parity gaps when planning next release.

## Confirmed parity gaps (Backend A has it; Backend B does not)

- **BeanieConversationStore missing**: varco_redis has `RedisConversationStore`, varco_sa has `SAConversationStore`, but varco_beanie has NO conversation store. `AbstractConversationStore` ABC exists in varco_core.
- **NATS missing durable infrastructure modules**: varco_nats has only: bus, channel, config, connection, di, dlq, health. Missing vs SA/Beanie: saga, inbox, job_store, conversation, audit, encryption_store, advisory-lock equivalent, rate_limit.
- **NATS missing rate_limit**: varco_redis has `RedisRateLimiter`; no equivalent in varco_nats.
- **No SA/Beanie deduplication**: varco_redis has `RedisDeduplicator`; varco_sa and varco_beanie have no equivalent (NATS deduplication is in bus.py via Nats-Msg-Id header).
- **varco_casbin only has memory/sqlalchemy adapters**: adapter.py + optional SA extra; no Beanie/MongoDB adapter for persisted policy storage.
- **OPA policy engine explicitly marked "future"**: varco_core/auth/policy.py references `OpaPolicyEngine (varco_opa, future)` — no varco_opa package exists.

## Partially implemented / stubs

- **varco_core query applicator**: only contains `applicator.py` (generic base). The SA applicator lives in varco_sa. No Beanie `QueryApplicator` equivalent — beanie has `BeanieQueryCompiler` and `BeanieAggregationApplicator` but no unified `QueryApplicator` wrapper.
- **varco_fastapi metrics endpoint**: tests skip when `prometheus_client` not installed — the functionality exists but the optional extra integration is conditional.

## Cross-cutting observations

- varco_ws: `WebSocketEventBus` and `SSEEventBus` are fully implemented with DI, but they are scan-discovered singletons that only handle all events on all channels; per-channel wiring requires manual FastAPI lifespan code — documented but no helper.
- varco_memcached: `MemcachedCache` and `MemcachedHealthCheck` fully implemented; no DI bootstrap() helper (unlike every other package).
- varco_sa `pool_metrics.py`: exists and is useful; no equivalent in varco_beanie.
- varco_sa has `SoftDeleteService` (mixin) in varco_core; Beanie test file references `deleted_at` in IS_NULL tests, so it works but there's no explicit Beanie integration test for soft delete with BeanieRepository.
- varco_core.auth.policy references `OpaPolicyEngine (varco_opa, future)` explicitly in source.
