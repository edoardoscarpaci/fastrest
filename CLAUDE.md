# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Quick reference**: See [ARCHITECTURE.md](ARCHITECTURE.md) for a complete technical map of all packages, modules, classes, type hierarchies, and design patterns. Use it to navigate the codebase efficiently without reading files one-by-one.

---

## Commands

All commands run from the **workspace root** (`/home/edoardo/projects/varco`) using a single shared virtual environment managed by `uv`.

```bash
# Install everything (all workspace members + dev deps)
uv sync

# Run all tests for one package (every workspace member has its own tests/ dir:
# varco_core, varco_kafka, varco_nats, varco_redis, varco_sa, varco_beanie,
# varco_memcached, varco_ws, varco_fastapi, varco_casbin)
uv run pytest varco_core/tests/
uv run pytest varco_kafka/tests/
uv run pytest varco_redis/tests/
uv run pytest varco_sa/tests/

# Run a single test file
uv run pytest varco_core/tests/test_event.py

# Run a single test by name
uv run pytest varco_core/tests/test_event.py::TestInMemoryEventBus::test_subscribe

# Run integration tests (require Docker — Kafka, NATS, Redis, Memcached, or MongoDB broker)
uv run pytest varco_kafka/tests/ -m integration
uv run pytest varco_redis/tests/ -m integration

# Import any workspace package directly (no install step needed)
uv run python -c "from varco_core.event import AbstractEventBus"
```

The `Makefile` (workspace root) wraps the above plus lint/type-check/build/docs targets across
every package in one call — `make lint` (`ruff check`), `make format` (`ruff format` + `ruff
check --fix`), `make type-check` (`mypy`), `make test` / `make test PKG=varco_redis`, `make
integration-test`, `make build`, `make publish`, `make docs` / `make docs-serve`. Run `make
help` for the full list. `pytest-asyncio` is installed with `asyncio_mode = "auto"` in every
package — all `async def test_*` functions run automatically without `@pytest.mark.asyncio`.

---

## Architecture

Varco is a **uv workspace monorepo** of ten packages (`pyproject.toml`'s `[tool.uv.workspace]`,
plus the `examples` workspace member). Each package is independently installable from PyPI.
`varco_core` has no sibling dependencies; every other package depends on it.

```
varco_core        — domain model, service layer, event system, resilience, DI contracts
varco_kafka       — Kafka event bus backend (aiokafka)
varco_nats        — NATS JetStream event bus backend (nats-py)
varco_redis       — Redis Pub/Sub event bus + cache backend (redis.asyncio)
varco_sa          — SQLAlchemy async ORM backend
varco_beanie      — Beanie/MongoDB async ODM backend
varco_memcached   — Memcached cache backend (aiomcache)
varco_ws          — WebSocket + Server-Sent Events (SSE) event bus backend
varco_fastapi     — FastAPI adapter — routing mixins, auth middleware, typed HTTP client, DI wiring, A2A/MCP surfaces
varco_casbin      — Casbin policy-engine authorization backend (ACL/RBAC/ABAC + REST admin)
```

### Dependency graph

```
varco_kafka     ──┐
varco_nats      ──┤
varco_redis     ──┤
varco_sa        ──┤─→ varco_core
varco_beanie    ──┤
varco_memcached ──┤
varco_ws        ──┤
varco_fastapi   ──┤   (+ optional varco_core for the REST admin router it hosts)
varco_casbin    ──┘   (+ optional varco_fastapi[fastapi] extra for the REST admin router)
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

**Rule**: services must never hold or call `AbstractEventBus` directly. They inject `AbstractEventProducer` and call `_produce()` / `_produce_many()`. The only accepted exceptions are `OutboxRelay` (infrastructure), `EventConsumer.register_to()` (wiring-time only), and `DlqRedriver` (`varco_core.event.redrive`, Plan 009 — publishes a dead letter back onto the bus on operator command; it is infrastructure, not application logic, same reasoning as `OutboxRelay`).

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

`AbstractDeadLetterQueue` is the interface. `InMemoryDeadLetterQueue` is for tests. Backend implementations (`KafkaDLQ`, `RedisDLQ`, `SADeadLetterQueue` in their respective packages) push to a dedicated topic/channel/table.

**Contract**: `push()` must never raise — the retry wrapper in `_make_retry_wrapper` cannot recover from DLQ failures, and neither can `OutboxRelay` or `JobRunner` (Plan 005 Phase 3/4). Implementations must log errors and swallow them.

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

**One `DeadLetterEntry`, three sources** (Plan 005 Phase 3, U-6) —
`DeadLetterSource.CONSUMER` (default, unchanged), `OUTBOX_RELAY`, `JOB`.
`event: DomainEvent` became `DomainEvent | None`; `source_ref`/`payload` are
new, all-defaulted fields. `OutboxRelay.__init__` gains `retry_policy=`,
`dlq=`, `max_attempts=` — with no `retry_policy` the relay is
byte-identical to today; `max_attempts` set without `dlq` raises `ValueError`
at construction (refuses to configure silent data loss). `RetryPolicy.durable_delivery()`
(`max_attempts=20, base_delay=15.0, max_delay=3600.0`) is the named preset
`OutboxRelay`/`AuditConsumer` reach for instead of changing the global
`RetryPolicy()` default. `AuditConsumer` is now safe-by-default
(`_default_retry_policy = RetryPolicy.durable_delivery()`); pass
`retry_policy=None` explicitly to restore fire-and-forget. See
`technical_docs/features/dead-letter-queues.md`.

**Redrive, retention, tenancy, Beanie backend, REST admin** (Plan 009) —
`AbstractDeadLetterQueue` gained `supports_random_access: ClassVar[bool]`,
`get()`/`list_entries()` (concrete-but-raising — no portable random access),
`delete()` (portable default → `ack()`), `delete_where()`/`count_by_channel()`
(concrete-but-raising, refuse with no predicate). `DlqRedriver`
(`varco_core.event.redrive`) owns the redrive *policy* (publish-then-ack,
never the reverse) rather than the ABC growing `redrive(bus=)` — see the
layer rule above. `DeadLetterEntry.tenant_id: str | None = None` is stamped
from the ambient `tenant_context()`, never a constructor param; a `None`
tenant is deliberately never matched by an explicit `tenant_id=` filter.
`BeanieDeadLetterQueue` (`varco_beanie.dlq`) ships with **no TTL index by
default** — dead letters must never be silently deleted (RD-2). `varco dlq`
(list/redrive/purge) and `varco retention prune` are new CLI verbs;
`mount_reliability_admin()` (`varco_fastapi.admin.mount`) is the bundled REST
surface, gated the same way as the tenant control plane (see the pitfall
table). Full detail: `technical_docs/features/dead-letter-queues.md`.

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

### Ambient request context (varco_core.context, Plan 011 / X1)

`AmbientVar[T]` (`context/ambient.py`) is the generic request-scoped ambient-value primitive —
a ~70-line generic over `contextvars.ContextVar[T | None]` with `.get()`/`.scope(value)` (sync
`@contextmanager`)/`.ascope(value)` (`@asynccontextmanager`), always token-reset in `finally`.
It is the *generalization* of `tenant_context()` (`service/tenant.py`) and `correlation_context()`
(`tracing.py`), which are **not** rewritten onto it — they stay exactly as they are; this module
documents them as the precedent it generalizes. `RequestContext` (`context/request.py`,
`@dataclass(frozen=True)`, `locale`/`timezone`/`extras`) is the **one** aggregate ambient value I2
and T1 both build on, held in a single `AmbientVar[RequestContext]` — one middleware pass, one
token, one reset, instead of two independent `ContextVar`s with two reset orders to get right.
`request_context(locale=, timezone=)` **merges** onto the enclosing context — setting a locale
never blanks an already-resolved timezone. `resolve_precedence(candidates: Sequence[tuple[str, T |
None]])` (`context/precedence.py`) is the one "first non-`None` wins" helper both I2's
`resolve_locale()` and T1's `resolve_timezone()` are thin consumers of — explicit `(source, value)`
pairs (not an `or`-chain, which would skip a legitimate falsy value) and returns *which* source won
(`Resolved.source`), turning "why did this user get German?" into one DEBUG log line.

**Rule: `RequestContext` never holds the tenant.** `current_tenant()` (`service/tenant.py`) stays
the single source of truth for "who is the tenant" — `TenantAwareService`, RLS, `tenancy_cache_key()`,
the DLQ tenant stamp, and the audit trail all read it directly. Composition with the tenant is by
*ordering* (`TenantResolutionMiddleware` runs before `LocalizationMiddleware`), never by
containment — see the pitfall table below.

**Note on `ContextVar` construction**: module-scope `ContextVar()` construction (as `AmbientVar`
does internally, and as `_request_context` does at `context/request.py` module scope) is
**correct**, not an exception to the lazy-`asyncio.Lock` rule — PEP 567 requires a `ContextVar` be
created once, typically at module scope, to behave correctly across `asyncio` tasks.
`ContextVar()` construction has no running-event-loop requirement, unlike `asyncio.Lock()`.

### Internationalization (varco_core.i18n, Plan 011 / I2)

Off by default (`I18nSettings.enabled=False`) — no catalog constructed, no middleware, no `.mo`
read, no `Content-Language` header. `MessageCatalog` (ABC): `get_message(key, locale) -> str |
None` (abstract) + `format_message()` (concrete default: `str.format_map` with a
`__missing__`-tolerant mapping, so a missing interpolation param leaves `{name}` visible instead of
raising inside the error-rendering path). Three implementations: `NullMessageCatalog` (the DI
default, zero I/O), `DictMessageCatalog` (in-memory, tests/small apps), `GettextMessageCatalog`
(production default — stdlib `gettext` only, **zero new runtime dependency**; `.mo` loading is
blocking file I/O and happens in `start()`, never lazily on the first request). **No process-global
`activate()`** — the locale lives only in X1's `ContextVar`; a `GettextMessageCatalog` is immutable
after `start()` and every lookup takes `locale` explicitly (avoids Flask-Babel's `force_locale`
cross-request leak, issue #117). Precedence chain (thin `resolve_precedence()` consumer):
`?lang= -> user_profile -> tenant_default -> Accept-Language -> fallback` — deliberately puts
`?lang=` before a stored profile (an explicit per-request override must not be overruled by a
stale stored value), a documented deviation from the design brief's ordering. `Accept-Language`
negotiation is a hand-rolled RFC 4647 §3.4 Lookup (`varco_core.i18n.negotiation`) — no standard
Python library implements Lookup. `RD-2`'s `TenantDefaultsProvider` Protocol (`context/defaults.py`)
resolves per-tenant locale/timezone defaults **without** a `varco_tenants` schema change; ships
`NullTenantDefaults` (zero I/O) and `StaticTenantDefaults`. `localization_cache_key(base,
locale=True)` (`i18n/cache_key.py`) fails closed (`RuntimeError`) with no ambient locale — locale
is never an implicit cache-key component (RD-6), same rule as `tenancy_cache_key()`. See
`technical_docs/features/i18n-and-localization.md` and `technical_docs/features/
error-taxonomy-and-i18n.md`.

### Timezones (varco_core.tz, Plan 011 / T1 / T2 / T3)

Off by default (`TimezoneSettings.enabled=False`) — no resolution, `current_timezone()` is `None`,
storage is unaffected. **varco never changes what it stores** — everything is still written
aware-UTC; T1 is a rendering/interpretation layer only (`to_user_tz()`, `now_local()`).
Five-source precedence chain: `?tz= -> X-Timezone header -> user_profile -> tenant_default ->
fallback`, every candidate validated via `validate_iana_zone()` before entering the chain (an
invalid zone falls through with one WARNING, never raises). `TimezoneSettings.default_timezone` is
validated **at startup** — a missing tzdata database (common on slim/distroless images) raises a
legible error naming `pip install tzdata` / `pip install "varco-core[tz]"` (the plan's only new
dependency anywhere, and optional). T2 (DST-safe one-shot scheduling) and T3 (query-layer datetime
coercion) are covered under Background jobs / Query system below. RFC 9557 (IXDTF) is an
**output-only** format (`varco_core.tz.format.format_rfc9557`) — no parser ships (no
production-ready Python implementation exists); an input carrying a bracket zone suffix is
rejected by the coercer with a legible error. See `technical_docs/features/timezone-handling.md`.

### Error taxonomy — `message_key`, `params`, i18n (varco_core.exception, Plan 011 / I1)

Every built-in `ServiceException` now carries a `message_key: ClassVar[str | None]` (e.g.
`varco.error.not_found`) alongside its existing stable `code` (e.g. `FASTREST_001`) — **`code` is
the machine identifier, `message_key` is the i18n key**; a prior docstring claiming `code` itself
was the i18n key was wrong and is corrected. `error_params()` (default `{}`) returns structured
interpolation data — treat it as a **new exfiltration surface**: `ServiceAuthorizationError`
deliberately excludes `reason` from its params, and any override must apply the same scrutiny,
never `vars(exc)`. `VarcoErrorCodes = FastrestErrorCodes` is a bare alias to the identical enum
object (not a subclass, no `DeprecationWarning`) — the backlog's `VARCO_XXXX` naming does not exist
and the codes are **not renamed**; renaming a value whose entire contract is stability is exactly
the change that contract forbids (see the pitfall table). `error_message_for(exc,
message_resolver=, envelope_settings=)` is the seam a `MessageCatalog` plugs into —
`message_resolver: (message_key, params) -> str | None`, `None`/an exception means "no
translation", falling back to `translator`/`default_message`. **Wired into both shipped HTTP
error paths** (Plan 011 drift-fix pass) — `add_exception_handlers()`/`_make_error_response()` and
`ErrorMiddleware` both accept `message_catalog=`/`set_content_language=`, read
`request.state.varco_request_context` (the RD-3 mirror `LocalizationMiddleware` sets), and — when
a catalog is supplied and a locale was resolved — pass `message_resolver=catalog.format_message`
into `error_message_for()` and set the response's `Content-Language` header themselves;
`create_varco_app()` wires `message_catalog=` automatically from its resolved `MessageCatalog`
when I18n is enabled. With no `message_catalog=` (i18n disabled, the default), both paths are
byte-identical to before this fix — `message_key`/`params` still appear on error bodies but the
rendered `message` text stays `default_message`. **D-4 — the one deliberate wire delta**:
`ErrorEnvelopeSettings(include_message_key=True, include_params=True)` (defaults **on**, unlike
every other item in this plan) adds up to two keys to a built-in exception's JSON body;
`VARCO_ERROR_INCLUDE_MESSAGE_KEY=false` / `VARCO_ERROR_INCLUDE_PARAMS=false` restores the exact
pre-plan body. An out-of-tree exception with no `message_key` set is unaffected regardless. RFC
9457 `application/problem+json` (`ErrorEnvelopeSettings(problem_details=True)`) is an **opt-in**
additive mode, never the default media type. See
`technical_docs/features/error-taxonomy-and-i18n.md`.

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

**Stampede protection** (`varco_core.cache.singleflight`, Plan 010 / C2) — `Singleflight` coalesces
N concurrent misses on the same key into one recompute per process; every other caller ("follower")
`await asyncio.shield(leader_future)` so its own `@timeout`/cancellation never kills the shared
recompute. Reached via `@cached(policy=CachePolicy(singleflight=True), singleflight=True)` or
`CacheServiceMixin._cache_policy = CachePolicy(singleflight=True)` — `CachePolicy()` (the default)
and `singleflight=False` are byte-identical to pre-Plan-010 behaviour. Per-process only (Decision
D-3) — a `SingleflightProtocol` seam is left for a future distributed implementation. All four R1
features (singleflight, L1 backplane, observability, SWR/jitter/negative caching) share one
algorithm, `varco_core.cache.readthrough.read_through()`, because coalescing a background refresh
while concurrently serving a stale value is one race, not two independent features. `LayeredCache(
backplane=RedisPubSubBackplane())` (`varco_redis.backplane`) keeps every pod's L1 coherent after
another pod's write — mandatory `promote_ttl` bounds the staleness a missed (fire-and-forget)
invalidation can cause. `install_cache_metrics()` (`varco_core.observability.cache`) is a manual
install function, same shape as `install_reliability_metrics()` — deliberately **not** a scanned
`@Configuration`. See `technical_docs/features/cache-hardening.md` for the full design (Decisions
D-1 through D-5, the write-ordering cost, the two-step envelope rollout).

**Bulk operations** (`BulkCache`, Plan 011 / C5) — a **separate**, additive `runtime_checkable`
Protocol (`get_many`/`set_many`/`delete_many`), never new methods on `AsyncCache` itself (D-11) —
adding them there would silently flip `isinstance(third_party_cache, AsyncCache)` to `False` for
every out-of-tree implementation, since a `runtime_checkable` Protocol's `isinstance()` tests
method presence. `CacheBackend` gets the three methods as concrete, portable loop-over-`get`/
`set`/`delete` defaults, so every shipped backend satisfies `BulkCache` immediately; `RedisCache`/
`MemcachedCache` override with native `MGET`/`get_multi` as an optimization only.
`CacheBackend(serializer=...)` reuses the existing `varco_core.serialization.Serializer` Protocol
— never a second cache-specific serializer. `read_through_many()` (`varco_core.cache.readthrough`)
shares the **same** `Singleflight` instance/slots as `read_through()`, so a bulk read and a
single read of the same key coalesce with each other. `LayeredCache.set_many()`/`delete_many()`
under a backplane publish **N per-key** `InvalidationMessage(kind="key")` messages, never a batched
`kind="keys"` (D-12) — Plan 010 froze that wire format deliberately, and a mixed-version fleet
would silently drop a batched message. `CacheServiceMixin._use_bulk_cache = True` (opt-in,
default `False`) routes `list()`'s single, already-namespaced list key through
`read_through_many()` (reusing `get_many`/`set_many` instead of a second implementation) when
`self._cache` satisfies `BulkCache` — `list()` still caches its whole result under one key, so
this buys the envelope/SWR/negative-caching/singleflight machinery for that key rather than a
true N-key batch read; call `read_through_many()` directly with your own per-entity keys for a
genuine multi-entity round trip. See `technical_docs/features/cache-hardening.md`'s "Bulk
operations" section.

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

**Datetime coercion contract** (`varco_core.query.policy.DatetimeCoercionPolicy`, Plan 011 / T3) —
`assume: Literal["naive", "utc", "context"] = "naive"` declares how `coerce_datetime()` interprets
a **naive** input; an already-aware value (an explicit offset) always wins under every policy, and
the coercer only ever attaches `tzinfo` to the returned value — it never emits `AT TIME ZONE` SQL
(that would defeat the index). `"naive"` (default) is byte-identical to pre-Plan-011 behaviour;
`"utc"` is the **recommended** setting for `TIMESTAMPTZ` columns (not the default, because
`asyncpg` rejects an aware datetime against a `TIMESTAMP WITHOUT TIME ZONE` column — turning
`"utc"` on for a naive-column app would break a working query); `"context"` reads
`current_timezone()` (opt-in — no mainstream framework does this by default). ⚠️ `policy=` is
wired into the free function `coerce_datetime()` only — `ASTTypeCoercion`
(`varco_core.query.visitor.type_coercion`), the visitor `QueryTransformer` actually drives, has no
`policy=` parameter and calls its registered coercer with no policy at all, so every datetime
field coerced through the AST path is always `"naive"` regardless of any `DatetimeCoercionPolicy`
you construct. See `technical_docs/features/timezone-handling.md`'s T3 section.

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

### Background jobs — time, lease, fencing (varco_core.job / Plan 005 Phase 4)

`AbstractJobStore`/`AbstractJobRunner` (`varco_core.job.base`) gained a time
dimension (`Job.run_at`, `AbstractJobRunner.enqueue(run_at=, delay=)`), bounded
retry (`Job.attempt`/`max_attempts`, `JobRunner(retry_policy=, dlq=)` — reuses
`varco_core.resilience.RetryPolicy`, no second retry model), and a fenced
lease (`try_claim(owner_id=, lease_ttl=)`, `renew()`, `reap_expired_leases()`,
`save(expected_epoch=)` → `StaleLeaseError` on a stale write). Every new
parameter is `None`/`1`-defaulted to reproduce today's exact behaviour.

```python
claimed = await store.try_claim(job_id, owner_id="worker-7", lease_ttl=30.0)
renewed = await store.renew(job_id, owner_id="worker-7", epoch=claimed.lease_epoch, lease_ttl=30.0)
await store.save(claimed.as_completed(result), expected_epoch=claimed.lease_epoch)
# StaleLeaseError if a stalled worker resumes after being fenced out by a reap
```

`JobPoller(lease_aware=True)` (the new default) detects death via
`store.reap_expired_leases()` instead of the old wall-clock `stale_threshold`
— falls back to the age check automatically when the store raises
`NotImplementedError` (no lease support). `renew()`/`reap_expired_leases()`
are concrete-but-raising on the ABC (no correct fallback for a lease exists);
`claim_next()` has a portable default (`list_by_status(PENDING)` +
`try_claim` loop). `delete_where(...)` (Plan 005 Phase 6, U-18) is the
retention primitive — refuses to run with no predicate at all (`ValueError`).
See `technical_docs/features/job-scheduling-and-leases.md` for the TTL/heartbeat
sizing formula, the retry-binding decision table, and the retention recipe.

**Zoned schedules — DST-safe one-shot scheduling** (Plan 011 / T2) — `Job` gains three additive,
defaulted fields: `run_at_wall: datetime | None`, `run_at_tz: str | None`, `run_at_fold: int = 0`.
**`run_at` is materialized, not replaced** — it keeps its exact current meaning as the UTC claim
predicate; the three new fields are the *intent* it was computed from. `run_at_tz IS NULL` (every
existing row) is byte-identical to today; no new index. `AbstractJobStore.
supports_zoned_schedules: ClassVar[bool] = False` — `SAJobStore`/`BeanieJobStore`/the in-memory
store all opt in and persist the columns/fields. `AbstractJobRunner._prepare_zoned_job(job, store,
run_at_wall=, tz=, fold=, gap=, overlap=)` is the concrete RD-5 guard + materialization helper on
the ABC, raising `ValueError` naming the store class when a zone targets a store that hasn't
declared support. **Wired into the shipped `JobRunner`** (Plan 011 drift-fix pass) —
`varco_fastapi.job.runner.JobRunner.enqueue(job, coro, *, run_at=, delay=, run_at_wall=, tz=,
fold=, gap=, overlap=)` calls `_prepare_zoned_job()` before `self._store.save(job)`, so the RD-5
guard runs on the standard submission path; `tz=None` (the default) is a pure passthrough, so
every pre-existing `enqueue(job, coro)` call site is byte-identical to before. Constructing a
`Job` with the three fields set directly (materializing `run_at` via
`varco_core.tz.schedule.resolve_zoned()` yourself) and calling `store.save()` still works and
still bypasses the guard — nothing requires going through `enqueue()`.
`varco_core.tz.schedule.resolve_zoned(wall, zone, gap=GapPolicy.NEXT_VALID,
overlap=OverlapPolicy.FIRST)` resolves DST gaps/overlaps with no `dateutil` dependency — default
`NEXT_VALID` (not brief 004's recommended `SKIP`) because "skip" on a one-shot job means silent
data loss, the same class of defect `OutboxRelay(max_attempts=)` refuses without a `dlq=`.
`ScheduleRematerializer` (`varco_core.job.reschedule`, `interval=0.0` default = never started) is
the opt-in recompute-on-read sweeper, fenced with `save(expected_epoch=)`. See
`technical_docs/features/job-scheduling-and-leases.md`'s "Zoned schedules" section and
`technical_docs/features/timezone-handling.md`'s T2 section.

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

**Retention, tenancy, REST admin, tamper evidence** (Plan 009) —
`AuditRepository.list_for_entity()` gained a **breaking**, keyword-only
`tenant_id: str | None = None` (an out-of-tree override without it now
raises `TypeError` at call time — the previously-silent security bug this
fixes). `list()` (concrete-but-raising) and `delete_where()`
(concrete-but-raising, no-predicate `ValueError`) are new; both are driven
by `mount_reliability_admin()`'s bundled REST surface
(`varco_fastapi.admin`), gated identically to the tenant control plane.
`SAAuditRepository(hash_chain=True)`/`BeanieAuditRepository(hash_chain=True)`
add opt-in tamper evidence (`AuditEntry.entry_hash()`,
`AuditRepository.verify_chain()`) — the chain link is established inside
`save()` under a backend-level serialization guarantee, never computed by
`AuditConsumer` (would fork silently under concurrent consumers). Full
detail: `technical_docs/features/database-auditing.md`.

### Field-level encryption & crypto-shredding (varco_core.encryption / encryption_store)

`FieldEncryptor` (Protocol) → `FernetFieldEncryptor` / `MultiKeyEncryptorRegistry`
(rotation) / `TenantAwareEncryptorRegistry` (per-tenant) / `ScopedEncryptorRegistry`
(per-arbitrary-scope, Plan 005 Phase 1). `EncryptionKeyManager` persists DEKs via an
`EncryptionKeyStore` (`InMemoryEncryptionKeyStore` for tests; `SAEncryptionKeyStore`,
`RedisEncryptionKeyStore`, `BeanieEncryptionKeyStore` for production).

**Scope vs tenant**: `EncryptionKeyEntry.scope` defaults to `tenant_id` verbatim at the
Python level (`__post_init__`/`from_dict`), so `load_for_tenant`/`build_tenant_registry()`
are unaffected by upgrading. ⚠️ `load_for_scope`/`destroy_scope`/`list_scopes` filter on
the persisted `scope` column/index itself on every backend — a one-time
`UPDATE ... SET scope = tenant_id WHERE scope IS NULL` (or equivalent per-backend backfill)
is required before `build_scoped_registry(tenant_id)` finds pre-existing rows. Use
`manager.build_scoped_registry(scope)` (loads **only** that scope, unlike the eager
`build_tenant_registry()`) for per-data-subject keys. Never embed personal data in a scope
string — varco does not parse it. See `technical_docs/features/crypto-shredding.md` for the
full backfill recipe.

**Destroy vs retire**: `retire(kid)` removes a key from rotation but decrypt still
works. `manager.destroy_scope(scope)` crypto-shreds every key for a scope (tombstone:
`key_material` blanked, `destroyed_at` set) — a subsequent decrypt raises
`KeyDestroyedError`, distinguishable from the generic `EncryptionError` an unknown kid
raises. See `technical_docs/features/crypto-shredding.md` for the full model, the
operator backup-retention obligation, and the capability-shim rule for third-party
`EncryptionKeyStore` implementations (it is a `runtime_checkable` Protocol — never call
`load_for_scope`/`list_scopes`/`destroy_scope` directly, always through
`EncryptionKeyManager`'s shim).

### A2A protocol surface — SkillAdapter + SkillSource (varco_fastapi.router.a2a / router.skill)

`SkillAdapter` exposes an agent over the Google A2A protocol. Plan 005 Phase 7 (U-3 + U-4)
decoupled the adapter's **subject** from `VarcoRouter` introspection and moved the **protocol**
to v1.0.0, mounted alongside the pre-v1.0.0 surface for one minor release:

```
SkillAdapter(router_cls | source=, ...)
  ↑ exactly one of the two — ValueError otherwise
router_cls   → wrapped into RouterSkillSource (today's introspect_routes() behaviour, verbatim)
source=      → any SkillSource — decouples the adapter from VarcoRouter entirely
  ↓ both implement
SkillSource (Protocol): skills() / agent_metadata() / async invoke(skill_id, payload, *, ctx=)
```

`adapter.mount(app, legacy_paths=True)` (default) always mounts the v1.0.0 surface
(`GET /.well-known/agent-card.json` — capability flags nested under `capabilities`, no
top-level `id` — and `POST /a2a`, a JSON-RPC 2.0 dispatcher for `message/send`, `tasks/get`,
`tasks/list`, `tasks/cancel`, `tasks/resubscribe`) and, while `legacy_paths=True`, also the
pre-v1.0.0 paths (`GET /.well-known/agent.json`, `POST /tasks/send`, `GET /tasks/{task_id}`,
`GET /tasks/{task_id}/history`) with one deprecation warning logged per mount.

**Async A2A already works and predates v1.0.0** — `SkillAdapter(job_runner=, job_store=,
conversation_store=)` makes `message/send`/`POST /tasks/send` return `state: working`
immediately and `tasks/get`/`GET /tasks/{task_id}` poll the real job status; Phase 7 did not
add this, it only moved the protocol shape (see Source correction 2 in
`plans/005-upstream-gaps.md`).

**`ctx` is the U-3 auth-passthrough contract**: `SkillSource.invoke(skill_id, payload, *,
ctx=)` receives the verified caller's `AuthContext` (or `None` when no auth middleware
populated one) so the three caller classes — end user, another agent, an integrating
platform — are distinguishable in the audit trail. `skills=` on `SkillAdapter.__init__`
accepts author-supplied `SkillDefinition` objects **verbatim** — hand-written skill text
reaches the Agent Card unaltered, never regenerated from route names.

See `technical_docs/features/a2a-surface.md` for the full v1.0.0 path/method table, a
non-router `SkillSource` example, and the legacy-path deprecation timeline.

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
| `VARCO_JWT_AUDIENCE` | `None` | this service's expected `aud` — **required** unless `VARCO_JWT_ALLOW_ANY_AUDIENCE=true` (Plan 005 Phase 2 / U-13, **BREAKING security default**: `JwtBearerAuth` now *refuses to construct* with `ValueError` when omitted, instead of logging a warning and proceeding) |
| `VARCO_JWT_ALLOW_ANY_AUDIENCE` | `false` | named escape hatch restoring the pre-Phase-2 "warn once and proceed with no audience enforcement" behaviour |
| `VARCO_JWT_ENFORCE_ISS` | `true` | whether `TrustedIssuerRegistry.verify()` checks the token's `iss` claim against the resolved issuer's registered value (Plan 005 Phase 2 / U-13, **BREAKING security default**: previously never enforced here) |
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
- Persisted dynamic CRUD needs `adapter="sqlalchemy"` (the `varco-casbin[sqlalchemy]` extra) or
  `adapter="beanie"` (the `varco-casbin[beanie]` extra, MongoDB via Beanie — also requires
  `VARCO_CASBIN_DB_NAME`); the default `memory` adapter is non-durable, and `adapter="file"`
  is durable but single-process only (concurrent writers can corrupt the CSV).

### Schema migrations (varco_core.migration + varco_sa/varco_beanie/varco_fastapi)

One backend-agnostic contract, two engines, one lifespan component, one CLI (Plan 006):

```
varco_core.migration            ← contracts only, zero third-party deps
  AbstractMigrator · Revision · MigrationPlan · MigrationReport · MigrationSettings
  MigrationError · PendingMigrationsError · MigrationLockTimeout
  IrreversibleMigrationError · MigrationBackendUnavailable · InMemoryMigrator
        ▲                          ▲                        ▲
varco_sa.migration        varco_beanie.migration    varco_fastapi.migrate
  AlembicMigrator            BeanieMigrator            MigrationLifecycle
   └ packaged "varco" branch  └ varco_migrations coll   └ prepended into VarcoLifespan
   └ migration_lock (D2)      └ lock doc + heartbeat    └ create_varco_app(migrations=)
   └ ops.rls_upgrade          └ IndexReconciler
        ▲                          ▲
        └──── varco_core.cli ──────┘   `varco migrate …` (entry-point group "varco.commands")
```

`varco_fastapi` imports **only** `varco_core.migration` — never `varco_sa`,
`varco_beanie`, or `alembic`. Same seam as `AbstractEventBus`/`AbstractJobStore`.

**Default is `off` — nothing runs.** `MigrationSettings.mode` (`VARCO_MIGRATE_MODE`):
`off` (default, nothing registered) / `check` (fail startup if behind, never writes DDL —
**the recommended production posture**) / `upgrade` (lock → apply → release; for
single-instance, dev, and PaaS-without-a-pre-deploy-hook). `on_failure`
(`VARCO_MIGRATE_ON_FAILURE`) is `fail` by default; `warn` keeps serving against a possibly
stale schema and is a foot-gun by construction. Full env table in the feature doc.

**Multi-pod exclusion is a held-open transaction, not a TTL lock.** A dedicated
`NullPool` connection does `BEGIN` → `SET LOCAL idle_in_transaction_session_timeout = 0`
→ `SAXactAdvisoryLock.xact(...)`, Alembic runs on its **own** connection with
`transaction_per_migration=True`, and the lock's `COMMIT` **is** the release — there is no
`release()` call. Process death releases it too, so there is no TTL to size. The
`SET LOCAL` is mandatory: a server-level `idle_in_transaction_session_timeout` would
otherwise kill the lock holder mid-migration and silently un-exclude concurrent DDL.
MongoDB has no advisory locks, so `BeanieMigrator` uses an owner-fenced lock document with
a heartbeat (interval ≤ `ttl/3`, cancelled in a `finally`) — and does have the TTL-sizing
problem.

**Framework tables get their own Alembic branch, shipped in the wheel.** Ten tables
(`varco_outbox`, `varco_inbox`, `varco_jobs`, `varco_sagas`, `varco_conversation_turns`,
`varco_dedup_log`, `varco_audit_log`, `varco_dead_letters`, `varco_encryption_keys`,
`varco_tenants` — the tenant catalog, Plan 007) are
owned by `varco_sa/migrations/versions/*` with `branch_labels=("varco",)`, appended to
`version_locations` automatically. Apps never list framework metadata in `env.py` — they
wire `include_object` from `varco_sa.migration.env_template` so their own autogenerate
skips those tables. Each owning module self-registers via `register_framework_metadata()`,
so a table added in a future release is picked up on `pip install -U varco-sa` with no app
change. Single-branch escape hatch: `include_framework_branch=False` +
`get_target_metadata(..., include_framework=True)`.

**`ensure_table()` is reconciled, not removed.** Framework baseline revisions are
idempotent (`has_table()`/`checkfirst` guarded), and
`AlembicMigrator.adopt_framework_tables()` / `varco migrate adopt` stamps `varco@head`
against an already-`ensure_table()`-built database without executing DDL. **Order: adopt,
then upgrade.** This is the one manual step in the feature.

**Mongo index reconciliation is `check` by default, independent of `mode`** —
`mode="upgrade"` never silently starts an index build. `index_mode="create"` is opt-in and
belongs in `varco migrate index --create` as a pre-deploy job.

⚠️ **`MigrationError` and `MigrationPlan` are NOT re-exported from `varco_core`** — the
pre-existing, unrelated `varco_core.migrator` (domain data/field migration) already owns
those names. Import them from `varco_core.migration` explicitly. Everything else
(`AbstractMigrator`, `Revision`, `MigrationReport`, `MigrationSettings`,
`InMemoryMigrator`, the other three exceptions) is on `varco_core` directly.

`alembic` is an optional extra: `pip install "varco-sa[migrations]"`. See
`technical_docs/features/schema-migrations.md`.

### Multitenancy — isolation strategies, control plane, global scope (Plan 007, Plan 008)

Tenant data isolation is a **selectable deployment strategy**, not one hard-coded shape.
Three `TenantIsolation` values (`SHARED` — default, unchanged; `SCHEMA` — Postgres only;
`DATABASE` — Postgres + Mongo), `enforce_rls: bool` as an additive hardening flag on
`SHARED` rather than a fourth enum value, and an orthogonal `TenantScope`
(`TENANT`/`GLOBAL`) for shared reference data under every strategy.

```
varco_core.tenancy            ← contracts only, zero third-party deps
  TenantIsolation · TenantScope · TenantStatus · TenancySettings
  AbstractTenantCatalog · StaticTenantCatalog · CachedTenantCatalog · TenantDescriptor
  TenantResourcePool[T] · DynamicTenantUoWProvider · GlobalUoWProvider
  AbstractTenantProvisioner · ExternalTenantProvisioner
  validate_service_scope() · tenancy_cache_key() · GlobalScopeReadOnlyError
  control/  → TenantProvisionRequested · TenantDeprovisionRequested   (commands)
              TenantCatalogChanged · TenantNodeReady                 (facts, Plan 008)
              TenantControlService · TenantProvisionConsumer
              TenantReadinessCoordinator · TenantReadiness           (Plan 008)
  fanout.py → TenantFanoutSupervisor
        ▲                                        ▲
varco_sa.tenancy                          varco_beanie.tenancy
  SASchemaRouter · SAEngineRegistry          BeanieTenantPool · BeanieTenantBinding
  SASchemaProvisioner · SATenantCatalog      BeanieDatabaseProvisioner
  rls_check.assert_rls_enabled               BeanieTenantCatalog
  global_scope (42501 → GlobalScopeReadOnlyError)
  admin/  → SAAdminEngine, SADatabaseProvisioner   (control plane ONLY)
        ▲                                        ▲
        └────────── varco_fastapi.tenancy ────────┘
              TenancyLifecycle · TenantResolutionMiddleware
              build_tenant_router() · mount_tenant_admin()
```

`varco_fastapi.tenancy` imports **only** `varco_core.tenancy` — never `varco_sa`,
`varco_beanie`, `sqlalchemy`, or `pymongo`. Same seam rule as `AbstractEventBus`/
`AbstractMigrator`.

**Default is byte-identical to pre-Plan-007 behaviour.** `TenancySettings()` defaults:
`isolation=SHARED`, `enforce_rls=False`, every model `TenantScope.TENANT`,
`fanout_framework_tables=False`. No pool, no extra engine/client, no symbolic schema, no
control-plane surface constructed. `create_varco_app(tenancy=None)` (the default)
registers nothing.

**Schema-per-tenant uses `schema_translate_map`, not `SET LOCAL search_path`.**
`SASchemaRouter` applies `engine.execution_options(schema_translate_map={"tenant": "t_acme"})`
per session. A forgotten routing call **fails closed** (compile/DB error) rather than
silently reading another tenant's rows — the decisive property over `search_path`, which
fails open on the same mistake. Raw `text()` SQL is **not** translated and must
self-qualify. `mechanism="search_path"` is a documented escape hatch, still
`set_config(..., true)` — never a bare session-scoped `SET`. See
`technical_docs/features/postgres-rls.md` §3.

**Global/shared scope has a dual-UoW API, not a `make_uow(scope=)` parameter.**
`Inject[GlobalUoWProvider]` is a distinct DI-token type from `Inject[IUoWProvider]` — no
change to `IUoWProvider`'s ABC. Under `DATABASE`, one transaction cannot span a tenant
database and the global database (no 2PC) — route atomic cross-scope writes through the
outbox/saga primitives instead. The app-facing global credential is **read-only by
default** (RD-10); a denied write surfaces as `GlobalScopeReadOnlyError`, never a raw
SQLSTATE `42501` traceback.

**Database-per-tenant needs the fan-out supervisor, or outbox/job/audit rows are
stranded.** Under `TenantIsolation.DATABASE`, a tenant's framework rows live in *that
tenant's* database — the process-wide `OutboxRelay`/`JobPoller`/`AuditConsumer` never
polls it. `TenantFanoutSupervisor` owns one child per active, pool-resident tenant,
reusing `OutboxRelay` etc. verbatim; failure is isolated per tenant (capped backoff,
restart). `varco_sa.tenancy.guard.guard_fanout_configuration()` refuses to construct a
db-per-tenant deployment with a relay wired but `fanout_framework_tables=False`.

**The tenant control plane is standalone by default, bundleable on request.** No new
workspace package. `mount_tenant_admin(app, control_service, acknowledge_bundled_admin=True,
server_auth=..., admin_role="tenant-admin")` is the **only** way to expose the admin
surface — there is deliberately **no** `VARCO_TENANCY_MOUNT_ADMIN` env var, ever.
Onboarding is dynamic (REST via `build_tenant_router()` or event-driven via
`TenantProvisionConsumer` on channel `"varco.tenancy"`), backed by the durable
`SATenantCatalog`/`BeanieTenantCatalog` (`varco_tenants`, the **tenth** framework table —
picked up by the existing dynamic `0001_varco_framework_baseline` revision via
`framework_metadata()`, no separate migration file). `TenantProvisionConsumer` is
safe-by-default: `RetryPolicy.durable_delivery()` + a DLQ, following `AuditConsumer`'s
precedent.

**Both onboarding entry points converge on one catalog transition (Plan 008, RD-11).**
`TenantProvisionConsumer` takes `control_service=` (a `TenantControlService`), not an
`AbstractTenantProvisioner` — the bus path now drives the identical `provision()`/
`deprovision()` transition the REST admin surface does, closing a defect where a
bus-onboarded tenant's storage existed with no catalog row (permanently unroutable —
every request 404s). `provisioner=`+`catalog=` survives one minor release as a shim
(`DeprecationWarning`); `provisioner=` alone is a `ValueError` naming `control_service=`
— there is no correct behavior to fall back to.

**Two new broadcast verbs, distinct from the local orchestration methods.**
`TenantControlService.request_provision(tenant_id)` /
`.request_deprovision(tenant_id, confirm=)` emit `TenantProvisionRequested`/
`TenantDeprovisionRequested` fleet-wide and do **nothing else** — no local catalog
write, no local DDL. The broadcaster is not included; a node that must also provision
itself calls `provision()` first (synchronous local failure), then `request_provision()`
(broadcast). REST: `POST /tenancy/tenants/{id}/request-provision` (202),
`DELETE /tenancy/tenants/{id}?broadcast=true`, `POST /tenancy/tenants/{id}/activate`
(manual `mark_active()` terminator), `GET /tenancy/tenants/{id}/readiness` (only mounted
when `build_tenant_router(..., coordinator=...)` is given a `TenantReadinessCoordinator`).

**The command/fact DAG rule (RD-13): no handler may emit a command event.**
Commands (`TenantProvisionRequested`/`TenantDeprovisionRequested`) may only be produced
by `request_provision()`/`request_deprovision()`. They may produce facts
(`TenantCatalogChanged`/`TenantNodeReady`); facts may produce nothing further except a
terminal action (cache invalidation, `mark_active()`). This is what keeps
`provision()`/the consumer from looping now that they share one code path — `provision()`
never re-emits its own command. A command event carries `origin: str | None` (the
broadcaster's `node_id`); a consumer whose own `node_id` matches `origin` skips the event
(one DEBUG log) — it already handled it synchronously before broadcasting.

**Fan-out mode: `catalog_authority=False` + `TenantReadinessCoordinator`.**
`TenantControlService(catalog_authority=False)` (worker mode, RD-16) never writes the
catalog — `provision()` reads it only to refuse a `DELETED`/`DEPROVISIONING` tenant,
always calls the provisioner (idempotency is now the provisioner's own `IF NOT EXISTS`
responsibility, not a status check), and emits `TenantNodeReady(tenant_id, node_id,
store_id)` instead. `TenantReadinessCoordinator(control_service=<authority service>,
expected_stores=frozenset({...}))` aggregates those facts per **store** (RD-17 — not per
pod: ten pods of one service share one store, so autoscaling never changes
`expected_stores`) and calls `control_service.mark_active()` once every expected store
has reported. A timeout (`timeout_s`, default `900.0`) logs one ERROR and **never**
activates a fleet known to be incomplete — the only two ways to `ACTIVE` under worker
mode are full readiness or the manual `POST …/activate`. Readiness state is in-memory
only (RD-18) — a coordinator restart resets it; recovery is one re-broadcast
(`request_provision(tenant_id)` again), safe because every layer downstream is already
idempotent. None of this is wired under the default `TenantIsolation.SHARED`.

**New env vars:** `VARCO_TENANCY_NODE_ID` (`TenantControlService.node_id` — defaults to
`f"{hostname}:{pid}"`, stamped as `origin` on broadcasts) and `VARCO_TENANCY_STORE_ID`
(`TenantControlService.store_id` — only meaningful under `catalog_authority=False`,
stamped on `TenantNodeReady`). See `technical_docs/features/multitenancy.md`'s "Fleet
fan-out" and "Fleet readiness" sections for the topology table, the command/fact diagram,
and the worked 3-service readiness example.

**Cluster DDL never reaches an app pod (RD-4).** `SADatabaseProvisioner` cannot be
constructed without an explicit `VARCO_TENANCY_ADMIN_DSN`, and refuses one equal to the
app's own request-path engine URL.

```python
from varco_fastapi.tenancy.mount import mount_tenant_admin

app = create_varco_app(container, routers=[...])       # tenant traffic — no admin privilege
mount_tenant_admin(                                     # ← privileged surface, opt-in
    app, control_service,
    acknowledge_bundled_admin=True,   # required; ValueError without it
    server_auth=auth, admin_role="tenant-admin", prefix="/tenancy",
)
```

`alembic`/Beanie-agnostic: `TenantFanoutMigrator` (`varco_core.migration.fanout`) applies
the global/framework migration run **before** every tenant's, in sorted order —
`--skip-global` is required to omit it (tenant tables may FK to global tables). See
`technical_docs/features/multitenancy.md` for the decision table, all six wiring recipes,
the connection-budget sizing worksheet, and the RD-7 Mongo clone-cost formula.

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

**Rate limiting**: Use `@rate_limit` to cap calls per second.  `InMemoryRateLimiter` is per-process; use `varco_redis.RedisRateLimiter` (already shipped — U-7's first leg) in multi-pod deployments.

**Bulkhead**: Use `Bulkhead` to cap concurrent in-flight calls to one dependency.  Must be a **shared** instance (same rule as `CircuitBreaker`).  `Bulkhead` is per-process, same limitation as `InMemoryRateLimiter` — use `varco_redis.RedisBulkhead` (U-7's second leg, Plan 005 Phase 8) for fleet-wide concurrency limiting in multi-pod deployments; rate limiting and concurrency limiting are different primitives and a service can be well within its rate budget while still overwhelming a downstream dependency with concurrent in-flight calls:

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

#### Scenario: Expose a non-router subject over A2A

Use `source=` instead of `router_cls` when the thing you want other agents to call is not a
`VarcoRouter` — a data pipeline, a wrapper around a third-party API, anything with no CRUD
routes to introspect:

```python
from varco_fastapi.router.a2a.source import SkillDefinition, AgentMetadata
from varco_fastapi.router.skill import SkillAdapter

class ReportSkillSource:
    def skills(self) -> list[SkillDefinition]:
        return [
            SkillDefinition(
                id="generate_report",
                name="Generate Report",
                description="Builds a PDF summary for the given date range",
                input_modes=("application/json",),
                output_modes=("application/json",),
                route=None,  # no VarcoRouter route backs this skill
            )
        ]

    def agent_metadata(self) -> AgentMetadata:
        return AgentMetadata(name="ReportAgent", description="Generates PDF reports")

    async def invoke(self, skill_id: str, payload: dict, *, ctx=None) -> dict:
        # ctx is the verified caller's AuthContext (U-3) — use it for the audit trail,
        # e.g. record which end user / agent / platform requested the report.
        return {"report_url": await build_report(payload, requested_by=ctx)}

adapter = SkillAdapter(
    None,                       # router_cls omitted
    source=ReportSkillSource(),
    agent_name="ReportAgent",
    agent_description="Generates PDF reports",
    client=None,                # not needed — invoke() does its own work
)
adapter.mount(app)              # same v1.0.0 + legacy A2A surface as a router-backed adapter
```

`adapter.router_class` is `None` for a non-router source — that is the documented contract,
not a bug. `router_cls` and `source=` are mutually exclusive; passing both or neither raises
`ValueError` at construction.

#### Scenario: Turn on schema-per-tenant isolation and onboard a tenant

```python
from varco_core.tenancy import TenancySettings, TenantIsolation
from varco_sa.tenancy.router import SASchemaRouter
from varco_sa.tenancy.provisioner import SASchemaProvisioner
from varco_sa.tenancy.catalog import SATenantCatalog
from varco_core.tenancy.control.service import TenantControlService

# 1. Opt in — everything else stays SHARED-shaped until this flips
settings = TenancySettings(isolation=TenantIsolation.SCHEMA)

# 2. Wire the router (schema_translate_map, not search_path — see
#    technical_docs/features/postgres-rls.md §3) and the provisioner
router = SASchemaRouter(schema_template=settings.schema_template)
provisioner = SASchemaProvisioner(engine=engine)

# 3. The control service ties catalog + provisioner + event emission together
control_service = TenantControlService(
    catalog=SATenantCatalog(session=admin_session),
    provisioner=provisioner,
    producer=producer,   # AbstractEventProducer — emits TenantCatalogChanged
)

# 4. Onboard — idempotent; a second call on an already-active tenant is a no-op
await control_service.provision("acme")

# 5. Per-request: TenantResolutionMiddleware checks catalog status BEFORE
#    pool.ensure(), then wraps the handler in tenant_context("acme") —
#    session_factory_for(engine, "acme") resolves every routed table to
#    schema "t_acme"; global + framework tables stay in the default schema.
```

**Caveat**: models generated under `SAModelFactory.build(..., isolation="schema")` carry a
symbolic `"tenant"` schema token only for `TenantScope.TENANT` models — a `GLOBAL` model or
one of the ten framework tables never does, and under `TenantIsolation.SHARED` (the
default) `__table__.schema` stays `None`, byte-identical to today.

#### Scenario: Call another varco service

```python
from varco_fastapi.client import client_for

client = client_for(OrderRouter, "https://orders.internal")   # importable router
order = await client.read(order_id)
await client.cancel(order_id, reason="oos")
```

`client_for()` is the front door — a ready-to-call instance, no subclassing.
For DI: `bind_clients_from(container, OrderRouter)` then
`Inject[VarcoClient[OrderRouter]]`. For a fleet of peers with resilience
pre-wired (retry, timeout, a shared circuit breaker, auth forwarding) and
"one env var per peer": `PeerRegistry.from_env()` +
`registry.client("orders", OrderRouter)` — see
`docs/peer-service-integration.md`. `make_client`/`GenericClient`/
`OpenAPIClient`/`ClientConfigurator`/`generate_client` still work but moved
to `varco_fastapi.client.advanced` — importing them from
`varco_fastapi.client` directly now raises `AttributeError` naming the new
path.

**Caveat**: `client_for()`'s custom `@route` methods still accept
`**kwargs: Any` — they are not yet built through the new typed
`build_client_method` machinery (that only drives `gen-client`/
`contract_client()` today). Do not document or assume `client_for()` gives
you a `TypeError` on a wrong kwarg yet. See
`technical_docs/features/portable-contracts.md`'s status note.

#### Scenario: Cross-repo service integration (no shared Python import)

```bash
# In the producing service's repo/CI:
varco export-contract myapp.routers:OrderRouter -o order.contract.json

# In the consuming repo — commit order.contract.json, then either:
varco gen-client -c order.contract.json -o order_client.py --class-name OrderClient
```

```python
from order_client import OrderClient          # generated, typed, checked in
client = OrderClient("https://orders.internal")

# ...or the runtime one-liner, no generated file:
from varco_fastapi.contract.runtime import contract_client
client = contract_client("order.contract.json", "https://orders.internal")
```

Both go through `build_client_method`, so they cannot diverge from each
other (enforced by `test_signature_parity`/`test_resolver_parity` — do not
delete either). See `docs/client-code-generation.md` and
`technical_docs/features/portable-contracts.md`.

#### Scenario: Opt into durability in one line

```python
from varco_core.reliability import ReliabilityPreset
from varco_fastapi import create_varco_app
from varco_sa.dlq import SADeadLetterQueue

dlq = SADeadLetterQueue(engine)
app = create_varco_app(container, routers=[...], reliability=ReliabilityPreset.durable(dlq=dlq))
```

Turns on `RetryPolicy.durable_delivery()` + the DLQ for every bare
`@listen(...)` handler (via `set_default_reliability_preset()`'s resolution
at `register_to()` time), starts an `OutboxRelay`, wires an `AuditConsumer`,
and installs the reliability metrics pack — all from one preset object.
`reliability=None` (the default) registers nothing — byte-identical to not
using this feature. See `technical_docs/features/reliability-preset.md`.

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

**Shared, session-scoped integration containers** (Plan 012 / RT1) — each package's
`tests/conftest.py` exposes ONE session-scoped fixture per external service (`redis_url`,
`mongo_url`, `postgres_url` + `postgres_container`, `kafka_bootstrap`, `memcached_host_port`,
`nats_url`), started once per test session instead of once per test file. **Per-test namespacing
rule**: because the container is shared, every test must confine itself to a key/topic/stream/
database/schema name it owns exclusively (a `uuid4().hex[:8]` run id is the established
convention) — never assume the server starts empty. A test that genuinely needs a pristine
server declares its own function-scoped `*_container_fresh` fixture instead, paying the full
container-boot cost explicitly and rarely.

**`VARCO_TEST_<SERVICE>_URL` override contract** (Open Question 1) — each session-scoped
fixture honors a namespaced override (`VARCO_TEST_REDIS_URL`, `VARCO_TEST_POSTGRES_URL`, …): when
set, no container is started and the value is used as-is, reported via `request.config.stash` and
in `scripts/integration_tests.sh`'s summary as "NOT a clean-room run". Bare names
(`REDIS_URL`/`DATABASE_URL`/…) are deliberately **never** honored — a developer with an unrelated
`DATABASE_URL` exported in their shell must never silently run destructive tests (schema
creates/drops) against their own dev database. `make integration-test-clean` unsets every
`VARCO_TEST_*` name first, guaranteeing fresh containers regardless of the calling shell's
environment.

**Conformance suite opt-in** (`testkit/varco_conformance`, Plan 012 / RT6) — a shared,
never-packaged suite of behavioral contract tests, one module per `varco_core` ABC
(`event_bus.py`, `cache.py`, `job_store.py`, `dlq.py`). Reached via one `pythonpath =
["../testkit"]` line in a package's `[tool.pytest.ini_options]`; a backend opts in with a thin
subclass overriding the abstract fixture:

```python
from varco_conformance.event_bus import EventBusConformance

class TestRedisEventBusConformance(EventBusConformance):
    @pytest.fixture
    async def bus(self, redis_url: str):
        async with RedisEventBus(RedisEventBusSettings(url=redis_url)) as bus:
            yield bus
```

The base classes are deliberately not named `Test*` — pytest never collects them standalone, so
an unimplemented fixture fails loudly (`NotImplementedError`) instead of silently passing.
`varco_core/tests/test_conformance_inmemory.py` runs the same four suites against every
in-process implementation with no Docker required — the fast feedback loop.

**A conformance failure that reveals a genuine backend ABC-contract violation becomes
`@pytest.mark.xfail(reason="BUG: ...", strict=True)` plus a one-line BACKLOG.md entry — never an
in-place production-code fix.** `strict=True` means the xfail itself fails loudly if the
underlying bug is ever fixed, so the marker doesn't silently rot. See BACKLOG.md's "Known issues
found while implementing Plan 012" table for the accumulated findings (e.g. `RedisCache`/
`MemcachedCache` truncating a sub-second `ttl` to `int()`, `KafkaDLQ`/`NatsDLQ.delete_where()`
never reaching the ABC's "no predicate → `ValueError`" check).

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
| **In-process `Bulkhead` in multi-pod** | Each pod caps its own concurrency; fleet-wide concurrency to the downstream dependency = N × `max_concurrent` | `varco_core.resilience.Bulkhead` is a per-process semaphore, not shared state — the same defect class as `InMemoryRateLimiter` above, but for *concurrency* rather than *rate* (U-7, Plan 005 Phase 8: they are genuinely different primitives) | Use `varco_redis.RedisBulkhead` — a Redis sorted-set-backed distributed semaphore with TTL-based reclaim for crashed holders (mirrors `RedisLock`'s Lua pattern); same `call()`/`protect()`/`available_slots()` surface as `Bulkhead` |
| **Hedging non-idempotent writes** | Duplicate side-effects (email sent twice, double charge) | Both hedged copies execute concurrently | Only apply `@hedge` to idempotent reads/upserts; never to INSERT or transactional writes |
| **`requires=` without `_auth`** | `RuntimeError` at `build_router()` startup | Guard can never be satisfied with no `AuthContext` | Set `_auth` on the router, or use `allow_anonymous()` if the route is public |
| **`ctx` declared but no `_auth`** | Handler gets 500 (missing argument) | Without auth middleware, no `AuthContext` is injected | Set `_auth` on the router or remove `ctx` from the handler signature |
| **Per-call `CasbinPolicyEngine`** | Policy reloaded every request; slow, in-memory edits lost | A new enforcer is built per call | Resolve it as a DI singleton (`bootstrap`); share one instance |
| **Policy authorizer silently active** | App's own authorizer is shadowed unexpectedly | A scanned `@Configuration` auto-activates on `scan` | The authorizer is opt-in via `enable_policy_authorizer(container)`; don't make it a scanned config |
| **`@Singleton` on pydantic `BaseSettings`** | `LookupError: Cannot resolve 'values'` at resolution | providify injects pydantic's `**values` ctor param | Register settings via a `@Provider` (see `varco_casbin.di`), not `@Singleton` |
| **Quoted `@Provider` return annotation** | An *unrelated* provider fails with `TypeError: xxx() missing 1 required positional argument`; every `Inject[...]` in the container is silently dropped | Under PEP 563 `-> "Foo"` is stored as the string `"'Foo'"`; providify's fallback `eval` returns the **str** `'Foo'` and registers it as the binding interface → `DIContainer._build_localns()` raises `AttributeError: 'str' object has no attribute '__name__'` → `_collect_kwargs_sync()` swallows it with `hints = {}` for **every** provider | Never quote a `@Provider` return annotation (`from __future__ import annotations` already makes it lazy) and import the type at **module scope** so `fn.__globals__` can resolve it. Guarded by `varco_fastapi/tests/test_di_binding_health.py` |
| **Quoted `TypeAlias` used in an injected annotation** | `AnnotationResolutionError: … TypeError: unsupported operand type(s) for \|: 'str' and 'NoneType'` at `validate_bindings()` (providify ≥ 1.1.0), or the parameter is **silently never injected** (< 1.1.0) | `X: TypeAlias = "Foo[Bar]"` binds the **string** to the module name at runtime, so any annotation doing `X \| None` evaluates `str \| None`. Quoting is usually a symptom of the inner type being imported under `TYPE_CHECKING` | Prefer annotating the **interface directly** (`Serializer[Event]`, not an alias). If you must alias, import the referenced types at **runtime** and leave it unquoted, or use PEP 695 `TypeAliasType`. Guarded by `varco_core/tests/test_event_serializer_alias.py` |
| **Protocol impl not resolvable by DI** | `container.get(Serializer[Event])` finds no binding although a conforming class exists | Structural (`Protocol`) satisfaction is invisible to the container — it binds on declared base classes | Subclass the protocol **explicitly** (`class JsonEventSerializer(Serializer[Event])`) and decorate it; use `@Singleton(priority=-sys.maxsize - 1)` for a framework default so any app binding wins regardless of registration order |
| **A package's suite is green but its container won't bootstrap** | Tests pass; a real app dies at startup with `AnnotationResolutionError` | No test hit a path that resolves *binding* annotations — unit tests construct objects directly instead of resolving them | Add a `container.scan(pkg); container.validate_bindings()` test per package — one call covers every present and future singleton (see `varco_redis/tests/test_redis_di.py`) |
| **`container.provide(lambda: X())`** | `ProviderBindingNotDecoratedError` at bootstrap | `provide()` only accepts `@Provider`-decorated callables and takes no second "interface" argument | Declare a module-level `@Provider(singleton=True) def x() -> X:` and pass the function |
| **Override registered after `install()`/`scan()`** | The package default wins; your settings are silently ignored | Equal-priority bindings resolve to the **first** registered | `provide()` before `install()`/`scan()`, or declare `@Provider(..., priority=100)` |
| **`memory` adapter in production** | Policies vanish on restart | The in-memory adapter has no durable store | Use `adapter="sqlalchemy"` (`varco-casbin[sqlalchemy]`) or `adapter="beanie"` (`varco-casbin[beanie]`) for persisted CRUD |
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
| **Token from another service accepted** | Since Plan 005 Phase 2: `JwtBearerAuth()` **fails to start** (`ValueError`) unless `audience=`/`VARCO_JWT_AUDIENCE`/`allow_any_audience=True` is set — this used to be a silent accept | `aud` was never enforced by default — now fails closed instead of warning | Set `VARCO_JWT_AUDIENCE` (or `JwtBearerAuth(audience=...)`), or explicitly opt out with `allow_any_audience=True` / `VARCO_JWT_ALLOW_ANY_AUDIENCE=true` |
| **Forged/misrouted `iss` claim accepted** | A token signed by issuer A's key but claiming `iss` of issuer B used to verify successfully | `TrustedIssuerRegistry.verify()` never checked `iss` against the resolving issuer | Since Plan 005 Phase 2 this is enforced by default (`VARCO_JWT_ENFORCE_ISS=true`); opt out per-call with `verify(enforce_issuer=False)` only if you have a specific reason |
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
| **Destroyed key renders as corrupt data** | Decrypt of a crypto-shredded record raises a generic-looking error | `KeyDestroyedError` was caught by a bare `except EncryptionError` and treated the same as tampered data | Catch `KeyDestroyedError` specifically (it's a subclass) and render "erased", not "corrupt" |
| **Per-subject registry built with `build_tenant_registry`** | Startup loads every key in the store, even ones for scopes not yet needed | `build_tenant_registry()` is eager-all; there is no per-scope equivalent by that name | Use `manager.build_scoped_registry(scope)` — loads exactly one scope's keys |
| **Poison outbox row silently stops a stream** | `OutboxRelay` retries the same undeliverable entry forever, blocking every entry queued behind it | No `retry_policy`/`dlq` wired — today's default is unbounded retry-in-place | Wire `retry_policy=` + `dlq=` on `OutboxRelay`; exhausted entries are pushed to the DLQ and deleted so the stream unblocks |
| **`OutboxRelay(max_attempts=...)` without a `dlq`** | `ValueError` at construction | Deleting a poison entry with nowhere durable to put it is silent data loss — refused by design | Pass a `dlq=` (e.g. `SADeadLetterQueue`) alongside `max_attempts` |
| **Long job killed at 5 minutes** | A legitimately long-running job is marked FAILED mid-work | `JobPoller`'s old wall-clock `stale_threshold` doesn't know the job is still alive | Enable leases (`try_claim(lease_ttl=...)`) — `JobPoller(lease_aware=True)` (default) judges liveness by lease expiry, not age |
| **Stalled worker resumes and overwrites a completed result** | A worker that stalled past its lease window resumes and clobbers another worker's write | Writes were not fenced against the lease epoch | Pass `expected_epoch=` on every `store.save()` call; catch `StaleLeaseError` and abort |
| **External `AbstractJobStore` subclass breaks on `lease_ttl`** | `TypeError: unexpected keyword argument 'owner_id'` on `try_claim()` | Pre-Phase-4 overrides only declared `try_claim(self, job_id)` — the lease kwargs are additions, not activation of a dormant parameter | Add `owner_id`/`lease_ttl` to your `try_claim()` override before enabling leases |
| **`JobPoller` reaps a legitimately-running unleased job** | A RUNNING job with no lease is immediately returned to PENDING regardless of age | `lease_aware=True` (default) treats "no lease at all" as evidence of a failed claim, not as "healthy, just old" | Adopt `lease_ttl` on every claim, or construct `JobPoller(lease_aware=False)` to keep the old age-based check |
| **`release()` returns false and the lock leaks** | A `SAAdvisoryLock` held by process A appears to release successfully but the next process to borrow that pooled connection inherits an already-locked key | Session-level advisory lock behind a transaction-mode connection pooler (PgBouncer `pool_mode=transaction`) — `release()` is routed to a different physical connection than `try_acquire()` used | Use `SAXactAdvisoryLock.xact(key, session)` instead — the lock is released by the caller's own COMMIT/ROLLBACK, so pooling never has a chance to misroute the release |
| **Retention sweep starves the pool** | A cleanup job/maintenance script pins a connection for minutes while deleting a huge backlog | `delete_where()` called once with no `limit` (or the caller manually enumerated ids one at a time) under a transaction-mode pooler | Loop bounded `delete_where(..., limit=1000)` calls (each its own short transaction) until it returns `0` — the chunked-sweep recipe on `AbstractJobStore.delete_where` |
| **Raw JWT readable in the jobs table** | An operator with read access to the jobs table/collection can read PII straight out of `request_token`'s claims | A JWT is base64-encoded, not encrypted — `store_raw_token=True` (default) stores it verbatim | Pass `store_raw_token=False` (`Job(...)`, `JobRunner.enqueue_task(...)`, or `VarcoRouter._store_raw_token = False`) and switch the completion callback to a service-credential/mTLS/signed-URL auth scheme instead of replaying the caller's token |
| **Hand-written RLS policy uses bare `current_setting(...)`** | A query on an RLS-protected table that flies at test data volumes goes from milliseconds to seconds in production (one documented case: 8 100 ms) | `current_setting()` is `VOLATILE` and not `LEAKPROOF` — without a scalar-subquery wrapper the Postgres planner cannot push the predicate below an index scan and falls back to a sequential scan | Always use `varco_sa.rls.enable_rls_ddl()`, which emits the `(SELECT current_setting(..., true))` InitPlan form; never hand-write `USING (tenant_id = current_setting(...)::uuid)` — see `technical_docs/features/postgres-rls.md` |
| **RLS tenant GUC set with `SET` instead of `SET LOCAL`** | Under a transaction-mode pooler (PgBouncer), one tenant's queries leak into a session that was actually serving a different tenant's next transaction | Session-scoped `SET`/`set_config(..., false)` survives past the transaction on a pooled connection — same defect class as `SAAdvisoryLock`'s session-scoped release (U-16) | Use `varco_sa.rls.set_tenant_local(session, tenant_id)` — `set_config(..., true)` (`is_local`) scopes the setting to the current transaction only |
| **`TenantAwareService._scoped_params` bypassed** | Cross-tenant rows returned from a query path that skipped the service mixin (e.g. a raw repository call, an ad-hoc script) | The mixin fails open by design — it only filters queries that actually go through it, there is no enforcement below the service layer | Enable Postgres RLS as defense-in-depth (`varco_sa.rls.enable_rls_ddl()`) on any table where a query bypassing the service layer would leak data across tenants |
| **`enable_rls_ddl()` on a `VARCHAR`/`TEXT` tenant column** | Every migration using the policy aborts with `operator does not exist: character varying = uuid` — this is exactly what made `varco_sa.rls_framework.framework_rls_upgrade()` inapplicable before its fix | `enable_rls_ddl()`'s `cast_type` defaults to `"uuid"`, matching a real `UUID` tenant column; a `String`/`VARCHAR` column needs the GUC cast to match | Pass `cast_type="text"` (`enable_rls_ddl(..., cast_type="text")`); `framework_rls_upgrade()` already does this for the two framework tables, whose `tenant_id` is `String(255)` |
| **RLS test/connection uses a superuser role** | RLS policies appear to do nothing — every row is visible regardless of the tenant GUC — even though `pg_class.relforcerowsecurity` is `True` and the policy is correctly applied | `FORCE ROW LEVEL SECURITY` only revokes the *table-owner* exemption; `rolbypassrls`/superuser connections bypass RLS **unconditionally**, `FORCE` or not — this is a hard Postgres rule, not a varco gap | Connect (and write RLS tests) as a dedicated non-superuser, non-`BYPASSRLS` application role — see `varco_sa/tests/test_rls.py`/`test_framework_rls.py`'s fixture |
| **`mode="upgrade"` in a large multi-pod deployment** | Rolling deploys crawl; pods log `MigrationLockTimeout`, exit, and get restarted before eventually serving | Every replica races for one migration lock — the leader migrates while the rest burn `lock_timeout`, re-plan, and (if the leader is still working) raise | Deploy `VARCO_MIGRATE_MODE=check` on the pods and run `varco migrate upgrade` in a pre-deploy job / init container. `upgrade` is the small-deployment and dev convenience, not the production posture |
| **`ensure_table()` and migrations both active** | Alembic's `CREATE TABLE` fails against a table `ensure_table()` already created | The two mechanisms are mutually exclusive *per deployment*, and the hazard is directional (`create_all(checkfirst=True)` against an Alembic table is a harmless no-op; the reverse is not) | Run `varco migrate adopt` (or `AlembicMigrator.adopt_framework_tables()`) **once**, then pick one mechanism. Order matters: adopt, then upgrade |
| **`upgrade head` (singular) with the framework branch present** | Only the app's tables (or only the framework's) get migrated; the other branch silently stays behind | `varco_sa` ships its own Alembic branch (`branch_labels=("varco",)`) in the wheel, so there are **two** heads | Always `heads` (plural) — every varco command defaults to it. Or opt out with `include_framework_branch=False` + `get_target_metadata(..., include_framework=True)` |
| **`index_mode="create"` on a large Mongo collection** | Pod startup blocks for minutes-to-hours; on a replica set the build replicates and can stall secondaries | An index build is real work, and `create` runs it inside the ASGI lifespan at exactly the moment a rolling deploy starts N new pods | Leave `index_mode="check"` (the default, independent of `mode`) and run `varco migrate index -t … --create` as a pre-deploy job |
| **`VARCO_MIGRATE_MODE` set but no `migrations=` passed** | Env var is set, nothing migrates, no error | `create_varco_app` has no migrator to run — it now logs one WARNING naming the env var rather than staying silent | Pass `migrations=<AbstractMigrator>` to `create_varco_app` |
| **RLS enabled by a startup hook** | Policies appear/disappear depending on which process booted last; unreviewed DDL in production | RLS is schema DDL that must be ordered after table creation and reviewed like any other change | Put it in a reviewed revision with `varco_sa.migration.ops.rls_upgrade(op, "orders")` / `rls_downgrade`. Nothing in varco auto-enables RLS, and no `VARCO_MIGRATE_MODE` value does either |
| **`from varco_core import MigrationError`** gets the wrong class | `except MigrationError` never catches a schema-migration failure | `varco_core.migrator` (domain data/field migration) already owns the top-level `MigrationError`/`MigrationPlan` names, so the schema-migration versions are deliberately **not** re-exported | `from varco_core.migration import MigrationError, MigrationPlan`. Every other migration name (`AbstractMigrator`, `Revision`, `MigrationReport`, `MigrationSettings`, `InMemoryMigrator`, the other exceptions) *is* on `varco_core` |
| **Raw `text()` SQL under `TenantIsolation.SCHEMA`** | A hand-written query returns rows from the wrong tenant's schema, or errors on a missing table | `schema_translate_map` rewrites schema references at SQL-compile time — it never touches raw `text()` SQL | Self-qualify the schema in the raw SQL, or route the query through the ORM so it carries the symbolic `"tenant"` token |
| **`SET` used instead of `SET LOCAL`/`set_config(..., true)` for schema routing** | Under a transaction-mode pooler, one tenant's schema routing leaks into the next logical caller's session | Session-scoped `SET` survives past the transaction on a pooled connection — same defect class as `SAAdvisoryLock`'s U-16 finding | Use `SASchemaRouter`'s default `mechanism="translate_map"`; if you must use the `"search_path"` escape hatch, it already emits `set_config(..., true)`, never a bare `SET` |
| **Per-tenant engine/binding never `dispose()`d** | Connections/clients accumulate until the pool or the process runs out | A caller evicts a tenant outside `TenantResourcePool`/`SAEngineRegistry`/`BeanieTenantPool`, bypassing their `closer` | Always go through the pool's `evict()`/`aclose()` — never hold a raw engine/client reference past eviction |
| **Unbounded per-tenant pool** | Connection exhaustion under `TenantIsolation.DATABASE` at even moderate tenant counts | `max_entries × (pool_size + max_overflow) × n_pods` was never checked against `max_connections` | Follow the sizing worksheet in `technical_docs/features/multitenancy.md`; varco enforces no cap (RD-5) — this is an operator responsibility |
| **`init_beanie()` rebinds every tenant to one database** | All Mongo tenants silently read the same database, no error | A second `init_beanie()` call with a different database rebinds the Document **class** globally — `BeanieDocRegistry` is process-global, keyed by domain class | Always go through `varco_beanie.tenancy.binding.build_tenant_binding()`, which clones each Document class per tenant instead of calling `init_beanie()` against the shared base classes |
| **`BeanieDocRegistry.get(User)` expected to return a tenant's clone** | A repository built from `BeanieDocRegistry.get(User)` silently operates on the wrong (base) database | Clones are deliberately never registered in `BeanieDocRegistry` — that registry's contract is "return the base class" | Use `binding.clone_for(User)` from the tenant's `BeanieTenantBinding`, not `BeanieDocRegistry.get(User)` |
| **`varco migrate upgrade` without `--all-tenants` under db-per-tenant** | Only the global/framework schema advances; every tenant is left N revisions behind with no error | The default target is the single, non-fanned-out migrator | Use `varco migrate upgrade --all-tenants` (or `--tenant <id>` for one), which runs global-then-fan-out via `TenantFanoutMigrator` |
| **Global migration run after the tenant fan-out** | Tenant-table foreign keys to global tables fail mid-migration | `TenantFanoutMigrator` orders the global/framework run before every tenant's specifically to prevent this | Never pass `--skip-global` unless you have already run the global migration separately and know it is current |
| **`TenantIsolation.DATABASE` without `fanout_framework_tables`** | Outbox/job/audit rows accumulate in each tenant's database and are never published/claimed/persisted | The process-wide `OutboxRelay`/`JobPoller`/`AuditConsumer` only polls the app's own (non-tenant) database | `guard_fanout_configuration()` raises at construction naming `VARCO_TENANCY_FANOUT_FRAMEWORK_TABLES` — set it, which wires `TenantFanoutSupervisor` |
| **`TenantIsolation.SCHEMA` on `varco_beanie`** | Expecting a Mongo equivalent of Postgres schemas | MongoDB has no schema-per-tenant primitive | `BeanieTenantPool` raises `ValueError` at construction naming MongoDB — use `SHARED` or `DATABASE` instead |
| **`TENANT`-scoped cache key built outside `tenant_context()`** | Expecting a graceful unnamespaced fallback | `tenancy_cache_key()` fails closed by design — an unnamespaced `TENANT` key is a cross-tenant leak waiting to happen | Wrap the call in `with tenant_context(tenant_id): ...`, or catch the `RuntimeError` and treat it as a real bug, not something to paper over |
| **`GLOBAL`-scoped cache key namespaced by tenant** | N× cache waste and N× DB load for reference data every tenant reads identically | A hand-rolled cache key included `tenant_id` for an entity that doesn't need it | Use `tenancy_cache_key(entity_cls, key)` — it detects `TenantScope.GLOBAL` via `is_global_entity()` and omits the tenant segment automatically |
| **`TenantAwareService` mixed into a `GLOBAL`-entity service** | `_scoped_params` filters on a `tenant_id` field that doesn't exist on the entity — an error at best, silently empty results at worst | The service's MRO includes `TenantAwareService` while its entity is declared `TenantScope.GLOBAL` | `validate_service_scope()` raises `TenantIsolationError` at wiring time naming both the service and the entity — drop the mixin |
| **Expecting one transaction across a tenant DB and the global DB** | `AttributeError`/design confusion trying to share a UoW across `Inject[IUoWProvider]` and `Inject[GlobalUoWProvider]` | Under `TenantIsolation.DATABASE` these are genuinely two different physical databases — there is no 2PC | Sequence two transactions, or route the atomic-looking write through the transactional outbox/saga primitives instead |
| **Global write raises `GlobalScopeReadOnlyError`** | A write to a `GLOBAL`-scoped entity from an app pod fails with SQLSTATE `42501` translated into a legible error | The app-facing global credential is read-only by default (RD-10) | Route the write through the tenant control plane, or opt in explicitly with `TenancySettings(global_writable=True)` / `VARCO_TENANCY_GLOBAL_WRITABLE=true` |
| **Literal DSN stored in `varco_tenants`** | `ValueError` on `catalog.add()` naming RD-2 | `dsn_ref` must be a secret **reference** (resolved by your own secret-manager hook), never a literal connection string | Store a reference, not a credential; pass `allow_literal_dsn=True` only for tests/bootstrap |
| **Admin DSN present in an app pod that never mounted the admin surface** | Nothing is actually exposed — but the credential sits unused in the wrong process | `VARCO_TENANCY_ADMIN_DSN` alone grants no route; only `mount_tenant_admin()` mounts one | One WARNING is logged recommending the standalone topology; prefer moving the DSN to a dedicated control-plane deployment |
| **`mount_tenant_admin()` without `acknowledge_bundled_admin=True`** | `ValueError` at mount time, nothing mounted | The friction is intentional — bundling puts admin-adjacent privilege in the app pod's own environment | Pass `acknowledge_bundled_admin=True` only after confirming the standalone deployment genuinely isn't justified |
| **Bundled admin router left ungated at the ingress** | The `/tenancy/*` admin surface is reachable from wherever the app itself is reachable | Role-guarding (`admin_role="tenant-admin"`) is an application-layer control, not a network one | Use the dedicated `prefix=` to deny it at the ingress, and/or pass `dependencies=[Depends(ip_allowlist)]`/an mTLS check |
| **Redelivered `TenantProvisionRequested` assumed unique** | Worry about double-provisioning on broker redelivery | `provision()` is idempotent (status is the check) and the consumer additionally dedups same-process redelivery by `event_id`; compose with the durable inbox for cross-restart idempotency | No action needed for the common case — redelivery is a documented, tested no-op, not a hazard to work around manually |
| **Bus-onboarded tenant 404s** | A tenant provisioned purely via `TenantProvisionRequested` is unroutable forever, even though its schema/database was created | Pre-Plan-008: the consumer called the provisioner directly, never wrote the catalog row `routing.py`/`TenantResolutionMiddleware` look up | Upgrade to a `control_service=`-based `TenantProvisionConsumer` (Plan 008), then repair each affected tenant with one idempotent `POST /tenancy/tenants` — re-running `provision()` finds the missing catalog row, adds it, and the provisioner's own idempotency means no duplicate/destructive DDL runs |
| **Consumer constructed with `provisioner=`** | `ValueError` at construction (bare `provisioner=`), or a `DeprecationWarning` you're ignoring (`provisioner=`+`catalog=`) | `provisioner=` is a Plan 008 (RD-12) deprecated shim scheduled for removal one minor release after landing | Pass `control_service=TenantControlService(catalog=..., provisioner=..., producer=...)` directly instead |
| **Bundled node called only `request_provision()` and never provisioned itself** | The broadcasting node's own local storage was never created — it relies on some other node's DDL that never runs there | `request_provision()`/`request_deprovision()` are broadcast-only by design (RD-14) — they deliberately exclude the caller | Call `provision()` **first** (local, synchronous), then `request_provision()` (broadcast) — this ordering also surfaces a local DDL failure before the fleet is told |
| **Missing store in `expected_stores`** | A tenant activates one store early — traffic is routed to a store that never got its own DDL | A service was added without updating the coordinator's `expected_stores` set | Update `expected_stores` when adding a service to the fleet; the coordinator logs the full expected/seen set at every partial step and on every unexpected-`store_id` WARNING to make the drift visible |
| **Counting pods instead of stores** | Expecting `expected_stores` to change on every autoscale event, or sizing it by pod count | The readiness unit is a **store** (RD-17), not a pod — ten pods of one service provision the same database, so nine of their `TenantNodeReady` reports are idempotent no-ops | Size `expected_stores` to the number of distinct services/databases, never to pod count; it changes only when a service is added or removed |
| **Expecting readiness to survive a restart** | A coordinator restart appears to "forget" tenants mid-onboarding — `GET …/readiness` answers 404 (`readiness()` raises `TenantNotFoundError` for a tenant it has not observed since the restart) | Readiness state is in-memory only (RD-18) — no durable/persisted readiness contract exists. The 404 is about the *coordinator's* state, not the catalog's — check `GET /tenancy/tenants?status=pending` for tenant existence | Re-broadcast `request_provision(tenant_id)` — every downstream layer is already idempotent, so this is the documented one-verb recovery, not data loss |
| **`list_entries(tenant_id="acme")` misses a framework-level dead letter** | An entry produced outside any tenant context (e.g. a boot-time outbox deserialize failure) never shows up under any explicit `tenant_id=` filter | `DeadLetterEntry.tenant_id=None` is deliberately never matched by `tenant_id="acme"` — a `None` tenant is not "every tenant" (Plan 009, RD-4/R4) | Use no `tenant_id` filter at all for the operator/global view; a `None`-tenant entry is correct, expected behaviour, not a bug |
| **`redrive(entry_id)` called on Kafka/NATS** | `DeadLetterNotAddressable` | Stream-backed stores cannot address a single message by id — `supports_random_access=False` (RD-4) | Use `redrive_batch()` / the CLI's `--batch` flag, which work on every backend |
| **`client_for()`'s custom `@route` method assumed typed/strict** | A wrong kwarg silently passes through instead of raising `TypeError`, unlike a `gen-client`-generated client for the same router | `_VarcoClientMeta` (the metaclass behind `client_for()`) was deliberately NOT rewired onto `build_client_method` in Plan 009 Phase 7 (high-blast-radius deferral) — only `contract_client()`/`gen-client` go through it | Don't assume parity between `client_for()` and a cross-repo generated client for custom routes; generate a typed client (`varco gen-client`) if you need the strict signature today — see `technical_docs/features/portable-contracts.md`'s status note |
| **`mount_reliability_admin()` without `acknowledge_bundled_admin=True`** | `ValueError` at mount time, nothing mounted | This surface can replay bus messages and delete audit/DLQ records — at least as privileged as the tenant control plane (RD-9) | Pass it only after confirming a standalone deployment isn't justified — same rule as `mount_tenant_admin()` |
| **Per-call breaker for a peer service** | Circuit never opens for a flaky peer | Building a fresh `PeerRegistry`/`CircuitBreaker` per request instead of reusing a singleton registry | Construct `PeerRegistry` once (module scope or a DI singleton via `bind_peers`); it caches one `CircuitBreaker` per peer *name*, never per call |
| **`ReliabilityPreset(outbox_max_attempts=...)` without `dlq`** | `ValueError` at construction | Mirrors `OutboxRelay`'s own refusal — deleting a poison entry with nowhere durable to put it is silent data loss | Pass a `dlq=` alongside `outbox_max_attempts` |
| **Per-call `Singleflight`** | Concurrent misses never coalesce — the loader runs once per caller, same as before | A fresh `Singleflight()` has an empty in-flight dict every call — same defect class as a per-call `CircuitBreaker`/`Bulkhead` | `@cached` creates one `Singleflight` per decorated function at decoration time; `CacheServiceMixin` creates one per service instance (lazily, on first use) — never construct one inside a request handler |
| **Coalescing on a pre-tenant-namespaced key** | Cross-tenant data leak — two tenants' concurrent misses share one recompute and one result | `Singleflight`/`read_through()` never build or namespace keys themselves; a caller that coalesces on the raw pk instead of the final `tenant:{id}:`-prefixed key defeats tenant isolation | Always pass the final, already-namespaced cache key (the one `tenancy_cache_key()`/`CacheServiceMixin._cache_key()` produced) to `Singleflight.do()`/`read_through()` — guarded by `varco_core/tests/test_cache_singleflight_tenancy.py` |
| **Cache metrics never appear** | Dashboards for `varco.cache.*` stay empty even though the cache is being hit | `install_cache_metrics()` (`varco_core.observability.cache`) was never called — same rule as `install_reliability_metrics()`: a manual install function, deliberately not a scanned `@Configuration` | Call `install_cache_metrics()` once at startup |
| **`LayeredCache` in multi-pod without a backplane** | Each pod's L1 silently serves stale entries after another pod's write/delete — the shipped bug Plan 010 / C1 closes | No `backplane=` wired — the default `LayeredCache(l1, l2, promote_ttl=...)` has no cross-node invalidation channel | Wire `backplane=RedisPubSubBackplane()` (`varco_redis.backplane`) for any `LayeredCache` shared across more than one process |
| **`LayeredCache(backplane=..., promote_ttl=None)`** | `ValueError` at construction | A Pub/Sub backplane is best-effort (message loss on subscriber disconnect); an unbounded L1 TTL behind it means a missed invalidation has no bound on how long it can serve stale data | Pass a `promote_ttl=` alongside `backplane=` — mirrors `OutboxRelay(max_attempts=...)` refusing to run without a `dlq=` |
| **Per-call `RedisPubSubBackplane`** | Invalidations never propagate — each instance has its own listener/subscription state | Same shared-instance rule as `CircuitBreaker`/`Bulkhead`/`Singleflight` | Construct one `RedisPubSubBackplane` and pass it into every `LayeredCache` that must share coherence; let `LayeredCache.start()`/`stop()` drive its lifecycle, never call `start()`/`stop()` directly |
| **Backplane key names visible fleet-wide** | Under a per-tenant-pod topology (`SCHEMA`/`DATABASE` isolation), every subscriber learns which tenant touched which entity id | The default `RedisPubSubBackplane` publishes one plaintext channel with raw key names (`tenant:{id}:Entity:pk`) | Use `channel_for=` (subscribe only to hosted tenants) or `hash_keys=True` (publish a key hash — degrades `delete_prefix()` invalidation to a local `clear` on receivers, documented not silent) |
| **`soft_ttl >= ttl`** | `ValueError` at `CachePolicy` construction | A soft TTL at or beyond the hard TTL can never fire — the SWR window would be dead code | Set `soft_ttl` strictly less than `ttl` |
| **Enabling envelope mode mid-rolling-deploy** | An **old** pod (or a pod whose policy doesn't set `soft_ttl`/`negative_ttl`/`stale_if_error`) reads a **new** pod's envelope and returns the raw `{"__varco_cache__": 1, ...}` wrapper dict to the application instead of the unwrapped value | `CacheEnvelope` is only tolerant on read in the safe direction (new pod reading old pod's legacy value) — the reverse direction is unsafe by design (D-5) | Roll out the new varco version to every pod with envelope-requiring policy fields off first, then turn them on — see the two-step deploy recipe in `technical_docs/features/cache-hardening.md` |
| **Negative caching hiding a fixed row** | A "not found" response keeps being served long after the underlying row was created | `negative_ttl` was set longer than the operational fix loop for the missing row | Keep `negative_ttl` short (shorter than `ttl`), or invalidate explicitly (`cache.delete(key)`) when the row is created |
| **Error body gained `message_key`/`params` after upgrade** | An exact-equality assertion on an error response body fails after a version bump | Plan 011 / D-4 — the one deliberate wire delta: built-in varco exceptions now emit `message_key` (`varco.error.not_found`) and non-empty `params` as extension members. An out-of-tree `ServiceException` with no `message_key` is unaffected | Assert on the keys you care about instead of the whole dict, or restore the exact pre-plan body with `VARCO_ERROR_INCLUDE_MESSAGE_KEY=false` / `VARCO_ERROR_INCLUDE_PARAMS=false` |
| **`tenant_id` expected in `RequestContext`** | `AttributeError`, or two disagreeing answers to "who is the tenant" | `RequestContext` deliberately holds only `locale`/`timezone`/`extras` (Plan 011 / D-6) — `TenantAwareService`, RLS, `tenancy_cache_key()`, the DLQ stamp and the audit trail all read `current_tenant()`, and a second source of truth is how they diverge | Call `current_tenant()`; compose by *ordering* (`LocalizationMiddleware` is the innermost built-in layer, so any app-supplied `TenantResolutionMiddleware` via `extra_middleware=` always dispatches first), never by containment |
| **Localized response cached and served to the wrong locale** | A `fr` body is returned to an `en` client | The cache key did not mention the locale — the i18n analogue of the cross-tenant cache leak, and easier to hit because localization is applied at render time, far from the cache call | Cache the **unlocalized** representation and localize at render time; where the cached artifact is itself localized, build the key with `localization_cache_key(base, locale=True)`, which fails closed (`RuntimeError`) with no ambient locale, exactly like `tenancy_cache_key()` |
| **Error response not localized although i18n is enabled** | A 404/500 body is in English (and has no `Content-Language` header) despite I18n being enabled and `?lang=fr` set | `create_varco_app()` only wires `message_catalog=` into the error paths when a `MessageCatalog` was actually resolved (`i18n.enabled=True` **and** a container was passed) — with no catalog bound, both `_make_error_response()`/`add_exception_handlers()` and `ErrorMiddleware` are byte-identical to before this fix: `message_key`/`params` still appear, but `message` stays `default_message` | Confirm `create_varco_app(container=..., i18n=I18nSettings(enabled=True))` and that a `MessageCatalog` (e.g. `GettextMessageCatalog`) is actually bound in the container; if you built a custom exception handler yourself, pass `message_catalog=`/`set_content_language=` explicitly — see `technical_docs/features/error-taxonomy-and-i18n.md`'s `message_resolver` section |
| **`enqueue(tz=...)` raises `ValueError` naming the store class** | A zoned-schedule `enqueue()` call fails at the store name instead of scheduling the job | Plan 011 / RD-5's `_prepare_zoned_job()` guard, now wired into the shipped `varco_fastapi.job.runner.JobRunner.enqueue()`, refuses a zoned schedule (`run_at_wall=`/`tz=`) targeting a store whose `supports_zoned_schedules` is `False` (the default) | Use a store that opts in (`SAJobStore`, `BeanieJobStore`, the in-memory store), or add the three columns/fields to a custom store and set `supports_zoned_schedules = True` |
| **`assume="utc"` breaks a working datetime filter** | `asyncpg` raises on a query that worked before the policy was changed | asyncpg rejects an **aware** datetime against a `TIMESTAMP WITHOUT TIME ZONE` column — which is exactly why `"naive"` (today's behaviour) is the default and `"utc"` is only the *recommendation* (Plan 011 / D-10) | Migrate the column to `TIMESTAMPTZ`, or leave the policy at `"naive"` and have clients send an explicit offset (`2026-01-01T00:00:00Z`) — an explicit offset wins under every policy |
| **`DatetimeCoercionPolicy(assume="utc")` has no effect on a `?field__gte=` filter** | The naive-string bound is still returned naive despite a policy being configured | `ASTTypeCoercion` (the visitor `QueryTransformer` drives) has no `policy=` parameter — only the free function `coerce_datetime(value, policy=...)` honours it | Call `coerce_datetime(value, policy=my_policy)` directly, or register a field-specific coercer via `TypeCoercionRegistry.register_field()` with `functools.partial(coerce_datetime, policy=my_policy)` |
| **`?lang=xx` silently ignored** | No 400, the response comes back in the fallback locale | `xx` is not in `I18nSettings.supported_locales` — by design, an unsupported explicit override falls through to the next precedence source rather than erroring | Add the locale to `supported_locales`, or expect the fallthrough — this is deliberate, not a bug |
| **`Content-Language` header missing** | I18n appears to do nothing on an otherwise-working response | `I18nSettings.enabled=False` (the default), or `set_content_language=False` | Set `VARCO_I18N_ENABLED=true` (and check `set_content_language`) |
| **tzdata absent in a slim image** | `ValueError` at `TimezoneSettings`/`I18nSettings`-adjacent startup naming a zone that "could not be resolved" | `python:*-slim`/distroless/Alpine images often ship no `/usr/share/zoneinfo` | `pip install tzdata` or `pip install "varco-core[tz]"` |
| **Adding a bulk method directly to `AsyncCache`** | `isinstance(third_party_cache, AsyncCache)` silently starts returning `False` for out-of-tree caches | `AsyncCache` is `runtime_checkable` — `isinstance()` tests method presence, so any new method changes what satisfies it | Add to `BulkCache` instead (Plan 011 / D-11) — `AsyncCache` stays byte-for-byte unchanged |

---

## Decision Tree: What to Implement Where?

```
Am I adding a new capability?
├─ Event system feature (new event type, new consumer pattern)?
│  └─ → varco_core.event (protocol) + varco_kafka/redis (backend)
│
├─ Cache feature (new invalidation strategy, new backend)?
│  └─ → varco_core.cache (ABC) + varco_redis/sa (impl)
│     ↳ a bulk/batch capability? → BulkCache with a portable CacheBackend
│       default, NEVER a new method on AsyncCache (breaks isinstance() for
│       out-of-tree caches, Plan 011 D-11)
│
├─ Query filtering (new comparison operator, new visitor)?
│  └─ → varco_core.query (parser + visitor) + varco_core.query.applicator.sqlalchemy
│
├─ Request-scoped ambient value (locale, timezone, anything else per-request)?
│  └─ → varco_core.context (AmbientVar + RequestContext + resolve_precedence)
│     ↳ tenant? → NO, use current_tenant() — never add it to RequestContext
│     ↳ HTTP resolution? → varco_fastapi.middleware.LocalizationMiddleware
│       (one middleware, two independent toggles — RD-3)
│
├─ Internationalization / localized output?
│  └─ → varco_core.i18n (MessageCatalog ABC + negotiation)
│     ↳ a new catalog format (ICU, MF2, Fluent)? → implement the ABC, do
│       NOT add a runtime dependency to varco_core
│     ↳ translatable entity data? → app side, a Non-goal (RD-7)
│
├─ Timezone / scheduling?
│  └─ → varco_core.tz
│     ↳ per-request user zone? → tz/resolve.py
│     ↳ DST-safe one-shot schedule? → tz/schedule.py + the three Job
│       columns (D-7)
│     ↳ recurring/RRULE? → Non-goal — a future Schedule entity that
│       produces Job rows exactly like these
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
├─ Migration / schema-upgrade feature?
│  └─ → varco_core.migration (AbstractMigrator contract + MigrationSettings)
│       + the backend migrator (varco_sa.migration.AlembicMigrator /
│         varco_beanie.migration.BeanieMigrator)
│       ↳ Startup wiring? → varco_fastapi.migrate.MigrationLifecycle
│       ↳ New CLI verb?   → varco_core.cli.migrate (shared verbs) or the backend's
│                            own migration/cli.py via the "varco.commands" group
│       ↳ New framework table? → register_framework_metadata() in its owning module
│                                 + a revision in varco_sa/migrations/versions/
│
├─ Multitenancy / isolation-strategy feature?
│  └─ → varco_core.tenancy (contracts: TenantIsolation/TenantScope/TenancySettings,
│         AbstractTenantCatalog, TenantResourcePool, DynamicTenantUoWProvider,
│         GlobalUoWProvider, AbstractTenantProvisioner, TenantFanoutSupervisor)
│       + the backend implementation (varco_sa.tenancy.SASchemaRouter/SAEngineRegistry/
│         SATenantCatalog / varco_beanie.tenancy.BeanieTenantPool/BeanieTenantCatalog)
│       ↳ Startup wiring? → varco_fastapi.tenancy.TenancyLifecycle +
│                            create_varco_app(tenancy=...)
│       ↳ Request-scoped tenant resolution? → varco_fastapi.middleware.
│                            TenantResolutionMiddleware
│       ↳ Admin/provisioning surface? → varco_fastapi.tenancy.mount_tenant_admin()
│                            (never a create_varco_app kwarg — RD-9)
│       ↳ New global/shared entity? → Meta.tenant_scope = TenantScope.GLOBAL,
│                            never a new mixin (validate_service_scope() guards it)
│
├─ Cross-repo service integration (calling a peer whose Python package is
│  not importable from this repo)?
│  └─ → varco_fastapi.contract (ServiceContract, build_contract, `varco
│         export-contract`) + varco_fastapi.client.method
│         (build_client_method, ImportedTypeResolver/SynthesizedTypeResolver)
│       ↳ Runtime, no generated file? → varco_fastapi.contract.runtime.contract_client
│       ↳ Checked-in typed client module? → `varco gen-client` /
│                            varco_fastapi.contract.codegen.render_client_module
│       ↳ Just IDE/mypy types for an existing client_for() call site?
│                            → `varco gen-client-stubs [--check]`
│       ↳ Fleet of peers, one env var each? → varco_fastapi.client.peer.PeerRegistry
│                            + bind_peers()
│       ⚠️ NOT client_for()'s custom-route methods — those are not wired
│         through build_client_method yet (see the pitfall table)
│
├─ Reliability feature (DLQ redrive/retention, audit retention/tamper
│  evidence, "opt into durability once")?
│  └─ → varco_core.event.redrive (DlqRedriver) / varco_core.event.dlq
│         (delete/delete_where/count_by_channel) / varco_core.service.audit
│         (list/delete_where/verify_chain) for the primitives
│       ↳ Bundling retry+DLQ+outbox+audit+metrics behind one object?
│                            → varco_core.reliability.ReliabilityPreset
│       ↳ FastAPI startup wiring? → varco_fastapi.reliability.ReliabilityLifecycle
│                            + create_varco_app(reliability=...)
│       ↳ REST admin/query surface? → varco_fastapi.admin.mount_reliability_admin()
│                            (never a create_varco_app kwarg — RD-9, same rule
│                            as mount_tenant_admin())
│       ↳ New CLI verb? → varco_core.cli.dlq / varco_core.cli.retention
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
