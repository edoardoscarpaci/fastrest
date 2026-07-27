# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Quick reference**: See [ARCHITECTURE.md](ARCHITECTURE.md) for a complete technical map of all packages, modules, classes, type hierarchies, and design patterns. Use it to navigate the codebase efficiently without reading files one-by-one.

---

## Commands

All commands run from the **workspace root** (`/home/edoardo/projects/varco`) using a single shared virtual environment managed by `uv`.

```bash
# Install everything (all workspace members + dev deps)
uv sync

# Run all tests for one package
uv run pytest varco_core/tests/
uv run pytest varco_kafka/tests/
uv run pytest varco_redis/tests/
uv run pytest varco_sa/tests/

# Run a single test file
uv run pytest varco_core/tests/test_event.py

# Run a single test by name
uv run pytest varco_core/tests/test_event.py::TestInMemoryEventBus::test_subscribe

# Run integration tests (require Docker — Kafka, Redis, or MongoDB broker)
uv run pytest varco_kafka/tests/ -m integration
uv run pytest varco_redis/tests/ -m integration

# Import any workspace package directly (no install step needed)
uv run python -c "from varco_core.event import AbstractEventBus"
```

There is no lint command configured. There is no type-check command configured. `pytest-asyncio` is installed with `asyncio_mode = "auto"` in every package — all `async def test_*` functions run automatically without `@pytest.mark.asyncio`.

---

## Architecture

Varco is a **uv workspace monorepo** of five packages. Each package is independently installable from PyPI. `varco_core` has no sibling dependencies; all other packages depend on it.

```
varco_core        — domain model, service layer, event system, resilience, DI contracts
varco_kafka       — Kafka event bus backend (aiokafka)
varco_redis       — Redis Pub/Sub event bus + cache backend (redis.asyncio)
varco_sa          — SQLAlchemy async ORM backend
varco_beanie      — Beanie/MongoDB async ODM backend
varco_casbin      — Casbin policy-engine authorization backend (ACL/RBAC/ABAC + REST admin)
```

### Dependency graph

```
varco_kafka  ──┐
varco_redis  ──┤
varco_sa     ──┤─→ varco_core
varco_beanie ──┤
varco_casbin ──┘   (+ optional varco_fastapi for the REST admin router)
```

`varco_core` is the only package without a `[tool.uv.sources]` sibling reference. All backend packages resolve it from the workspace rather than PyPI during development.

---

## Key Abstractions and Layer Rules

### Event system (varco_core.event)

Three concentric layers — never skip a layer:

```
User code (services, handlers)
  ↓ may use
AbstractEventProducer   — publish side; services depend on THIS, not the bus
EventConsumer + @listen — consume side; methods decorated at class-definition time
  ↓ both delegate to
AbstractEventBus        — low-level interface; only producers/consumers touch it
  ↓ implemented by
InMemoryEventBus        — for tests
KafkaEventBus           — varco_kafka
RedisEventBus           — varco_redis
```

**Rule**: services must never hold or call `AbstractEventBus` directly. They inject `AbstractEventProducer` and call `_produce()` / `_produce_many()`. The only accepted exceptions are `OutboxRelay` (infrastructure) and `EventConsumer.register_to()` (wiring-time only).

**`@listen` is declarative / `register_to` is imperative.** The decorator stores metadata on the function object at class-definition time. No subscription is created until `consumer.register_to(bus)` is called (typically in a `@PostConstruct` method). This separation makes the consumer bus-agnostic and testable.

```python
# Correct wiring pattern
class OrderConsumer(EventConsumer):
    @PostConstruct
    def _setup(self) -> None:
        self.register_to(self._bus)   # wiring happens here, not in @listen

    @listen(OrderPlacedEvent, channel="orders")
    async def on_order(self, event: OrderPlacedEvent) -> None: ...
```

### Service layer (varco_core.service)

`AsyncService[D, PK, C, R, U]` is generic over five type parameters:
- `D` — DomainModel subclass
- `PK` — primary key type
- `C` / `R` / `U` — Create / Read / Update DTO subclasses

Concrete subclasses implement exactly one abstract method:
```python
def _get_repo(self, uow: AsyncUnitOfWork) -> AsyncRepository[D, PK]: ...
```

Three optional hooks chain via `super()` for mixin composition: `_scoped_params`, `_check_entity`, `_prepare_for_create`. Authorization is enforced at the service layer (not HTTP), via the injected `AbstractAuthorizer`.

**Mixin composition pattern** — `ValidatorServiceMixin`, `TenantAwareService`, `SoftDeleteService`, `EventConsumer` all compose via MRO. Chain hooks with `super()` so every mixin in the chain runs.

### DI wiring (providify)

Each backend package ships a `di.py` with a `bootstrap()` helper that runs `container.scan(...)` to discover its `@Singleton` classes. Some packages also expose an opt-in `@Configuration` for resources that need imperative async setup (e.g. `RedisCacheConfiguration`). The DI module is the only place that knows concrete types — application code always injects interfaces (`AbstractEventBus`, `AsyncRepository[D]`, `IUoWProvider`).

```python
# Typical app bootstrap
container = DIContainer()
container.scan("varco_kafka", recursive=True)   # discovers the Kafka bus @Singletons
container.install(SAModule)
bind_repositories(container, User, Post)
```

### Resilience (varco_core.resilience)

Three standalone decorators composable with any callable:

```python
@timeout(10.0)                                         # async only
@retry(RetryPolicy(max_attempts=3, base_delay=0.5))   # sync or async
@circuit_breaker(CircuitBreakerConfig(failure_threshold=5))
async def call_external() -> Response: ...
```

**`CircuitBreaker` must be a shared instance per external dependency** — a per-call instance will never accumulate enough failures to open. Use `@circuit_breaker(config)` for per-function breakers, or `breaker.protect(fn)` for a shared breaker across multiple functions.

`@retry` is also integrated into `@listen` via `retry_policy=` and `dlq=` parameters. The wrapper is built at `register_to()` time (not decoration time) so the resolved channel string and bound `self` are available.

### Dead Letter Queue (varco_core.event.dlq)

`AbstractDeadLetterQueue` is the interface. `InMemoryDeadLetterQueue` is for tests. Backend implementations (`KafkaDLQ`, `RedisDLQ` in their respective packages) push to a dedicated topic/channel.

**Contract**: `push()` must never raise — the retry wrapper in `_make_retry_wrapper` cannot recover from DLQ failures. Implementations must log errors and swallow them.

```python
# Handler that retries 3x then routes to DLQ
@listen(
    OrderPlacedEvent,
    channel="orders",
    retry_policy=RetryPolicy(max_attempts=3, base_delay=1.0),
    dlq=my_dlq,
)
async def on_order(self, event: OrderPlacedEvent) -> None: ...
```

### SQLAlchemy backend (varco_sa)

`varco_sa` generates SQLAlchemy ORM models at import time from `DomainModel` subclasses via `SAModelFactory`. Models are never declared manually — they are derived from the domain model's field annotations and `FieldHint` / `ForeignKey` / `PrimaryKey` metadata decorators in `varco_core.meta`.

The `SAConfig` object (engine + declarative base + entity classes) is the single injectable configuration object — it doubles as the DI settings object, avoiding a parallel `SASettings` class.

### Observability (varco_core.observability)

`@span`/`@counter`/`@histogram` decorators, `TracingServiceMixin`/`TracingRepositoryMixin`,
`Metric`/`register_gauge`, and `OtelConfig`/`OtelConfiguration` provide OpenTelemetry tracing
and metrics. Two modules add automatic instrumentation on top, both opt-out rather than opt-in:

- **`varco_core.observability.params`** — every `@span` (and `create_span(..., params=...)`)
  automatically records the decorated function's call arguments as `param.<name>` span
  attributes: name-based redaction (`password`/`token`/`secret`/…), truncation, and
  scalar-only rendering by default (`ParamCaptureConfig(value_mode="scalars")`). Kill switches:
  `SpanConfig(capture_params=False)` per-decorator, `VARCO_OTEL_CAPTURE_PARAMS=false` /
  `set_capture_enabled(False)` process-wide. ⚠️ **`TracingServiceMixin`/`TracingRepositoryMixin`
  do NOT auto-capture `pk`/`dto`/`params`** — only `@span`-decorated functions and
  `create_span(..., params=...)` get automatic parameter capture; CRUD spans only carry global
  attributes + `SpanConfig.attributes` + `correlation_id`.
- **`varco_core.observability.attributes`** — a process-wide `GlobalAttributes` registry
  (`set_global_attributes()` / `register_global_attribute_provider()`) stamped on **every**
  span AND **every** metric measurement via a single instrument-creation choke point
  (`wrap_instrument()`). Env-var bootstrap: `VARCO_OTEL_GLOBAL_ATTRS` /
  `VARCO_OTEL_GLOBAL_ATTR_ENV` / `VARCO_OTEL_GLOBAL_ATTRS_SPANS` / `VARCO_OTEL_GLOBAL_ATTRS_METRICS`.

**Rule — Resource attribute vs. global attribute registry**: static process identity
(`k8s.pod.name`, `deployment.environment`, a Helm release) belongs in
`OtelConfig.extra_resource_attrs` (free — exported once per batch, never multiplies metric
series). The global attribute registry is for values not known at bootstrap or that must be
filterable/`group by`-able as a metric **label** — every key in the registry becomes a label on
every metric series it touches. See `technical_docs/features/observability-attributes.md` for
the full decision table, the PII section, and the Kubernetes Downward-API recipe.

### Profiling (varco_core.profiling)

Diagnostic CPU + memory profiler. Complements the aggregate OTel observability layer
(spans/metrics answer "how slow on average"; the profiler answers "which function is hot"
and "what allocated this memory").

**Off by default — zero overhead when disabled:**

```python
from varco_core.profiling import profile, profiled, ProfileConfig, set_profiling_enabled

# Enable globally (or set VARCO_PROFILING_ENABLED=true)
set_profiling_enabled(True)

# Decorator form — works on sync and async functions
@profile(ProfileConfig(top_n=10))
async def slow_query() -> list[Row]: ...

# Context manager form — gives access to the report
async with profiled("batch_job") as session:
    await process_batch()
print(session.report.format())
```

**FastAPI middleware:**

```python
# Env-var driven:
# VARCO_PROFILER_ENABLED=true VARCO_PROFILER_ATTACH_HEADERS=true

app = create_varco_app(container, enable_profiling=True)
```

**Adding a custom backend (memray, pyinstrument, py-spy):**

```python
from varco_core.profiling import CpuProfilerBackend, CpuProfileResult, register_cpu_backend

class PyinstrumentBackend:
    name = "pyinstrument"
    def start(self) -> None: ...
    def collect(self, top_n: int, sort_by: str) -> CpuProfileResult: ...

register_cpu_backend("pyinstrument", PyinstrumentBackend)
cfg = ProfileConfig(cpu_backend="pyinstrument")
```

**Rules:**
- `cProfile` and `tracemalloc` are **process-global** — one session at a time. The FastAPI
  middleware serialises with a process-wide `asyncio.Lock`; concurrent requests pass through
  unprofiled rather than blocking.
- `cProfile` across an `await` captures all coroutines on the event loop thread. Use it for
  CPU-bound or isolated async work; use a sampling backend (pyinstrument) for busy loops.
- `tracemalloc` prior state is always **restored** on session exit — safe to use in apps
  that already enable it.
- **Never leave profiling always-on in production** — `cProfile`/`tracemalloc` add 20–100%
  overhead. Use the kill-switch (`set_profiling_enabled`) or `VARCO_PROFILING_ENABLED`.

### Cache system (varco_core.cache)

`AsyncCache[K, V]` is a `runtime_checkable` Protocol; `CacheBackend[K, V]` is the ABC backends subclass (adds `start()`/`stop()` lifecycle). Hierarchy:

```
AsyncCache (Protocol)  ←  structural checks, type hints
  ↑
CacheBackend (ABC)     ←  inherit start/stop + async context manager
  ↑
InMemoryCache  NoOpCache  RedisCache (varco_redis)  LayeredCache
```

`InvalidationStrategy` is a separate ABC — every strategy (TTL, tag-based, event-driven, composite) implements `start()`/`stop()` called by the hosting backend. Use `CompositeStrategy` to combine strategies. The `@cached` decorator (`varco_core.cache.decorator`) wraps any async callable; `CacheServiceMixin` adds caching to service layer methods.

**Rule**: never instantiate `InvalidationStrategy` outside its backend's `start()`/`stop()` lifecycle — it may hold subscriptions or background tasks.

### Query system (varco_core.query)

The query system builds a typed AST over filter/sort/pagination parameters and applies it to backends:

```
QueryParams (HTTP layer input)
  ↓ parsed by
QueryParser          → FilterNode AST (ComparisonNode / AndNode / OrNode / NotNode)
  ↓ visited by
ASTVisitor           → e.g. SQLAlchemyFilterVisitor → WHERE clause
QueryOptimizer       → constant-folding / dead-branch elimination
TypeCoercionVisitor  → coerce string scalars to annotated field types
  ↓ applied by
QueryApplicator      → attaches filter + sort + pagination to a backend query
```

All AST nodes are `@dataclass(frozen=True)` — immutable, hashable, safe to cache. `QueryTransformer` wires the full pipeline in one call. The SQLAlchemy applicator lives in `varco_core.query.applicator.sqlalchemy` (not in `varco_sa`) so the query system stays backend-agnostic.

### Transactional Outbox (varco_core.service.outbox)

Services must **not** publish events directly after a DB commit — a broker failure will silently drop the event. Use the outbox pattern instead:

1. Within the DB transaction, save the event as an `OutboxEntry` via `OutboxRepository`.
2. A background `OutboxRelay` polls pending entries, publishes to the bus, and deletes on success.

```python
# Correct: event is persisted in the same transaction as the domain entity
async with uow:
    await repo.save(entity)
    await outbox_repo.save_outbox(OutboxEntry.from_event(event))
# OutboxRelay publishes asynchronously — delivery is guaranteed even on broker restart
```

`OutboxRepository` is an ABC; `varco_sa` and `varco_beanie` each ship a concrete implementation. `OutboxRelay` is the only place allowed to call `AbstractEventBus` directly (besides `EventConsumer.register_to()`).

### Database auditing (varco_core.service.audit)

An append-only audit trail for `create`/`update`/`delete` mutations, event-driven like the
outbox pattern above but persisted by a dedicated consumer rather than a relay:
`AuditLogMixin` (service mixin, composes to the LEFT of `AsyncService`) emits an `AuditEvent`
on the `"varco.audit"` channel via the service's existing `AbstractEventProducer` —
`AuditConsumer` subscribes and persists each event as an `AuditEntry` via an injected
`AuditRepository` (`SAAuditRepository` in `varco_sa`, `BeanieAuditRepository` in `varco_beanie`).

```python
class OrderService(
    AuditLogMixin,                                              # ← left of AsyncService
    AsyncService[Order, UUID, CreateOrderDTO, OrderReadDTO, UpdateOrderDTO],
):
    def _get_repo(self, uow): return uow.orders
    def _get_audit_actor(self, ctx): return ctx.sub            # override — base returns None

# Wire the consumer from @PostConstruct, same rule as any other EventConsumer
class AuditWiring:
    def __init__(self, bus: Inject[AbstractEventBus], audit_repo: Inject[SAAuditRepository]):
        self._bus = bus
        self._consumer = AuditConsumer(audit_repo=audit_repo)

    @PostConstruct
    def _setup(self) -> None:
        self._consumer.register_to(self._bus)
```

**Idempotency is backend-specific** — `SAAuditRepository.save` uses Postgres
`INSERT ... ON CONFLICT (entry_id) DO NOTHING` (idempotent on redelivery, falling back to a
plain `IntegrityError`-raising insert on non-Postgres dialects); `BeanieAuditRepository.save`
is a plain `doc.insert()` with no conflict handling — a duplicate `entry_id` raises
`DuplicateKeyError`. `AuditConsumer.on_audit_event` ships with no `retry_policy`/`dlq` —
subclass and re-declare the `@listen`-decorated method if you need resilience. For a
"must not lose an audit record" guarantee, route the `AuditEvent` through the transactional
outbox instead of a direct `_produce()` call. See
`technical_docs/features/database-auditing.md` for the full wiring guide (Alembic/Beanie
setup, `list_for_entity()`, consistency trade-offs).

### Authority / JWT system (varco_core.authority)

`JwtAuthority` signs tokens with a private key; `TrustedIssuerRegistry` verifies tokens from multiple trusted issuers. Key rotation is zero-downtime via `MultiKeyAuthority`:

```python
# Signing
authority = JwtAuthority.from_pem(pem_bytes, kid="svc:A", issuer="my-svc", algorithm="RS256")
token = authority.sign(authority.token().subject("usr_1").expires_in(timedelta(hours=1)))

# Rotation
multi = MultiKeyAuthority(authority)
multi.rotate(JwtAuthority.from_pem(new_pem, kid="svc:B", ...))
multi.retire("svc:A")   # only after all tokens signed with svc:A have expired

# Verification (multi-issuer)
registry = TrustedIssuerRegistry.from_env()
await registry.load_all()
payload = await registry.verify(raw_token)
```

Key sources (`varco_core.authority.sources`): `PemFile`, `PemFolder`, `JwksUrl`, `OidcDiscovery`. `TrustedIssuerRegistry.from_env()` reads issuer config from environment variables.

**JWKS caching knobs**: `TrustedIssuerRegistry(min_refresh_interval=..., ttl_seconds=...)`
(env: `VARCO_JWKS_MIN_REFRESH_SECONDS` default `10.0`, `VARCO_JWKS_TTL_SECONDS` default `0.0` =
disabled) tune when the in-memory keyset cache refreshes. `ttl_seconds` makes `get_key()`
proactively reload once the cache is stale, without waiting for a `kid` miss. ⚠️ **There is no
background refresher task** — a registry that never receives a `verify()` call never refreshes
on its own regardless of these knobs; a real background-refresh task is deliberately deferred
(needs its own lifespan start/stop wiring).

#### Claim transformation + token profiles (varco_core.jwt.transform / varco_core.jwt.profile)

Real-world issuers (Keycloak, Cognito, Auth0, a bespoke internal claim) rarely name their
roles/scopes/tenant claims the way varco expects. `varco_core.jwt.transform` maps a foreign
claim shape onto the canonical names `JwtParser` builds `AuthContext` from — **with zero
application code changes**, because `JwtParser._from_raw_claims` is the single funnel both
`JwtParser.parse()` and `TrustedIssuerRegistry.verify()` (and therefore `varco_fastapi`'s
`JwtBearerAuth`/`PassthroughAuth`) go through:

```bash
export VARCO_JWT_TRANSFORM_ROLES_FIELD="sofy-roles,realm_access.roles"
export VARCO_JWT_TRANSFORM_SCOPES_FIELD="scope"          # space-delimited OAuth2 scope
export VARCO_JWT_TRANSFORM_TENANT_FIELD="org.id"
```

`varco_core.jwt.profile.TokenProfile` / `TokenProfileRegistry` replace the single
`JwtUtil.SYSTEM_ISSUER` class variable with **named, composable profiles** (`system`,
`internal`, `partner`, `service-mesh`, …), matched on issuer/token_type/audience/required
claims and optionally granting `implied_roles`/`implied_scopes`:

```bash
export VARCO_JWT_PROFILE__INTERNAL__ISS="mesh-signer"
export VARCO_JWT_PROFILE__INTERNAL__TOKEN_TYPE="system"
export VARCO_JWT_PROFILE__INTERNAL__ROLES="internal"     # implied_roles
```

```python
from varco_fastapi.auth.guard import require_token_profile

@route("GET", "/internal", requires=require_token_profile("internal"))
async def internal_only(self, ctx: AuthContext) -> dict: ...
```

`JwtUtil.SYSTEM_ISSUER` and `is_system()` **keep working** — no removal scheduled, no runtime
`DeprecationWarning`. `is_system()` prefers a registered `"system"` profile when one exists;
otherwise falls back to the live `SYSTEM_ISSUER` `ClassVar` comparison (documentation-only
deprecation). See `technical_docs/features/jwt-claim-transformer.md` and
`technical_docs/features/token-profiles.md` for the full env-var reference, per-issuer
precedence, and IdP recipes (Keycloak/Cognito/Auth0).

**`VARCO_JWT_*` env-var reference** (verification hardening — `varco_core.jwt.config.JwtVerificationSettings`):

| Env var | Default | Effect |
|---|---|---|
| `VARCO_JWT_LEEWAY_SECONDS` | `0.0` | clock-skew leeway for `exp`/`nbf` checks — fixes intermittent cross-host 401s |
| `VARCO_JWT_AUDIENCE` | `None` | this service's expected `aud` — `None` = **not enforced** (opt-in hardening); `JwtBearerAuth` logs one warning at construction when unset |
| `VARCO_JWT_TRANSFORM_*` | — | claim-transform mapping (global) — see the claim-transformer feature doc |
| `VARCO_JWT_TRANSFORM__<LABEL>__*` | — | per-issuer claim-transform override, keyed by `iss` |
| `VARCO_JWT_PROFILE__<NAME>__*` | — | named token profile declaration |
| `VARCO_JWKS_MIN_REFRESH_SECONDS` | `10.0` | rate limit between kid-miss JWKS refreshes |
| `VARCO_JWKS_TTL_SECONDS` | `0.0` | proactive JWKS reload age threshold (`0` = disabled) |

### Authorization — policy engine (varco_core.auth.policy + varco_casbin)

Two layers of authorization coexist:

- **Static, token-derived** (`varco_core.auth.base`) — `AuthContext.can(action, resource_key)`
  over JWT-encoded `ResourceGrant`s. Zero-latency, stateless. Helpers: `GrantBasedAuthorizer`,
  `RoleBasedAuthorizer`, `OwnershipAuthorizer`. `BaseAuthorizer` is the permissive fallback.
- **Dynamic, engine-driven** (`varco_core.auth.policy`) — a pluggable `PolicyEngine` evaluating
  ACL/RBAC/ABAC rules held outside the token (file or DB), editable at runtime.

```
AsyncService → AbstractAuthorizer            (the seam services already inject)
                  ↑ implemented by
              PolicyEngineAuthorizer          (bridge — raises ServiceAuthorizationError on deny)
                  ↓ delegates to
              PolicyEngine.enforce(EnforcementRequest)   (hot path, backend-agnostic)
              PolicyManagement.add/remove/list/reload    (cold admin surface)
                  ↑ both implemented by
              varco_casbin.CasbinPolicyEngine  (wraps casbin.AsyncEnforcer)
```

`RequestMapper` maps `(AuthContext, Action, Resource)` → `EnforcementRequest` (subject/object/action
+ ABAC `subject_attrs`/`object_attrs` + optional `domain`). It reuses `_default_resource_key`, so
token grants and engine rules share one resource-key namespace. Override `subject_for` /
`object_for` / `domain_for` for custom keying (e.g. tenant domains).

**Wiring** (`varco_casbin.di`):
```python
container = bootstrap(DIContainer())     # binds CasbinPolicyEngine → PolicyEngine + PolicyManagement
enable_policy_authorizer(container)      # OPT-IN: binds PolicyEngineAuthorizer → AbstractAuthorizer
```

**Rules**:
- The authorizer is **opt-in** via `enable_policy_authorizer(container)` — it is NOT a scanned
  `@Configuration` (scan auto-activates those), so importing/bootstrapping `varco_casbin` never
  silently shadows an app's own authorizer.
- `CasbinPolicyEngine` must be a **shared singleton** (DI handles this) — a per-call engine reloads
  policy every request.
- `CasbinSettings` (pydantic `BaseSettings`) is registered via a `@Provider` in `bootstrap`, NOT
  `@Singleton` — providify cannot inject pydantic's `**values` constructor.
- The REST admin API is `build_policy_router(engine, server_auth=..., admin_role="admin")` (requires
  the `varco-casbin[fastapi]` extra) — a plain FastAPI `APIRouter` rather than a `VarcoRouter`
  (a standalone admin surface with its own JSON-body handlers; it predates `@route`'s full
  FastAPI-parameter support and there is no need to migrate it).
- Persisted dynamic CRUD needs `adapter="sqlalchemy"` (the `varco-casbin[sqlalchemy]` extra); the
  default `memory` adapter is non-durable.

---

## Planning & Development Workflows

### Before Adding a Feature

1. **Check ARCHITECTURE.md type hierarchies** — Find existing abstractions that apply. For example:
   - Adding authentication? → Look at `authority/jwt_authority.py` and `TrustedIssuerRegistry`
   - Adding caching? → Extend `CacheBackend` and pick an `InvalidationStrategy`
   - Adding event handling? → Extend `EventConsumer`, use `@listen`, wire with `register_to()`

2. **Check if a backend implementation already exists** — Don't implement the same interface twice:
   - Event bus? → Kafka and Redis backends exist; add a new one only if truly needed
   - Cache? → In-memory, Redis, and layered exist
   - ORM? → SQLAlchemy and Beanie exist
   - Query filtering? → AST + visitor pattern handles this; extend `ASTVisitor` if needed
   - CPU profiling? → `"cprofile"` backend exists; add pyinstrument/py-spy by implementing `CpuProfilerBackend`
   - Memory profiling? → `"tracemalloc"` backend exists; add memray by implementing `MemoryProfilerBackend`

3. **Identify the layer boundary** — Where does this feature live?
   - Protocol/ABC in `varco_core`? → Used by app code
   - Concrete impl in a backend (`varco_kafka`, `varco_redis`, `varco_sa`)? → Backend-specific
   - Service mixin? → If it composes via MRO with other mixins

### Common Scenarios

#### Scenario: Add a new event type and handler

```python
# 1. Define the event in varco_core (domain layer)
class OrderShippedEvent(DomainEvent):
    order_id: UUID
    shipped_at: datetime

# 2. Emit from service (via producer, not bus directly)
class OrderService(AsyncService[Order, UUID, ...]):
    async def ship_order(self, order_id: UUID) -> None:
        async with self._uow_provider.get_uow() as uow:
            order = await repo.get(order_id)
            order.status = "shipped"
            await repo.save(order)
            await self._producer.produce(OrderShippedEvent(
                order_id=order_id,
                shipped_at=datetime.now(UTC),
            ))

# 3. Handle in a consumer (EventConsumer subclass)
class NotificationConsumer(EventConsumer):
    def __init__(self, bus: AbstractEventBus, mailer: Mailer):
        self._bus = bus
        self._mailer = mailer

    @PostConstruct
    def _setup(self) -> None:
        self.register_to(self._bus)

    @listen(OrderShippedEvent, channel="orders")
    async def on_order_shipped(self, event: OrderShippedEvent) -> None:
        await self._mailer.send(f"Order {event.order_id} shipped!")

# 4. Wire in DI
container = DIContainer()
container.scan("varco_kafka", recursive=True)   # discovers the Kafka bus @Singletons
container.install(NotificationConsumerModule)
```

**Caveat**: If the handler can fail (e.g., email send timeout), add `retry_policy` and `dlq`:

```python
@listen(
    OrderShippedEvent,
    channel="orders",
    retry_policy=RetryPolicy(max_attempts=3, base_delay=1.0),
    dlq=my_dlq,  # routes to DLQ after 3 failures
)
async def on_order_shipped(self, event: OrderShippedEvent) -> None: ...
```

#### Scenario: Add caching to a service method

```python
# 1. Choose invalidation strategy
from varco_core.cache import TTLStrategy, TaggedStrategy, CompositeStrategy

# 2. Mix in CacheServiceMixin (order matters in MRO!)
class UserService(
    CacheServiceMixin,          # ← LEFT side (runs first)
    TenantAwareService,
    AsyncService[User, UUID, UserCreateDTO, UserReadDTO, UserUpdateDTO],
):
    _cache_config = CacheConfig(
        backend=RedisCache(...),
        invalidation_strategy=CompositeStrategy([
            TTLStrategy(ttl_seconds=300),
            TaggedStrategy(),
        ]),
    )

    def _get_repo(self, uow: AsyncUnitOfWork) -> AsyncRepository[User, UUID]:
        return uow.get_repository(User)

# 3. Use @cached on methods (works with any async callable)
from varco_core.cache import cached

@cached(key_fn=lambda self, user_id: f"user:{user_id}")
async def get_user_profile(self, user_id: UUID) -> UserProfile:
    # This is cached; invalidation strategy handles eviction
    return await self.read(user_id)

# 4. Invalidate explicitly when needed
@listen(UserUpdatedEvent, channel="users")
async def on_user_updated(self, event: UserUpdatedEvent) -> None:
    await self._cache.invalidate_by_tag(f"user:{event.user_id}")
```

**Caveat**: Cache invalidation is hard. Order strategies by most-to-least-aggressive:
- `EventDrivenStrategy` (immediate, requires event emission)
- `TaggedStrategy` (explicit, requires you to call invalidate)
- `TTLStrategy` (eventual, stale reads until expiry)

#### Scenario: Add filtering to a list endpoint

```python
# 1. HTTP layer receives filter strings (e.g., ?age__gte=18&status__eq=active)
from varco_core.query import QueryParams

params = QueryParams(
    filters=request.query_params.getlist("filter"),  # ["age__gte=18", "status__eq=active"]
    sort=request.query_params.getlist("sort"),       # ["+created_at"]
    limit=int(request.query_params.get("limit", 50)),
    offset=int(request.query_params.get("offset", 0)),
)

# 2. Pass to service (uses QueryTransformer internally)
page = await user_service.list(params, tenant_id=current_user.tenant_id)

# 3. Service.list() handles:
#    - Parse filters → AST (ComparisonNode, AndNode, OrNode, NotNode)
#    - Type coercion (string "18" → int 18)
#    - Optimize (constant folding, dead branches)
#    - Apply to backend query (SQLAlchemy: WHERE clause)
```

**Caveat**: Filter operators are backend-agnostic AST. If adding a new comparison operator:
- Update `QueryParser` to recognize it (e.g., `"__between"`)
- Extend `ASTVisitor` subclasses to handle it (e.g., `SQLAlchemyFilterVisitor`)
- Test on every backend (SQLAlchemy, Beanie, etc.)

#### Scenario: Build a service-free / data-processing REST server

Use `GenericRouter` (alias for `VarcoRouter` with no type args) when the server
has no `AsyncService` or repository — e.g. a data-transformation pipeline, an
API gateway, or computed analytics routes.  All cross-cutting features (middleware,
telemetry, auth, `RouteGuard` authorization) work identically to a service-backed router.

```python
from varco_fastapi.router.presets import GenericRouter
from varco_fastapi.router.endpoint import route
from varco_fastapi.auth import JwtBearerAuth
from varco_fastapi.auth.guard import require_scopes, require_roles

class ReportRouter(GenericRouter):
    _prefix = "/reports"
    _auth = JwtBearerAuth(...)               # authentication stays in middleware

    @route("GET", "/summary", requires=require_scopes("reports:read"))
    async def get_summary(self, ctx: AuthContext) -> dict:
        return {"total": compute_total(ctx)}

    @route("DELETE", "/cache", requires=require_roles("admin"))
    async def purge_cache(self, ctx: AuthContext) -> None:
        invalidate_cache()

# Wire exactly like a normal router
app = create_varco_app(routers=[ReportRouter])
```

**Key facts**:
- `validate_router_class` does NOT warn about missing generic type args (`D/PK/C/R/U`) for a `GenericRouter` — they are legitimately absent.
- `requires=` kwarg on `@route` takes a `RouteGuard` (built with `require_scopes`, `require_roles`, `require_grant`, `require_predicate`, or `allow_anonymous`).
- Guard is checked **before** the handler runs; denial raises `ServiceAuthorizationError` → HTTP 403.
- If `ctx` is declared in the handler, `_auth` must be set (or it will not be populated).
- For truly public endpoints (no auth needed), use `requires=allow_anonymous()` or omit `requires=` altogether and don't declare `ctx`.
- **Custom `@route` handlers get full FastAPI parameter injection** — declare `Query(...)`, `Body(...)` (Pydantic models), `Depends(...)`, `Request`/`Response`/`BackgroundTasks`, and **type-coerced** path params, exactly like a hand-written FastAPI endpoint; the return annotation drives the OpenAPI response model. `build_router()` synthesizes a wrapper whose `__signature__` mirrors the method so FastAPI parses everything natively (see `_make_custom_handler` / `_synthesize_custom_signature` in `router/base.py`). `ctx`/`auth`/`context` and the `RouteGuard`/async-offload behavior are unchanged. **Exception:** on an `async_capable` route with a job runner wired, `response_model` inference is suppressed (the route may return a `JobAcceptedResponse` when `?with_async=true`).

#### Scenario: Expose custom service methods on a typed CRUD router

`VarcoCRUDRouter` (and the `CRUDRouter`/`ReadOnlyRouter`/`WriteRouter`/`NoDeleteRouter`
presets) accept an optional, defaulted 6th type parameter `S` — the concrete
`AsyncService` subclass. Add it as the 6th type arg to get `self._service` typed
`S | None` and the `self.service` property typed non-Optional `S`, with zero
per-subclass boilerplate (no cast, no hand-rolled `@property` override):

```python
from varco_fastapi.router.presets import CRUDRouter
from varco_fastapi.router.endpoint import route

class OrderService(AsyncService[Order, UUID, OrderCreate, OrderRead, OrderUpdate]):
    async def cancel_order(self, order_id: UUID) -> None: ...

class OrderRouter(CRUDRouter[Order, UUID, OrderCreate, OrderRead, OrderUpdate, OrderService]):
    _prefix = "/orders"

    @route("POST", "/{order_id}/cancel")
    async def cancel(self, order_id: UUID) -> None:
        # self.service is typed OrderService (not the erased AsyncService base) —
        # .cancel_order is visible to the type checker with no cast.
        await self.service.cancel_order(order_id)
```

- 5-arg subscription (`CRUDRouter[Order, UUID, OrderCreate, OrderRead, OrderUpdate]`) still
  works unchanged — `S` defaults to `AsyncService[Any, ...]` via PEP 696
  (`typing_extensions.TypeVar`, since `requires-python = ">=3.12"` predates the native syntax).
- `self.service` raises `RuntimeError` if `_service` was never injected/set — prefer it over
  `self._service` at call sites that invoke custom methods, so you don't repeat an
  `is None` guard. The 501-Not-Implemented CRUD fallback path is unaffected — it still reads
  `getattr(self, "_service", None)` directly, not the property.
- Fallback for anyone staying on 5 type args: declare a subclass `@property` that casts, e.g.
  `@property\n    def service(self) -> OrderService:\n        return cast(OrderService, self._service)`.

#### Scenario: Combine multiple services into one all-in-one deployment

Use `create_composite_app` (`varco_fastapi.composite`) to run several **already-built**
varco services in a single ASGI process. Each service keeps its own container, database,
environment, middleware, and `/docs` — they are mounted as ASGI sub-apps under prefixes.

```python
from varco_fastapi import create_composite_app, ServiceMount

from orders_service.app import app as orders_app      # its own create_varco_app()
from billing_service.app import app as billing_app     # its own container + DB + env

composite = create_composite_app([
    ServiceMount("/orders", orders_app),
    ServiceMount("/billing", billing_app),
])
# uvicorn composite:composite
```

**Key facts**:
- Mounting is purely additive — existing service code is untouched. Each sub-app serves
  its own `{prefix}/docs` + `{prefix}/openapi.json`; there is no merged OpenAPI schema.
- `create_composite_app` installs a `CompositeLifespan` that drives **each sub-app's own
  lifespan**. This is required: Starlette's `Router.lifespan` does NOT descend into
  mounted sub-apps, so without it every service's DB pool / `AbstractEventBus` /
  `OutboxRelay` would silently never start.
- Startup is **fail-fast** — one service failing to start aborts the whole process
  (no half-broken deployment). Shutdown is LIFO.
- `aggregate_health=True` (default) exposes a root `GET /health` that probes each
  service's own health in-process (via `httpx.ASGITransport`) and returns 503 if any is
  unhealthy — one readiness signal for the whole deployment.
- **Env isolation**: all services share one `os.environ`. Runtime isolation is automatic
  (each app holds its own container/engine objects). The only hazard is *build-time*
  env-name collision — two services reading bare `os.environ["DATABASE_URL"]` see the
  same value. Fix by namespacing env vars per service, or use
  `build_service(prefix, factory, env={...})` which overlays a scoped environment only
  while that one service is built, then restores it.
- No composite-level middleware by default — each sub-app owns its full middleware stack
  and runs it exactly as standalone (avoids double-processing, e.g. tracing wrapped twice).

#### Scenario: Profile a slow operation

```python
from varco_core.profiling import profile, profiled, ProfileConfig, set_profiling_enabled

# 1. Enable globally (or VARCO_PROFILING_ENABLED=true in env)
set_profiling_enabled(True)

# 2a. Decorator form — wraps every call
@profile(ProfileConfig(top_n=10))
async def slow_query() -> list[Row]:
    return await db.execute("SELECT ...")

# 2b. Context manager form — gives access to the report object
async with profiled("batch_export") as session:
    rows = await db.fetch_all()
    await write_csv(rows)
print(session.report.format())   # human-readable table to stderr/logs

# 3. FastAPI: enable via env var or create_varco_app flag
#    VARCO_PROFILER_ENABLED=true VARCO_PROFILER_ATTACH_HEADERS=true
app = create_varco_app(container, enable_profiling=True)
# → X-Profile-Wall-Ms + X-Profile-Mem-Kb headers on each response
```

**Swap in a future backend (e.g. memray):**

```python
from varco_core.profiling import MemoryProfilerBackend, MemoryProfileResult, register_memory_backend

class MemrayBackend:
    name = "memray"
    def start(self) -> None: ...
    def collect(self, top_n: int) -> MemoryProfileResult: ...

register_memory_backend("memray", MemrayBackend)
cfg = ProfileConfig(memory_backend="memray")
```

**Caveats**:
- `cProfile` and `tracemalloc` are process-global — one session at a time.  The middleware
  serialises via an `asyncio.Lock`; concurrent requests pass through unprofiled.
- `cProfile` across `await` captures all coroutines on the loop thread.  Best for
  CPU-bound or isolated coroutines; use a sampling backend for concurrent async code.
- Always disable after diagnosis: `set_profiling_enabled(False)` or unset the env var.

#### Scenario: Integrate a new external API (with resilience)

```python
from varco_core.resilience import (
    retry, timeout, circuit_breaker, rate_limit, bulkhead,
    RetryPolicy, CircuitBreakerConfig, RateLimitConfig,
    BulkheadConfig, InMemoryRateLimiter,
)

# Shared instances — one per external dependency (NOT per-call)
_payment_limiter = InMemoryRateLimiter(RateLimitConfig(rate=100, period=1.0))
_payment_bulkhead = Bulkhead(BulkheadConfig(max_concurrent=10, max_wait=0.5))

class PaymentService:
    def __init__(self, http_client: httpx.AsyncClient):
        self._client = http_client
        # Shared breaker per external service (NOT per-call)
        self._breaker = CircuitBreaker(
            CircuitBreakerConfig(failure_threshold=5, recovery_timeout=60.0)
        )

    @timeout(10.0)  # Fail fast if API hangs
    @retry(RetryPolicy(max_attempts=3, base_delay=0.5, max_delay=5.0))
    @circuit_breaker(config=...)  # Or use self._breaker.protect(fn)
    async def charge_card(self, amount: float, card_token: str) -> TransactionId:
        response = await self._client.post(
            "https://payment-api.example.com/charge",
            json={"amount": amount, "token": card_token},
        )
        if response.status_code >= 500:
            raise ExternalServiceError("Payment API down")
        return TransactionId(response.json()["id"])

# Decorator order matters (bottom-to-top execution):
# 1. circuit_breaker checks state
# 2. retry wraps and retries on failure
# 3. timeout cancels if > 10s
```

**Rate limiting**: Use `@rate_limit` to cap calls per second.  `InMemoryRateLimiter` is per-process; use `varco_redis.RedisRateLimiter` in multi-pod deployments.

**Bulkhead**: Use `Bulkhead` to cap concurrent in-flight calls to one dependency.  Must be a **shared** instance (same rule as `CircuitBreaker`):

**Hedged requests**: Use `@hedge` only for idempotent reads to cut tail latency — never for writes.

**Caveat**: `CircuitBreaker` and `Bulkhead` must both be **shared** per external service. A per-call instance never accumulates failures / concurrency:

```python
# ❌ WRONG: New breaker each call
def charge(self):
    breaker = CircuitBreaker(config)  # Fresh instance!
    return breaker.protect(self._call_payment_api)()

# ✅ CORRECT: Shared breaker
self._breaker = CircuitBreaker(config)  # Once at __init__

async def charge(self):
    return await self._breaker.protect(self._call_payment_api)()
```

#### Scenario: Consume a foreign-shaped JWT (Keycloak/Cognito)

An IdP-minted token rarely uses varco's canonical claim names. Set env vars — **no code
changes** — and every JWT entry point (`JwtParser.parse()`, `TrustedIssuerRegistry.verify()`,
`JwtBearerAuth`, `PassthroughAuth`) picks up the mapping for free:

```bash
# Keycloak: roles live under realm_access.roles, Spring-style "ROLE_" prefix
export VARCO_JWT_TRANSFORM_ROLES_FIELD="realm_access.roles"
export VARCO_JWT_TRANSFORM_ROLES_STRIP_PREFIX="ROLE_"
export VARCO_JWT_TRANSFORM_TOKEN_TYPE_FIELD="typ"

# Cognito: groups + token_use instead of roles/token_type
export VARCO_JWT_TRANSFORM_ROLES_FIELD="cognito:groups"
export VARCO_JWT_TRANSFORM_TOKEN_TYPE_FIELD="token_use"
```

```python
from varco_core.jwt import JwtParser

token = JwtParser.parse(raw_token, secret)   # unchanged call site
token.auth_ctx.roles                         # populated from the foreign claim
token.extra_claims["realm_access"]            # original claim still visible (non-destructive)
```

For per-issuer overrides (mixed fleets, gateway forwarding tokens from several IdPs), a code
escape hatch (`ClaimMapping` / a custom `ClaimTransformer`), and the full env-var table, see
`technical_docs/features/jwt-claim-transformer.md`.

#### Scenario: Gate a route on a named token profile (replacing `SYSTEM_ISSUER`)

Instead of a single `JwtUtil.SYSTEM_ISSUER` value, declare one or more named profiles and
gate routes on the resolved profile:

```bash
export VARCO_JWT_PROFILE__INTERNAL__ISS="mesh-signer"
export VARCO_JWT_PROFILE__INTERNAL__TOKEN_TYPE="system"
export VARCO_JWT_PROFILE__INTERNAL__ROLES="internal"
```

```python
from varco_fastapi.router.presets import GenericRouter
from varco_fastapi.router.endpoint import route
from varco_fastapi.auth import JwtBearerAuth
from varco_fastapi.auth.guard import require_token_profile

class MeshRouter(GenericRouter):
    _prefix = "/mesh"
    _auth = JwtBearerAuth(registry)

    @route("GET", "/internal-only", requires=require_token_profile("internal"))
    async def internal_only(self, ctx) -> dict:
        return {"ok": True}
```

A bare `sub`+`iss` service-mesh token with no roles/scopes/grants still gets a fully
authorized `AuthContext` when it matches a profile declaring `implied_roles`/`implied_scopes`
(⚠️ intentional materialisation — see `technical_docs/features/token-profiles.md`).
`JwtUtil.SYSTEM_ISSUER`/`is_system()` keep working unchanged for callers not ready to migrate.

---

## Coding Standards

All code in this repo follows the **coding-practice** skill. Key non-obvious rules specific to this codebase:

- `from __future__ import annotations` at the top of every file.
- `asyncio.Lock` is always created **lazily** (never at module level or `__init__`) — locks must be created inside a running event loop.
- Frozen `@dataclass(frozen=True)` for all value objects and config. Mutable dataclasses are a red flag.
- `TYPE_CHECKING` guards for cross-package type hints that would create circular imports at runtime (e.g. `consumer.py` importing from `dlq.py`).
- Every design decision gets a `DESIGN:` block with `✅` benefits and `❌` drawbacks.
- Docstrings include `Args:`, `Returns:`, `Raises:`, `Edge cases:`, `Thread safety:` / `Async safety:` where relevant.

---

## Test Conventions

- All tests are `async def` — no `@pytest.mark.asyncio` needed (auto mode).
- Integration tests require a real broker via Docker and are tagged `@pytest.mark.integration`. They are skipped by default; run with `-m integration`.
- `InMemoryEventBus` is the standard bus for unit tests. Use `bus.drain()` after publishes when `DispatchMode.BACKGROUND` is active.
- `InMemoryDeadLetterQueue` is the standard DLQ for unit tests.
- If a timing-sensitive test becomes flaky, increase its sleep margin rather than marking it xfail.

---

## Common Pitfalls & How to Avoid Them

| Pitfall | Symptom | Root Cause | Fix |
|---------|---------|-----------|-----|
| **Direct bus access in service** | Service holds `AbstractEventBus` and calls `bus.publish()` | Violates layer rule; bus is infra, not for app logic | Always inject `AbstractEventProducer`; it abstracts the bus |
| **Events published after commit** | Events silently lost when broker is unavailable | Post-commit publish has no rollback path | Use `OutboxRepository` + `OutboxRelay` within same transaction |
| **Subscription in `__init__`** | Service won't instantiate if broker is down; hard to test | Coupling service creation to bus state | Defer to `@PostConstruct` + `register_to()` |
| **Per-call CircuitBreaker** | Circuit never opens, all requests fail after threshold | Instance never accumulates enough failures | Use shared `CircuitBreaker` per external dependency |
| **Mixin hook doesn't chain** | Later mixins in MRO never run | Hook returns value without calling `super()` | Always `return await super()._hook_name(...)` |
| **Instantiate InvalidationStrategy outside lifecycle** | Consumer crashes on `start()` or subscriptions leak | Strategy may spawn background tasks or hold subscriptions | Let `CacheBackend` create it; call `backend.start()` / `stop()` |
| **Cache key collision** | Wrong data returned to different users | Key function doesn't include scope (tenant_id, user_id) | Use namespaced keys: `f"user:{tenant_id}:{user_id}"` |
| **Forgot `@PostConstruct` on consumer** | Events never delivered | `register_to()` never called; subscription created at wrong time | Add `@PostConstruct` method that calls `self.register_to(self._bus)` |
| **Async lock at module level** | `RuntimeError: no running event loop` | Locks created before event loop starts | Create locks lazily inside methods: `self._lock = self._lock or asyncio.Lock()` |
| **Missing `await` on async call** | Coroutine leaked, cleanup never runs | Easy to miss on unfamiliar APIs | IDE linting catches this; always `await` calls to `async def` |
| **Per-call Bulkhead** | Concurrency never limited, dependency can be overwhelmed | Instance has its own fresh semaphore each call | Shared `Bulkhead` per external dependency, same as `CircuitBreaker` |
| **InMemoryRateLimiter in multi-pod** | Each pod has its own counter; total rate = N × configured rate | Per-process storage, not shared | Use `varco_redis.RedisRateLimiter` for distributed (multi-pod) enforcement |
| **Hedging non-idempotent writes** | Duplicate side-effects (email sent twice, double charge) | Both hedged copies execute concurrently | Only apply `@hedge` to idempotent reads/upserts; never to INSERT or transactional writes |
| **`requires=` without `_auth`** | `RuntimeError` at `build_router()` startup | Guard can never be satisfied with no `AuthContext` | Set `_auth` on the router, or use `allow_anonymous()` if the route is public |
| **`ctx` declared but no `_auth`** | Handler gets 500 (missing argument) | Without auth middleware, no `AuthContext` is injected | Set `_auth` on the router or remove `ctx` from the handler signature |
| **Per-call `CasbinPolicyEngine`** | Policy reloaded every request; slow, in-memory edits lost | A new enforcer is built per call | Resolve it as a DI singleton (`bootstrap`); share one instance |
| **Policy authorizer silently active** | App's own authorizer is shadowed unexpectedly | A scanned `@Configuration` auto-activates on `scan` | The authorizer is opt-in via `enable_policy_authorizer(container)`; don't make it a scanned config |
| **`@Singleton` on pydantic `BaseSettings`** | `LookupError: Cannot resolve 'values'` at resolution | providify injects pydantic's `**values` ctor param | Register settings via a `@Provider` (see `varco_casbin.di`), not `@Singleton` |
| **`memory` adapter in production** | Policies vanish on restart | The in-memory adapter has no durable store | Use `adapter="sqlalchemy"` (`varco-casbin[sqlalchemy]`) for persisted CRUD |
| **Sync Casbin adapter with AsyncEnforcer** | `RuntimeError: Invalid parameters for enforcer` | `AsyncEnforcer` requires an `AsyncAdapter` | Use `casbin.persist.adapters.asyncio.*` (the factory in `varco_casbin.adapter` already does) |
| **Profiling left always-on** | 20–100% overhead in production | `cProfile`/`tracemalloc` are expensive deterministic tools | Default is off (`VARCO_PROFILING_ENABLED=false`); activate only to diagnose a hotspot |
| **Two profiling sessions concurrent** | Contaminated reports (each session records the other's frames) | `cProfile`/`tracemalloc` are process-global | The middleware serialises with a `Lock`; for manual use, never profile two operations simultaneously |
| **Naive `app.mount()` in a composite** | Mounted services answer requests with dead DB pools / no event bus | Starlette's `Router.lifespan` never descends into mounted sub-apps | Use `create_composite_app` — its `CompositeLifespan` drives each sub-app's own lifespan |
| **Two composite services share a bare env name** | Both read the same `DATABASE_URL`; second service silently uses the first's config | One process = one `os.environ`; env is read at build time | Namespace env vars per service, or build each with `build_service(prefix, factory, env={...})` |
| **`cProfile` across `await` on a busy loop** | Report includes frames from other coroutines | `cProfile` captures the whole event loop thread | Use a sampling backend (e.g. pyinstrument) for concurrent async code; cProfile is best for CPU-bound or isolated coroutines |
| **tracemalloc state not restored** | App's own tracemalloc usage broken after a profiling session | Session left tracemalloc on when it found it off (or vice versa) | `TracemallocMemoryBackend.collect()` always restores the prior tracing state; if writing a custom memory backend, do the same |
| **Custom service method unknown on `self._service`** | Type checker reports `.compile`/`.custom_method()` etc. as unknown attributes | Router declared without the 6th `S` type arg — `self._service` is only typed as the erased `AsyncService[Any, ...]` base | Subscript `CRUDRouter[..., ConcreteService]` (or `VarcoCRUDRouter[..., ConcreteService]`) and use `self.service`/`self._service`, both narrowed to `ConcreteService` |
| **Roles empty although the JWT has them** | `AuthContext.roles` is empty for a valid, correctly-signed token | The claim is named `sofy-roles`/`realm_access.roles`, not `roles` — the default mapping never looked there | Set `VARCO_JWT_TRANSFORM_ROLES_FIELD` (see `technical_docs/features/jwt-claim-transformer.md`) |
| **`is_system()` false for my internal token** | A token minted by your own internal issuer is not recognised as "system" | Only one static `SYSTEM_ISSUER` was configured, and this token's issuer doesn't match it | Define `VARCO_JWT_PROFILE__SYSTEM__ISS` (or any named `TokenProfile`) instead — see `technical_docs/features/token-profiles.md` |
| **Token from another service accepted** | A JWT minted for a different service (different `aud`) verifies successfully here | `aud` was never enforced — `JwtBearerAuth`/`TrustedIssuerRegistry.verify()` default to `audience=None` | Set `VARCO_JWT_AUDIENCE` (or `JwtBearerAuth(audience=...)`) to opt in to audience enforcement |
| **Intermittent 401 across hosts** | Same token, same secret, fails verification only on some hosts/some requests | Clock skew between hosts — `exp`/`nbf` checked with zero tolerance by default | Set `VARCO_JWT_LEEWAY_SECONDS=30` (or `leeway=` on `parse()`/`verify()`) |
| Secret in a span attribute | A password/token value visible in the trace UI | Param capture is on and the param name isn't in the redact list | Add it to `VARCO_OTEL_CAPTURE_PARAMS_EXCLUDE`/`redact_patterns`, or `capture_params=False` on that `@span` |
| Metric series explosion after adding a global attribute | Prometheus TSDB churn / OOM after a deploy | `k8s.pod.name` was added as a *per-measurement* attribute, so every pod creates its own series for every metric | Put static process identity in `OtelConfig.extra_resource_attrs` (Resource), not in the global attribute registry |
| Global attribute never appears | Registry set, spans/metrics unlabelled | `configure_global_attributes(apply_to_spans/metrics=False)` or the corresponding env var is `false` | Check `VARCO_OTEL_GLOBAL_ATTRS_SPANS` / `_METRICS` |
| Provider called on every measurement | Latency regression on the hot path | Provider registered with `cache_ttl=0.0` | Use the default `cache_ttl=None` (evaluate once) for immutable values |
| `isinstance(create_counter(...), Counter)` is False | Type check fails after upgrade | The instrument is wrapped in `GlobalAttrInstrument` | Use duck typing, or `.unwrap()`, or `apply_to_metrics=False` |
| Audit entries never written | Service emits, DB table stays empty | `AuditConsumer.register_to(bus)` never called | Call it from a `@PostConstruct` method |
| `relation "varco_audit_log" does not exist` | Consumer raises on first audit event | `audit_metadata` not in the Alembic `target_metadata` | Add `from varco_sa.audit import audit_metadata` to `env.py` |
| `CollectionWasNotInitialized` on audit save | Beanie raises when the consumer persists | `AuditDocument` missing from `init_beanie(document_models=...)` | Register it at startup |
| Audit record lost on broker outage | Domain write committed, no audit row | Audit is emitted post-commit as a plain event | Emit the `AuditEvent` through the transactional outbox |

---

## Decision Tree: What to Implement Where?

```
Am I adding a new capability?
├─ Event system feature (new event type, new consumer pattern)?
│  └─ → varco_core.event (protocol) + varco_kafka/redis (backend)
│
├─ Cache feature (new invalidation strategy, new backend)?
│  └─ → varco_core.cache (ABC) + varco_redis/sa (impl)
│
├─ Query filtering (new comparison operator, new visitor)?
│  └─ → varco_core.query (parser + visitor) + varco_core.query.applicator.sqlalchemy
│
├─ Resilience pattern (new retry/timeout/breaker variant)?
│  └─ → varco_core.resilience (decorator + config)
│
├─ Profiling / performance diagnostic?
│  ├─ New CPU backend (pyinstrument, py-spy)?
│  │  └─ → implement CpuProfilerBackend + register_cpu_backend()
│  ├─ New memory backend (memray)?
│  │  └─ → implement MemoryProfilerBackend + register_memory_backend()
│  └─ New profiling primitive (service mixin, consumer wrapper)?
│     └─ → varco_core.profiling (use ProfileSession as the engine)
│
├─ Authentication/JWT feature?
│  └─ → varco_core.authority (protocol) + varco_core.authority.sources (key sources)
│
├─ JWT claim shape (foreign IdP roles/scopes/tenant naming)?
│  └─ → varco_core.jwt.transform (ClaimMapping / ClaimTransformer) — env-driven or code-configured
│
├─ Named internal/system token recognition (replacing SYSTEM_ISSUER)?
│  └─ → varco_core.jwt.profile (TokenProfile / TokenProfileRegistry) + varco_fastapi's require_token_profile
│
├─ Service layer feature (mixin, hook, outbox)?
│  └─ → varco_core.service (ABC + mixin) + varco_sa/beanie (repository impl)
│
└─ ORM/database feature?
   └─ → varco_sa (SQLAlchemy) and/or varco_beanie (MongoDB)
        ↳ Models auto-generated from varco_core.model.DomainModel
        ↳ Implement backend-specific Repository, OutboxRepository

---

Should I create a new backend implementation?
├─ Only if you're supporting a genuinely different transport/storage:
│  ├─ New event bus (e.g., RabbitMQ, AWS SNS)? → new package varco_[backend]
│  ├─ New cache backend (e.g., Memcached)? → add to varco_redis or new package
│  ├─ New ORM (e.g., Tortoise)? → new package varco_[backend]
│  └─ New DLQ (e.g., S3-based dead letters)? → new package or existing
│
└─ Do NOT create a new backend just for:
   ├─ A different config (use the existing backend with new settings)
   ├─ A new feature (extend the existing backend's interface)
   └─ Convenience (keep it simple; fewer backends = fewer bugs)

---

Should I add it to varco_core or a backend?
├─ varco_core if:
│  ├─ ✅ It's a protocol/ABC that backends implement
│  ├─ ✅ It's used by application code (services, handlers)
│  ├─ ✅ It's transport/storage agnostic (event types, domain model)
│  └─ ✅ All backends need it (caching, query, resilience)
│
└─ Backend (varco_kafka/redis/sa/beanie) if:
   ├─ ✅ It's a concrete implementation of a varco_core interface
   ├─ ✅ It depends on third-party libraries specific to that backend (aiokafka, redis, sqlalchemy)
   └─ ✅ It only makes sense for one transport/storage system
```

---

## Pre-Implementation Checklist

Before writing code, ask yourself:

- [ ] **Is this already implemented elsewhere?** → Search ARCHITECTURE.md type hierarchies, check `varco_*/` for similar patterns
- [ ] **Does this belong in varco_core or a backend?** → Use decision tree above
- [ ] **Am I respecting layer boundaries?** → Services inject protocols, not concrete implementations; only DI knows concrete types
- [ ] **Will this compose via MRO if it's a mixin?** → Does it call `super()` on every hook?
- [ ] **Is my event consumer testable?** → Decorated with `@listen`, wired in `@PostConstruct`, no bus reference in `__init__`?
- [ ] **If I'm publishing events, am I using the outbox pattern?** → Events saved in same DB transaction, relayed asynchronously?
- [ ] **If I'm caching, is my key namespaced?** → Includes tenant_id, user_id, or other scope identifier?
- [ ] **If I'm using external APIs, do I have resilience?** → Timeout + retry + circuit breaker + bulkhead (shared instances), with optional rate limiting?
- [ ] **If I'm rate-limiting, is my limiter appropriate for the deployment?** → `InMemoryRateLimiter` for single-process; `RedisRateLimiter` for multi-pod.
- [ ] **If I'm using `@hedge`, is the operation truly idempotent?** → Hedging non-idempotent writes causes duplicate side-effects.
- [ ] **Are my dataclasses frozen?** → `@dataclass(frozen=True)` for value objects, configs, AST nodes?
- [ ] **Am I creating locks lazily?** → Never at module level or `__init__`, always inside methods?
- [ ] **Did I add docstrings with Args/Returns/Raises/Edge cases?** → Especially for new abstractions and non-obvious code
- [ ] **Did I test with the right bus?** → `InMemoryEventBus` for unit tests, real broker (Docker) for integration tests?
