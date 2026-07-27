# Varco Documentation

Varco is a **uv workspace monorepo** of async Python packages for building
event-driven, layered services. Each package is independently installable from
PyPI; `varco_core` is the dependency-free foundation every other package builds on.

```
varco_core        — domain model, service layer, event system, resilience, DI contracts
varco_kafka       — Kafka event bus backend (aiokafka)
varco_nats        — NATS event bus backend
varco_redis       — Redis Pub/Sub event bus + cache backend (redis.asyncio)
varco_sa          — SQLAlchemy async ORM backend
varco_beanie      — Beanie/MongoDB async ODM backend
varco_memcached   — Memcached cache backend
varco_ws          — WebSocket support
varco_fastapi     — FastAPI integration (routers, auth, generic REST server)
```

## How this site is organized

- **Features** — hand-written, conceptual technical docs for each feature: what it
  is, how to use it, how data flows through it, and where it is wired in. Start here
  to understand *how* a feature works.
- **API Reference** — auto-generated from source docstrings. Every public class,
  function, and module, with signatures, arguments, return types, and raised
  exceptions. Use this as the authoritative API surface — you should never need to
  open the source to understand an API.

## Notable guides

- [OTel automatic parameter capture & global attributes](features/observability-attributes.md)
  — how `@span` records call arguments as span attributes, and how the process-wide
  global attribute registry labels every span and metric.
- [Database auditing](features/database-auditing.md) — wiring `AuditLogMixin` +
  `AuditConsumer` to an append-only audit trail in `varco_sa` / `varco_beanie`.

## Building the docs

```bash
make docs-deps   # install documentation tooling (once)
make docs        # build the static HTML site into ./site
make docs-serve  # live-reload preview at http://127.0.0.1:8000
```
