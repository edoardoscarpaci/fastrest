# Varco Examples Catalog

## Package → example map

| Package | Examples |
|---|---|
| `varco_core` | 00, 01, 02, 04, 05, 06, 07, 08, 09, 11, 12, 14, 15, 16, 17, 19, 21, 22 |
| `varco_fastapi` | 01, 02, 03, 04, 07, 23, 24 |
| `varco_kafka` | 15, 16 |
| `varco_redis` | 17, 19 |
| `varco_sa` | 00, 01, 02, 04, 05, 06, 09, 11, 12, 14 |
| `varco_beanie` | 21, 22 |
| `varco_casbin` | 07 |

Focused, deployable reference apps — each demonstrates 1–4 closely-related varco features.
Every example runs with `uv run` or `docker compose up` and includes a smoke test.

## Coverage matrix

| # | Example | Features covered | Infra |
|---|---------|-----------------|-------|
| 00 | [full-stack-post-api](00-full-stack-post-api/) | Full-stack showcase — all packages | postgres, redis, kafka |
| 01 | [minimal-crud-api](01-minimal-crud-api/) | VarcoCRUDRouter, DomainModel/DTO, providify DI, in-memory repo | none |
| 02 | [api-gateway-guards](02-api-gateway-guards/) | GenericRouter, RouteGuard, require_scopes/roles/predicate, JwtBearerAuth | none |
| 03 | [observability-metrics](03-observability-metrics/) | enable_metrics, MetricsMiddleware, OTel tracing, Prometheus scrape | prometheus (optional) |
| 04 | [profiling-hotspot](04-profiling-hotspot/) | @profile, profiled(), ProfilingMiddleware, X-Profile-* headers, custom backend | none |
| 05 | [jwt-authority-rotation](05-jwt-authority-rotation/) | JwtAuthority, MultiKeyAuthority, TrustedIssuerRegistry, key sources, JWK | tiny jwks service |
| 06 | [grant-based-authz](06-grant-based-authz/) | AuthContext.can, ResourceGrant, GrantBased/RoleBased/Ownership authorizers | none |
| 07 | [casbin-policy-engine](07-casbin-policy-engine/) | varco_casbin ACL/RBAC/ABAC, PolicyEngineAuthorizer, REST admin router, SA adapter | postgres |
| 08 | [field-encryption](08-field-encryption/) | encrypt sensitive fields at rest, transparent decrypt on read | postgres |
| 09 | [sqlalchemy-postgres](09-sqlalchemy-postgres/) | SAModelFactory, repository/UoW, migrator, query filtering → SQL WHERE | postgres |
| 10 | [beanie-mongo](10-beanie-mongo/) | Beanie ODM repository/UoW, query → Mongo, BeanieOutbox/ConversationStore | mongo |
| 11 | [query-filtering](11-query-filtering/) | QueryParser, AST nodes, TypeCoercionVisitor, QueryOptimizer, pagination/sort | none |
| 12 | [cache-look-aside-redis](12-cache-look-aside-redis/) | RedisCache, @cached, TTL/Tagged/EventDriven invalidation, CacheServiceMixin | redis |
| 13 | [layered-cache-memcached](13-layered-cache-memcached/) | LayeredCache (L1 InMemory + L2 memcached), NoOpCache for tests | memcached |
| 14 | [kafka-order-events](14-kafka-order-events/) | varco_kafka bus, producer/consumer/@listen, retry_policy, KafkaDLQ | kafka |
| 15 | [nats-jetstream-events](15-nats-jetstream-events/) | varco_nats JetStream bus, EventConsumer, ChannelManager, HealthCheck | nats |
| 16 | [redis-pubsub-streams](16-redis-pubsub-streams/) | RedisEventBus pub/sub vs Redis Streams (at-least-once) toggle | redis |
| 17 | [transactional-outbox](17-transactional-outbox/) | OutboxRepository + OutboxRelay, SADeduplicator, same-tx event persistence | postgres + redis |
| 18 | [realtime-ws-sse](18-realtime-ws-sse/) | WebSocketEventBus + SSEEventBus fan-out to browser clients | none |
| 19 | [resilience-payment-gateway](19-resilience-payment-gateway/) | @timeout, @retry, @circuit_breaker, Bulkhead, @hedge | none |
| 20 | [distributed-rate-limit](20-distributed-rate-limit/) | RedisRateLimiter (multi-pod) vs InMemoryRateLimiter, @rate_limit | redis |
| 21 | [async-job-runner](21-async-job-runner/) | job module, enqueue async jobs, 202 Accepted + job_id, poll status | none |
| 22 | [multi-tenant-soft-delete](22-multi-tenant-soft-delete/) | TenantAwareService + SoftDeleteService + ValidatorServiceMixin via MRO | postgres |
| 23 | [composite-all-in-one](23-composite-all-in-one/) | create_composite_app — combine multiple services into one deployment; per-service isolation, aggregate health, build_service scoped env | none |
| 24 | [custom-route-params](24-custom-route-params/) | Full FastAPI parameter injection on custom `@route` handlers — Query/Body/Depends/Request + typed path params, with ctx + RouteGuard | none |

## Conventions

- **Run:** each example has a single `uv run uvicorn app:app --reload` or `docker compose up` command in its README.
- **Tests:** `uv run pytest` (no Docker) for smoke tests; `uv run pytest -m integration` for infra-backed tests.
- **Infra:** each example's `docker-compose.yml` is independent and pinned to specific image versions.
- **DI:** wired with providify — inject interfaces, never concrete types.
