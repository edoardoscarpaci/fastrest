# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Quick reference**: [ARCHITECTURE.md](ARCHITECTURE.md) — package map, dependency graph, and
type hierarchies (navigate the codebase without reading files one-by-one). [README.md](README.md)
— runnable usage snippets and env-var reference tables for every subsystem.
[technical_docs/features/](technical_docs/features/) — per-feature design rationale and
operator Pitfalls tables.

---

## Commands

All commands run from the **workspace root** (`/home/edoardo/projects/varco`) using a single shared virtual environment managed by `uv`.

```bash
# Install everything (all workspace members + dev deps, including ruff/mypy — see below)
uv sync --all-packages --all-extras

# One-time, per clone: makes `git blame` skip the mechanical ruff sweep commits (Plan 017 / RL-6)
git config blame.ignoreRevsFile .git-blame-ignore-revs

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
every package in one call — `make lint` (`ruff check` **+** `ruff format --check` — the
formatter is a CI gate as of Plan 020 / RL-17, adopted at zero `.py` churn), `make format` (`ruff
format` + `ruff check --fix`), `make type-check` (`mypy`), `make test` (now delegates to `scripts/unit_tests.sh`
— runs **all eleven** workspace suites (ten packages + `examples/00-full-stack-post-api`) and
*accumulates* pass/fail/skip into one summary instead of aborting at the first red package; `make
test PKG=varco_redis` narrows to one), `make integration-test` / `make integration-test-clean`,
`make build`, `make publish`, `make docs` / `make docs-serve`. Run `make help` for the full list.
`pytest-asyncio` is installed with `asyncio_mode = "auto"` in every package — all `async def
test_*` functions run automatically without `@pytest.mark.asyncio`.

`make integration-test` / `make integration-test-clean` now **exclude chaos tests by default**
(Plan 018 / RT7) — `scripts/integration_tests.sh` defaults `MARKER_EXPR` to `"integration and not
chaos"`. Chaos tests (`@pytest.mark.chaos`, always paired with `@pytest.mark.integration`)
kill/pause/restart a real container mid-test and are strictly noisier and slower than a plain
broker round-trip. Run them with `make chaos-test` / `make chaos-test PKG=varco_redis` /
`make chaos-test-clean` — the same script with `MARKER_EXPR="integration and chaos"`, and the
same six-`VARCO_TEST_*_URL`-unset clean-room contract `integration-test-clean` uses.

`ruff` and `mypy` are **pinned dev dependencies**, declared in the root `pyproject.toml`'s
`[dependency-groups] lint` group (`ruff==0.16.4`, `mypy==2.3.1`) and pulled in by the `dev` group
via PEP 735 `{ include-group = "lint" }`. `make lint`/`make type-check` invoke them as `uv run
ruff`/`uv run mypy` — resolving the exact pin recorded in `uv.lock`. **Never invoke linting via
`uvx ruff`** — `uvx` resolves whatever the newest ruff release is at the moment you run it, which
can silently diverge from the pin CI enforces (see the Common Pitfalls table).

**mypy strictness ramp — complete** (Plan 020 / RL-14 → Plan 021, RL-14/RL-14b/RL-14c/RL-14d all
CLOSED; full design: `plans/021-mypy-strict-full-ramp.md`). `[tool.mypy]` is `strict = true`
(mypy 2.3.1's 13-flag bundle — see the `pyproject.toml` comment block for the exact enumeration,
sourced from `mypy --help`'s own `--strict` description, not brief prose) plus
`disallow_any_unimported = true`, landed separately because it is not part of `--strict`. The
`[[tool.mypy.overrides]]` section is empty — `check_untyped_defs` was hoisted from ten per-package
override blocks to one global flag (Plan 021 Phase 1). Two metrics, two different roles, both
still meaningful post-ramp: **M1** (`rg -o 'type: ignore' varco_*/varco_* | wc -l`) is a
directional suppression-debt gauge only, never a gate; **M2** (`uv run mypy <ten dirs>`) is the
actual gate — CI's `lint` job running plain `mypy` (no flags — the ten dirs' own `[tool.mypy]`
already carries `strict`) now **is** M2, trivially.

`disallow_any_expr` is the **one permanent exclusion** — confirmed not part of `--strict` in mypy
2.3.1 (research brief 004 §1), and untargeted by any brief's "Skip these entirely" reversal
evidence. `warn_unreachable` is also not in `--strict` and stays out, unmeasured, out of scope.
Every other flag once filed as "Stopped"/"Decided never"/"Out of scope" (`disallow_any_generics`,
`warn_return_any`, `disallow_untyped_calls`, `disallow_any_unimported`, `disallow_incomplete_defs`,
`disallow_untyped_defs`) was re-opened on a fresh measurement (research briefs 004/005) and landed
— see Plan 021 §D1–§D6 for the full per-flag remediation pattern and U-8 evidence discipline
applied to each re-opened "never" verdict.

### Public API surface snapshot (`scripts/api_surface.py`)

`scripts/api_surface.py` (Plan 022 / Phase 0, §D-AUDIT) records the public API surface — for every
name in each distribution package's top-level `__all__`, its kind (`class`/`function`/`constant`),
defining module, and (functions only) `inspect.signature()`. It derives its package list by
*executing* `scripts/packages.sh`, so it structurally cannot drift the way a hand-written list
would (Plan 020 / RL-18).

```bash
uv run python scripts/api_surface.py          # regenerate the committed snapshot
uv run python scripts/api_surface.py --check  # diff live tree vs. snapshot; exit 1 on a break
```

Outputs `design/api-freeze-and-standards/measurements/api-surface.json` (machine-readable, what
`--check` diffs) and a sibling `.md` (sorted, human-diffable in a PR). `--snapshot PATH` and
`--packages PKG…` narrow the run.

⚠️ **This is not a gate today.** `--check` is deliberately *not* wired into `make lint` or any CI
job — the plan schedules that for Phase 7 (§D-AA4's snapshot-plus-`--check` precedent). Until then
it is a tool a contributor must know to re-run **by hand** after touching any `__all__` or any
exported function's signature, and to commit the regenerated snapshot alongside the change.

⚠️ **Known limitation, deliberate:** class signatures are **not** recorded. They are synthesised
from `__init__`/`__new__` and, for pydantic models and dataclasses, from generated code whose
rendering is not guaranteed identical across the 3.12/3.13 unit-test matrix — a snapshot that
differed by interpreter would make `--check` unrunnable in CI. So `--check` catches **removals and
*function* signature changes only**; a narrowed class `__init__` is invisible to it. (Relatedly,
heap addresses in sentinel default values are stripped, or every run would report a spurious
change — see `_ADDRESS_RE`.) Additions and module moves are reported as notes and never fail.

### CI

Two GitHub Actions workflows gate `main` (`.github/workflows/`):

- **`test.yml`** — runs on every push/PR to `main`. Three jobs: `lint` (`ruff check .` + `mypy`
  over the ten source dirs, Python 3.12 only — mypy is version-independent given a pinned
  `python_version` in `[tool.mypy]`), `unit` (matrix `[3.12, 3.13]`, `fail-fast: false`, runs
  `scripts/unit_tests.sh`), and `all-green` (`needs: [lint, unit]`, `if: always()`, asserts both
  results are literally `'success'` — a *skipped* leg fails it too). **`all-green` is the only
  required status check** — never select an individual matrix leg in branch protection, or a
  skipped leg leaves the check permanently pending.
- **`integration.yml`** — `push: [main]` + nightly `schedule` + `workflow_dispatch` only (not on
  PRs). Runs `make integration-test-clean` via testcontainers, no `services:` blocks. Not a
  required check — a nightly failure is a BACKLOG/Phase-3 signal, not a merge blocker.
  A second, independent `chaos` job in the same file (Plan 018 / RT7-ci) runs `make
  chaos-test-clean`, gated `if: github.event_name != 'push'` — chaos never runs on the `push:
  main` trigger, only nightly `schedule` and `workflow_dispatch`, so a chaos flake can never
  appear on the one trigger a human is watching land on `main`. It is not in either job's
  `needs:` and must **never** become a required check, on any schedule — unlike `integration`
  (whose eventual promotion is a possibility after a measured flake rate), `chaos` exists to find
  real bugs under deliberate container failure, not to gate merges.

**Branch protection (repository setting, not in the repo tree — apply manually):** Settings →
Branches → rule for `main` → Require status checks to pass → select **only** `Tests / All tests
passed` (the `all-green` job). This has not yet been applied to this repository as of Plan 017 —
apply it before relying on it.

---

## Architecture

Varco is a **uv workspace monorepo** of ten packages (`pyproject.toml`'s `[tool.uv.workspace]`,
plus the `examples` workspace member). Each package is independently installable from PyPI.
`varco_core` has no sibling dependencies; every other package depends on it. Package roles,
per-module listings, and the dependency graph live in ARCHITECTURE.md's *Package Overview*.

---

## Key Abstractions and Layer Rules

### Event system (varco_core.event)

Three concentric layers — never skip a layer. Type hierarchy: ARCHITECTURE.md's "Event
System". Usage: README's "Consumer — EventConsumer + @listen".

**Rule**: services must never hold or call `AbstractEventBus` directly. They inject `AbstractEventProducer` and call `_produce()` / `_produce_many()`. The only accepted exceptions are `OutboxRelay` (infrastructure), `EventConsumer.register_to()` (wiring-time only), and `DlqRedriver` (`varco_core.event.redrive`, Plan 009 — publishes a dead letter back onto the bus on operator command; it is infrastructure, not application logic, same reasoning as `OutboxRelay`).

**`@listen` is declarative / `register_to` is imperative.** The decorator stores metadata on the function object at class-definition time. No subscription is created until `consumer.register_to(bus)` is called (typically in a `@PostConstruct` method). This separation makes the consumer bus-agnostic and testable.

**`ChannelManager` implementations must satisfy `declare_channel(c)` ⟹ `channel_exists(c)` is `True` until `delete_channel(c)`** — declared-or-present, not "carries data". Enforced by `testkit/varco_conformance/channel_manager.py`, one of **five** conformance modules (Plan 019 / RT2-C — see §Test Conventions' conformance paragraph).

### Service layer (varco_core.service)

`AsyncService[D, PK, C, R, U]` — generic type parameters and the `_get_repo` abstract method:
ARCHITECTURE.md's "Service Layer". Usage: README's "Service Layer".

Authorization is enforced at the service layer (not HTTP), via the injected `AbstractAuthorizer`.

**Mixin composition pattern** — `ValidatorServiceMixin`, `TenantAwareService`, `SoftDeleteService`, `EventConsumer` all compose via MRO. Chain hooks (`_scoped_params`, `_check_entity`, `_prepare_for_create`) with `super()` so every mixin in the chain runs.

### DI wiring (providify)

Each backend package ships a `di.py` with a `bootstrap()` helper that runs `container.scan(...)` to discover its `@Singleton` classes. Some packages also expose an opt-in `@Configuration` for resources that need imperative async setup (e.g. `RedisCacheConfiguration`). The DI module is the only place that knows concrete types — application code always injects interfaces (`AbstractEventBus`, `AsyncRepository[D]`, `IUoWProvider`).

```python
# Typical app bootstrap
container = DIContainer()
container.scan("varco_kafka", recursive=True)  # discovers the Kafka bus @Singletons
container.install(SAModule)
bind_repositories(container, User, Post)
```

### DI wiring verb taxonomy

The DI wiring verbs above (`bootstrap`, `bind_*`, `enable_*`, `mount_*`, `install_*`, plus
`async_bootstrap`) look like one family but are six distinct shapes. This table is the index —
it does not restate any individual function's reasoning; follow the example path to that
function's own docstring for the "why".

| Verb | Shape | Meaning | Example |
|---|---|---|---|
| `bootstrap(container=None, ...)` | sync, returns container or `None` | one per package; wraps `container.scan(pkg)`; returns `None` if providify is absent | `varco_kafka.di.bootstrap` |
| `async_bootstrap(...)` | async, returns container | `bootstrap()` + an `await container.ainstall(SomeConfiguration)` step, only where an async connection must open before the singleton is usable | `varco_redis.di.async_bootstrap(setup_cache=True)`, `varco_memcached.di.async_bootstrap` |
| `bind_*(container, ...)` | sync, mutates container | registers N *typed, per-item* generic bindings unknowable before app startup | `varco_sa.di.bind_repositories`, `varco_fastapi.client.bind_clients_from`, `varco_ws.di.bind_websocket_adapter` |
| `enable_*(container)` | sync, mutates container | flips on an opt-in DI **binding** that would shadow an app default if auto-registered | `varco_casbin.di.enable_policy_authorizer` |
| `mount_*(app, ...)` | sync, mutates the ASGI app | flips on an opt-in privileged **HTTP surface**, always behind an explicit acknowledgement kwarg | `varco_fastapi.tenancy.mount_tenant_admin`, `varco_fastapi.admin.mount_reliability_admin` |
| `install_*(...)` | sync, **container-free**; **two shapes** | ⚠️ one verb, two shapes (Plan 022 / AB-3). **(a)** a process-global side effect (OTel instrument registration), taking no argument at all; **(b)** an ASGI-app mutation, taking and modifying an `app`. Neither takes a container — both are unrelated to `container.install(SomeConfiguration)` | (a) `install_cache_metrics`, `install_reliability_metrics` · (b) `install_middleware_stack`, `install_cors` |

Name collisions this table exists specifically to call out (audited at Plan 022's RL-8
checkpoint — see `design/api-freeze-and-standards/api-break-candidates.md` for each verdict):
- `install_*` in this taxonomy takes **no container** — `container.install(X)` is providify's
  unrelated `@Configuration`-install verb. **AB-3 verdict: `leave-and-document`** — renaming four
  functions to fix a naming *adjacency* this row already resolves is a poor use of the 3.0.0
  window. What the audit did fix is the row itself, which used to claim `install_*` was uniformly
  "a process-global side effect": that is false of `install_middleware_stack` and `install_cors`,
  which take and mutate an ASGI `app`. Do **not** move that pair into the `mount_*` family —
  `mount_*` is "an opt-in privileged HTTP surface, always behind an explicit acknowledgement
  kwarg", which middleware installation is not.
- ~~`enable_rls_ddl()`~~ → **`render_rls_ddl()`** (`varco_sa/varco_sa/rls.py`). **AB-1 verdict:
  `rename+alias`, landed in 3.0.0.** It was never in the `enable_*` family — it is a pure
  DDL-string generator, touches no container, performs no I/O — and `render_*` now says so. The
  old name remains as a deprecated alias until 4.0.0.

Several `bind_*` factories above register a binding whose interface is only known at call time
(a generic alias like `AsyncRepository[User]`, or a plain class captured in a closure) —
providify needs the *real* return type, not the placeholder `from __future__ import
annotations` leaves on a closure. Framework code doing this uses providify's native
`container.provide(Provider(singleton=...)(factory), returns=...)` / `@Provider(returns=...)`
(providify ≥ 2.0.0, Plan 016 / RL-2) — the `returns=` override is applied at
decoration/registration time, so no `factory.__annotations__["return"] = ...` patching is
needed. (Prior to providify 2.0.0 this went through a since-deleted `varco_core`
compat shim — see UPSTREAM-GAPS.md U-20.)

### Resilience (varco_core.resilience)

Standalone decorators composable with any callable — usage: README's "Resilience" section.
Types: ARCHITECTURE.md's "Resilience".

**`CircuitBreaker` must be a shared instance per external dependency** — a per-call instance will never accumulate enough failures to open. Use `@circuit_breaker(config)` for per-function breakers, or `breaker.protect(fn)` for a shared breaker across multiple functions.

`@retry` is also integrated into `@listen` via `retry_policy=` and `dlq=` parameters. The wrapper is built at `register_to()` time (not decoration time) so the resolved channel string and bound `self` are available.

### Dead Letter Queue (varco_core.event.dlq)

`AbstractDeadLetterQueue` is the interface; `InMemoryDeadLetterQueue`/`KafkaDLQ`/`RedisDLQ`/
`SADeadLetterQueue`/`BeanieDeadLetterQueue` are the implementations. Dead letters must never
be silently deleted (no TTL index by default).

**Contract**: `push()` must never raise — the retry wrapper in `_make_retry_wrapper` cannot recover from DLQ failures, and neither can `OutboxRelay` or `JobRunner` (Plan 005 Phase 3/4). Implementations must log errors and swallow them.

Usage: README's "Dead Letter Queue" section. Full detail (redrive policy, retention, tenancy,
REST admin): `technical_docs/features/dead-letter-queues.md`.

### Observability (varco_core.observability)

`@span`/`@counter`/`@histogram` decorators, `TracingServiceMixin`/`TracingRepositoryMixin`,
`Metric`/`register_gauge`, and `OtelConfig`/`OtelConfiguration` provide OpenTelemetry tracing
and metrics, with automatic parameter capture and a process-wide global-attribute registry on
top (both opt-out). Usage: README's "Observability" section. Full design (decision table, PII
section, Kubernetes Downward-API recipe): `technical_docs/features/observability-attributes.md`.

**Rule — Resource attribute vs. global attribute registry**: static process identity
(`k8s.pod.name`, `deployment.environment`, a Helm release) belongs in
`OtelConfig.extra_resource_attrs` (free — exported once per batch, never multiplies metric
series). The global attribute registry is for values not known at bootstrap or that must be
filterable/`group by`-able as a metric **label** — every key in the registry becomes a label on
every metric series it touches.

⚠️ **`TracingServiceMixin`/`TracingRepositoryMixin` do NOT auto-capture `pk`/`dto`/`params`** —
only `@span`-decorated functions and `create_span(..., params=...)` get automatic parameter
capture; CRUD spans only carry global attributes + `SpanConfig.attributes` + `correlation_id`.

### Ambient request context (varco_core.context, Plan 011 / X1)

`AmbientVar[T]` (`context/ambient.py`) is the generic request-scoped ambient-value primitive
`RequestContext`/`resolve_precedence()` build on. Full design narrative:
`technical_docs/features/i18n-and-localization.md`'s "`varco_core.context`" section.

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
read, no `Content-Language` header. `MessageCatalog` (ABC) with three implementations:
`NullMessageCatalog` (DI default, zero I/O), `DictMessageCatalog` (in-memory), `GettextMessageCatalog`
(production default — stdlib `gettext` only, zero new runtime dependency). Full design
(precedence chain, `Accept-Language` negotiation, `TenantDefaultsProvider`):
`technical_docs/features/i18n-and-localization.md`.

**Rule**: `localization_cache_key(base, locale=True)` (`i18n/cache_key.py`) fails closed
(`RuntimeError`) with no ambient locale — locale is never an implicit cache-key component
(RD-6), same rule as `tenancy_cache_key()`.

### Timezones (varco_core.tz, Plan 011 / T1 / T2 / T3)

Off by default (`TimezoneSettings.enabled=False`) — no resolution, `current_timezone()` is `None`,
storage is unaffected. Five-source precedence chain, startup tzdata validation, and RFC 9557
output formatting: `technical_docs/features/timezone-handling.md`.

**Rule**: **varco never changes what it stores** — everything is still written aware-UTC; this
is a rendering/interpretation layer only (`to_user_tz()`, `now_local()`). RFC 9557 (IXDTF) is an
**output-only** format — no parser ships.

### Error taxonomy — `message_key`, `params`, i18n (varco_core.exception, Plan 011 / I1)

Every built-in `ServiceException` carries a `message_key: ClassVar[str | None]` alongside its
existing stable `code`. Full design (D-4 wire delta, `VarcoErrorCodes` alias reasoning, RFC 9457
opt-in, `error_message_for()` wiring): `technical_docs/features/error-taxonomy-and-i18n.md`.

**Rule**: `code` is the machine identifier, `message_key` is the i18n key — a prior docstring
claiming `code` itself was the i18n key was wrong and is corrected.

⚠️ `error_params()` (default `{}`) returns structured interpolation data — treat it as a **new
exfiltration surface**: `ServiceAuthorizationError` deliberately excludes `reason` from its
params, and any override must apply the same scrutiny, never `vars(exc)`.

### Profiling (varco_core.profiling)

Diagnostic CPU + memory profiler, off by default (zero overhead when disabled). Usage
(decorator/context-manager forms, FastAPI middleware, custom-backend registration): README's
"Profiling" section.

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

`AsyncCache[K, V]` is a `runtime_checkable` Protocol; `CacheBackend[K, V]` is the ABC backends
subclass. Hierarchy: ARCHITECTURE.md's "Cache System". Usage: README's "Cache System".

**Rule**: never instantiate `InvalidationStrategy` outside its backend's `start()`/`stop()` lifecycle — it may hold subscriptions or background tasks.

Stampede protection (`Singleflight`, per-process only) and bulk operations (`BulkCache`, a
**separate** Protocol from `AsyncCache` — see the pitfall table) are covered in
`technical_docs/features/cache-hardening.md`.

### Query system (varco_core.query)

The query system builds a typed AST over filter/sort/pagination parameters and applies it to
backends. Pipeline diagram: ARCHITECTURE.md's "Query System". Usage: README's "Query System".

**Rule**: all AST nodes are `@dataclass(frozen=True)` — immutable, hashable, safe to cache. The SQLAlchemy applicator lives in `varco_core.query.applicator.sqlalchemy` (not in `varco_sa`) so the query system stays backend-agnostic.

Datetime coercion policy (`DatetimeCoercionPolicy`, Plan 011 / T3), including the ⚠️
`ASTTypeCoercion`-has-no-`policy=` caveat, is covered in
`technical_docs/features/timezone-handling.md`'s T3 section.

### Transactional Outbox (varco_core.service.outbox)

Services must **not** publish events directly after a DB commit — a broker failure will silently drop the event. Use the outbox pattern instead. Mechanism + usage: README's "Transactional Outbox". Types: ARCHITECTURE.md's "Outbox Pattern".

**Rule**: `OutboxRelay` is the only place allowed to call `AbstractEventBus` directly (besides `EventConsumer.register_to()`).

### Background jobs — time, lease, fencing (varco_core.job / Plan 005 Phase 4)

`AbstractJobStore`/`AbstractJobRunner` (`varco_core.job.base`) support a time dimension, bounded
retry (reuses `varco_core.resilience.RetryPolicy`, no second retry model), and a fenced lease
(`try_claim`/`renew`/`reap_expired_leases`/`save(expected_epoch=)` → `StaleLeaseError` on a stale
write). **`run_at` is materialized, not replaced** by the zoned-schedule fields
(`run_at_wall`/`run_at_tz`/`run_at_fold`) — it keeps its exact current meaning as the UTC claim
predicate; the three new fields are the *intent* it was computed from.

Usage: README's "Background Jobs" section. Full detail (TTL/heartbeat sizing, retry-binding
decisions, zoned-schedule DST resolution): `technical_docs/features/job-scheduling-and-leases.md`
and `technical_docs/features/timezone-handling.md`'s T2 section.

### Database auditing (varco_core.service.audit)

An append-only audit trail for `create`/`update`/`delete` mutations, event-driven like the
outbox pattern but persisted by a dedicated consumer rather than a relay. **`AuditLogMixin`
composes to the LEFT of `AsyncService`**, and the consumer must be wired from `@PostConstruct`
(same rule as any other `EventConsumer`).

Usage: README's "Database Auditing" section. Full detail (idempotency per backend, retention,
tenancy, REST admin, tamper evidence via `hash_chain=True`):
`technical_docs/features/database-auditing.md`.

### Field-level encryption & crypto-shredding (varco_core.encryption / encryption_store)

`FieldEncryptor` (Protocol) → `FernetFieldEncryptor` / `MultiKeyEncryptorRegistry`
(rotation) / `TenantAwareEncryptorRegistry` (per-tenant) / `ScopedEncryptorRegistry`
(per-arbitrary-scope). `EncryptionKeyManager` persists DEKs via an `EncryptionKeyStore`.
Full design (scope-vs-tenant backfill requirement, destroy-vs-retire model, capability-shim
rule): `technical_docs/features/crypto-shredding.md`.

**Rule**: never embed personal data in a scope string — varco does not parse it.

**Rule**: `destroy(kid)`/`manager.destroy_scope(scope)` crypto-shred (tombstone); `retire(kid)`
only removes a key from rotation — decrypt of existing ciphertext still works after `retire`,
but raises `KeyDestroyedError` after `destroy`.

### A2A protocol surface — SkillAdapter + SkillSource (varco_fastapi.router.a2a / router.skill)

`SkillAdapter` exposes an agent over the Google A2A protocol, mounted at both the v1.0.0
surface and (while `legacy_paths=True`, the default) the pre-v1.0.0 paths. Full design
(path/method table, legacy-path deprecation timeline, async-A2A provenance):
`technical_docs/features/a2a-surface.md`. Usage: README's "A2A — exposing a non-router
subject" subsection.

**Rule**: `router_cls` and `source=` are mutually exclusive — `ValueError` otherwise.

**`ctx` is the U-3 auth-passthrough contract**: `SkillSource.invoke(skill_id, payload, *,
ctx=)` receives the verified caller's `AuthContext` (or `None` when no auth middleware
populated one) so the three caller classes — end user, another agent, an integrating
platform — are distinguishable in the audit trail.

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
claim shape onto the canonical names, and `varco_core.jwt.profile.TokenProfile` replaces the
single `JwtUtil.SYSTEM_ISSUER` class variable with named, composable profiles. Usage: README's
"Consume a foreign-shaped JWT" / "Gate a route on a named token profile" worked examples. Full
detail: `technical_docs/features/jwt-claim-transformer.md` and
`technical_docs/features/token-profiles.md`.

**Rule**: `JwtParser._from_raw_claims` is the single funnel both `JwtParser.parse()` and
`TrustedIssuerRegistry.verify()` (and therefore `varco_fastapi`'s `JwtBearerAuth`/
`PassthroughAuth`) go through — this is what makes claim transformation zero-code-change.

**Two BREAKING security defaults**: `VARCO_JWT_AUDIENCE` is required unless
`VARCO_JWT_ALLOW_ANY_AUDIENCE=true` (`JwtBearerAuth` refuses to construct otherwise); `iss` is
enforced by default (`VARCO_JWT_ENFORCE_ISS=true`). Full `VARCO_JWT_*` env-var reference:
README's "Verification hardening (VARCO_JWT_*)" subsection.

### Authorization — policy engine (varco_core.auth.policy + varco_casbin)

Two layers of authorization coexist: static, token-derived (`varco_core.auth.base`) and
dynamic, engine-driven (`varco_core.auth.policy`, a pluggable `PolicyEngine` evaluating
ACL/RBAC/ABAC rules held outside the token). Bridge diagram + type hierarchy: ARCHITECTURE.md's
"Authorization — policy engine". Full design (`RequestMapper` keying, per-adapter durability
trade-offs): `technical_docs/features/casbin-authorization.md`.

**Wiring** (`varco_casbin.di`):
```python
container = bootstrap(DIContainer())  # binds CasbinPolicyEngine → PolicyEngine + PolicyManagement
enable_policy_authorizer(container)  # OPT-IN: binds PolicyEngineAuthorizer → AbstractAuthorizer
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

One backend-agnostic contract, two engines, one lifespan component, one CLI. Type diagram:
ARCHITECTURE.md's "Schema migrations" (if present) or `technical_docs/features/schema-migrations.md`
for the full picture (held-open-transaction locking mechanism, the ten-framework-table branch
story, `ensure_table()` reconciliation, Mongo index-mode).

**Rule**: `varco_fastapi` imports **only** `varco_core.migration` — never `varco_sa`,
`varco_beanie`, or `alembic`. Same seam as `AbstractEventBus`/`AbstractJobStore`.

**Default is `off` — nothing runs.** `MigrationSettings.mode` (`VARCO_MIGRATE_MODE`):
`off` (default, nothing registered) / `check` (fail startup if behind, never writes DDL —
**the recommended production posture**) / `upgrade` (lock → apply → release; for
single-instance, dev, and PaaS-without-a-pre-deploy-hook).

**Renamed in 3.0.0 (Plan 022 / AB-2): `SchemaMigrationError` / `SchemaMigrationPlan`.** The
schema-migration pair used to be called `MigrationError`/`MigrationPlan`, colliding at the
`varco_core` top level with the unrelated, older `varco_core.migrator` (domain data/field
migration) pair of the same names — so the schema pair was deliberately *not* re-exported and had
to be imported from `varco_core.migration` explicitly. The rename closes that hole: **the entire
schema-migration surface is now on `varco_core` directly**, and an import site says which concept
it means. `varco_core.migration.MigrationError` / `.MigrationPlan` still resolve, to the identical
objects (so `except` and `isinstance` are unaffected), emit a `DeprecationWarning`, and are removed
in 4.0.0. `varco_core.MigrationError`/`.MigrationPlan` still mean the **domain-migration** pair and
are unchanged.

`alembic` is an optional extra: `pip install "varco-sa[migrations]"`. See
`technical_docs/features/schema-migrations.md`.

### Multitenancy — isolation strategies, control plane, global scope (Plan 007, Plan 008)

Tenant data isolation is a **selectable deployment strategy**, not one hard-coded shape.
Three `TenantIsolation` values (`SHARED` — default, unchanged; `SCHEMA` — Postgres only;
`DATABASE` — Postgres + Mongo), `enforce_rls: bool` as an additive hardening flag on
`SHARED` rather than a fourth enum value, and an orthogonal `TenantScope`
(`TENANT`/`GLOBAL`) for shared reference data under every strategy. Type diagram + full design
(RD-4/RD-7/RD-9…RD-18 reasoning, the command/fact DAG rule, readiness-coordinator semantics,
`schema_translate_map`-vs-`search_path`, fan-out supervisor narrative, new env vars, the
connection-budget sizing worksheet, all six wiring recipes): `technical_docs/features/multitenancy.md`.
Usage: README's "Multi-tenancy (DB-level)" section.

**Rule**: `varco_fastapi.tenancy` imports **only** `varco_core.tenancy` — never `varco_sa`,
`varco_beanie`, `sqlalchemy`, or `pymongo`. Same seam rule as `AbstractEventBus`/
`AbstractMigrator`.

**Default is byte-identical to pre-Plan-007 behaviour.** `TenancySettings()` defaults:
`isolation=SHARED`, `enforce_rls=False`, every model `TenantScope.TENANT`,
`fanout_framework_tables=False`. No pool, no extra engine/client, no symbolic schema, no
control-plane surface constructed. `create_varco_app(tenancy=None)` (the default)
registers nothing.

**Rule**: `mount_tenant_admin(app, control_service, acknowledge_bundled_admin=True,
server_auth=..., admin_role="tenant-admin")` is the **only** way to expose the admin
surface — there is deliberately **no** `VARCO_TENANCY_MOUNT_ADMIN` env var, ever.

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

Worked usage examples for every subsystem live in README.md — see its Table of Contents.

---

## When you hit a `providify` limitation or bug

**Do not work around it in application code.** The user owns the `providify` library
(`/home/edoardo/projects/providify` when present locally). If something inside `providify`
itself is missing, wrong, or forces a hand-rolled workaround (private-attribute access,
copy-pasted patch-before-register dances, silent behavior differences across versions),
**stop and report it — don't paper over it.**

**The report is a file; the ledger is only an index.** Two artifacts, and the split matters:

1. **`design/upstream-gaps/<library>-<short-slug>.md` — the durable report.** This is the real
   deliverable and it is never deleted. Cover: what upstream does today, why it is a gap, a
   minimal reproduction, the ask (with ✅/❌ per candidate fix, so the maintainer is choosing
   between options rather than reading a complaint), and any interim workaround.
2. **[UPSTREAM-GAPS.md](UPSTREAM-GAPS.md) — a one-row pointer into that file.**
   ⚠️ **This ledger is cleared from time to time, and its absence is expected, not an error.**
   If it is missing it was wiped to clear resolved rows — recreate it from the template in its
   own header and add your row. Never skip filing because the ledger is not there, and never go
   hunting for what happened to it. Nothing is lost in a clearing because the ledger holds no
   content of its own; it can always be rebuilt with `ls design/upstream-gaps/*.md`.
   (An earlier incarnation inlined every entry's full body, was deleted in `cae7f33`, and took
   all of them with it. That is the failure this split prevents.)

- **Verify the claim in providify's own source, citing `file:line`** — not from memory, not from
  `varco`'s docs about it, not from a docstring. The register carries a standing lesson (U-8)
  about entries filed off documentation that did not survive contact with source. A docstring
  that *contradicts* the source is itself good evidence, but quote both.
- **Guard every gap with a `strict=True` xfail** so the fix cannot land unnoticed and untested.
  Prefer a fast, dependency-free reproduction that runs in `make test` over one gated behind
  Docker and `-m integration` — a nightly-only guard will not warn you while you work.
- **Say so plainly if the gap is partly ours.** If varco can fix its own symptom today with a
  supported mechanism, that belongs in its own section of the report. A report that blames
  upstream for something we control is worse than no report — it turns our bug into a wait.
  (Worked example: `P22-PROVIDER-PREDESTROY` §5, where a `@Disposes` closes varco's leak with
  no upstream change at all.)
- If a workaround is genuinely unavoidable in the short term (e.g. the now-deleted
  `varco_core` compat shim filed under U-20 — six independent hand-rolled annotation
  patches consolidated into one shared, documented, deletable helper until providify 2.0.0
  shipped `@Provider(returns=...)` natively), centralize it in exactly one place, name it as a
  shim intended for deletion, and still write the report — the shim is not a substitute for it.
- This mirrors the rule for downstream consumers of `varco_*` — inside this repo, `providify` is
  the upstream and the same discipline applies to it.

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

**Integration tests run in CI too, now** (`.github/workflows/integration.yml`, Plan 017 / RL-5)
— push to `main`, nightly `schedule`, and `workflow_dispatch`. It always invokes `make
integration-test-clean`, never plain `make integration-test`, so every CI integration run is a
genuine clean-room run — the same guarantee described above, just automated. This is exactly why
CI provisions brokers via testcontainers rather than GitHub Actions `services:` blocks: a
`services:` block can only be wired to the conftests through the **bare** env var names
(`REDIS_URL`, `DATABASE_URL`, …) that the `VARCO_TEST_<SERVICE>_URL` contract deliberately never
honors — so a `services:`-backed workflow would either be silently ignored by the fixtures (each
one boots its own container anyway, doubling cost) or would require breaking the "bare names are
never honored" invariant just to make CI convenient. Testcontainers-only keeps the "NOT a
clean-room run" signal meaningful on the one class of run — CI — where it matters most, and keeps
local and CI runs on byte-identical code paths.

**Chaos tests** (`testkit/varco_chaos`, Plan 018 / RT7) — the `chaos` marker is **additive** to
`integration` (`pytestmark = [pytest.mark.integration, pytest.mark.chaos]`), never a replacement:
a chaos test always also carries `integration`. `scripts/integration_tests.sh` defaults
`MARKER_EXPR` to `"integration and not chaos"`, so `make integration-test` never runs one; `make
chaos-test` / `make chaos-test-clean` flip it to `"integration and chaos"`.

There are now **three** container-scope conventions, each solving a different isolation need:

| Scope | Fixture name pattern | When |
|---|---|---|
| `session` (shared) | `redis_url`, `kafka_bootstrap`, `postgres_url`, … | The default — every non-chaos, non-pristine-requiring test |
| `function` (fresh) | `*_container_fresh` | A test needs a pristine server (e.g. asserting the full topic/key list) but must not break it for anyone else |
| `module` (chaos) | `*_container_chaos` | A test is **allowed to break** the container (restart/pause) — declared **inside the chaos test module itself, never in `conftest.py`**, so no non-chaos test can accidentally depend on a container that gets restarted under it |

`ChaosContainer` (`testkit/varco_chaos/containers.py`) is the **only** sanctioned caller of
`DockerContainer.get_wrapped_container()` in the repo — every chaos test goes through its
surface (`restart()`, `paused()`, `wait_ready()`, and its `url` property, Plan 019 / §RT7b-port)
instead of reaching for the raw docker-py handle. `restart()` always uses docker-py's own
`Container.restart()`, **never** `DockerContainer.stop()` + `.start()` — the latter deletes and
recreates the container on testcontainers' side, losing even the container ID.
⚠️ `restart()` does **not** guarantee the host port survives — research 006 §A/§B/§F: Docker's
own Engine API reference documents that the allocated port "might be changed when restarting the
container", and this is platform-independent and version-stable (moby v1.3.0 → v29.1), including
GitHub Actions' native-Linux dockerd. This is documented Docker behaviour, not a WSL2-specific
flake (research 002 §1's original "port survives" claim is superseded — see its in-tree banner).
See the Common Pitfalls table's "chaos `restart()` port instability" row — the fix is
`ChaosContainer.url`, never a captured string.

Chaos tests run nightly + `workflow_dispatch` only (`.github/workflows/integration.yml`'s
`chaos` job, `if: github.event_name != 'push'`), never on `push: main`, and are never a required
check — a chaos scenario is designed to provoke a real race under container failure, and an
occasional red run is a BACKLOG/operator-triage signal, not a merge blocker.

**Conformance suite opt-in** (`testkit/varco_conformance`, Plan 012 / RT6, plus
`channel_manager.py` added by Plan 019 / RT2-C) — a shared, never-packaged suite of behavioral
contract tests, **five** modules, one per `varco_core` ABC (`event_bus.py`, `cache.py`,
`job_store.py`, `dlq.py`, `channel_manager.py`). Reached via one `pythonpath =
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
`varco_core/tests/test_conformance_inmemory.py` runs the other four suites (`event_bus`, `cache`,
`job_store`, `dlq`) against every in-process implementation with no Docker required — the fast
feedback loop. `channel_manager.py` has no in-process implementation to run there (there is no
`InMemoryChannelManager` — `ChannelManager` is inherently a broker-admin concern) and is
subclassed only by the three real-broker backends (`varco_kafka`, `varco_redis`, `varco_nats`).

**A conformance failure that reveals a genuine backend ABC-contract violation becomes
`@pytest.mark.xfail(reason="BUG: ...", strict=True)` plus a one-line BACKLOG.md entry — never an
in-place production-code fix.** `strict=True` means the xfail itself fails loudly if the
underlying bug is ever fixed, so the marker doesn't silently rot. See BACKLOG.md's "Known issues
found while implementing Plan 012" table for the accumulated findings (e.g. `RedisCache`/
`MemcachedCache` truncating a sub-second `ttl` to `int()`, `KafkaDLQ`/`NatsDLQ.delete_where()`
never reaching the ABC's "no predicate → `ValueError`" check).

**providify's `pytest11` plugin fixtures** (providify ≥ 2.0.0, Plan 016 / RL-3d) — installing
`providify` activates its own `pytest11` entry point (`providify/pytest_plugin.py`) in every
project, with **four** function-scoped, yield-based, non-autouse fixtures:

| Fixture | Yields |
|---|---|
| `di_container` | A fresh, empty `DIContainer` — a new instance per test. |
| `di_acontainer` | The async counterpart — same fresh-per-test contract, usable directly under this repo's `asyncio_mode = "auto"` with no extra marker; `container.ashutdown()` is awaited automatically at teardown. |
| `di_overrides` | A `ContainerOverrides` bound to that test's `di_container`; any override made through it is undone automatically at teardown. |
| `di_global` | Makes `DIContainer.current()` return the test's container for the duration of the test, then restores whatever `current()` returned before. |

varco **deliberately does not re-export or wrap any of these** — see Design §RL-3d in Plan 016:
`testkit/varco_conformance` is never packaged, so it cannot deliver fixtures to a downstream
consumer anyway, and a second name for an identical fixture is pure confusion. Use them directly
via `providify`'s own names. A project's own `conftest.py` **can** redefine `di_container` (or any
of the other three) and that consumer definition wins over the plugin default
(`providify/pytest_plugin.py:33-37`) — ordinary pytest fixture-override semantics, no varco-side
opt-out mechanism needed. A test that requests none of the four sees zero behavioural difference
from providify's plugin not being installed at all — verified by
`varco_core/tests/test_providify_pytest_plugin.py`, which exercises all four plus this inertness
guarantee with no conftest edits anywhere in the repo.

---

## Common Pitfalls & How to Avoid Them

| Pitfall | Symptom | Root Cause | Fix |
|---------|---------|-----------|-----|
| **Direct bus access in service** | Service holds `AbstractEventBus` and calls `bus.publish()` | Violates layer rule; bus is infra, not for app logic | Always inject `AbstractEventProducer`; it abstracts the bus |
| **NATS handler raises under `FIRE_FORGET`** | Message is acked and never retried despite `AT_LEAST_ONCE` | The exception never leaves `_dispatch` (`bus.py`'s `FIRE_FORGET` branch only logs), so `_on_message` sees a successful dispatch | Use `COLLECT_ALL` (the default) or `FAIL_FAST` if you want JetStream-level redelivery on handler failure (Plan 019 / RT2-B) |
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
| **`@Singleton` on pydantic `BaseSettings`** | `LookupError: Cannot resolve 'values'` at resolution — on providify < 1.1.0 | providify injects pydantic's `**values` ctor param | Register settings via a `@Provider` (see `varco_casbin.di`), not `@Singleton`. **Version nuance (Plan 014):** on providify ≥ 1.1.0 the per-parameter resolver skips `VAR_KEYWORD` params outright (`providify/_annotations.py:583-590`), so `@Singleton` on this exact shape now *appears* to work — `container.get(SomeSettings)` resolves and is a singleton, and a required field raises `pydantic.ValidationError`, not `LookupError` (characterized in `varco_kafka/tests/test_kafka_di.py::TestKafkaRequiredFieldCharacterization`). The rule is still enforced regardless: the sanctioned shape must not depend on an undocumented third-party implementation detail that a future providify release could revert. `KafkaEventBusSettings`/`NatsEventBusSettings`/`RedisEventBusSettings` were converted to `@Provider` factories (`kafka_event_bus_settings`/`nats_event_bus_settings`/`redis_event_bus_settings`) for exactly this reason — see `plans/014-refactor-di-settings-and-provider-helper.md` |
| **Quoted `@Provider` return annotation** | An *unrelated* provider fails with `TypeError: xxx() missing 1 required positional argument`; every `Inject[...]` in the container is silently dropped | Under PEP 563 `-> "Foo"` is stored as the string `"'Foo'"`; providify's fallback `eval` returns the **str** `'Foo'` and registers it as the binding interface → `DIContainer._build_localns()` raises `AttributeError: 'str' object has no attribute '__name__'` → `_collect_kwargs_sync()` swallows it with `hints = {}` for **every** provider | Never quote a `@Provider` return annotation (`from __future__ import annotations` already makes it lazy) and import the type at **module scope** so `fn.__globals__` can resolve it. Guarded by `varco_fastapi/tests/test_di_binding_health.py` |
| **Quoted `TypeAlias` used in an injected annotation** | `AnnotationResolutionError: … TypeError: unsupported operand type(s) for \|: 'str' and 'NoneType'` at `validate_bindings()` (providify ≥ 1.1.0), or the parameter is **silently never injected** (< 1.1.0) | `X: TypeAlias = "Foo[Bar]"` binds the **string** to the module name at runtime, so any annotation doing `X \| None` evaluates `str \| None`. Quoting is usually a symptom of the inner type being imported under `TYPE_CHECKING` | Prefer annotating the **interface directly** (`Serializer[Event]`, not an alias). If you must alias, import the referenced types at **runtime** and leave it unquoted, or use PEP 695 `TypeAliasType`. Guarded by `varco_core/tests/test_event_serializer_alias.py` |
| **Protocol impl not resolvable by DI** | `container.get(Serializer[Event])` finds no binding although a conforming class exists | Structural (`Protocol`) satisfaction is invisible to the container — it binds on declared base classes | Subclass the protocol **explicitly** (`class JsonEventSerializer(Serializer[Event])`) and decorate it; use `@Singleton(priority=-sys.maxsize - 1)` for a framework default so any app binding wins regardless of registration order |
| **A package's suite is green but its container won't bootstrap** | Tests pass; a real app dies at startup with `AnnotationResolutionError` (or a cycle/scope-leak that only surfaces once every binding is wired together) | No test hit a path that resolves *binding* annotations — unit tests construct objects directly instead of resolving them | Add a `container.scan(pkg); container.validate_bindings()` test per package — one call covers every present and future singleton's annotations (see `varco_redis/tests/test_redis_di.py`) **plus** `container.validate(raise_on_error=False)` filtered to structural errors only (`AMBIGUOUS_BINDING`/`CIRCULAR_DEPENDENCY`/`SCOPE_LEAK`/`LIVE_REQUIRED`/`UNRESOLVED_ANNOTATION`, tolerating `MISSING_BINDING` since a package scanned alone legitimately lacks the app's own bindings) — providify ≥ 2.0.0, via the shared `testkit/varco_conformance.providify_health.assert_no_structural_di_issues()` helper (Plan 016 / RL-3a) |
| **`container.provide(lambda: X())`** | `ProviderBindingNotDecoratedError` at bootstrap | `provide()` only accepts `@Provider`-decorated callables and takes no second "interface" argument | Declare a module-level `@Provider(singleton=True) def x() -> X:` and pass the function |
| **Override registered after `install()`/`scan()`** | The package default wins; your settings are silently ignored | Equal-priority bindings resolve to the **first** registered | `provide()` before `install()`/`scan()`, or declare `@Provider(..., priority=100)` |
| **Per-call `Singleflight`** | Concurrent misses never coalesce — the loader runs once per caller, same as before | A fresh `Singleflight()` has an empty in-flight dict every call — same defect class as a per-call `CircuitBreaker`/`Bulkhead` | `@cached` creates one `Singleflight` per decorated function at decoration time; `CacheServiceMixin` creates one per service instance (lazily, on first use) — never construct one inside a request handler |
| **Coalescing on a pre-tenant-namespaced key** | Cross-tenant data leak — two tenants' concurrent misses share one recompute and one result | `Singleflight`/`read_through()` never build or namespace keys themselves; a caller that coalesces on the raw pk instead of the final `tenant:{id}:`-prefixed key defeats tenant isolation | Always pass the final, already-namespaced cache key (the one `tenancy_cache_key()`/`CacheServiceMixin._cache_key()` produced) to `Singleflight.do()`/`read_through()` — guarded by `varco_core/tests/test_cache_singleflight_tenancy.py` |
| **Per-call `RedisPubSubBackplane`** | Invalidations never propagate — each instance has its own listener/subscription state | Same shared-instance rule as `CircuitBreaker`/`Bulkhead`/`Singleflight` | Construct one `RedisPubSubBackplane` and pass it into every `LayeredCache` that must share coherence; let `LayeredCache.start()`/`stop()` drive its lifecycle, never call `start()`/`stop()` directly |
| **Adding a bulk method directly to `AsyncCache`** | `isinstance(third_party_cache, AsyncCache)` silently starts returning `False` for out-of-tree caches | `AsyncCache` is `runtime_checkable` — `isinstance()` tests method presence, so any new method changes what satisfies it | Add to `BulkCache` instead (Plan 011 / D-11) — `AsyncCache` stays byte-for-byte unchanged |
| **Forgot `<pkg>.bootstrap(container)`** | App starts, `AbstractEventBus` silently absent | `_try_resolve_component()` used to swallow every skip (import error, missing binding, construction failure) into `except Exception: pass` with zero logging | Plan 014 / audit F2 — one WARNING now names the missing binding at startup (`_lifecycle_discovery_warns()`); silence it with `VARCO_LIFECYCLE_DISCOVERY_WARN=false` if the app genuinely has no bus/job runner. Control flow is unchanged — the component is still skipped, the app still starts |
| **`varco_memcached.async_bootstrap()` opens a pool you didn't want** | An unwanted Memcached connection is opened just from calling `async_bootstrap()` | `setup_cache` defaults `True` — unconditional `ainstall(MemcachedCacheConfiguration)`, unlike `varco_redis.di.async_bootstrap()` which defaults `setup_cache=False` | Pass `setup_cache=False` for the sync-scan-only equivalent of `bootstrap()`. Note the defaults deliberately differ: `varco_redis`'s `async_bootstrap` also serves a streams/event-bus path where no cache is wanted; memcached's only reason to exist is the cache, so its default stays `True` for backward compatibility (Plan 014 / audit F7) |
| **Linting with `uvx ruff`** (applies to `ruff check` AND `ruff format --check`, Plan 020 / RL-17) | Local green, CI red (or vice versa) with no code change | `uvx` resolves the newest ruff release at invocation time; CI resolves the pin recorded in `uv.lock` | Always `uv run ruff`, never `uvx ruff` — the pin lives in the root `[dependency-groups] lint` group (Plan 017 / RL-6) |
| **Assuming `f"{SomeVarcoEnum.MEMBER}"` still yields `ClassName.MEMBER`** | Log lines / f-strings involving `HealthStatus`, `CircuitState`, `ErrorPolicy`, `DispatchMode`, `PKStrategy`, `KafkaDeliverySemantics`, `NatsDeliverySemantics`, or `BackpressurePolicy` suddenly print the bare value instead of `ClassName.MEMBER` | Python 3.11 changed `Enum.__format__` to print `ClassName.MEMBER` for `(str, Enum)` mixins; Plan 020 / RL-15 migrated all eight of these to `enum.StrEnum`, which undoes that change and formats by value | Intended as of 3.0.0 (BREAKING, see CHANGELOG) — `.value`, `json.dumps(member)`, and pydantic `model_dump(mode="json")` are all unaffected either way; update any `str()`/`%s`-log-scraping regex that depended on the old `ClassName.MEMBER` text |
| **Restarting a session-scoped container** | Unrelated tests in the same package fail with connection errors after a chaos test runs | The session-scoped fixture (`redis_url`, `kafka_bootstrap`, …) is shared by the whole package suite — restarting/pausing it under one test breaks every other test mid-flight or afterward | Declare a **module-scoped `*_container_chaos` fixture inside the chaos module itself**, never in `conftest.py` (Plan 018 / RT7, §chaos-fixture) — see Test Conventions' "Chaos tests" paragraph |
| **Assuming a chaos container's URL is stable across `restart()`** | `ConnectionRefusedError` after `restart()` even though `wait_ready()` returned successfully | Docker re-allocates ephemeral host ports on restart **by design** (research 006 §A/§B/§F) — platform-independent, version-stable moby v1.3.0 → v29.1, including GitHub Actions' native-Linux dockerd. Not a WSL2-specific flake and not unverified — research 002 §1's original "port survives restart" claim is superseded (in-tree banner points here) | Fixed (Plan 019 / §RT7b-port, closes BACKLOG's RT7b-port-remap): read `chaos.url` at every use — it re-derives fresh from the docker daemon on every access, never memoised. Pin the host port when the server advertises its own mapped address at first boot (Kafka's `tc-start.sh` bakes `KAFKA_ADVERTISED_LISTENERS` in as a literal — re-querying alone cannot fix it, so `kafka_container_chaos` additionally pins via `testkit/varco_chaos/ports.py`'s `reserve_host_port()` + `with_bind_ports`); do not assume the pause-based tests (`test_redis_chaos.py`, `test_nats_health_chaos.py`) needed this — pausing never remaps a port |

Feature-specific operational pitfalls (wrong env var → wrong runtime behaviour) live in each feature's own `technical_docs/features/*.md` **Pitfalls** section, not here.

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
