# Changelog

All notable changes to the varco framework are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Varco packages use [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Fixed

- **`varco-core` — event serializers are now genuinely injectable.** The
  `EventSerializer` type alias was a quoted forward reference
  (`EventSerializer: TypeAlias = "Serializer[Event]"`) with `Event` imported only
  under `TYPE_CHECKING`, so at runtime the module-level name was bound to the
  **string** `"Serializer[Event]"` rather than to a type. Every bus backend
  annotated its constructor with
  `Annotated[EventSerializer | None, InjectMeta(optional=True)]`, which therefore
  evaluated `str | None` and raised `TypeError: unsupported operand type(s) for |`.

  **Impact:** under providify < 1.1.0 the failure was swallowed into an empty hints
  dict, silently dropping DI for the `serializer` parameter on `KafkaEventBus`,
  `RedisEventBus`, and `NatsEventBus` — each fell back to `JsonEventSerializer()`,
  so a user-supplied serializer was **never** injected on any bus. Under providify
  >= 1.1.0, which correctly refuses to report a clean bill of health it cannot
  prove, the same defect aborts `container.validate_bindings()` and any app that
  scanned `varco_kafka`, `varco_redis`, or `varco_nats` failed at startup with
  `AnnotationResolutionError`.

  ⚠️ `varco_redis`'s test suite passed green throughout — no redis test exercised a
  path that resolves binding annotations. New `validate_bindings()` regression tests
  in all three backend packages close that coverage gap.

- **`varco-fastapi` — `bind_clients()` now works.** It previously always raised and
  registered nothing: the internal `_factory` closure was never
  `@Provider`-decorated, so `container.bind()` raised (`issubclass()` on a
  function), the `provide()` fallback raised `ProviderBindingNotDecoratedError`, and
  the last-resort `bind(client_cls, client_cls)` raised
  `ClassBindingNotDecoratedError`. `_factory` is now decorated with
  `@Provider(singleton=True)` after its return annotation is patched, and the three
  nested `except Exception` fallbacks — which could never succeed and only masked
  the real cause — have been removed in favour of a single un-guarded
  `container.provide()` call.

### Changed

- **`Serializer[Event]` is the event-serializer injection interface.** Bus
  constructors now annotate `Annotated[Serializer[Event] | None,
  InjectMeta(optional=True)]` instead of a type alias, and `JsonEventSerializer`
  explicitly subclasses `Serializer[Event]` and carries
  `@Singleton(priority=-sys.maxsize - 1)` — registered as the lowest-priority
  default, so it works out of the box but loses to any application-supplied
  serializer regardless of registration order:

  ```python
  @Provider(singleton=True)
  def my_serializer() -> Serializer[Event]:
      return MyCompactSerializer()

  container.provide(my_serializer)   # wins over JsonEventSerializer
  ```

  **Breaking:** the `EventSerializer` alias is removed. Replace
  `EventSerializer` with `Serializer[Event]` (from `varco_core.serialization`) in
  any annotation. `JsonSerializer`, `NoOpSerializer`, and `TypedJsonSerializer` now
  subclass `Serializer[Any]` explicitly for the same DI reason.

- **Workspace pins the vendored `providify` 1.1.0 wheel** (was 0.1.6). The previous
  pin did not satisfy the `providify>=1.1.0` constraint declared by every package,
  meaning varco was developed and tested against a version no PyPI consumer would
  resolve. Comments in `pyproject.toml` now note the sync requirement.

---

## [0.1.0] — 2026-04-07

First public alpha release of the varco framework. All eight packages are published
to PyPI simultaneously. This release establishes the public API surface — expect
breaking changes between alpha versions while the API stabilises.

### New packages

| Package | Version | Description |
|---------|---------|-------------|
| `varco-core` | 0.1.0 | Domain model, service layer, event system, resilience, query AST, JWT authority |
| `varco-kafka` | 0.1.0 | Apache Kafka event bus backend (aiokafka) |
| `varco-redis` | 0.1.0 | Redis Pub/Sub event bus, cache, DLQ, rate limiter (redis.asyncio) |
| `varco-sa` | 0.1.0 | SQLAlchemy async ORM backend with auto-generated models |
| `varco-beanie` | 0.1.0 | Beanie/MongoDB async ODM backend |
| `varco-ws` | 0.1.0 | WebSocket and SSE event bus implementations |
| `varco-fastapi` | 0.1.0 | FastAPI adapter — routing mixins, auth middleware, typed HTTP client, DI wiring |
| `varco-memcached` | 0.1.0 | Memcached cache backend |

---

### varco-core

#### Added
- **`AsyncService`** — generic service base class over five type parameters
  (`DomainModel`, primary key, Create/Read/Update DTOs). Implements full CRUD
  with `_get_repo()` as the single required hook.
- **`AbstractEventBus` / `AbstractEventProducer` / `EventConsumer`** — layered
  event system. `@listen` stores metadata declaratively; `register_to()` wires
  subscriptions imperatively at startup.
- **`InMemoryEventBus`** — zero-dependency event bus for unit tests.
- **`AbstractDeadLetterQueue` / `InMemoryDeadLetterQueue`** — DLQ interface and
  in-memory implementation for test-time failure inspection.
- **`@retry` / `@timeout` / `@circuit_breaker`** — composable resilience
  decorators. `CircuitBreaker` and `Bulkhead` are shared-instance patterns.
- **`@rate_limit` / `InMemoryRateLimiter`** — per-process rate limiting. Use
  `varco-redis` `RedisRateLimiter` for multi-pod deployments.
- **`@hedge`** — hedged request decorator for tail-latency reduction on
  idempotent reads.
- **`QueryParser` / `ASTVisitor` / `QueryOptimizer` / `TypeCoercionVisitor`** —
  typed query AST pipeline. Filter strings (`age__gte=18`) are parsed into
  `FilterNode` trees, optimised, and applied to backends via visitor pattern.
- **`JwtAuthority` / `MultiKeyAuthority` / `TrustedIssuerRegistry`** — JWT
  signing and verification with zero-downtime key rotation.
- **Key sources** (`PemFile`, `PemFolder`, `JwksUrl`, `OidcDiscovery`) — pluggable
  key loading for `TrustedIssuerRegistry`.
- **`OutboxRepository` / `OutboxRelay`** — transactional outbox pattern.
  Events are saved in the same DB transaction as domain entities; `OutboxRelay`
  publishes them asynchronously.
- **`AsyncCache` / `CacheBackend` / `LayeredCache`** — cache protocol hierarchy.
  `InMemoryCache` and `NoOpCache` ship in core.
- **`InvalidationStrategy`** — pluggable cache invalidation.
  `TTLStrategy`, `TaggedStrategy`, `EventDrivenStrategy`, `CompositeStrategy`
  all ship in core.
- **`@cached` / `CacheServiceMixin`** — decorator and mixin for caching service
  methods.
- **`TenantAwareService` / `SoftDeleteService` / `ValidatorServiceMixin` /
  `AsyncValidatorServiceMixin`** — composable service mixins via MRO.
- **`@span` / `@counter` / `@histogram`** — OpenTelemetry tracing and metrics
  decorators. `OtelConfiguration` wires `TracerProvider` / `MeterProvider` via DI.
- **`VarcoSettings`** — base pydantic-settings class for all backend
  configuration objects.

---

### varco-kafka

#### Added
- **`KafkaEventBus`** — `AbstractEventBus` implementation backed by aiokafka.
  Supports topic-per-channel routing, configurable consumer groups, and
  backpressure via `DispatchMode`.
- **`KafkaDLQ`** — Dead letter queue that routes failed events to a dedicated
  Kafka topic after exhausting retry attempts.
- **`KafkaChannelManager`** — manages topic creation and partition assignment.
- **`KafkaEventBusConfiguration` / `KafkaChannelManagerConfiguration`** —
  `@Configuration` classes for DI wiring.
- **`KafkaHealthCheck`** — liveness / readiness probe for Kafka connectivity.

---

### varco-redis

#### Added
- **`RedisEventBus`** — `AbstractEventBus` implementation backed by Redis
  Pub/Sub.
- **`RedisStreamEventBus`** — alternative implementation backed by Redis Streams
  for durable, consumer-group-aware delivery.
- **`RedisDLQ`** — Dead letter queue using a Redis Hash + Sorted Set backend.
  `push()` never raises — failures are logged and swallowed per the DLQ contract.
- **`RedisCache`** — `CacheBackend` implementation backed by Redis.
- **`RedisRateLimiter`** — distributed rate limiter using Redis atomic counters.
  Use this instead of `InMemoryRateLimiter` in multi-pod deployments.
- **`RedisEncryptionKeyStore`** — encrypted key storage backed by Redis.
- **`RedisLock`** — distributed lock backed by Redis `SET NX EX`.
- **`RedisEventBusConfiguration` / `RedisCacheConfiguration` /
  `RedisStreamConfiguration` / `RedisDLQConfiguration`** — `@Configuration`
  classes for DI wiring.
- **`RedisHealthCheck`** — liveness / readiness probe for Redis connectivity.

---

### varco-sa

#### Added
- **`SAModelFactory`** — generates SQLAlchemy ORM models at import time from
  `DomainModel` subclasses. Models are never declared manually.
- **`SARepository`** — `AsyncRepository` implementation for SQLAlchemy.
- **`SAUnitOfWork` / `SAUoWProvider`** — unit-of-work pattern over SQLAlchemy
  async sessions.
- **`SAOutboxRepository`** — `OutboxRepository` implementation for SQLAlchemy.
- **`SAEncryptionKeyStore`** — encrypted key storage backed by SQLAlchemy.
- **`SQLAlchemyFilterVisitor` / `SQLAlchemyQueryApplicator`** — applies the
  `varco-core` query AST to SQLAlchemy `Select` statements.
- **`SAModule`** / **`bind_repositories()`** — DI wiring helpers.
- **`SAHealthCheck`** — liveness / readiness probe for database connectivity.

---

### varco-beanie

#### Added
- **`BeanieModelFactory`** — generates Beanie `Document` models from
  `DomainModel` subclasses.
- **`BeanieRepository`** — `AsyncRepository` implementation for Beanie/MongoDB.
- **`BeanieUnitOfWork` / `BeanieUoWProvider`** — unit-of-work pattern over
  Beanie sessions.
- **`BeanieOutboxRepository`** — `OutboxRepository` implementation for Beanie.
- **`BeanieModule`** / **`bind_repositories()`** — DI wiring helpers.
- **`BeanieHealthCheck`** — liveness / readiness probe for MongoDB connectivity.

---

### varco-ws

#### Added
- **`WebSocketEventBus` / `WebSocketConnection`** — `AbstractEventBus`
  implementation that delivers events over WebSocket connections.
- **`SSEEventBus` / `SSEConnection`** — `AbstractEventBus` implementation that
  delivers events as Server-Sent Events streams.

---

### varco-fastapi

#### Added
- **`VarcoRouter`** — base `APIRouter` subclass with built-in DI resolution.
- **CRUD mixins** — `CreateMixin`, `ReadMixin`, `UpdateMixin`, `DeleteMixin`,
  `ListMixin`, `StreamMixin` — compose standard HTTP endpoints without boilerplate.
- **`AuthMiddleware`** — validates JWT bearer tokens on every request using
  `TrustedIssuerRegistry`.
- **`CORSMiddleware`** — env-var-driven CORS configuration.
- **`AsyncVarcoClient` / `SyncVarcoClient`** — typed HTTP clients with
  automatic JWT injection, retry, and circuit breaker.
- **`SkillAdapter`** — mounts Google A2A (Agent-to-Agent) skill endpoints from
  a `SkillDefinition`. Install the `[a2a]` extra for the A2A SDK types.
- **`MCPAdapter`** — mounts Model Context Protocol (MCP) tool endpoints.
  Install the `[mcp]` extra (`mcp>=1.0`) for full support.
- **`VarcoFastAPIModule`** / **`bind_clients()`** — DI wiring for FastAPI.
- **Background job runner** — `AsyncJobRunner` backed by `asyncio.TaskGroup`
  for lifecycle-managed background tasks.

---

### varco-memcached

#### Added
- **`MemcachedCache`** — `CacheBackend` implementation backed by aiomcache.
- **`MemcachedCacheConfiguration`** — `@Configuration` class for DI wiring.

---

## [Unreleased]

### varco-core

#### Added
- **JWT claim transformation** (`varco_core.jwt.transform`) — consume foreign-shaped
  JWTs (Keycloak, Cognito, Auth0, a bespoke claim, …) without any application code
  change. `ClaimMapping` / `ClaimRule` / `ClaimPath` (code-configured) and
  `JwtTransformSettings` / `JwtTransformConfig` (env-driven, `VARCO_JWT_TRANSFORM_*`
  + per-issuer `VARCO_JWT_TRANSFORM__<LABEL>__*`) both resolve through the
  `ClaimTransformer` Protocol; `JwtParser.parse()`, `TrustedIssuerRegistry.verify()`,
  and `varco-fastapi`'s `JwtBearerAuth`/`PassthroughAuth` all pick it up for free
  through one shared funnel. Zero-config behaviour is unchanged (`IDENTITY`
  transformer, no copy). See `technical_docs/features/jwt-claim-transformer.md`.
- **Named token profiles** (`varco_core.jwt.profile`) — `TokenProfile` /
  `TokenProfileRegistry` recognise multiple kinds of special/internal tokens
  (`system`, `internal`, `partner`, `service-mesh`, …) by issuer/token_type/audience/
  required claims, env-configured via `VARCO_JWT_PROFILE__<NAME>__*`, and can grant
  `implied_roles`/`implied_scopes`. `JwtUtil.matches_profile()` /
  `.profile_name()` / `.assert_profile()`; `JwtBuilder.as_profile()`. See
  `technical_docs/features/token-profiles.md`.
- **JWT verification hardening** — `VARCO_JWT_LEEWAY_SECONDS` (clock-skew leeway for
  `exp`/`nbf`, default `0.0`) and `VARCO_JWT_AUDIENCE` (expected `aud`, default
  `None` = not enforced) via `varco_core.jwt.config.JwtVerificationSettings`,
  threaded through `JwtParser.parse()`, `TrustedIssuerRegistry.verify()`, and
  `JwtBearerAuth`.
- **JWKS caching knobs** — `TrustedIssuerRegistry(min_refresh_interval=...,
  ttl_seconds=...)` (env: `VARCO_JWKS_MIN_REFRESH_SECONDS` default `10.0`,
  `VARCO_JWKS_TTL_SECONDS` default `0.0` = disabled) allow a proactive, age-based
  keyset reload in addition to the existing reactive kid-miss refresh. A background
  refresher task remains out of scope (needs its own lifespan wiring) — deferred.
- **`ValueShape.GRANTS`** validation gives an actionable `ClaimTransformError` naming
  the offending list index and missing key for a malformed `grants` claim, replacing
  a previously bare `KeyError`.

#### Changed
- ⚠️ **Widened `AuthContext` materialisation on JWT parse.** A token carrying only
  `tenant_id`/`actor` claims (no `roles`/`scopes`/`grants`), or matching a
  `TokenProfile` with `implied_roles`/`implied_scopes`, now materialises a non-`None`
  `auth_ctx` where it previously stayed `None`. Canonical tokens with none of
  `roles`/`scopes`/`grants`/`tenant_id`/`actor` and no matching profile still yield
  `auth_ctx is None`. Code doing `if token.auth_ctx is None: treat as machine token`
  should account for this.
- `JsonWebToken.to_claims()` now emits `tenant_id`/`act` claims when present in
  `auth_ctx.metadata["tenant_id"]`/`["actor"]`, so varco-minted tokens round-trip
  tenant/actor through re-parsing. `_RESERVED_CLAIM_KEYS` was **not** extended to
  include `tenant_id`/`act`/`user_id`/`actor` (a deviation from the original plan —
  the executable test suite requires `JwtBuilder().claim("tenant_id", ...)` /
  `.claim("act", ...)` to keep succeeding); `JwtBuilder.claim()` behaviour for these
  keys is unchanged.
- `JwtUtil.is_system()` now prefers a registered `"system"` `TokenProfile` when one
  exists, falling back to the historical `SYSTEM_ISSUER` `ClassVar` comparison
  otherwise. `SYSTEM_ISSUER` is documentation-deprecated in favour of
  `VARCO_JWT_PROFILE__SYSTEM__ISS` — it keeps working with no removal scheduled and
  no runtime `DeprecationWarning`.

#### Fixed
- Corrected every documented DI override example (`varco_core.observability.di`
  docstrings, README) that showed `container.install(OtelConfiguration,
  config=...)` or `container.provide(lambda: OtelConfig(...))` — neither call
  shape has ever worked: `install()` takes no `config=` keyword and `provide()`
  rejects undecorated callables (`ProviderBindingNotDecoratedError`). The
  correct pattern is a module-level `@Provider`-decorated factory function
  registered with `container.provide(fn)` **before** `install()`/`scan()`
  (equal-priority bindings resolve first-registered, not last). See
  `ARCHITECTURE.md`'s DI Wiring section for the full corrected pattern and the
  quoted-return-annotation landmine below.

### varco-kafka

#### Fixed
- 🐛 **`container.get(KafkaChannelManager)` / `KafkaChannelManagerSettings`
  was hard-broken** (`LookupError: Cannot resolve 'values: typing.Any'`) —
  `KafkaChannelManagerSettings` carried `@Singleton` directly on a pydantic
  `BaseSettings` subclass, and providify cannot constructor-inject a
  `**values: Any` signature. Replaced with a lowest-priority `@Provider`
  factory (`kafka_channel_manager_settings` in `varco_kafka.channel`), the
  same pattern already used for `varco_casbin` settings. Guarded by
  `varco_kafka/tests/test_kafka_di.py`.

### varco-nats

#### Fixed
- 🐛 **`container.get(NatsStreamManager)` / `NatsChannelManagerSettings` was
  hard-broken** — same root cause and fix as the `varco-kafka` entry above
  (`@Singleton` on a pydantic `BaseSettings` class replaced by a
  lowest-priority `nats_channel_manager_settings` `@Provider` factory in
  `varco_nats.channel`). Guarded by `varco_nats/tests/test_nats_di.py`.

### varco-fastapi

#### Changed
- ⚠️ **Error response bodies now include a `detail` field when present.**
  `add_exception_handlers()` and `ErrorMiddleware` both stopped silently dropping
  `ErrorMessage.detail` — a 403 from a denied `RouteGuard` (missing scope/role/token
  profile/grant) now surfaces its actionable message in the JSON body under
  `"detail"`, not just `"message"`. Clients parsing only `{"code", "message"}` are
  unaffected; clients that assert the *absence* of a `"detail"` key should update.
- **`PassthroughAuth` refactored** onto `JwtParser.parse_unverified()` instead of
  hand-rolled claim parsing — it now benefits from the claim-transformer pipeline
  (env-driven or explicit) like every other JWT entry point. A regression test pins
  the resulting `AuthContext` for a canonical token to the pre-refactor behaviour.

#### Added
- **`JwtBearerAuth(audience=..., leeway=...)`** — opt-in audience enforcement and
  configurable clock-skew leeway, both falling back to `VARCO_JWT_AUDIENCE` /
  `VARCO_JWT_LEEWAY_SECONDS` when omitted. Logs one warning at construction when
  audience is left unenforced.
- **`RouteGuard.token_profiles` / `require_token_profile(*names)`** — gate a
  `@route` on the JWT's resolved token profile (`ctx.metadata["token_profile"]`),
  checked between the role check and the grant check.
- **`create_varco_app(configure_jwt=True)`** — calls
  `configure_jwt_from_env()` once at startup so the process-global claim-transform
  and token-profile registries match what `VarcoFastAPIModule`'s DI providers hand
  out. Set `configure_jwt=False` to manage the registries yourself.

#### Fixed
- 🐛 **`container.get(TracerProvider)` raised `TypeError: tracer_provider()
  missing 1 required positional argument: 'config'`** when `VarcoFastAPIModule`
  and `varco_core.observability.di.OtelConfiguration` shared one container —
  `Inject[OtelConfig]` was silently not injected into `OtelConfiguration`'s
  provider method, even though the two modules looked unrelated. Root cause:
  `VarcoFastAPIModule.profiling_settings` declared a *quoted* return
  annotation (`-> "ProfilingSettings"`); under PEP 563 that annotation
  resolves to the literal string `"'ProfilingSettings'"`, and providify's
  `eval` fallback (`providify/binding.py`) registered the resulting **string**
  as a binding interface. That one malformed binding then made
  `DIContainer._build_localns()` raise, which `_collect_kwargs_sync()`
  silently swallowed (`except Exception: hints = {}`) — disabling constructor
  and provider injection for **every** binding in the container, not just the
  broken one. Fixed by dropping the quotes (`from __future__ import
  annotations` already made the annotation lazy) and keeping
  `ProfilingSettings` imported at module scope. The underlying defect is in
  `providify` (a sibling library) and is **not** fixed here — see
  `ARCHITECTURE.md`'s DI Wiring section for the landmine and its one-line
  diagnostic (`[b for b in container._bindings if isinstance(b.interface,
  str)]`). Guarded by `varco_fastapi/tests/test_di_binding_health.py` and
  `varco_core/tests/test_observability_di.py`.

---

<!-- Links -->
[0.1.0]: https://github.com/edoardoscarpaci/varco/releases/tag/v0.1.0
[Unreleased]: https://github.com/edoardoscarpaci/varco/compare/v0.1.0...HEAD
