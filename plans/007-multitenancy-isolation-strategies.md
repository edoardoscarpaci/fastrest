# Plan 007 — Multitenancy: isolation strategies, tenant control plane, and global/shared scope

## Goal

Make tenant data isolation a **selectable deployment strategy**, add a **dynamic tenant
control plane** (REST + event-driven onboarding/offboarding backed by a durable
catalog, deployable either standalone or bundled into an app), and make
**globally-scoped (shared, non-tenant-routed) entities** a first-class concept under
every strategy.

| # | Backend | Strategy |
|---|---------|----------|
| 1 | Postgres | shared schema + discriminator column (**today's behaviour, unchanged default**) |
| 2 | Postgres | shared schema + discriminator + **RLS asserted** |
| 3 | Postgres | **schema-per-tenant** (`schema_translate_map`) |
| 4 | Postgres | **database-per-tenant** (bounded per-tenant engine pool + fanned-out relay/runner) |
| 5 | MongoDB | shared collection + discriminator (**today's behaviour**) |
| 6 | MongoDB | **database-per-tenant** (per-tenant Document clones + `init_beanie`) |

Plus, orthogonal to all six: any entity may be declared `TenantScope.GLOBAL` and is
served from one shared location every tenant reads.

---

## Resolved decisions

The user reviewed two rounds of open questions and answered all ten. Verbatim answers
are quoted for audit; the resolution states what the plan now does. **Nothing in this
plan is undecided.**

- **RD-1 — the tenant catalog is `varco_tenants`, and onboarding/offboarding is fully
  dynamic.**
  > "varco_tenants, the onboarding, offboarding of tenant should be totally dinamic, it can be done via rest or via queue/pub-sub"

  `SATenantCatalog` / `BeanieTenantCatalog` (durable store) become the authoritative
  catalog; `StaticTenantCatalog` stays as a test/bootstrap double. A **tenant control
  plane** is added with two write surfaces — a REST admin router and an event-driven
  consumer (`TenantProvisionRequested` / `TenantDeprovisionRequested`) — plus a
  `TenantDescriptor.status` lifecycle and a cross-pod catalog invalidation path.
  **Phases 4 and 5.**

- **RD-2 — per-tenant DSN override confirmed.**
  > "Confirm the override"

  Template + `TenantDescriptor` override (superset) ships. Because `varco_tenants` is
  REST-writable, the column holds a **secret reference** (`dsn_ref`), never a literal
  credential — Phase 6.

- **RD-3 — symbolic schema stamping accepted.**
  > "Confirm acceptable"

  Opt-in per deployment (`isolation == SCHEMA`), per-model overridable, default
  byte-identical.

- **RD-4 — cluster DDL is opt-in AND confined to the control plane.**
  > "only acceptable as opt-in and into varco_tenant, the normal apps/services should never use or think they have administrator privilage"

  An architectural constraint: a separate `VARCO_TENANCY_ADMIN_DSN` + short-lived
  maintenance engine, never the request-path `SAConfig.engine`; the provisioner
  **cannot be constructed** without it; a runtime guard makes cluster DDL unreachable
  from an app that did not opt in. See Phase 6. **RD-9 below refines how this interacts
  with the bundled deployment shape.**

- **RD-5 — varco does not decide the tenant-count ceiling.**
  > "The user will enable the one that think it's better for their enviroment. Varco doens't decide taht"

  All "supported to ~200 tenants" claims removed. No enforced cap. The
  connection-budget arithmetic and sizing worksheet stay as **informational,
  order-of-magnitude, environment-dependent** guidance. `max_entries` is a knob with a
  conservative default and a WARNING on breach.

- **RD-6 — assert-only, and the failure must teach.**
  > "assert-only and maybe add a guide or an error that point to the documentation that explain how to tenable"

  `assert_rls_enabled()` never emits DDL. `TenantIsolationError` names the table, the
  concrete remediation (`varco_sa.migration.ops.rls_upgrade(op, "<table>")` in a
  reviewed revision) and the doc path. Asserted on the message text.

- **RD-7 — Mongo clones acceptable because opt-in; document clearly.**
  > "Acceptable if the user opt int for that implementation need to be documentated celany"

  Clones ship. A **prominent** docs subsection carries the memory formula, a worked
  example with real numbers, how `max_entries` bounds it, and how to observe it.

- **RD-8 — per-tenant fan-out must be implemented, not refused.**
  > "need to implement B if it's the only solution, it need to work we cannot have feature not working or atleast injected into the library so the structure is there and then enabled it later"

  A working, tested **`TenantFanoutSupervisor`** (**Phase 8**) owns one `OutboxRelay` /
  job poller / audit consumer per active tenant. Removed from Non-goals. The Phase-6
  guard is retained but **re-framed as a config guard**: it fires only when
  db-per-tenant is configured *without* fan-out enabled, and names the enabling flag.

- **RD-9 — the control plane is a separate deployable by default, and bundleable on
  request.**
  > "By default separate deployable but if the user need or doesn;'t want another container he should be able to bundle it into a varco_fastapi app"

  **Both shapes are first-class.** Shared modules, no new workspace package.
  *Standalone* (default, recommended): a small app mounting only the tenancy admin
  router, with the admin DSN in **its** environment only; app pods mount nothing.
  *Bundled* (explicitly opt-in): one `mount_tenant_admin(app, …,
  acknowledge_bundled_admin=True)` call, role-guarded, one WARNING at mount. **Env vars
  alone can never mount it** — see the DESIGN block below.

- **RD-10 — global DB co-located with the control plane; app credential read-only by
  default.**
  > "Yeah seems fine"

  The global/shared database is the **same physical database as the control plane by
  default**, with a `VARCO_TENANCY_GLOBAL_DSN` knob falling back to the app's own DSN.
  The app-facing global credential is **read-only by default**; writes go through the
  control plane. A denied write surfaces as a legible `GlobalScopeReadOnlyError`, not a
  raw driver traceback. Opt-in to a writable credential is explicit
  (`global_writable=True` / `VARCO_TENANCY_GLOBAL_WRITABLE=true`).

**RD-11 … RD-19 continue this numbering in Plan 008** (tenant control plane:
entry-point convergence, fleet fan-out, and readiness) — same namespace deliberately,
since Plan 008's decisions constrain and amend these. See
`plans/008-tenant-control-plane-convergence-and-fleet-fanout.md`.

---

## Non-goals

- **Cross-tenant aggregate queries** (one report spanning all tenants). Under 3/4/6
  this needs a fan-out query executor + result merge. Not built.
- **Migrating a tenant between strategies** or moving a tenant's data between
  databases/hosts. No data-move tooling.
- **Distributed transactions / 2PC** across a tenant database and the global database.
  Explicitly impossible; the outbox/saga primitives are the supported answer.
- **Per-tenant read replicas / read-write splitting.**
- **Per-tenant schema drift** as a steady state. Fan-out *reports* drift; it does not
  support it.
- **A DI request/tenant scope primitive in providify.** Routing stays a runtime
  ContextVar pattern, as `TenantUoWProvider` already is.
- **Rewriting `TenantAwareService`.** Unchanged; strategies 3/4/6 make it redundant
  *for isolation* but it still shapes queries harmlessly.
- **A tenant self-service signup UI / billing / quota enforcement.** The control plane
  exposes provisioning primitives, not a commercial onboarding product.
- **A `varco_tenant` workspace package.** RD-9 is satisfied by shared modules + two
  deployment shapes; an eleventh package is not created.
- **Per-tenant database credentials / roles.** The control plane may create databases;
  minting a distinct Postgres role per tenant is out of scope (noted where `CREATEROLE`
  appears).

---

## Design

### Naming: `TenantIsolation` (not `TenancyStrategy`)

**DESIGN: `TenantIsolation` enum + behaviour objects named for what they do**
- ✅ `…Strategy` is already taken in this codebase by *behaviour* ABCs with
  `start()`/`stop()` lifecycles (`InvalidationStrategy`, `TTLStrategy`,
  `CompositeStrategy`). The new enum is a declarative value with no lifecycle.
- ✅ `TenantIsolation` names what the operator chooses: how strongly tenants are
  isolated.
- ❌ Mild asymmetry with `InvalidationStrategy`. Accepted.

### Three enum values, not six — RLS is additive

```python
class TenantIsolation(StrEnum):
    SHARED = "shared"  # one schema/db/collection + discriminator
    SCHEMA = "schema"  # one Postgres schema per tenant   (Postgres only)
    DATABASE = "database"  # one logical database per tenant  (Postgres + Mongo)
```

RLS is `enforce_rls: bool`, a hardening flag on `SHARED` — strategies 1 and 2 differ
only by it. Keeps the enum backend-neutral and avoids six near-duplicate values.

### Orthogonal axis: `TenantScope` (global tables)

```python
class TenantScope(StrEnum):
    TENANT = "tenant"  # default — routed per tenant under SCHEMA/DATABASE
    GLOBAL = "global"  # one shared copy; every tenant reads it
```

**DESIGN: declared as `Meta.tenant_scope`, normalised into `ParsedMeta.tenant_scope`**
- ✅ `ParsedMeta` is documented (`meta.py:616-641`) as the *only* thing backend
  factories read — "backends read from this object only; they never call `getattr` on
  the domain class directly". A defaulted `tenant_scope` field preserves that invariant
  and gives `SAModelFactory` and `BeanieModelFactory` the value for free.
- ✅ Scope is a **per-entity** property, and `Meta` already holds per-entity properties
  (`table`, `constraints`, `customize`).
- ✅ Default `TENANT` is **fail-closed**: a forgotten declaration routes per-tenant
  (worst case: a shared table needlessly duplicated per tenant — visible, fixable)
  rather than un-routed (worst case: a tenant table lands in the shared schema and
  every tenant reads every row — a silent cross-tenant leak).
- ✅ The ten framework tables are **forced** `GLOBAL` regardless of declaration.
- ❌ A new (defaulted, appended) field on a frozen dataclass external code may
  construct positionally. Noted in the CHANGELOG.
- **Rejected: an `Annotated[...]` field marker** — ❌ wrong granularity; scope is
  per-entity. **Rejected: a `GlobalModel(DomainModel)` base** — ❌ interacts with model
  mixins/MRO, ❌ not overridable per deployment.

### Layering — same seam rule as `AbstractEventBus` / `AbstractMigrator`

```
varco_core.tenancy                      ← contracts only, zero third-party deps
  TenantIsolation · TenantScope · TenancySettings · TenantDescriptor · TenantStatus
  AbstractTenantCatalog · StaticTenantCatalog · CachedTenantCatalog
  AbstractTenantProvisioner · ExternalTenantProvisioner · TenantResourcePool[T]
  DynamicTenantUoWProvider · GlobalUoWProvider · GlobalScopeReadOnlyError
  control/  → TenantProvisionRequested · TenantDeprovisionRequested
              TenantCatalogChanged · TenantControlService · TenantProvisionConsumer
  migration/fanout.py → TenantFanoutMigrator
  fanout.py           → TenantFanoutSupervisor
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

`varco_fastapi` imports **only** `varco_core.tenancy` — never `varco_sa`,
`varco_beanie`, `sqlalchemy`, or `pymongo`. Enforced by an import-guard test.

### Backwards compatibility — absolute

With `TenancySettings()` defaults (`isolation=SHARED`, `enforce_rls=False`,
`fanout_framework_tables=False`, every model `TenantScope.TENANT`):
- No pool, no extra engine/client beyond today's `SAConfig.engine` /
  `BeanieSettings.mongo_client`.
- `SAModelFactory` stamps **no** symbolic schema → generated `__table__.schema is None`.
- `TenantUoWProvider` and `varco_core/service/tenant.py` are **not modified at all**;
  the dynamic behaviour is a *new* class.
- `AlembicMigrator`'s new `schema=`/`version_table_schema=` default `None` → existing
  `env_ctx.configure(...)` kwargs byte-identical.
- No control-plane router, consumer, admin engine, or supervisor is constructed.

### DESIGN: `schema_translate_map` over `SET LOCAL search_path`

Chosen: **SQLAlchemy `schema_translate_map`**, applied per-session via
`engine.execution_options(schema_translate_map={"tenant": "t_acme"})` bound into a
per-tenant `async_sessionmaker`.

- ✅ **Fails closed.** A path that forgets to route yields an unresolved symbolic
  schema → a compile/DB error. A forgotten `SET LOCAL search_path` silently resolves to
  the default schema — i.e. **another tenant's rows, returned successfully**. That
  asymmetry decides it; the point of the feature is to stop depending on every path
  remembering (the existing `_scoped_params` fail-open class).
- ✅ **Pooler-safe unconditionally** — nothing written to session state, so PgBouncer
  `pool_mode=transaction` cannot misroute it (unlike `SET`, per `postgres-rls.md`
  §2-3 and the `SAAdvisoryLock` U-16 defect).
- ✅ **Zero extra round trips**; ✅ SQLAlchemy's compiled cache keys on the map's
  **keys**, so N tenants share one compiled form.
- ✅ Global tables carry **no symbolic token**, so they resolve to the real default
  schema — cross-schema joins with tenant tables work in one transaction.
- ❌ Requires a symbolic token on every routed table → touches `SAModelFactory` (RD-3).
- ❌ **Raw `text()` SQL is NOT translated** — must self-qualify. Documented loudly.
- ❌ Alembic must be told separately (`version_table_schema` + the map) — Phase 9.

**Rejected: `SET LOCAL search_path`** — ❌ fail-open on a missed call (disqualifying);
❌ a round trip per transaction; ✅ would have covered raw SQL with no model change.
Kept as a documented escape hatch (`SASchemaRouter(mechanism="search_path")`).

**`PostgresConnectionSettings.schema_name` is unchanged and not repurposed** — it stays
"the default schema for this install" and is where **untranslated** (global +
framework) tables live.

### DESIGN: bounded `TenantResourcePool[T]`

The hazard, stated plainly: **N tenants × `pool_size` connections.** At
`pool_size=5, max_overflow=10`, 200 tenants is up to 3 000 Postgres connections from
one process.

```
TenantResourcePool[T]                  (T = AsyncEngine | BeanieTenantBinding)
  max_entries: int = 50      soft cap; LRU-evict least-recently-used *idle* entry
  idle_ttl_s:  float = 300.0 sweeper closes entries idle longer than this
  factory / closer           create / dispose(engine) or close(client)

  async def ensure(tid) -> T    create-or-hit; per-tenant lazy asyncio.Lock
  def peek(tid) -> T | None     sync cache hit, never creates
  def lease(tid)                refcount ctx manager — eviction can't pull the rug
  async def evict(tid) / aclose()
```

- **Eviction never disposes a busy entry.** `lease()` refcounts; the sweep skips
  refcount > 0 and, if *every* entry is busy, **exceeds the soft cap with one WARNING
  per breach** rather than breaking correctness. Resource pressure fails open;
  isolation never does.
- **Sizing worksheet (docs, informational only — RD-5):** budget
  `max_entries × (pool_size + max_overflow) × n_pods ≤ 0.8 × max_connections`.
  Recommended per-tenant sizing for db-per-tenant is `pool_size=1, max_overflow=2`
  (per-tenant engines are mostly idle). **varco enforces no tenant-count cap and
  publishes no supported ceiling** — the decision table's ceiling column is
  order-of-magnitude guidance for the operator's own environment.
- **Lazy `asyncio.Lock` per tenant** (repo rule) — created inside `ensure()`, so the
  pool is constructible outside a running loop.
- **Lifecycle owner:** `varco_fastapi.tenancy.TenancyLifecycle`, prepended into
  `VarcoLifespan` like `MigrationLifecycle`. `stop()` awaits `aclose()` so **every
  engine is `dispose()`d and every owned Mongo client closed**. Non-FastAPI callers use
  `async with pool:`.

### DESIGN: Mongo database-per-tenant via per-tenant Document clones

**The obstacle, precisely:** `BeanieRepositoryProvider.init()` calls
`init_beanie(database=self._client[self._db_name], document_models=BeanieDocRegistry.
all_documents())` (`provider.py:87-93`); `init_beanie` binds each Document **class** to
one database via class-level state, and `BeanieDocRegistry._registry` is a
**process-global dict keyed by domain class** (`factory.py:90`). A second `init_beanie`
with a different database therefore **rebinds every Document class globally** — last
tenant wins and every tenant silently reads one database.

Chosen: **per-tenant Document class clones + one `init_beanie` per tenant database.**

- ✅ Public Beanie API only — no monkeypatching, version-stable.
- ✅ Reuses the mapper seam: a binding-backed provider hands
  `AsyncBeanieRepository`/`BeanieUnitOfWork` their clone+mapper, so services above are
  untouched.
- ✅ Plugs into `DynamicTenantUoWProvider` — one provider per tenant, the shape
  `TenantUoWProvider.register()` already expects.
- ❌ `N_active_tenants × N_models` classes (RD-7) — bounded by `max_entries`, documented
  prominently with a worked example.
- ❌ Clones must **bypass `BeanieDocRegistry`** (keyed by domain class → clones would
  overwrite each other). `BeanieDocRegistry.get(User)` keeps returning the **base**
  class; that is the documented contract.
- ❌ `init_beanie` is `async` → cannot run inside the **sync** `IUoWProvider.make_uow()`,
  which is why an async pre-warm step exists and why `TenantUoWProvider` is left alone.

**Rejected: `get_motor_collection()` interception** — ✅ zero duplication, ✅ no
pre-warm; ❌ depends on Beanie internals, ❌ ContextVar read per collection access, ❌ a
miss silently hits the default database (fail-open). **Rejected: `with_options()`** —
❌ cannot switch database.

### DESIGN: global/shared scope and the dual-UoW constraint

**Per-strategy semantics.**

- **`SHARED` (± RLS).** A global entity simply carries no `tenant_id` and no RLS policy.
  Nothing routes, so `GLOBAL` is a no-op for storage — but **not** for validation:
  `assert_rls_enabled()` must **skip** `GLOBAL` tables, otherwise RD-6's assertion flags
  every shared reference table as "missing a policy" and the feature is unusable. A
  real trap; it gets its own test.
- **`SCHEMA`.** Global tables stay in the untranslated default/shared schema (no
  symbolic token). Because tenant schemas and the global schema live in **one
  database**, a query may join a tenant table to a global table **inside one
  transaction, on one connection, transactionally consistent**. A genuine advantage of
  `SCHEMA` over `DATABASE`; it is in the decision table.
- **`DATABASE` (Postgres and Mongo).** Global entities live in the shared/control-plane
  database (RD-10: the same physical database as the control plane by default,
  overridable with `VARCO_TENANCY_GLOBAL_DSN`), so reading them needs a **second,
  non-routed UoW**. **A single transaction cannot span a tenant database and the global
  database — there is no 2PC.** Any workflow that must change both atomically goes
  through the existing transactional outbox (write locally + relay) or the saga
  primitives. This is the load-bearing constraint of the whole feature.

**DESIGN: `GlobalUoWProvider` as a distinct DI token (chosen)**

```python
class ArtifactService(AsyncService[Artifact, UUID, ...]):
    def __init__(self, uow_provider: Inject[GlobalUoWProvider], ...): ...
    def _get_repo(self, uow): return uow.artifacts
```

- ✅ **No change to `IUoWProvider`'s ABC.** Its `make_uow()` is `def` with no
  parameters; every existing implementation (including external subclasses) keeps
  working untouched.
- ✅ A distinct wrapper type = a distinct DI binding, which is how providify
  disambiguates two things that are both "a UoW provider" (the repo's typed-token
  idiom). `Inject[GlobalUoWProvider]` vs `Inject[IUoWProvider]` is unambiguous.
- ✅ Explicit at the call site: a service's constructor states which scope it touches,
  and the impossibility of a cross-scope transaction becomes **visible in the type
  signature** — you cannot accidentally obtain one UoW spanning both.
- ✅ Under `SHARED`/`SCHEMA` it is just the ordinary non-routed provider, so the same
  service code runs unchanged across all strategies.
- ❌ A service needing both scopes injects both and sequences two transactions. That is
  the truth of the underlying system, not an API artifact.
- **Rejected: `make_uow(scope=...)`** — ❌ changes the ABC signature, breaking every
  external `IUoWProvider` implementation; ❌ pushes the decision to every call site.
- **Rejected: scope-aware routing inside `DynamicTenantUoWProvider`** — ❌ `make_uow()`
  does not know which entity is about to be used; the UoW carries all repositories.
- **Rejected: one UoW exposing both scopes** — ❌ actively invites the "one transaction
  across two databases" bug this section exists to prevent.

**Service layer.** A global-entity service must **not** mix in `TenantAwareService` —
`_scoped_params` would filter on a non-existent `tenant_id` (error at best, silently
empty results at worst) and `_prepare_for_create` would stamp a non-existent field.
Decision: **no `GlobalScopedService` mixin** (a marker with an empty body is noise), but
the **mistake is guarded** — `validate_service_scope()` raises `TenantIsolationError`
when a service with `TenantAwareService` in its MRO serves a `GLOBAL` entity, and warns
in the reverse direction. Tested both ways.

**DESIGN: writes to global data are a privilege boundary (RD-10)**
The app-facing global credential is **read-only by default**; writes go through the
control plane.
- ✅ Pairs with RD-4: an app pod is not merely *asked* not to write shared reference
  data, it is *unable* to. Defence is at the credential, not at a code convention.
- ✅ Blast radius: a compromised or buggy app pod cannot corrupt data every tenant
  reads (the highest-fan-out data in the system).
- ❌ A legitimate app-side write needs an explicit opt-in. Provided:
  `TenancySettings(global_writable=True)` / `VARCO_TENANCY_GLOBAL_WRITABLE=true`, which
  resolves a separate writable DSN; the docs recommend routing the write through the
  control plane instead.
- ❌ The failure arrives from the driver as a bare SQLSTATE `42501`
  (`InsufficientPrivilege`) — an unhelpful traceback. **Mitigated:** the global UoW
  translates `42501` into `GlobalScopeReadOnlyError` (a `TenantIsolationError`
  subclass) naming the entity, stating the global credential is read-only by default,
  and giving both remedies (`global_writable=True`, or route via the control plane).
  The contract lives in `varco_core.tenancy`; the SQLSTATE detection lives in
  `varco_sa.tenancy.global_scope`, keeping driver specifics out of core.
- Router-level, in addition: global write routes ship role-guarded, and the docs' recipe
  uses `ReadOnlyRouter` for the tenant-facing surface.

**Caching.** The existing "cache key collision" pitfall gains a symmetric twin: a
`TENANT`-scoped key that is *not* tenant-namespaced is a **cross-tenant leak**; a
`GLOBAL`-scoped key that *is* namespaced is **N× cache waste and N× DB load** (correct
but expensive). Both get a pitfall row and a helper (`tenancy_cache_key()`) that
namespaces iff the entity is `TENANT`-scoped.

**Migrations.** Global + framework tables are migrated **once**, by the ordinary
non-fanned-out run; the fan-out migrator targets **tenant-scoped metadata only**.
**Required ordering: the global/framework run completes BEFORE the tenant fan-out**,
because tenant tables may carry foreign keys to global tables. `varco migrate upgrade
--all-tenants` enforces the order internally; skipping needs an explicit
`--skip-global`.

### DESIGN: control-plane deployment — standalone by default, bundleable on request (RD-9)

Shared modules, no new workspace package. `varco_core.tenancy.control` holds
contracts/events/orchestration; `varco_sa.tenancy.admin` holds the admin engine and
cluster-DDL provisioner; `varco_fastapi.tenancy` exposes `build_tenant_router(...)` and
`mount_tenant_admin(...)`. **Two supported shapes:**

```
STANDALONE (default, recommended)          BUNDLED (explicit opt-in)
┌──────────────┐  ┌──────────────┐         ┌───────────────────────────┐
│ app pod      │  │ control pane │         │ one app                   │
│ no admin DSN │  │ ADMIN_DSN ✔  │         │ ADMIN_DSN ✔ + ack ✔       │
│ no admin rt  │  │ admin router │         │ tenant routes + admin rt  │
└──────────────┘  └──────────────┘         └───────────────────────────┘
 privilege absent from app pods             privilege present — guarded, logged
```

- ✅ No eleventh workspace package: no extra pyproject/release/docs/DI-bootstrap cost,
  and it would have depended on `varco_sa` + `varco_fastapi` anyway, so the separation
  it buys is of *deployment*, which the two shapes already give.
- ✅ Standalone keeps RD-4 structural: an app pod without the admin DSN **cannot
  construct** `SADatabaseProvisioner` (it raises), so cluster DDL is unreachable rather
  than discouraged.
- ✅ Bundled serves the real cases the user named: single-container/PaaS deployments,
  dev, and small installs where a second container is not justified.
- ❌ Bundling puts the admin DSN in the app pod's environment — exactly the privilege
  RD-4 keeps out. Confronted below rather than hidden.

**DESIGN: `mount_tenant_admin(app, …)` over a `create_varco_app(tenant_admin=…)` kwarg**
- ✅ **Matches the two existing precedents for "an extra mounted surface"**:
  `SkillAdapter.mount(app, legacy_paths=True)` and
  `app.include_router(build_policy_router(engine, server_auth=auth))`. Neither is a
  `create_varco_app` kwarg. `create_varco_app`'s kwargs (`migrations=`,
  `enable_profiling=`) configure the app's *own lifecycle/behaviour*; a privileged
  admin surface is an additional surface, so it reads as the same idiom.
- ✅ **Grep-able**: `grep -rn mount_tenant_admin` finds every deployment that exposes
  provisioning. A kwarg buried in a `create_varco_app(...)` call is far easier to
  acquire by config copy-paste and harder to audit.
- ✅ **Impossible to enable by environment alone.** There is deliberately **no**
  `VARCO_TENANCY_MOUNT_ADMIN` env var. An app that merely happens to have
  `VARCO_TENANCY_ADMIN_DSN` set exposes **nothing** — this directly satisfies the
  accidental-bundle requirement, which a kwarg defaulting off would satisfy less
  robustly (config templating sets kwargs).
- ❌ Two calls instead of one for the bundled case. Accepted — the friction is the point.

```python
from varco_fastapi.tenancy import mount_tenant_admin

app = create_varco_app(container, routers=[...])  # tenant traffic
mount_tenant_admin(  # ← privileged surface, opt-in
    app,
    control_service,
    acknowledge_bundled_admin=True,  # required; ValueError without it
    server_auth=auth,
    admin_role="tenant-admin",  # distinct from a generic "admin"
    prefix="/tenancy",
    dependencies=[Depends(ip_allowlist)],  # optional extra network-level gate
)
```

Security consequences, each with a concrete mechanism:
1. **Explicit acknowledgement, not env presence.** `acknowledge_bundled_admin` defaults
   `False` → `ValueError` naming the trade-off and the standalone alternative. Env vars
   never mount the router.
2. **Role-guarded with a dedicated role.** Default `admin_role="tenant-admin"`, *not*
   the generic `"admin"` — so an existing app admin does not silently gain tenant
   provisioning. Unauthenticated/underprivileged calls are **403, not 500**
   (`_auth` must be present; the router refuses to mount without it, mirroring the
   `requires=` without `_auth` rule).
3. **Independently gateable.** A dedicated `prefix` (default `/tenancy`) so an ingress
   can deny it externally, plus a `dependencies=` passthrough for an extra guard
   (IP allowlist, mTLS check).
4. **One WARNING at mount**, naming the trade-off, the role, and the prefix — so the
   privileged surface is visible in startup logs, not only in code.
5. **Accidental bundling is impossible**: no env-var path, an explicit acknowledgement,
   and a mandatory `server_auth`. Three independent barriers.

**Required Postgres grants (docs):** the control-plane role needs `CREATEDB` (and
`CREATEROLE` only if per-tenant roles are used — out of scope); the app role needs
**neither**, plus `CONNECT` on tenant databases and `USAGE`/DML on tenant schemas only,
and **read-only** on the global schema by default (RD-10). Supported alternative:
`ExternalTenantProvisioner`, which records intent and returns, with databases created by
DBA/Terraform out of band — the `status` lifecycle handles the asynchrony.

### DESIGN: REST admin surface — plain `APIRouter` (`build_tenant_router`)

Chosen: a plain FastAPI `APIRouter` factory, mirroring
`build_policy_router(engine, server_auth=..., admin_role="admin")`.

- ✅ Direct precedent for exactly this: a standalone admin surface with JSON-body
  handlers guarded by `require_roles(admin_role)`.
- ✅ Provisioning is **not CRUD** — `POST /tenants` is an orchestration (validate →
  create schema/db → migrate → activate) with intermediate states, not a repository
  `create()`. `VarcoCRUDRouter`'s generated CRUD is the wrong shape and its
  `D/PK/C/R/U` args would have to be invented for a control-plane resource.
- ✅ The catalog is an `AbstractTenantCatalog`, not an `AsyncService`, so
  `VarcoCRUDRouter`'s service seam does not apply.
- ❌ No automatic query/filter/pagination. Acceptable — `list_tenants(status=...)`
  covers a small list.

Surface: `POST /tenancy/tenants`, `GET /tenancy/tenants`, `GET /tenancy/tenants/{id}`,
`PATCH /tenancy/tenants/{id}` (suspend/resume), `DELETE /tenancy/tenants/{id}`
(requires an explicit confirm field), `POST /tenancy/tenants/{id}/migrate`.

### DESIGN: event-driven onboarding, idempotency, cross-pod visibility (RD-1)

```
TenantProvisionRequested / TenantDeprovisionRequested   (DomainEvent, "varco.tenancy")
        ↓  @listen(..., retry_policy=RetryPolicy.durable_delivery(), dlq=...)
TenantProvisionConsumer(EventConsumer)      register_to(bus) in @PostConstruct
        ↓  idempotency: the EXISTING inbox/dedup primitives, not a new mechanism
TenantControlService.provision(tenant_id)   status: pending → active
        ↓  emits
TenantCatalogChanged  →  every pod's CachedTenantCatalog invalidates
```

- **Retry/DLQ is mandatory here.** A dropped provision event means a paying tenant that
  never exists. `RetryPolicy.durable_delivery()` + a `dlq=` are wired by default,
  following `AuditConsumer`'s safe-by-default precedent.
- **Idempotency reuses what exists.** (1) the consumer is wrapped by the existing
  **inbox** pattern, whose optimistic `UPDATE ... WHERE processed_at IS NULL`
  (`varco_core/service/inbox.py:274-280`) is already the documented idempotency
  primitive; (2) `provision()` is itself idempotent — an `active` tenant returns without
  DDL (`CREATE SCHEMA IF NOT EXISTS`, existence probe before `CREATE DATABASE`).
  **No new dedup mechanism is invented.**
- **Status lifecycle and routing:**

  | status | in `list_tenants()` default | request routing | migration fan-out |
  |---|---|---|---|
  | `pending` | no | **rejected** (503 — provisioning in flight) | no |
  | `active` | yes | routed normally | **yes** |
  | `suspended` | no | **rejected** (403 — named reason) | yes (kept current, so resume is instant) |
  | `deprovisioning` | no | **rejected** (410) | no |
  | `deleted` | no | rejected (404); descriptor kept as a tombstone | no |

  Routing consults the catalog **before** `pool.ensure()`, so a non-`active` tenant
  never causes an engine/binding to be created.

**DESIGN: cross-pod catalog visibility — event invalidation + TTL backstop**
Chosen: `CachedTenantCatalog` with **all three** of (a) `TenantCatalogChanged`
invalidation, (b) a `catalog_ttl_s = 60.0` re-read, (c) read-through on a miss
(rate-limited for unknown ids).
- ✅ (a) sub-second propagation, so REST/queue onboarding feels immediate.
- ✅ (b) the self-healing backstop: a **dropped invalidation event** would otherwise make
  a tenant permanently invisible on one pod — the worst class of bug here, and buses do
  drop messages.
- ✅ (c) onboarding is instant even on a pod that missed the event entirely.
- ❌ Three mechanisms; ❌ up to `catalog_ttl_s` staleness for a *suspension* (mitigated:
  suspension also goes through (a), and routing re-reads on deny).
- **Rejected: TTL-only** — ❌ up to a minute of 404s for a new tenant.
  **Rejected: event-only** — ❌ one dropped message = a permanently invisible tenant.
  **Rejected: no cache** — ❌ a DB round trip on every request for daily-changing data.

### DESIGN: `TenantFanoutSupervisor` — one relay per tenant vs one loop over tenants (RD-8)

Under `DATABASE`, a tenant's `OutboxEntry`, `Job`, and `AuditEntry` rows live in *that
tenant's* database, which the single process-wide `OutboxRelay`/`JobPoller`/
`AuditConsumer` never polls — **events silently never published**.

Chosen: **one supervised `OutboxRelay` (and job poller, and audit consumer) per active,
pool-resident tenant**, owned by a `TenantFanoutSupervisor`.

- ✅ **Reuses `OutboxRelay` verbatim.** The alternative forks the retry/DLQ/
  `max_attempts` semantics Plan 005 Phase 3 deliberately built into
  `OutboxRelay.__init__` (`outbox.py:482-492`); a tenant-parameterised re-implementation
  would duplicate and drift from it. Decisive.
- ✅ **Failure isolation is structural**: each relay runs in its own supervised task; a
  crash is caught, logged, restarted with capped exponential backoff, and one tenant's
  failure cannot stop another's.
- ✅ **Already bounded by the engine pool** — only pool-resident tenants get relays, so
  `max_entries` bounds connections *and* pollers with one knob. No second bound.
- ✅ Per-tenant `poll_interval`/`batch_size` become possible.
- ❌ N asyncio tasks and N × (1/`poll_interval`) queries/sec. Mitigated by a **startup
  stagger** (tenant *i*'s first tick offset by `i × poll_interval/N`) plus the pool bound.
- **Rejected: a single loop iterating tenants** — ✅ one task, trivially bounded;
  ❌ **head-of-line blocking** (one slow/failing tenant delays every other tenant's
  events, and fixing it means adding concurrency control until it converges on the
  chosen design); ❌ requires re-implementing `OutboxRelay`'s retry/DLQ semantics per
  tenant. Reuse outweighs the resource argument, especially since the pool bounds N.

Contract: `start()`/`stop()` (lifespan-driven, LIFO), `on_tenant_activated(tid)` /
`on_tenant_deactivated(tid)` driven by `TenantCatalogChanged` and pool eviction,
`aclose()` awaiting every child's `stop()`. **Enabled vs structural:** the supervisor
ships **working and tested for `OutboxRelay`** (integration test proves a tenant-DB
outbox entry is genuinely published), gated behind
`TenancySettings.fanout_framework_tables` (default `False`, since db-per-tenant is
itself opt-in). Job-poller and audit-consumer children ship on the same supervisor with
the same wiring seam and their own coverage (Phase 8 acceptance). Nothing is advertised
that is not exercised.

### Decision table

Ceiling figures are **order-of-magnitude, environment-dependent guidance — varco
enforces no cap and does not choose for you (RD-5).**

| | 1. PG shared | 2. PG shared + RLS | 3. PG schema/tenant | 4. PG database/tenant | 5. Mongo shared | 6. Mongo database/tenant |
|---|---|---|---|---|---|---|
| **Isolation strength** | weakest — app-layer only, **fails open** on any bypassing query | strong — DB-enforced per row | strong — separate namespace; a wrong query errors rather than leaks | strongest — separate DB, separate credentials possible | weakest — app-layer only, fails open | strongest available on Mongo |
| **Blast radius of a bug** | all tenants | one tenant | one tenant | one tenant | all tenants | one tenant |
| **Ops cost** | none | one reviewed RLS revision per table | schema provisioning per tenant | DB provisioning, per-DB backup/restore, connection budgeting, **relay fan-out** | none | DB provisioning per tenant, **relay fan-out** |
| **Tenant-count guidance** | ~unbounded | ~unbounded | 1 000s (PG catalog bloat, slower `pg_dump`) | 10s–100s (connection + poller budget) | ~unbounded | 10s–100s (client/clone budget) |
| **Migration cost** | 1 run | 1 run | 1 global + **N** | 1 global + **N** | 1 run | 1 global + **N** |
| **Noisy neighbour** | full | full | mostly shared (same DB/WAL/vacuum) | isolated per DB (host still shared) | full | isolated |
| **Cross-tenant queries** | trivial | privileged role/policy bypass | hard — UNION across schemas | very hard — cross-DB | trivial | very hard |
| **Per-tenant backup / erase** | hard (row surgery) | hard | medium (`pg_dump -n`) | **easy** (`pg_dump` / `DROP DATABASE`) | hard | **easy** (`dropDatabase`) |
| **Global / shared reference data** | trivial — same tables, no `tenant_id` | trivial — global tables skipped by the RLS assertion | ✅ **same-database join with tenant tables in one transaction** | ⚠️ separate DB → **dual UoW, no cross-scope transaction; read-only from app pods by default** | trivial | ⚠️ separate DB → dual UoW, no cross-scope transaction |
| **Framework tables (outbox/jobs/audit)** | shared ✅ | shared ✅ | shared, untranslated schema ✅ | per-tenant — **requires the fan-out supervisor (Phase 8)** | shared ✅ | per-tenant — requires the fan-out supervisor |
| **Control plane** | optional | optional | recommended (schema provisioning) | **required** (DB provisioning) | optional | **required** |
| **Pick it when** | internal / low-risk | **default recommendation** — DB-enforced *and* scale-unbounded | namespace separation required, per-tenant `pg_dump`, you join global↔tenant data a lot | contractual hard isolation, few large tenants, per-tenant restore required | internal / low-risk | per-tenant hard isolation or `dropDatabase` erasure |

**Headline recommendation for the docs:** strategy **2** suits almost everyone — the only
option that is both DB-enforced and scale-unbounded. 3/4/6 are for contractual,
regulatory, or per-tenant-restore requirements, not performance.

### Alternatives considered (feature level)

- **Do nothing; document `TenantUoWProvider` as the extension point.** ✅ zero code;
  ❌ every caller re-invents the bounded pool, disposal, `init_beanie` cloning,
  migration fan-out, and relay fan-out — the five genuinely hard parts; ❌ the sync
  `make_uow()` makes the Mongo path a runtime trap. **Rejected.**
- **Six flat enum values.** ✅ explicit; ❌ collapses two orthogonal axes (backend, RLS)
  into one and would double again for `TenantScope`. **Rejected.**
- **A providify request/tenant DI scope.** ✅ the "clean" answer; ❌ a framework change
  with its own teardown semantics when the ContextVar pattern already works and is
  tested. **Rejected — out of scope.**
- **Per-request engine creation, disposed after.** ✅ no bookkeeping; ❌ TCP+TLS+auth per
  request, defeats pooling. **Rejected.**

---

## Phases

Each phase is independently mergeable and testable and leaves `main` green with default
behaviour unchanged. Tests precede implementation within each phase.

### Phase 1 — Core contracts (`varco_core.tenancy`), no backend deps

1. [ ] `varco_core/tests/test_tenancy_settings.py` — failing tests: defaults
   (`isolation=SHARED`, `enforce_rls=False`, `max_entries=50`, `idle_ttl_s=300.0`,
   `catalog_ttl_s=60.0`, `fanout_framework_tables=False`, `global_writable=False`);
   `from_env()` for `VARCO_TENANCY_ISOLATION`, `_ENFORCE_RLS`, `_SCHEMA_TEMPLATE`,
   `_DB_TEMPLATE`, `_MAX_ENTRIES`, `_IDLE_TTL`, `_CATALOG_TTL`,
   `_FANOUT_FRAMEWORK_TABLES`, `_GLOBAL_DSN`, `_GLOBAL_WRITABLE`; **no
   `VARCO_TENANCY_MOUNT_ADMIN` key is recognised** (RD-9 — asserted, so the admin
   surface can never be enabled by env); invalid enum → `ValueError`; frozen.
2. [ ] `varco_core/varco_core/tenancy/settings.py` — `TenantIsolation`, `TenantScope`,
   `TenantStatus` (`StrEnum`s), `TenancySettings` (frozen, `from_env(source=None)`
   mirroring `MigrationSettings.from_env()`).
3. [ ] `varco_core/tests/test_tenant_catalog.py` — failing tests: `StaticTenantCatalog`
   `list_tenants()` sorted+deterministic (fan-out must be reproducible), filtering to
   `active` by default, `status=` filter; unknown → `TenantNotFoundError`;
   `add`/`remove` idempotent; `TenantDescriptor` frozen with defaults (`schema=None`,
   `database=None`, `dsn_ref=None`, `status=pending`).
4. [ ] `varco_core/varco_core/tenancy/catalog.py` — `TenantDescriptor`,
   `AbstractTenantCatalog` (`list_tenants`, `get`, `add`, `update_status`, `remove`),
   `StaticTenantCatalog`, `TenantNotFoundError`, `TenantIsolationError`.
5. [ ] `varco_core/tests/test_tenant_pool.py` — failing tests: `ensure()` creates once
   and caches; 10 concurrent `ensure()` for one tenant call the factory **once**;
   `peek()` never creates; LRU evicts the least-recently-used entry at
   `max_entries + 1` and calls `closer`; an entry under `lease()` is **never** evicted
   and the cap is exceeded with one WARNING; the `idle_ttl_s` sweep closes idle
   entries; `aclose()` closes all, idempotent; a raising factory leaves **no poisoned
   entry**; a raising `closer` is logged and swallowed and remaining entries still close.
6. [ ] `varco_core/varco_core/tenancy/pool.py` — `TenantResourcePool[T]`; lazy
   per-tenant `asyncio.Lock`; `lease()`; `start_sweeper()`/`stop_sweeper()`;
   `__aenter__`/`__aexit__`.
7. [ ] `varco_core/tests/test_dynamic_tenant_uow.py` — failing tests: `make_uow()`
   outside `tenant_context()` → `RuntimeError` (message shape matching
   `TenantUoWProvider`'s); tenant active but not `ensure()`d → `RuntimeError` naming
   `ensure()` (**asserted: never returns a default provider**); after `ensure()` → that
   tenant's provider; two tenants → two providers.
8. [ ] `varco_core/varco_core/tenancy/provider.py` — `DynamicTenantUoWProvider`
   (`IUoWProvider`) + `tenant_session()` helper. Imports `current_tenant()` from
   `varco_core.service.tenant`; **`tenant.py` is not modified.**
9. [ ] `varco_core/varco_core/tenancy/provisioner.py` +
   `varco_core/tests/test_tenant_provisioner.py` — `AbstractTenantProvisioner`
   (`provision`, `deprovision(*, confirm_destroy=False)`) and
   `ExternalTenantProvisioner` (the no-op/DBA-workflow implementation, RD-4). Failing
   test: `deprovision(confirm_destroy=False)` raises `DestructiveOperationRefused` for
   every implementation **including a subclass that overrides and calls `super()`** —
   the gate lives in the ABC so no backend can forget it.
10. [ ] `varco_core/varco_core/__init__.py` — re-export the new names. **Grep for
    collisions first** (the `MigrationError`/`MigrationPlan` lesson from Plan 006:
    `varco_core.migrator` already squats two names). Add a test asserting each new
    top-level export resolves to the `varco_core.tenancy` class.

**Acceptance:** `uv run pytest varco_core/tests/ -q` green; `make type-check` clean;
`grep -rn "sqlalchemy\|pymongo\|beanie" varco_core/varco_core/tenancy/` empty; no
existing test modified; no env var can enable an admin surface.

### Phase 2 — Global/shared scope: `TenantScope`, dual UoW, read-only default (RD-10)

1. [ ] `varco_core/tests/test_meta_tenant_scope.py` — failing tests:
   `MetaReader.read(cls).tenant_scope` is `TENANT` when `Meta.tenant_scope` is absent
   (**default preserves today's behaviour**); `GLOBAL` when declared; an invalid value
   raises `ValueError` naming the field; `ParsedMeta` stays frozen and the field is
   defaulted (constructible without it).
2. [ ] `varco_core/varco_core/meta.py` — add `tenant_scope: TenantScope =
   TenantScope.TENANT` to `ParsedMeta` (defaulted, appended last) and read
   `Meta.tenant_scope` in `MetaReader.read()`. No other behaviour change.
3. [ ] `varco_core/tests/test_global_uow_provider.py` — failing tests:
   `GlobalUoWProvider.make_uow()` works **outside** any `tenant_context()` (the
   defining difference from `DynamicTenantUoWProvider`); it ignores an active tenant
   context entirely (asserted — a global read must not be tenant-routed); it is a
   distinct type from `IUoWProvider` so DI can bind both.
4. [ ] `varco_core/varco_core/tenancy/global_scope.py` — `GlobalUoWProvider`,
   `is_global_entity(entity_cls)`, and `GlobalScopeReadOnlyError`
   (`TenantIsolationError` subclass) whose message names the entity, states the global
   credential is read-only by default (RD-10), and gives both remedies
   (`global_writable=True` / route the write through the control plane).
5. [ ] `varco_sa/tests/test_global_readonly_translation.py` — failing tests: a
   `DBAPIError` carrying SQLSTATE **`42501`** raised through the global UoW is
   translated into `GlobalScopeReadOnlyError` (**not** a raw driver traceback), with
   the entity name, the doc path, and both remedies in the message (asserted on the
   text); a `42501` from a **tenant** UoW is *not* translated (it is a genuine
   permission bug, not the read-only-global design); with `global_writable=True` no
   translation wrapper is installed at all.
6. [ ] `varco_sa/varco_sa/tenancy/global_scope.py` — the SQLSTATE → error translation.
   Driver specifics stay out of `varco_core`.
7. [ ] `varco_core/tests/test_scope_guard.py` — failing tests:
   `validate_service_scope()` raises `TenantIsolationError` when a service with
   `TenantAwareService` in its MRO serves a `GLOBAL` entity (message names both the
   service and the entity and says to drop the mixin); the reverse (a `TENANT` entity
   served with no tenant filtering under `SHARED`) logs one WARNING, not an exception;
   a correct pairing in either direction is silent.
8. [ ] `varco_core/varco_core/tenancy/scope_guard.py` — `validate_service_scope()`.
9. [ ] `varco_core/tests/test_tenancy_cache_key.py` — failing tests:
   `tenancy_cache_key(TenantEntity, "42")` **is** tenant-namespaced;
   `tenancy_cache_key(GlobalEntity, "42")` is **not**, and is byte-identical under two
   different active tenants (the N×-waste direction); outside a tenant context a
   `TENANT`-scoped key **raises** rather than emitting an unnamespaced key (the leak
   direction fails closed).
10. [ ] `varco_core/varco_core/tenancy/cache_key.py` — `tenancy_cache_key()`.

**Acceptance:** `varco_core/tests/` + `varco_sa/tests/` green; a `TENANT`-scoped cache
key can never be produced unnamespaced; a denied global write produces a legible varco
error naming the remedy; `ParsedMeta`'s default proves byte-identical metadata for every
existing model.

### Phase 3 — Postgres schema-per-tenant + RLS assertion (RD-3, RD-6)

1. [ ] `varco_sa/tests/test_schema_router.py` — failing tests: the session factory's
   bind carries `schema_translate_map={"tenant": "t_acme"}`; compiled SQL for a routed
   model is `t_acme.`-qualified; a **`GLOBAL`** model and the framework tables are
   **not** translated; `schema_name_for("acme")` applies `schema_template` and
   **rejects** identifiers failing `^[A-Za-z_][A-Za-z0-9_]*$` with `ValueError` (schema
   names cannot be bound parameters — the only injection defence);
   `mechanism="search_path"` emits `set_config('search_path', …, true)` and **never** a
   bare `SET`.
2. [ ] `varco_sa/varco_sa/tenancy/router.py` — `SASchemaRouter`.
3. [ ] `varco_sa/tests/test_factory_symbolic_schema.py` — failing tests: under `SHARED`
   the generated `__table__.schema is None` (**byte-identical default**); under
   `SCHEMA` a `TENANT` model carries the symbolic token; a `GLOBAL` model does **not**;
   the ten framework tables never do.
4. [ ] `varco_sa/varco_sa/factory.py` — thread the symbolic schema through
   `SAModelFactory.build()` behind an explicit defaulted parameter, keyed off
   `ParsedMeta.tenant_scope`.
5. [ ] `varco_sa/tests/test_schema_provisioner.py` (+ `…_integration.py`,
   `@pytest.mark.integration`) — unit: `CREATE SCHEMA IF NOT EXISTS "t_acme"` (quoted,
   validated); `DROP SCHEMA … CASCADE` only with `confirm_destroy=True`. Integration
   (real Postgres): provision two schemas, `create_all` into each, insert the same PK
   in both, assert full read isolation, and assert **a join from a tenant table to a
   global table in the default schema succeeds inside one transaction** (the
   `SCHEMA`-strategy advantage in the decision table).
6. [ ] `varco_sa/varco_sa/tenancy/provisioner.py` — `SASchemaProvisioner`. Docstring
   notes `CREATE SCHEMA` **is** transactional in Postgres, unlike `CREATE DATABASE`
   (Phase 6).
7. [ ] `varco_sa/tests/test_rls_assertion.py` — failing tests: `assert_rls_enabled()`
   returns tables missing a policy (via `pg_policies`/`pg_class.relrowsecurity`);
   `enforce_rls=True` + a missing policy → `TenantIsolationError`; **`GLOBAL` tables and
   the framework tables are skipped, not flagged** (the RD-6 trap); the message contains
   the table name, the literal remediation
   `varco_sa.migration.ops.rls_upgrade(op, "<table>")`, and the doc path
   `technical_docs/features/postgres-rls.md` (RD-6 — asserted on the text);
   `enforce_rls=False` never queries; a non-Postgres dialect skips with one WARNING.
8. [ ] `varco_sa/varco_sa/tenancy/rls_check.py` — `assert_rls_enabled()`. **Assert
   only; never emits DDL.**
9. [ ] `varco_sa/tests/test_sa_tenancy_di.py` — `container.scan("varco_sa")` +
   `container.validate_bindings()`.

**Acceptance:** `varco_sa/tests/` green incl. `-m integration`; default model generation
provably unchanged; the RLS error text asserted to teach the fix.

### Phase 4 — Durable catalog + status lifecycle + cross-pod invalidation (RD-1)

1. [ ] `varco_sa/tests/test_sa_tenant_catalog.py` (+ integration) — failing tests:
   round-trip of every `TenantDescriptor` field; `list_tenants()` filters to `active` by
   default and is deterministically ordered; `update_status()` transitions and
   **rejects illegal transitions** (`deleted → active`) with `ValueError`; `add()` twice
   is idempotent; a **literal DSN** in `dsn_ref` is **rejected** (RD-2 — secret
   reference only) unless `allow_literal_dsn=True`.
2. [ ] `varco_sa/varco_sa/tenancy/models.py` + `catalog.py` — `varco_tenants` as the
   **tenth framework table** (`tenant_id` PK, `schema_name`, `database_name`, `dsn_ref`,
   `status`, `created_at`, `updated_at`), self-registering via
   `register_framework_metadata()` like the other nine, and forced `TenantScope.GLOBAL`.
3. [ ] `varco_sa/migrations/versions/<rev>_varco_tenants.py` — baseline revision on
   `branch_labels=("varco",)`, `has_table()`-guarded/idempotent per Plan 006 D3, added
   to `adopt_framework_tables()`'s table list + its test.
4. [ ] `varco_beanie/tests/test_beanie_tenant_catalog.py` — the same contract suite
   against `BeanieTenantCatalog` (`varco_tenants` collection).
5. [ ] `varco_beanie/varco_beanie/tenancy/catalog.py` — `BeanieTenantCatalog`.
6. [ ] `varco_core/tests/test_cached_catalog.py` — failing tests: a `get()` hit does not
   touch the store; `TenantCatalogChanged` invalidates immediately; `catalog_ttl_s`
   expiry triggers a re-read (**the dropped-event backstop**); a **miss** reads through
   immediately and repeated misses for the same unknown id are rate-limited to one store
   read per window; a `suspended` tenant stops routing after invalidation.
7. [ ] `varco_core/varco_core/tenancy/cached_catalog.py` — `CachedTenantCatalog` (lazy
   `asyncio.Lock`, frozen snapshot swap).
8. [ ] `varco_core/tests/test_tenant_status_routing.py` — failing tests: the routing
   check maps `pending`→503, `suspended`→403, `deprovisioning`→410, `deleted`/unknown
   →404; **a non-`active` tenant never causes `pool.ensure()` to run** (asserted with a
   counting fake).

**Acceptance:** the catalog contract suite passes against static/SA/Beanie
implementations; the status→routing table is enforced by tests; `varco_tenants` is
adoptable by `varco migrate adopt`.

### Phase 5 — Control-plane surfaces: REST + events, standalone and bundled (RD-1, RD-9)

1. [ ] `varco_core/tests/test_tenant_control_service.py` — failing tests: `provision()`
   is **idempotent** (a second call on an `active` tenant performs no DDL — counting
   fake provisioner); it drives `pending → active` and emits `TenantCatalogChanged`; a
   provisioner failure leaves status `pending` (not `active`) and re-raises;
   `deprovision(confirm=False)` refuses; `deprovision(confirm=True)` drives
   `active → deprovisioning → deleted`, stops the tenant's fan-out children, and evicts
   the pool entry **before** destructive DDL; `suspend()`/`resume()` transitions.
2. [ ] `varco_core/varco_core/tenancy/control/service.py` — `TenantControlService`
   (catalog + provisioner + migrator + supervisor hooks + producer).
3. [ ] `varco_core/varco_core/tenancy/control/events.py` — `TenantProvisionRequested`,
   `TenantDeprovisionRequested`, `TenantCatalogChanged` (`DomainEvent`s, channel
   `"varco.tenancy"`).
4. [ ] `varco_core/tests/test_tenant_provision_consumer.py` — failing tests: the handler
   is `@listen(...)`-decorated with a `retry_policy` **and** a `dlq` by default
   (asserted on the decorator metadata — provisioning must not be silently lost);
   `register_to(bus)` happens in a `@PostConstruct` method, not `__init__`; a
   **redelivered** event is a no-op (inbox `mark_processed` + idempotent `provision()`,
   asserted with `InMemoryEventBus` + a counting provisioner); an exhausted retry lands
   in `InMemoryDeadLetterQueue` with `DeadLetterSource.CONSUMER` and the tenant id in
   `source_ref`; a deprovision event without the confirm flag is rejected and DLQ'd
   rather than executed.
5. [ ] `varco_core/varco_core/tenancy/control/consumer.py` —
   `TenantProvisionConsumer(EventConsumer)`; `_default_retry_policy =
   RetryPolicy.durable_delivery()` following `AuditConsumer`'s precedent; wrapped by the
   existing inbox primitives for idempotency.
6. [ ] `varco_fastapi/tests/test_tenant_router.py` — failing tests: every route is
   guarded by `require_roles(admin_role)` and a non-admin gets **403, not 500**;
   `POST /tenancy/tenants` provisions → 201 with the descriptor; a duplicate POST is
   idempotent (200), not 500; `DELETE` **without** the explicit confirm field is 400 and
   performs nothing; `PATCH` suspends/resumes; `GET` lists with a `status=` filter;
   `build_tenant_router` **refuses to build without `server_auth`** (a guard that can
   never be satisfied is a startup error, mirroring the `requires=`-without-`_auth`
   rule).
7. [ ] `varco_fastapi/varco_fastapi/tenancy/router.py` — `build_tenant_router(
   control_service, *, server_auth, admin_role="tenant-admin")`, a plain `APIRouter`
   mirroring `build_policy_router`. Imports only `varco_core.tenancy`.
8. [ ] `varco_fastapi/tests/test_mount_tenant_admin.py` — failing tests (**RD-9, the
   bundled-mode contract**):
   - **default = not mounted** — `create_varco_app(...)` exposes no `/tenancy/*` route
     even when `VARCO_TENANCY_ADMIN_DSN` **is set in the environment** (the
     accidental-bundle case, asserted explicitly);
   - **bundled without acknowledgement = refused** —
     `mount_tenant_admin(app, svc, server_auth=auth)` raises `ValueError` naming
     `acknowledge_bundled_admin` and the standalone alternative, and mounts nothing;
   - **bundled = mounted and role-guarded** — with `acknowledge_bundled_admin=True` the
     routes exist and an admin call succeeds;
   - **underprivileged/unauthenticated call to a bundled admin route = 403, not 500**
     (both a valid non-`tenant-admin` token and no token at all);
   - the default `admin_role` is `"tenant-admin"`, **not** `"admin"` — a generic app
     admin is refused (asserted);
   - **exactly one WARNING** is logged at mount, naming the trade-off, the role and the
     prefix;
   - `dependencies=` are applied to every mounted route;
   - mounting twice is refused rather than silently duplicating routes.
9. [ ] `varco_fastapi/varco_fastapi/tenancy/mount.py` — `mount_tenant_admin(app,
   control_service, *, acknowledge_bundled_admin=False, server_auth,
   admin_role="tenant-admin", prefix="/tenancy", dependencies=())`. **No env-var path
   exists** — the only way to mount is this call.
10. [ ] `examples/NN-tenant-control-plane/` — **both** shapes: `standalone/app.py` (its
    own app, admin DSN in its env only, mounts the router + provision consumer) and
    `bundled/app.py` (one app calling `mount_tenant_admin(..., acknowledge_bundled_admin
    =True)`), with a README comparing them and stating app pods in the standalone
    topology must not set `VARCO_TENANCY_ADMIN_DSN`.
11. [ ] `varco_fastapi/tests/test_tenancy_di.py` — `scan` + `validate_bindings()`.

**Acceptance:** onboarding works end-to-end over **both** REST and the bus against
in-memory fakes; a redelivered provision event performs no second DDL; a default app
exposes no admin route **even with the admin DSN present**; the bundled path is mounted
only via an acknowledged explicit call and answers 403 (never 500) to unauthorised
callers.

> **Amended by Plan 008 (Phase 1, RD-11):** step 5's consumer takes a
> `TenantControlService`, not an `AbstractTenantProvisioner`. Both control-plane entry
> points (REST step 7, bus step 5) must converge on the single catalog transition in step
> 2; a consumer that calls the provisioner directly produces storage with no catalog row,
> which `routing.py`/`TenantResolutionMiddleware` render as a permanent 404. Step 4's test
> list is extended with the event-path routability pairing. RD-13/RD-15 (Plan 008)
> additionally forbid any handler from emitting a command event, which constrains 007's
> event vocabulary in step 3.

### Phase 6 — Postgres database-per-tenant + admin confinement (RD-2, RD-4, RD-5, RD-10)

1. [ ] `varco_sa/tests/test_engine_registry.py` — failing tests: DSN built from
   `db_template`, and from the resolved `dsn_ref` (**override wins**, RD-2); one engine
   per tenant; `aclose()` calls `dispose()` on **every** engine; LRU eviction disposes;
   default per-tenant sizing `pool_size=1, max_overflow=2`; **a DSN credential never
   appears** in `repr()`, logs, or exception text (asserted by scanning captured log
   output for the password string).
2. [ ] `varco_sa/varco_sa/tenancy/engine_registry.py` — `SAEngineRegistry` over
   `TenantResourcePool[AsyncEngine]`; `provider_for(tid)` returning
   `SQLAlchemyRepositoryProvider.from_components(base=…, session_factory=…)` (the
   existing seam, `provider.py:117-155`); `SecretRef` resolution (env-var indirection /
   secret-manager hook) so `varco_tenants` never stores a literal credential.
3. [ ] `varco_sa/tests/test_global_dsn_resolution.py` — failing tests (**RD-10**): with
   no `VARCO_TENANCY_GLOBAL_DSN` the global provider binds the **app's own** engine;
   with it set, a separate engine is built and **disposed** on `aclose()`; the resolved
   global credential is **read-only by default** (the translation wrapper from Phase 2
   is installed) and `global_writable=True` omits it; the global DSN defaults to the
   control-plane database.
4. [ ] `varco_sa/tests/test_admin_engine_guard.py` — failing tests (**RD-4/RD-9**):
   `SADatabaseProvisioner` **cannot be constructed** without an explicit admin DSN —
   `ValueError` naming `VARCO_TENANCY_ADMIN_DSN`; it **refuses** an admin DSN equal to
   the request-path `SAConfig.engine`'s URL (an app pod must not be its own admin); the
   maintenance engine is `NullPool`, used, and **disposed in a `finally`**; a process
   serving tenant traffic **with** an admin DSN present but **no** acknowledged
   `mount_tenant_admin` logs one WARNING recommending the standalone topology (RD-9 —
   the DSN alone grants nothing and exposes nothing).
5. [ ] `varco_sa/varco_sa/tenancy/admin/engine.py` — `SAAdminEngine` (short-lived,
   `NullPool`, context-managed).
6. [ ] `varco_sa/tests/test_database_provisioner.py` (+ integration) — unit:
   `CREATE DATABASE` is issued with
   `execution_options(isolation_level="AUTOCOMMIT")` — asserted, because
   `CREATE DATABASE` **cannot run inside a transaction block**; an existence probe
   precedes it (idempotency for RD-1 redelivery); `DROP DATABASE` first disposes and
   evicts the tenant engine, then optionally `pg_terminate_backend`s stragglers with
   `force=True`; `confirm_destroy=False` refuses. Integration: create a real database,
   migrate it, insert, assert isolation from a second tenant DB, drop it.
7. [ ] `varco_sa/varco_sa/tenancy/admin/db_provisioner.py` — `SADatabaseProvisioner`.
   Docstring lists the required grants (`CREATEDB`), states the app role needs **none**
   of them and only read-only access to the global schema, and names
   `ExternalTenantProvisioner` as the supported DBA/Terraform path.
8. [ ] `varco_sa/tests/test_fanout_config_guard.py` — failing tests (**RD-8,
   re-framed as a config guard**): db-per-tenant configured **without**
   `fanout_framework_tables=True` while a relay/runner/audit consumer is wired raises
   `TenantIsolationError` explaining that tenant-database outbox rows would never be
   published **and naming `VARCO_TENANCY_FANOUT_FRAMEWORK_TABLES` as the flag that
   enables fan-out**; with the flag set, construction succeeds and the supervisor is
   wired; with no relay/runner configured at all, no error.
9. [ ] `varco_sa/varco_sa/tenancy/guard.py` — that config guard.

**Acceptance:** every engine `dispose()`d on shutdown (asserted); integration proves
real cross-database isolation; cluster DDL unreachable without an explicit admin DSN and
refused when it equals the app DSN; no credential reaches any log; the fan-out guard
points at its flag.

### Phase 7 — Mongo shared + database-per-tenant (RD-7)

1. [ ] `varco_beanie/tests/test_tenant_binding.py` — failing tests: clones are distinct
   classes per tenant; `init_beanie` awaited **once per tenant** with that tenant's
   database and only that tenant's clones; 10 concurrent `ensure()` for one tenant
   awaits `init_beanie` once; `BeanieDocRegistry.get(User)` still returns the **base**
   class (documented contract) and the **process-global registry is not polluted**;
   a `GLOBAL` entity binds to the shared database and is **not** cloned.
2. [ ] `varco_beanie/varco_beanie/tenancy/binding.py` — `BeanieTenantBinding` (frozen) +
   `build_tenant_binding(...)`.
3. [ ] `varco_beanie/tests/test_beanie_tenant_pool.py` — failing tests: eviction does
   **not** close a shared `AsyncMongoClient` serving all tenant DBs; a
   client-per-tenant mode does; `aclose()` closes only what it owns; clone count is
   bounded by `max_entries` (the RD-7 observability claim, asserted).
4. [ ] `varco_beanie/varco_beanie/tenancy/pool.py` — `BeanieTenantPool` over
   `TenantResourcePool[BeanieTenantBinding]`; `provider_for(tid)`.
5. [ ] `varco_beanie/tests/test_beanie_tenant_integration.py` —
   `@pytest.mark.integration`: two tenant databases, the same `_id` in both, full read
   isolation, `dropDatabase` removes one only.
6. [ ] `varco_beanie/varco_beanie/tenancy/provisioner.py` —
   `BeanieDatabaseProvisioner`: `provision()` creates collections + reconciles indexes
   (Mongo creates databases lazily, so provisioning *is* collection/index creation —
   reusing Plan 006's `IndexReconciler`); `deprovision(confirm_destroy=True)` →
   `dropDatabase`. Docstring: the **per-tenant GDPR erasure primitive**, pairing with
   crypto-shredding.
7. [ ] `varco_beanie/tests/test_tenancy_schema_rejected.py` — failing test:
   `TenantIsolation.SCHEMA` on Beanie raises `ValueError` naming MongoDB rather than
   silently behaving as `SHARED`.
8. [ ] `varco_beanie/tests/test_beanie_tenancy_di.py` — `scan` + `validate_bindings()`.

**Acceptance:** `varco_beanie/tests/` green incl. `-m integration`; clone count bounded
and observable; `SCHEMA` on Mongo fails loudly.

### Phase 8 — `TenantFanoutSupervisor` (RD-8 — the feature must actually work)

1. [ ] `varco_core/tests/test_fanout_supervisor.py` — failing tests: `start()` starts one
   child relay per **active, pool-resident** tenant; `on_tenant_activated()` adds one and
   `on_tenant_deactivated()`/pool eviction stops and removes one; **failure isolation**
   — a child raising repeatedly is restarted with capped backoff while the *other*
   children keep making progress (deliberately-failing fake relay); `stop()` awaits
   every child LIFO and is idempotent; children are **staggered** (asserted on scheduled
   delays); children never exceed the pool's `max_entries`; with
   `fanout_framework_tables=False` the supervisor starts **nothing**.
2. [ ] `varco_core/varco_core/tenancy/fanout.py` — `TenantFanoutSupervisor` owning
   per-tenant `OutboxRelay` (reused verbatim), job poller, and audit consumer children;
   lazy `asyncio.Lock`; supervised tasks with backoff.
3. [ ] `varco_sa/tests/test_fanout_integration.py` — `@pytest.mark.integration`,
   **the RD-8 acceptance test**: two real tenant databases; write an `OutboxEntry` inside
   tenant A's database via the routed UoW; assert the supervisor **genuinely publishes
   it** to the bus and deletes the row, that tenant B's relay never sees it, and that
   stopping the supervisor leaves no un-awaited task.
4. [ ] `varco_sa/tests/test_fanout_jobs_audit.py` — per-tenant job claiming
   (`try_claim(owner_id=…, lease_ttl=…)` against the tenant's own store, reusing the
   existing lease/fencing primitives — no second retry or lease model) and per-tenant
   audit persistence; a job enqueued in tenant A is never claimed by tenant B's poller.
5. [ ] `varco_fastapi/tests/test_fanout_lifecycle.py` — the supervisor is started and
   stopped by `TenancyLifecycle`, with stop awaited **before** pool `aclose()` so no
   relay outlives its engine (asserted on call order).

**Acceptance:** step 3 proves a tenant-database outbox entry is really published — the
feature is exercised, not merely present. One tenant's failing relay provably does not
stop another's. With the flag off nothing starts; with it on, outbox/jobs/audit all have
passing coverage.

### Phase 9 — Migration fan-out + CLI (global first, then tenants)

1. [ ] `varco_core/tests/test_migration_fanout.py` — failing tests: `plan()` aggregates
   per-tenant plans keyed by tenant in **sorted** order; `upgrade()` applies the
   **global/framework run first**, then every tenant (asserted on call order — tenant
   tables may FK to global tables); `--skip-global` is required to omit it;
   `fanout_on_failure="stop"` (default) halts at the first failure and the report names
   applied / failed / **not-attempted**; `"continue"` attempts all and aggregates;
   `check` fails if **any** tenant is behind and names **every** behind tenant; only
   `active`/`suspended` tenants are targeted (`pending`/`deleted` skipped); an empty
   catalog is a successful no-op with a WARNING; a per-tenant `MigrationLockTimeout`
   does not abort the others under `"continue"`.
2. [ ] `varco_core/varco_core/migration/fanout.py` — `TenantFanoutMigrator`
   (`AbstractMigrator`) composing `AbstractTenantCatalog` + a
   `Callable[[TenantDescriptor], AbstractMigrator]`; `TenantMigrationReport` (frozen:
   `global_report`, `per_tenant`, `failures`, `not_attempted`, `duration_s`).
   Backend-agnostic — works for `AlembicMigrator` **and** `BeanieMigrator` unchanged.
3. [ ] `varco_sa/tests/test_alembic_schema_scoped.py` — failing tests:
   `AlembicMigrator(engine, schema="t_acme")` sets `version_table_schema="t_acme"`
   **and** the translate map; the schema-scoped `alembic_version` is read correctly
   (⚠️ `_sync_current_heads` is currently a `@staticmethod` at `migrator.py:180-184` and
   **must become an instance method** to carry the schema — the single load-bearing
   edit); tenant-scoped metadata only (global/framework tables excluded from the fan-out
   target); with `schema=None` the `env_ctx.configure(...)` kwargs are **identical to
   today's** (asserted kwarg-by-kwarg); the per-tenant lock key is
   `dataclasses.replace(settings, lock_key=f"{base}:{schema}")` so tenants do not
   serialise against each other.
4. [ ] `varco_sa/varco_sa/migration/migrator.py` — add `None`-defaulted `schema` /
   `version_table_schema`; convert `_sync_current_heads` to an instance method; thread
   the schema through `_sync_upgrade`/`_sync_downgrade`/`_sync_stamp`
   (`migrator.py:186-233`) and the lock key (`migrator.py:269-274`).
5. [ ] `varco_core/tests/test_cli_migrate_tenants.py` — failing tests:
   `varco migrate upgrade --all-tenants` runs global-then-fan-out; `--tenant acme`
   targets one; an unknown tenant exits non-zero naming the catalog;
   `varco migrate check --all-tenants` exits non-zero listing every behind tenant;
   `varco tenant provision <id>` provisions **then** migrates, in that order;
   `varco tenant deprovision <id>` refuses without `--yes-i-really-mean-it` and prints
   what would be destroyed; `varco tenant list` renders statuses; every `varco tenant`
   verb that needs cluster DDL **fails with a clear message when no admin DSN is
   configured** (RD-4 — the CLI is a control-plane tool).
6. [ ] `varco_core/varco_core/cli/tenant.py` + `cli/migrate.py` — the `tenant` verb
   group and `--all-tenants`/`--tenant`/`--skip-global` flags, via the existing
   `"varco.commands"` entry-point group.

**Acceptance:** fan-out is deterministic and ordered; global runs before tenants; partial
failure reports applied/failed/not-attempted; single-tenant behaviour and
`AlembicMigrator`'s default configure-kwargs provably unchanged.

### Phase 10 — FastAPI wiring + docs

1. [ ] `varco_fastapi/tests/test_tenancy_lifecycle.py` — failing tests: `start()` starts
   the sweeper, the catalog-invalidation subscription, and (if enabled) the supervisor;
   `stop()` stops the supervisor **before** `pool.aclose()` and awaits `aclose()`
   (**engine/client leak regression guard**); prepended into `VarcoLifespan` like
   `MigrationLifecycle`; `create_varco_app(tenancy=None)` registers nothing;
   `VARCO_TENANCY_ISOLATION` set **without** `tenancy=` logs one WARNING naming the env
   var (mirroring Plan 006's `VARCO_MIGRATE_MODE` behaviour).
2. [ ] `varco_fastapi/varco_fastapi/tenancy/lifecycle.py` + the `create_varco_app
   (tenancy=…)` parameter (note: **no** `tenant_admin=` kwarg — mounting is
   `mount_tenant_admin()` only, RD-9). Plus an **import-guard test** asserting no
   `varco_sa`/`varco_beanie`/`sqlalchemy`/`pymongo` import appears anywhere in
   `varco_fastapi/varco_fastapi/tenancy/`.
3. [ ] `varco_fastapi/tests/test_tenant_resolution_middleware.py` — failing tests: the
   middleware checks the **catalog status first**, then `await`s `ensure()`, inside the
   `tenant_context()` block; a request with no tenant passes through untouched (public
   routes still work); status → 503/403/410/404 per the Phase-4 table, **never** a
   default-database fallback and never a 500; `ensure()` is called at most once per
   request.
4. [ ] `varco_fastapi/varco_fastapi/middleware/tenant_resolution.py`.
5. [ ] `technical_docs/features/multitenancy.md` — the decision table; six wiring
   recipes; a **control-plane** chapter (REST + queue onboarding, status lifecycle,
   required grants, the no-op/DBA path) including a **"Standalone vs bundled control
   plane"** subsection (RD-9) comparing **blast radius, credential scope, ops cost, and
   when to bundle** — single-container/PaaS deploys, dev, small installs — with the
   three barriers against accidental bundling and the `mount_tenant_admin` recipe; a
   **global/shared scope** chapter (declaration, per-strategy semantics, the dual-UoW
   API, "one transaction cannot span tenant + global — use the outbox/saga", the
   read-only-by-default credential (RD-10), what a denied write looks like and how to
   opt in, caching rules); a **prominent "Resource cost of Mongo database-per-tenant"**
   subsection (RD-7: the `N_active_tenants × N_models` formula, a worked example with
   real numbers, how `max_entries` bounds it, how to observe the class count); the
   connection-budget sizing worksheet marked **informational, environment-dependent, no
   varco-enforced cap** (RD-5); the raw-`text()` caveat; the fan-out chapter.
6. [ ] `technical_docs/features/postgres-rls.md` — rewrite §3 (lines 105-135) from
   "unimplemented hazard" to "**the supported mechanism is `schema_translate_map`**, and
   here is why not `search_path`"; add that `assert_rls_enabled()` skips `GLOBAL`
   tables; update §4's fail-open note to point at strategies 3/4 as the structural fix.
7. [ ] `CLAUDE.md` — a "Multitenancy — isolation strategies, control plane, global scope"
   section under Key Abstractions; a Decision-Tree branch; and **Pitfalls rows**:
   raw `text()` SQL not schema-translated · `SET` vs `SET LOCAL search_path` ·
   per-tenant engine never `dispose()`d · unbounded pool → connection exhaustion ·
   `init_beanie` global rebind (all tenants read one DB) · `BeanieDocRegistry.get()`
   returns the base class · `upgrade` without `--all-tenants` leaves N-1 tenants behind ·
   **global migration run after fan-out → FK failures** · `deprovision` destroys data ·
   **db-per-tenant without `fanout_framework_tables` → outbox rows stranded** ·
   `TenantIsolation.SCHEMA` on Mongo · **`TENANT` cache key not namespaced →
   cross-tenant leak** · **`GLOBAL` cache key namespaced → N× cache waste and N× DB
   load** · **`TenantAwareService` mixed into a `GLOBAL`-entity service** · **expecting
   one transaction across a tenant DB and the global DB** · **global write fails with
   `GlobalScopeReadOnlyError` (read-only by default — route via the control plane or set
   `global_writable`)** · literal DSN stored in `varco_tenants` · **admin DSN present in
   an app pod (grants nothing, but is the wrong topology)** · **`mount_tenant_admin`
   without `acknowledge_bundled_admin` → `ValueError`** · **bundled admin router left
   ungated at the ingress** · redelivered provision event assumed unique.
8. [ ] `CHANGELOG.md` (note the additive `ParsedMeta.tenant_scope` field and the
   `_sync_current_heads` staticmethod→method change as subclass-visible), root
   `README.md`, `varco_sa/README.md`, `varco_beanie/README.md`, `ARCHITECTURE.md`,
   `mkdocs.yml` nav.
9. [ ] `examples/NN-multi-tenant-isolation/` — one runnable app per strategy plus a
   global-entity example, following `examples/22-multi-tenant-soft-delete/`'s shape.

**Acceptance:** `make lint type-check test` green; `make docs` builds; the import-guard
test proves the `varco_fastapi` → `varco_core.tenancy` seam; every pitfall row has a
corresponding test somewhere in Phases 1-9.

---

## Edge cases

- **No tenant active** under `SCHEMA`/`DATABASE` → `RuntimeError`. Never a default-DB
  fallback.
- **Tenant active but not `ensure()`d** → `RuntimeError` naming `ensure()` (the
  consequence of `init_beanie` being async and `make_uow()` sync).
- **Tenant not `active`** → 503/403/410/404 per status; `pool.ensure()` is never called.
- **Unknown tenant** → read-through catalog miss → still unknown → 404, negative results
  rate-limited.
- **Concurrent first request for a new tenant** → per-tenant lazy `asyncio.Lock`;
  factory/`init_beanie` runs once.
- **Factory raises during `ensure()`** → no cache entry left behind; next request
  retries cleanly.
- **All pool entries busy at the cap** → soft cap exceeded + one WARNING. Resource
  pressure fails open; isolation never does.
- **`closer` raises** → logged and swallowed; remaining entries still close (same "must
  never raise" contract as `DLQ.push()`).
- **Tenant deprovisioned mid-request** → `lease()` refcount defers disposal; the control
  service stops fan-out children and evicts *before* destructive DDL.
- **`DROP DATABASE` with live connections** → dispose + evict first, then optional
  `pg_terminate_backend` with `force=True`.
- **Schema/database name from an untrusted tenant id** → identifier regex validation
  raises. These cannot be bound parameters, so it is the only defence.
- **Raw `text()` SQL under `SCHEMA`** → not translated; resolves via `search_path`.
  Documented; must self-qualify.
- **Write to a `GLOBAL` entity from an app pod (default)** → SQLSTATE `42501` translated
  into `GlobalScopeReadOnlyError` naming the entity and both remedies — never a raw
  driver traceback (RD-10).
- **`42501` from a tenant UoW** → **not** translated; it is a genuine permission bug and
  must not be mislabelled as the read-only-global design.
- **`global_writable=True`** → no translation wrapper installed; writes proceed against
  the writable global DSN.
- **`GLOBAL` entity queried inside a tenant context** → served from the global provider,
  unrouted (asserted).
- **`GLOBAL` table under `enforce_rls=True`** → **skipped**, not flagged as missing a
  policy (the RD-6 trap).
- **A service mixing `TenantAwareService` with a `GLOBAL` entity** →
  `TenantIsolationError` at startup naming both.
- **Cross-scope write needed atomically** → unsupported; the error/doc points at the
  outbox/saga primitives.
- **`VARCO_TENANCY_ADMIN_DSN` present in an app pod that never called
  `mount_tenant_admin`** → **no admin route exists**; one WARNING recommends the
  standalone topology. The DSN alone exposes nothing (RD-9).
- **`mount_tenant_admin` without `acknowledge_bundled_admin=True`** → `ValueError`,
  nothing mounted.
- **`mount_tenant_admin` without `server_auth`** → refused at mount (a guard that can
  never be satisfied is a startup error).
- **Unauthenticated or non-`tenant-admin` call to a bundled admin route** → **403, never
  500**; a generic `"admin"` role is not sufficient.
- **`mount_tenant_admin` called twice** → refused rather than duplicating routes.
- **Admin DSN equal to the app DSN** → refused (an app pod must not be its own admin).
- **Redelivered `TenantProvisionRequested`** → inbox `mark_processed` + idempotent
  `provision()` → no second DDL, no error.
- **Provisioner fails mid-provision** → status stays `pending`; retried per
  `durable_delivery()`, then DLQ'd; no half-`active` tenant.
- **`TenantCatalogChanged` dropped** → `catalog_ttl_s` re-read heals it within a bounded
  window; a miss reads through immediately.
- **Illegal status transition** (`deleted → active`) → `ValueError`.
- **Fan-out `check` with 0 tenants** → success + WARNING (an empty catalog is far more
  likely a misconfiguration than a real state).
- **Fan-out where tenant 3 of 10 fails** → `"stop"`: 1-2 applied, 3 failed, 4-10
  `not_attempted`, all named. Never a bare boolean.
- **Global migration skipped before fan-out** → tenant FKs to global tables fail;
  prevented by enforced ordering and an explicit `--skip-global`.
- **Two tenants in one database under `SCHEMA`** → distinct per-schema lock keys, so they
  migrate concurrently without contending (Plan 006's lock semantics).
- **One tenant's relay crashing** → restarted with capped backoff; other tenants
  unaffected (asserted).
- **Supervisor stopped after the pool** → forbidden; lifecycle asserts stop order so a
  relay never outlives its engine.
- **`enforce_rls=True` on a non-Postgres dialect** → skip with one WARNING (mirrors
  `SAAuditRepository`'s dialect fallback).
- **`TenantIsolation.SCHEMA` on Beanie** → `ValueError` at construction.

## Verification

```bash
uv run pytest varco_core/tests/ varco_sa/tests/ varco_beanie/tests/ varco_fastapi/tests/
uv run pytest varco_sa/tests/ -m integration        # real PG: schemas, databases, fan-out
uv run pytest varco_beanie/tests/ -m integration    # real MongoDB: databases
make lint && make type-check && make test && make docs

# Seam guards (must print nothing)
grep -rn "varco_sa\|varco_beanie\|sqlalchemy\|pymongo" varco_fastapi/varco_fastapi/tenancy/
grep -rn "import sqlalchemy\|import pymongo\|import beanie" varco_core/varco_core/tenancy/
# RD-9: no env var may mount the admin surface
grep -rn "MOUNT_ADMIN" varco_core/ varco_fastapi/ | grep -v tests

# RD-4 + RD-9 guards: privilege confinement and the bundled-admin contract
uv run pytest varco_sa/tests/test_admin_engine_guard.py \
              varco_sa/tests/test_fanout_config_guard.py \
              varco_fastapi/tests/test_mount_tenant_admin.py

# RD-10: read-only global credential surfaces a legible error
uv run pytest varco_sa/tests/test_global_readonly_translation.py \
              varco_sa/tests/test_global_dsn_resolution.py

# RD-8 acceptance: a tenant-database outbox entry is genuinely published
uv run pytest varco_sa/tests/test_fanout_integration.py -m integration

# Backwards-compat proof: default path untouched
uv run pytest varco_core/tests/test_tenant.py varco_core/tests/test_tenant_event.py \
              varco_core/tests/test_tenant_cache.py varco_sa/tests/test_alembic_migrator.py \
              varco_sa/tests/test_framework_metadata.py
```

## Risks

- **Connection exhaustion under db-per-tenant.** *Invariant:*
  `max_entries × (pool_size + max_overflow) × n_pods ≤ 0.8 × max_connections`.
  Mitigated by the bounded pool, `pool_size=1` guidance, and the sizing worksheet —
  **not** by an enforced cap (RD-5). Most likely production failure.
- **Engine/client leak.** *Invariant:* every resource the pool creates is disposed
  exactly once, on eviction or `aclose()`; the supervisor stops before the pool closes.
  Guarded by call-order and dispose-count tests.
- **Bundled admin surface exposed accidentally (RD-9).** *Invariant:* three independent
  barriers — **no env-var path exists**, `acknowledge_bundled_admin=True` is mandatory,
  and `server_auth` is mandatory — so an app that merely has `VARCO_TENANCY_ADMIN_DSN`
  set exposes **no** provisioning route. Additionally: the guard role is `tenant-admin`
  (not the generic `admin`), unauthorised calls are 403 not 500, and one WARNING at
  mount makes the privileged surface visible in startup logs. If any barrier is removed,
  a copy-pasted config could turn an app pod into a cluster-DDL endpoint.
- **Bundled admin reachable from the public internet.** *Invariant:* a dedicated
  `prefix` (default `/tenancy`) plus a `dependencies=` passthrough so the ingress and an
  extra guard can gate it independently; documented in the standalone-vs-bundled
  comparison as the main reason to prefer standalone.
- **App pod holding admin privilege.** *Invariant:* the provisioner cannot be
  constructed without an explicit admin DSN, refuses an admin DSN equal to the app's,
  and warns when a traffic-serving process has one.
- **Global reference data corrupted from a tenant request (RD-10).** *Invariant:* the
  app-facing global credential is read-only by default, so the write is impossible at
  the database, not merely discouraged; the resulting `42501` is translated into a
  legible `GlobalScopeReadOnlyError`. Highest-fan-out data in the system — every tenant
  reads it.
- **RD-8 fan-out silently disabled** → tenant outbox rows stranded, events never
  published. *Invariant:* db-per-tenant + a configured relay **without** the fan-out flag
  raises at construction, and the flag's happy path has integration coverage.
- **A relay outliving its engine** → `InterfaceError` storms on shutdown. *Invariant:*
  lifecycle stop order is supervisor → pool, asserted.
- **`schema_translate_map` bypassed by raw SQL** → queries the default schema.
  *Invariant:* every routed table carries the symbolic token; raw SQL self-qualifies.
  Only partly mitigable by docs + review.
- **`init_beanie` global rebind.** *Invariant:* clones are never registered in
  `BeanieDocRegistry`, and `init_beanie` is only called with one tenant's clone set. A
  regression makes **all tenants read one database with no error**.
- **Cross-scope transaction assumed.** *Invariant:* tenant and global UoWs are distinct
  types, so one transaction spanning both is not expressible.
- **Cache key errors in both directions.** *Invariants:* a `TENANT` key is never emitted
  unnamespaced (raises); a `GLOBAL` key is never namespaced (asserted identical across
  tenants).
- **Credential leakage via the catalog or logs.** *Invariant:* `varco_tenants` stores a
  **secret reference**, never a literal DSN; no DSN password appears in any repr, log, or
  exception (asserted by scanning captured logs).
- **Non-idempotent provisioning on redelivery.** *Invariant:* inbox `mark_processed` +
  `IF NOT EXISTS`/existence-probe DDL; status becomes `active` only after success.
- **Catalog staleness across pods** → a new tenant 404s or a suspended tenant keeps
  serving. *Invariant:* event invalidation + TTL backstop + read-through on miss; a
  dropped event self-heals within `catalog_ttl_s`.
- **Migration ordering inverted** (fan-out before global) → FK failures. *Invariant:*
  the fan-out migrator runs the global step first; skipping is explicit.
- **Touching `SAModelFactory` / `ParsedMeta`.** *Invariants:* under `SHARED` the
  generated `__table__.schema is None`; `ParsedMeta.tenant_scope` defaults to `TENANT`
  and is constructible without it. Both asserted.
- **`_sync_current_heads` staticmethod → instance method** could break external
  subclasses. *Invariant:* with `schema=None` the `env_ctx.configure(...)` kwargs are
  identical to today's; noted in the CHANGELOG.
- **N supervised relays add query load.** *Invariant:* children ≤ pool `max_entries`,
  first ticks staggered. Revisit only if a real deployment shows the single-loop shape is
  needed.
