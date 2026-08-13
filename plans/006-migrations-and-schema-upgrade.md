# Plan 006 — Migrations & schema upgrade (Postgres/Alembic + MongoDB/Beanie, auto-on-startup)

## Goal

Give varco a first-class schema-migration layer for **both** persistence backends, with the same
contract on each:

- `varco_core.migration` — one backend-agnostic `AbstractMigrator` contract + `MigrationSettings`
  (`VARCO_MIGRATE_*`), so nothing above the storage layer imports `alembic` or `pymongo`.
- `varco_sa.migration` — `AlembicMigrator`, a thin programmatic wrapper over Alembic, plus a
  **framework-owned Alembic branch** shipped inside the wheel so `varco_outbox`/`varco_jobs`/
  `varco_audit_log`/… evolve on `pip install -U varco-sa` with no app-side `env.py` edits.
- `varco_beanie.migration` — a versioned migration runner for MongoDB (`varco_migrations`
  collection + lock document) and an opt-in index reconciler built on the existing
  `BeanieIndexGuard`.
- `varco_fastapi.migrate` — `MigrationLifecycle`, an `AbstractLifecycle` that runs the migrator
  during the app lifespan under a distributed lock, so `uvicorn app:app` upgrades the schema
  before the first request — safely with N replicas.
- A `varco` **CLI** (`varco migrate …`) for the CI / pre-deploy-job path, which is the path most
  production deployments should actually use.

After this plan a varco service can pick exactly one of three postures per environment, by env var
only: **off** (today's behaviour, the default), **check** (fail startup if the schema is behind,
never write DDL), **upgrade** (migrate at startup under a lock).

## Non-goals

- **Not a new migration engine.** Alembic is the Postgres engine; varco wraps it and never
  reimplements revision graph resolution, autogenerate, or offline SQL rendering.
- **No autogenerate for MongoDB.** Mongo is schemaless; there is nothing to diff except indexes.
  Mongo migrations are hand-written `up()`/`down()` scripts plus index reconciliation. Do not
  invent a document-shape differ.
- **No automatic downgrade on failure.** Ever. A failed `upgrade` leaves the DB at the last
  successfully-applied revision and the process exits; an automatic `downgrade` of a
  data-destructive revision is strictly worse than a failed deploy. `downgrade` is a deliberate,
  human-invoked CLI action only.
- **No removal of `ensure_table()`** from `varco_sa`. It is published API on six classes. It is
  reconciled (see Phase 2) and documented as mutually exclusive with migration management, not
  deleted.
- **No auto-enabling of RLS.** Phase 6 makes `enable_rls_ddl` usable *inside a reviewed revision*;
  it never runs from a startup hook.
- **No data migrations / backfill framework.** Alembic revisions can already run arbitrary DML,
  and the Beanie runner's `up(db)` receives the database. A batched-backfill orchestrator (resumable,
  throttled, progress-tracked) is a separate, larger piece — explicitly deferred.
- **No multi-tenant schema-per-tenant fan-out** (`upgrade` across N Postgres schemas). Out of scope;
  the lock-key design in Phase 4 leaves room for it.
- **No changes to `SchemaGuard` / `BeanieIndexGuard` behaviour.** Both are read-only drift
  detectors today and stay that way; the new code *consumes* them.

---

## Source corrections — the scout report is right, but incomplete in four places

Verified against source before designing. The plan is written against source.

1. **`varco_sa.print_create_ddl` is broken on SQLAlchemy 2.x.**
   `varco_sa/varco_sa/alembic_helpers.py:213-217` calls
   `create_engine(f"{dialect}://", strategy="mock", executor=_capture)`. The `strategy=`/`executor=`
   arguments were removed in SQLAlchemy 1.4 (replaced by `sqlalchemy.create_mock_engine`); on the
   pinned `sqlalchemy==2.0.48` (`uv.lock:1805-1812`) this raises
   `TypeError: Invalid argument(s) 'strategy','executor' sent to create_engine()`. **No test covers
   it** — `rg "print_create_ddl|get_target_metadata" varco_sa/tests` returns nothing. Phase 0 fixes
   it and adds the first tests this module has ever had. Also re-verify `Table.tometadata()`
   (`:127`, `:134`) against 2.0 — `to_metadata()` is the current spelling.

2. **Three framework metadata objects are not reachable from `varco_sa`'s public API.**
   `varco_sa/varco_sa/__init__.py:74-131` exports `outbox_metadata`, `inbox_metadata`,
   `jobs_metadata`, `sagas_metadata`, `conversation_metadata`, `dedup_metadata` — but **not**
   `audit_metadata` (`varco_sa/audit.py:148`, exists, unexported) and **not**
   `dead_letters_metadata` (`varco_sa/dlq.py:121`, exists, unexported). Worse,
   `varco_sa/encryption_store.py:80` has only a module-private `_metadata` with **no public alias at
   all** — the `varco_encryption_keys` table is currently *impossible* to put into Alembic's
   `target_metadata` without touching a private name. The scout's "developer must manually add each
   framework metadata" understates it: for one table they cannot.

3. **`ensure_table()` is on six classes, not two.** `SAEncryptionKeyStore` (`:162`),
   `SADeadLetterQueue` (`dlq.py:161`), `SAJobStore` (`job_store.py:319`), `SASagaRepository`
   (`saga.py:164`), `SAConversationStore` (`conversation.py:161`), `SADeduplicator`
   (`deduplication.py:233`). Every one is `metadata.create_all(checkfirst=True)`. Any reconciliation
   must cover all six, and the hazard is directional: `create_all(checkfirst=True)` is a harmless
   no-op against an Alembic-managed table that already exists, but an Alembic `CREATE TABLE` against
   a table `ensure_table()` already made **fails**. Phase 2's answer is idempotent framework baseline
   revisions, not a behaviour change to `ensure_table()`.

4. **The drift-detection half already exists on both backends.** `varco_sa.SchemaGuard` /
   `SchemaDriftReport` (`schema_guard.py:46-120`) and `varco_beanie.BeanieIndexGuard` /
   `IndexDriftReport` (`index_guard.py:100-300`) are shipped, exported, and report-only. This plan
   adds the *apply* half and reuses these verbatim for `mode="check"`. Do not write a second differ.

Also verified: `alembic` is **not** a dependency of any package (`rg alembic **/*.toml` matches only
a keyword string in `varco_sa/pyproject.toml:9`) — it must arrive as an optional extra. No
`[project.scripts]` exists anywhere in the workspace. There is one root `CHANGELOG.md`, not
per-package changelogs (Plan 005's per-package changelog references were aspirational).

---

## Design

### Layering

```
                        varco_core.migration            ← contracts only, zero deps
   AbstractMigrator (ABC) · MigrationPlan · MigrationReport · MigrationSettings
   MigrationError · PendingMigrationsError · MigrationLockTimeout · IrreversibleMigrationError
            ▲                                   ▲                        ▲
            │ implemented by                    │ implemented by         │ consumed by
   varco_sa.migration                  varco_beanie.migration     varco_fastapi.migrate
   AlembicMigrator                     BeanieMigrator             MigrationLifecycle
     └ wraps alembic.command             └ varco_migrations coll     └ AbstractLifecycle
     └ ships a "varco" branch            └ Migration registry        └ registered into
     └ SAXactAdvisoryLock                └ lock document                VarcoLifespan
                                          └ IndexReconciler
            ▲                                   ▲
            └───────── varco_core.cli ──────────┘   ← `varco migrate …`
                (entry-point group "varco.commands", lazy import)
```

`varco_fastapi` never imports `varco_sa`, `varco_beanie`, or `alembic` — it depends only on
`varco_core.migration.AbstractMigrator`. This is the same seam as `AbstractEventBus`/`AbstractJobStore`
and is what keeps `create_varco_app(migrations=...)` backend-agnostic.

### The five decisions that define this feature

#### D1 — Default mode is `off`. Auto-on-startup is opt-in.

`MigrationSettings.mode: Literal["off", "check", "upgrade"] = "off"`
(env `VARCO_MIGRATE_MODE`). With `mode="off"` **nothing is registered into the lifespan and no code
path changes** — byte-identical to today. This is the repo's established phasing convention
(Plan 005, "Compatibility posture").

| mode | What happens at startup | Who should use it |
|---|---|---|
| `off` (default) | nothing | today's users; anyone running migrations out-of-band already |
| `check` | resolve pending revisions + run `SchemaGuard`/`BeanieIndexGuard`; **fail startup** if behind; never writes DDL | **recommended production posture** — migrations ran in a pre-deploy job; this proves it |
| `upgrade` | acquire lock → `upgrade heads` → release; fail-fast on error | single-instance / dev / small deployments; PaaS with no pre-deploy hook |

`check` exists because "run migrations automatically at startup" and "never let a pod serve traffic
against a schema it doesn't understand" are two different requirements, and only the second one is
universally correct. The headline `uvicorn`-runs-migrations feature is `upgrade`; `check` is what we
tell people to actually deploy.

#### D2 — Multi-pod exclusion: `SAXactAdvisoryLock.xact()` held open across Alembic's own transactions.

Chosen over `RedisLock` and over a lock row in the version table.

The lock lives **in the database being migrated**. That is the correct failure domain: if Postgres is
unreachable the migration cannot run anyway, whereas a Redis outage forces a choice between blocking
startup on an unrelated service or (worse) proceeding unlocked. It also needs no extra infrastructure
— a varco service using `varco_sa` may not deploy Redis at all.

`SAXactAdvisoryLock` (transaction-scoped, `varco_sa/advisory_lock.py:382-572`) over `SAAdvisoryLock`
(session-scoped) for the reason already documented in CLAUDE.md's pitfall table (U-16): a
session-scoped lock behind a transaction-mode pooler can have its `release()` routed to a different
physical connection. A transaction-scoped lock is released by the caller's own COMMIT/ROLLBACK — and,
critically, **by process death**, so an OOM-killed pod mid-migration leaves no orphaned lock and there
is no TTL to size. Sizing a TTL is the fatal flaw of the Redis option: the TTL must exceed the longest
migration, which is unknowable, and expiry mid-migration means two pods running DDL concurrently.

The non-obvious mechanic, and the thing the implementer must get right:

```
conn_lock  ──┐  BEGIN                              (dedicated connection, held open)
             │  SET LOCAL idle_in_transaction_session_timeout = 0
             │  SELECT pg_try_advisory_xact_lock(hash('varco:migrate'))  → true
             │
conn_ddl   ──┼──▶ alembic upgrade heads   (its OWN connection; transaction_per_migration=True,
             │                             so each revision commits independently)
             │
             └  COMMIT   ← this is the release. No release() call exists.
```

Alembic must run on a **separate** connection because it manages its own transaction boundaries; the
lock transaction simply stays open around it. That open transaction is `idle in transaction` for the
whole migration, which is why `SET LOCAL idle_in_transaction_session_timeout = 0` is mandatory — a
server- or role-level `idle_in_transaction_session_timeout` would otherwise kill the lock holder
mid-migration and silently un-exclude the critical section. Document the vacuum/xmin-horizon cost
(one held snapshot for the migration's duration) as the accepted price.

MongoDB has no advisory locks, so `BeanieMigrator` uses a **lock document**
(`{_id: "__lock__"}` in `varco_migrations`) acquired with a conditional `find_one_and_update` upsert
— `_id` uniqueness gives the atomicity — carrying `owner`, `acquired_at`, `expires_at`, `heartbeat_at`.
A background heartbeat renews `expires_at` while the migration runs; a crashed holder is reclaimed
after TTL expiry. This is the same shape as the fenced job lease from Plan 005 Phase 4, and it *does*
have the TTL-sizing problem — which is stated in the docs as one more reason Mongo index builds
belong in the CLI path, not the startup path (see D5).

#### D3 — Framework tables get their own Alembic branch, shipped in the wheel.

This is the answer to "the user must manually list framework metadata in `env.py`": **they never list
it.** `varco_sa` ships `varco_sa/migrations/versions/*.py` with `branch_labels=("varco",)` inside the
package. `AlembicMigrator` appends that directory to `version_locations` automatically, so:

- `varco migrate upgrade heads` applies both the app branch and the `varco` branch.
- `pip install -U varco-sa` that adds a column to `varco_jobs` brings its revision with it; the next
  `upgrade heads` applies it. Nothing in the app repo changes.
- The app's `autogenerate` must **not** try to re-create framework tables, so
  `varco_sa.migration.include_object` (a ready-made Alembic `include_object` callback) filters out
  every table owned by the `varco` branch. Wiring it is one line in `env.py`.

Framework baseline revisions are written **idempotently** — `if not inspector.has_table(...)` guards
around `op.create_table` — which is precisely what reconciles the `ensure_table()` bypass (source
correction 3): a database where `SAJobStore.ensure_table()` already created `varco_jobs` upgrades
cleanly instead of erroring on `CREATE TABLE`. `AlembicMigrator.adopt_framework_tables()` (and
`varco migrate adopt`) then stamps the `varco` branch head so subsequent upgrades start from the right
place.

`framework_metadata()` (Phase 0) still exists and is still exported — it is what `mode="check"`,
`print_create_ddl`, `create_all` in tests, and the single-branch escape hatch
(`get_target_metadata(..., include_framework=True)`) consume.

#### D4 — Failure semantics: fail-fast, and "lock busy" is not a failure.

`MigrationSettings.on_failure: Literal["fail", "warn"] = "fail"`. On `fail`,
`MigrationLifecycle.start()` raises → `VarcoLifespan.__call__` stops already-started components and
re-raises (`lifespan.py:194-203`) → FastAPI startup fails → uvicorn exits non-zero → the orchestrator
never routes traffic to the pod. That is the whole point: a pod serving requests against a schema it
does not understand is the failure we are preventing. `warn` exists for the deployment that would
rather serve degraded than not at all; it logs at ERROR and continues.

Lock contention during a rolling deploy is the **normal** case, not an error, and the algorithm must
say so:

```
acquire lock (deadline = lock_timeout, default 30s, poll every 0.5s)
├── acquired  → run upgrade under it (deadline = timeout, default 300s) → release → serve
└── timed out → re-evaluate pending()
        ├── empty      → another pod finished the work → serve   (the common rolling-deploy path)
        └── non-empty  → on_failure applies (default: raise MigrationLockTimeout)
```

Partial failure is explicit, not hidden. `transaction_per_migration=True` means a failure in revision
N leaves N-1 applied and `alembic_version` at N-1 — a re-run resumes correctly. The exceptions are
documented, not solved: DDL that cannot run in a transaction (`CREATE INDEX CONCURRENTLY`,
`ALTER TYPE … ADD VALUE` on older PG) must use Alembic's `autocommit_block()` and can leave an INVALID
index behind on failure, requiring manual `DROP INDEX`. There is no automatic rollback (see Non-goals).

Two Postgres-side timeouts are set on the DDL connection so a migration blocked behind a long-running
query fails loudly rather than hanging startup forever: `lock_timeout` (default 10s — how long a DDL
statement waits for a table lock) and `statement_timeout` (default = `MigrationSettings.timeout`).
Both configurable, both settable to `0` to disable.

#### D5 — MongoDB index creation is `check` by default, even in `upgrade` mode.

`BeanieMigrationSettings.index_mode: Literal["off", "check", "create"] = "check"`, **independent of
`mode`**. Running `mode="upgrade"` does not silently start building indexes.

An index build on a large collection is minutes-to-hours of work; on a replica set it replicates and
can stall secondaries; and it happens exactly when a rolling deploy is starting N new pods. Wiring
that into a startup hook by default would be the single most dangerous thing in this plan. So:
`check` reports drift via the existing `BeanieIndexGuard` and lets `on_failure` decide; `create` is
opt-in, applies missing indexes (via `create_indexes` with the collection's own options), and its
docstring and the feature doc both state plainly that it is unsafe on large collections and belongs in
`varco migrate beanie index --create` run as a pre-deploy job.

Hand-written `Migration.up(db)` scripts run under `mode="upgrade"` normally — the restriction is
specific to *reconciled* indexes, which are the ones varco derives implicitly and could therefore
surprise an operator with.

### Composite deployments

`create_composite_app`'s `CompositeLifespan` already drives each sub-app's own lifespan, so each
service's own `MigrationLifecycle` runs with its own settings and its own database. No composite-level
code is needed for correctness. Two properties must be documented:

- **Startup time is the sum, not the max** — N services migrate sequentially (fail-fast composite
  startup is serial by design). Budget `N × timeout` in the readiness probe's `initialDelaySeconds`.
- **Two services sharing one database converge on the same lock automatically.** The default lock key
  is the literal `"varco:migrate"`, and Postgres advisory locks are already scoped per-database, so
  two composite members on the same DB serialize with no configuration. Two services on *different*
  databases never contend. `lock_key=` is available for schema-per-service setups that want finer
  granularity.

### Alternatives considered

- **App's `env.py` lists `framework_metadata()` in `target_metadata`, framework tables autogenerate
  into the app's own revisions (single branch).** ❌ Rejected as the *default*: every app produces a
  different, hand-edited revision for the same framework table, and an operator who forgets to
  autogenerate after `pip install -U varco-sa` gets a runtime column-missing error instead of a
  migration. ✅ Kept as an opt-in escape hatch (`include_framework=True`) for shops with a policy
  against vendored revisions or multiple Alembic heads. The cost of the chosen design is that
  operators must learn `upgrade heads` (plural) — called out in the docs.

- **`RedisLock` for the migration mutex.** ❌ Rejected: requires infrastructure a `varco_sa`-only
  service may not have; wrong failure domain (Redis down blocks a migration Postgres could serve); and
  the TTL must exceed the longest migration, which is unknowable — expiry mid-migration means
  concurrent DDL, the exact thing the lock exists to prevent. ✅ Kept as a documented option for
  fleets already standardised on `RedisLock`, via the `lock=` parameter which accepts any
  `AbstractDistributedLock` (`varco_core/lock.py:218`).

- **Session-scoped `SAAdvisoryLock` instead of `SAXactAdvisoryLock`.** ❌ Rejected for the reason
  already in CLAUDE.md's pitfall table: `release()` can be routed to a different physical connection
  behind a transaction-mode pooler, leaking the lock. ✅ `xact()` inside a held-open transaction is
  pooler-safe and releases on process death.

- **Delete `ensure_table()` and make migrations mandatory.** ❌ Rejected: six published classes, and
  the zero-migration startup convenience is genuinely the right choice for tests and single-file
  demos. ✅ Idempotent framework baseline revisions make the two mechanisms coexist; the
  documentation states they are mutually exclusive *per deployment*, and `adopt` bridges an existing
  `ensure_table()`-built database into migration management.

- **A varco-native migration engine for Postgres (no Alembic dependency).** ❌ Rejected outright:
  revision graphs, branch merges, offline SQL and autogenerate are years of work that Alembic already
  does, and every SQLAlchemy shop already knows its CLI. ✅ Wrapping means `alembic` stays an optional
  extra and existing `alembic/` directories keep working untouched.

- **A document-shape differ / JSON-schema validator for MongoDB autogenerate.** ❌ Rejected: MongoDB
  is schemaless by design; a differ would compare varco's generated `Document` shape against sampled
  documents and produce guesses. ✅ Explicit hand-written migrations + index reconciliation is the
  honest surface (and matches what `beanie`'s own ecosystem does).

- **Auto-apply RLS policies at startup when `enable_rls_ddl` is configured.** ❌ Rejected: RLS is DDL
  that must be ordered after table creation and reviewed like any other schema change; a startup hook
  that silently `CREATE POLICY`s is the same anti-pattern this plan is removing. ✅ Phase 6 provides
  `rls_upgrade(op, ...)` / `rls_downgrade(op, ...)` so it lands in a revision.

- **A single `varco-migrate` script per backend package (`varco-sa-migrate`, `varco-beanie-migrate`).**
  ❌ Rejected: two commands with divergent flags, and no place to add `varco migrate check` spanning
  both. ✅ One `varco` entry point in `varco_core` that discovers subcommands via the
  `varco.commands` entry-point group — core keeps zero sibling dependencies, and a third-party backend
  can contribute `varco migrate mybackend` the same way.

### Sequencing

```
Phase 0  metadata aggregation + alembic_helpers repair (no new deps)   ← unblocks 2 and 4
   │
   ├── Phase 1  varco_core.migration contracts + settings              ← unblocks 2, 3, 4, 5
   │      │
   │      ├── Phase 2  varco_sa.migration (Alembic + varco branch)     ┐ independent,
   │      ├── Phase 3  varco_beanie.migration (runner + indexes)       ┘ parallelisable
   │      │        ▼
   │      ├── Phase 4  varco_fastapi auto-on-startup (the headline)    ← needs 1 + (2 or 3)
   │      └── Phase 5  varco CLI                                       ← needs 1 + (2 or 3)
   │
   └── Phase 6  RLS migration ops + operations docs                    ← needs 2
```

Phase 0 ships alone and is useful alone (it fixes a broken function and exposes three unreachable
metadata objects). Phases 2 and 3 are independently shippable and can land in either order. Phase 4 is
the requested headline but deliberately lands *after* the CLI-capable engines exist, so the risky
automatic path is built on top of a manually-verifiable one.

### Compatibility posture

Additive and default-preserving, per Plan 005. Every new parameter is keyword-only with a default
reproducing today's behaviour; `VARCO_MIGRATE_MODE` defaults to `off`. **No breaking changes and no
changed defaults in this plan** — the only behaviour change anywhere is Phase 0's repair of
`print_create_ddl`, which currently raises `TypeError` unconditionally and therefore has no working
behaviour to preserve. `alembic` is an optional extra (`varco-sa[migrations]`), so installs that
never touch migrations gain no dependency. Version bumps: `varco-sa` and `varco-beanie` minor,
`varco-fastapi` minor, `varco-core` minor.

---

## Steps

### Phase 0 — Metadata aggregation + `alembic_helpers` repair — `varco_sa`, no new deps

Depends on: nothing. Blocks: Phase 2 (`framework_metadata()` is what the baseline revision and the
`include_object` filter enumerate) and Phase 4 (`check` mode).

1. [ ] `varco_sa/tests/test_alembic_helpers.py` (new) — **failing tests first**, this module has no
       tests at all: `print_create_ddl(SomeDomainCls)` returns a non-empty string containing
       `CREATE TABLE` for `dialect="postgresql"` and for `dialect="sqlite"` (this fails today with
       `TypeError: Invalid argument(s) 'strategy','executor'`, per source correction 1);
       `get_target_metadata(Cls)` returns a `MetaData` whose `.tables` contains the generated table;
       `get_target_metadata()` with no args returns an empty `MetaData`;
       `get_target_metadata(base=Base)` includes hand-written tables; an unregistered domain class
       raises `KeyError`. Use the existing fresh-`DeclarativeBase`-per-test fixture from
       `varco_sa/tests/conftest.py:29-80`.
2. [ ] `varco_sa/varco_sa/alembic_helpers.py:205-225` — replace the removed
       `create_engine(..., strategy="mock", executor=...)` with
       `sqlalchemy.create_mock_engine(f"{dialect}://", _capture)`, and call
       `CreateTable(orm_cls.__table__).compile(dialect=engine.dialect)` → replace with
       `engine.execute(CreateTable(orm_cls.__table__))` (the mock engine's executor is what captures
       the string; `.compile()` alone never invoked `_capture`, which is the second half of the bug).
3. [ ] `varco_sa/varco_sa/alembic_helpers.py:127,134` — verify `Table.tometadata()` against
       SQLAlchemy 2.0.48 and switch to `Table.to_metadata()` if it has been removed; the test from
       step 1 is the oracle.
4. [ ] `varco_sa/varco_sa/dlq.py`, `varco_sa/varco_sa/encryption_store.py` — expose public metadata
       aliases next to the private ones: `dead_letters_metadata` already exists at `dlq.py:121`
       (only unexported); `encryption_store.py:80` needs a new `encryption_metadata = _metadata`
       alias. Keep `_metadata` as-is so nothing internal moves.
5. [ ] `varco_sa/varco_sa/metadata.py` (new) — the single aggregated export:
       ```python
       _FRAMEWORK_METADATA: dict[str, MetaData] = {}   # module-qualified name → MetaData

       def register_framework_metadata(name: str, md: MetaData) -> None: ...
       def framework_metadata() -> MetaData: ...          # one merged MetaData, all framework tables
       def framework_table_names() -> frozenset[str]: ... # cheap; used by include_object
       ```
       Each of the eight owning modules (`outbox`, `inbox`, `job_store`, `saga`, `conversation`,
       `deduplication`, `audit`, `dlq`, `encryption_store`) calls `register_framework_metadata` at
       import time. `framework_metadata()` imports all of them lazily on first call, so a caller gets
       the complete set without importing nine modules by hand. **This is the mechanism by which a
       framework table added in a future varco version is automatically included** — a new module
       registers itself and existing callers pick it up on upgrade with no code change.
6. [ ] `varco_sa/tests/test_framework_metadata.py` (new) — the completeness guard, modelled on
       `varco_fastapi/tests/test_di_binding_health.py`: walk `varco_sa` with `pkgutil.walk_packages`,
       import every module, collect every module-level `MetaData` instance **and** every
       `DeclarativeBase` subclass's `.metadata`, and assert each one's tables are a subset of
       `framework_metadata().tables` (excluding the test-local bases and the app-facing
       `BaseDatabaseModel`). This test fails the day someone adds a framework table without
       registering it — which is the guarantee Phase 0 is selling.
7. [ ] `varco_sa/tests/test_framework_metadata.py` — also assert the exact expected table-name set
       (`varco_outbox`, `varco_inbox`, `varco_jobs`, `varco_sagas`, `varco_conversation_turns`,
       `varco_dedup`, `varco_audit_log`, `varco_dead_letters`, `varco_encryption_keys` — read the
       literal names from source, do not guess) so an accidental rename is caught.
8. [ ] `varco_sa/varco_sa/alembic_helpers.py` — `get_target_metadata` gains
       `include_framework: bool = False` (keyword-only). When `True`, merge `framework_metadata()`
       into the result. Default `False` = today's exact behaviour, and is the correct default because
       Phase 2's branch owns those tables.
9. [ ] `varco_sa/varco_sa/__init__.py` — export `framework_metadata`, `framework_table_names`,
       `register_framework_metadata`, `audit_metadata`, `dead_letters_metadata`,
       `encryption_metadata`; add them to `__all__` under a new
       `# ── Framework schema ──` section.
10. [ ] `varco_sa/README.md` + `ARCHITECTURE.md` — document `framework_metadata()` as the one thing to
        put in `env.py` if you are not using the Phase 2 branch; record that
        `print_create_ddl` was broken on SQLAlchemy 2.x and is fixed. `CHANGELOG.md` (root) — Fixed +
        Added entries.

**Migration:** none. **Verification:** `uv run pytest varco_sa/tests/test_alembic_helpers.py
varco_sa/tests/test_framework_metadata.py -q`; `make type-check`.

---

### Phase 1 — `varco_core.migration` contracts + settings — `varco_core`, no new deps

Depends on: nothing. Blocks: Phases 2, 3, 4, 5.

**Design.** Contracts only, so `varco_fastapi` and the CLI can be written against one interface. The
shape mirrors Alembic's vocabulary (`current`/`heads`/`pending`/`upgrade`/`downgrade`/`stamp`) because
that vocabulary is already in every SQLAlchemy user's fingers, and `BeanieMigrator` can satisfy it
honestly (its "revisions" are sortable version strings).

11. [ ] `varco_core/tests/test_migration_contracts.py` (new) — **failing tests first**:
        `MigrationSettings.from_env()` with an empty environ yields `mode="off"`, `on_failure="fail"`,
        `lock_timeout=30.0`, `timeout=300.0`, `lock_key="varco:migrate"`;
        `VARCO_MIGRATE_MODE=upgrade` / `VARCO_MIGRATE_ON_FAILURE=warn` /
        `VARCO_MIGRATE_LOCK_TIMEOUT=5` are parsed; an unknown `mode` raises `ValueError` naming the
        three legal values; `MigrationPlan.is_empty` is `True` for no pending revisions;
        `MigrationReport.format()` renders applied revisions and duration.
12. [ ] `varco_core/varco_core/migration/__init__.py` (new package) — re-exports.
13. [ ] `varco_core/varco_core/migration/base.py` (new) — the contract:
        ```python
        @dataclass(frozen=True)
        class Revision:
            id: str                       # alembic rev hash, or beanie version string
            label: str                    # human name / message
            branch: str | None = None     # "varco" for framework revisions, None for app

        @dataclass(frozen=True)
        class MigrationPlan:
            current: tuple[str, ...]      # heads currently applied ( () on a virgin DB )
            pending: tuple[Revision, ...]
            @property
            def is_empty(self) -> bool: ...
            def format(self) -> str: ...

        @dataclass(frozen=True)
        class MigrationReport:
            applied: tuple[Revision, ...]
            duration_s: float
            skipped_locked: bool = False   # another holder did the work
            def format(self) -> str: ...

        class AbstractMigrator(ABC):
            @abstractmethod
            async def plan(self) -> MigrationPlan: ...
            @abstractmethod
            async def upgrade(self, target: str = "heads", *, dry_run: bool = False) -> MigrationReport: ...
            @abstractmethod
            async def downgrade(self, target: str) -> MigrationReport: ...
            @abstractmethod
            async def stamp(self, target: str = "heads") -> None: ...
            async def check(self) -> MigrationPlan: ...          # concrete: plan() + raise if pending
            async def close(self) -> None: ...                   # concrete no-op; engines override
        ```
        `check()` and `close()` are **concrete** (not abstract) so a third-party migrator is not broken
        by their addition — same rule Plan 005 applied to `AbstractJobStore`.
14. [ ] `varco_core/varco_core/migration/errors.py` (new) — `MigrationError(VarcoError)` and
        subclasses `PendingMigrationsError` (carries the `MigrationPlan`),
        `MigrationLockTimeout` (carries `lock_key`, `waited_s`), `IrreversibleMigrationError`
        (Mongo migration with no `down()`), `MigrationBackendUnavailable` (optional extra not
        installed — message names the exact `pip install` line). Match the existing exception base in
        `varco_core/varco_core/exception/`.
15. [ ] `varco_core/varco_core/migration/settings.py` (new) — `MigrationSettings`, frozen dataclass
        (not pydantic `BaseSettings` — see CLAUDE.md's `@Singleton`-on-`BaseSettings` pitfall; a frozen
        dataclass with a `from_env()` classmethod matches `SAConfig`/`BeanieSettings`):
        ```python
        @dataclass(frozen=True)
        class MigrationSettings:
            mode: Literal["off", "check", "upgrade"] = "off"          # VARCO_MIGRATE_MODE
            on_failure: Literal["fail", "warn"] = "fail"              # VARCO_MIGRATE_ON_FAILURE
            lock_key: str = "varco:migrate"                           # VARCO_MIGRATE_LOCK_KEY
            lock_timeout: float = 30.0                                # VARCO_MIGRATE_LOCK_TIMEOUT
            timeout: float = 300.0                                    # VARCO_MIGRATE_TIMEOUT
            target: str = "heads"                                     # VARCO_MIGRATE_TARGET_REV
            dry_run: bool = False                                     # VARCO_MIGRATE_DRY_RUN
            @classmethod
            def from_env(cls, env: Mapping[str, str] | None = None) -> MigrationSettings: ...
        ```
        `env=` is injectable so tests never mutate `os.environ` (and so composite services can pass a
        scoped mapping, per `build_service(prefix, factory, env=...)`).
16. [ ] `varco_core/varco_core/migration/inmemory.py` (new) — `InMemoryMigrator`, the test double
        that every downstream phase's unit tests use: constructed with a list of `Revision`s and a
        starting position, records every call, optionally raises on the Nth `upgrade`. Mirrors
        `InMemoryEventBus`/`InMemoryDeadLetterQueue` as the standard unit-test implementation.
17. [ ] `varco_core/varco_core/__init__.py` — export the new names. `ARCHITECTURE.md` — add a
        "Migrations" section to the type hierarchy. `CHANGELOG.md` — Added.

**Migration:** none. **Verification:** `uv run pytest varco_core/tests/test_migration_contracts.py -q`.

---

### Phase 2 — `varco_sa.migration`: Alembic wrapper + framework branch — `varco_sa`

Depends on: Phases 0, 1.

**Design.** `AlembicMigrator` is a thin async facade over `alembic.command` / `ScriptDirectory` /
`MigrationContext`. Alembic's API is synchronous and does blocking I/O, so every public method runs the
Alembic call inside `asyncio.to_thread` against a **sync** engine derived from the async URL
(`AsyncEngine.sync_engine` where available, otherwise `create_engine` on the URL with the async driver
swapped — Alembic's documented async recipe is `connection.run_sync`, use that with the caller's
`AsyncEngine` and do not create a second pool).

18. [ ] `varco_sa/pyproject.toml` — new optional extra `migrations = ["alembic>=1.13"]`; add `alembic`
        to the `dev` dependency group so tests can run. Bump `version` to `2.2.0`.
19. [ ] `varco_sa/varco_sa/migration/__init__.py` (new package) — re-exports; module docstring states
        the `pip install "varco-sa[migrations]"` requirement and that importing the package without
        alembic raises `MigrationBackendUnavailable` with that exact line.
20. [ ] `varco_sa/tests/test_alembic_migrator.py` (new) — **failing tests first**, all against
        in-memory/temp-file SQLite with a temp `alembic/` directory built by a fixture:
        `plan()` on a virgin DB returns `current == ()` and every revision pending;
        `upgrade()` applies them and `plan().is_empty` becomes `True`;
        `upgrade()` a second time is a no-op returning an empty `MigrationReport.applied`;
        `downgrade("base")` reverses; `stamp("heads")` marks without executing DDL (assert the table
        was NOT created); `upgrade(dry_run=True)` emits SQL to the report and touches no table;
        a revision that raises leaves `alembic_version` at N-1 and re-running resumes.
21. [ ] `varco_sa/varco_sa/migration/migrator.py` (new) — `AlembicMigrator(AbstractMigrator)`:
        ```python
        class AlembicMigrator(AbstractMigrator):
            def __init__(
                self,
                engine: AsyncEngine,
                *,
                script_location: str | Path | None = None,   # app's alembic/ dir; None → framework only
                version_locations: Sequence[str | Path] = (),
                include_framework_branch: bool = True,
                lock: AbstractDistributedLock | None = None, # None → SAXactAdvisoryLock(engine)
                settings: MigrationSettings | None = None,
            ) -> None: ...
        ```
        `include_framework_branch=True` appends the packaged
        `importlib.resources.files("varco_sa") / "migrations" / "versions"` to `version_locations`.
        Sets `transaction_per_migration=True` and `compare_type=True` in the `EnvironmentContext`
        configuration.
22. [ ] `varco_sa/varco_sa/migration/lock.py` (new) — `migration_lock(engine, key, *, timeout)`, the
        async context manager implementing D2 exactly: open a dedicated connection, `BEGIN`,
        `SET LOCAL idle_in_transaction_session_timeout = 0`, poll
        `SAXactAdvisoryLock.xact(key, session)` until acquired or `timeout` elapses, yield the
        acquired boolean, and `COMMIT` on exit (the release). Non-Postgres dialects (SQLite in unit
        tests) short-circuit to "acquired" with a one-time `logging.info` — SQLite is single-writer,
        so this is honest rather than a silent downgrade. Accepts a caller-supplied
        `AbstractDistributedLock` to route to `RedisLock` instead.
23. [ ] `varco_sa/tests/test_migration_lock.py` (new, `@pytest.mark.integration`) — **mandatory
        Postgres, SQLite cannot express this**: two `AlembicMigrator`s against one testcontainer
        Postgres run `upgrade()` concurrently; assert exactly one applies the revisions, the other
        returns `skipped_locked=True` with an empty `applied`, and the final schema is correct.
        Second test: hold the lock in one task, run the other with `lock_timeout=0.5`, assert
        `MigrationLockTimeout` when revisions are still pending and clean return when they are not.
        Third test: `SET idle_in_transaction_session_timeout = '1s'` at the role level, run a
        migration that sleeps 3s, assert the lock is still held (i.e. the `SET LOCAL` override works)
        — this is the D2 mechanic and it must have a regression test.
24. [ ] `varco_sa/varco_sa/migration/env_template.py` (new) — `include_object` (an Alembic
        `include_object` callback that returns `False` for any table in `framework_table_names()`,
        so app autogenerate never re-declares a framework table) and `configure_kwargs()` returning
        the recommended `context.configure(...)` kwargs dict in one call. Both are what an app's
        `env.py` imports.
25. [ ] `varco_sa/varco_sa/migrations/` (new, **packaged data**) — `env.py`-free version directory
        holding the framework branch. First revision `0001_varco_framework_baseline.py`,
        `branch_labels = ("varco",)`, `down_revision = None`. It creates every table in
        `framework_metadata()` **guarded by `sa.inspect(op.get_bind()).has_table(name)`**, which is
        what makes an `ensure_table()`-built database upgrade cleanly (source correction 3). Add
        `[tool.hatch.build.targets.wheel] force-include` / `artifacts` config so the `.py` revision
        files ship in the wheel — verify with `uv build && unzip -l`.
26. [ ] `varco_sa/varco_sa/migration/migrator.py` — `adopt_framework_tables()`: inspect the live DB;
        if every framework table already exists and `alembic_version` has no `varco` branch head,
        `stamp("varco@head")`. Idempotent; returns the list of adopted table names. This is the
        documented upgrade path for an existing deployment that has been using `ensure_table()`.
27. [ ] `varco_sa/tests/test_framework_branch.py` (new) — `upgrade("varco@head")` on a virgin SQLite
        DB creates all nine framework tables; running `SAJobStore.ensure_table()` **first** and then
        upgrading succeeds (the idempotence guard); `adopt_framework_tables()` on an
        `ensure_table()`-built DB stamps without executing DDL and a subsequent `plan()` is empty.
28. [ ] `varco_sa/tests/test_migration_di.py` (new) — `container.scan("varco_sa");
        container.validate_bindings()` still passes with the new modules present (the per-package
        bootstrap-health test convention from CLAUDE.md's pitfall table).
29. [ ] `varco_sa/README.md` — new "Migrations" section: the extra, the two-branch model,
        `upgrade heads` (plural), the three lines an existing `env.py` needs, and the
        `ensure_table()` trade-off. `CHANGELOG.md` — Added.

**Migration:** none for varco itself; **operators of existing deployments run
`varco migrate adopt` (Phase 5) or `AlembicMigrator.adopt_framework_tables()` once** before their
first `upgrade`. Document this prominently — it is the one manual step in the whole plan.

---

### Phase 3 — `varco_beanie.migration`: versioned runner + index reconciler — `varco_beanie`

Depends on: Phase 1. Independent of Phase 2.

**Design.** Two independent mechanisms behind one `AbstractMigrator`:
(a) hand-written, ordered `Migration` scripts recorded in a `varco_migrations` collection;
(b) index reconciliation derived from `BeanieIndexGuard`, defaulting to report-only (D5).

30. [ ] `varco_beanie/tests/test_beanie_migrator.py` (new) — **failing tests first**, unit-level with
        the existing conftest pattern (monkeypatch `Document.get_pymongo_collection()` / a fake `db`
        object exposing `find`, `find_one_and_update`, `insert_one`, `list_collection_names`):
        `plan()` on a virgin DB lists every registered migration as pending;
        `upgrade()` applies them in `version` order and writes one record per migration;
        a second `upgrade()` applies nothing; a migration that raises leaves the earlier ones recorded
        and itself unrecorded; `downgrade` of a migration with no `down()` raises
        `IrreversibleMigrationError`; a recorded migration whose `checksum` no longer matches the
        source raises (tamper detection) unless `verify_checksums=False`.
31. [ ] `varco_beanie/varco_beanie/migration/__init__.py` (new package) — re-exports.
32. [ ] `varco_beanie/varco_beanie/migration/base.py` (new):
        ```python
        class Migration(ABC):
            version: ClassVar[str]        # sortable, e.g. "20260812_001"
            name: ClassVar[str]
            @abstractmethod
            async def up(self, db: Any) -> None: ...
            async def down(self, db: Any) -> None:     # concrete → raises IrreversibleMigrationError
                raise IrreversibleMigrationError(...)

        class MigrationRegistry:
            def register(self, *migrations: type[Migration]) -> None: ...
            def discover(self, package: str) -> None: ...   # importlib walk, mirrors container.scan
            def ordered(self) -> tuple[type[Migration], ...]: ...   # sorted by version; duplicate → ValueError
        ```
        `version` uniqueness and sortability are validated in `register()`, not at apply time — a
        duplicate version is a developer error that must surface at import.
33. [ ] `varco_beanie/varco_beanie/migration/store.py` (new) — the `varco_migrations` collection:
        applied-record documents `{_id: version, name, checksum, applied_at, duration_ms, applied_by}`
        and the lock document `{_id: "__lock__", owner, acquired_at, expires_at, heartbeat_at}`.
        `acquire(owner, ttl)` is a single conditional `find_one_and_update(upsert=True)` matching
        `{_id: "__lock__", $or: [{expires_at: {$lt: now}}, {owner: owner}]}`; `heartbeat(owner, ttl)`
        renews; `release(owner)` deletes only when `owner` matches (fencing — a reclaimed holder must
        not delete the new holder's lock). Raw pymongo, no `Document` class — matching what
        `BeanieEncryptionKeyStore` already does for its own collection.
34. [ ] `varco_beanie/varco_beanie/migration/migrator.py` (new) — `BeanieMigrator(AbstractMigrator)`:
        ```python
        class BeanieMigrator(AbstractMigrator):
            def __init__(
                self,
                db: Any,                                   # AsyncDatabase
                registry: MigrationRegistry,
                *,
                index_guard: BeanieIndexGuard | None = None,
                index_mode: Literal["off", "check", "create"] = "check",
                settings: MigrationSettings | None = None,
                verify_checksums: bool = True,
                owner_id: str | None = None,               # None → f"{hostname}:{pid}"
            ) -> None: ...
        ```
        `upgrade()` acquires the lock document, starts a heartbeat task (interval = `ttl / 3`), applies
        pending migrations in order recording each, then runs index reconciliation per `index_mode`,
        then releases. `plan()` includes pending *migrations*; missing indexes appear in
        `MigrationPlan.pending` as `Revision(id="index:<collection>:<label>", branch="index")` so
        `mode="check"` reports them uniformly.
35. [ ] `varco_beanie/varco_beanie/migration/indexes.py` (new) — `IndexReconciler(guard, db)`:
        `report()` delegates to `BeanieIndexGuard.report()` verbatim; `apply()` creates only the
        `missing_indexes` (never drops `unexpected_indexes` — dropping an index someone added
        deliberately is destructive and is explicitly not done). Docstring carries the D5 warning
        about large collections and replica-set stalls, plus the recommendation to run it from the
        CLI as a pre-deploy job.
36. [ ] `varco_beanie/varco_beanie/migration/framework.py` (new) — registers varco's **own** Mongo
        migrations, the analogue of Phase 2's framework branch: an initial migration that creates the
        framework collections' indexes (`varco_outbox`, `varco_jobs`, `varco_audit_log`,
        `varco_encryption_keys` scope index, …) so `pip install -U varco-beanie` can add framework
        index requirements the same way. `MigrationRegistry` auto-registers these unless
        `include_framework=False`.
37. [ ] `varco_beanie/tests/test_beanie_migration_lock.py` (new, `@pytest.mark.integration`) — real
        MongoDB testcontainer: two `BeanieMigrator`s concurrently `upgrade()`; exactly one applies;
        the other returns `skipped_locked=True`. Second test: kill the heartbeat (simulate crash) and
        assert the lock is reclaimable only after `ttl`. Third test: `index_mode="create"` actually
        creates the missing index and `index_mode="check"` does not.
38. [ ] `varco_beanie/varco_beanie/__init__.py` — export `Migration`, `MigrationRegistry`,
        `BeanieMigrator`, `IndexReconciler`. `varco_beanie/pyproject.toml` — bump minor.
        `varco_beanie/README.md` + `CHANGELOG.md` — the Mongo migration model and the D5 index
        warning.

**Migration:** the `varco_migrations` collection is created lazily on first use. Existing deployments
that already have all indexes see an empty plan.

---

### Phase 4 — Auto-on-startup: `MigrationLifecycle` — `varco_fastapi` — **the headline**

Depends on: Phase 1, plus at least one of Phase 2 / Phase 3.

**Design.** One `AbstractLifecycle` component holding one or more `AbstractMigrator`s. Registered into
`VarcoLifespan` **first** (before the event bus, before the outbox relay, before the job runner) so
nothing touches a table that does not exist yet. `varco_fastapi` imports only
`varco_core.migration` — the concrete migrator is constructed by the app and passed in, exactly like
`AbstractEventBus`.

39. [ ] `varco_fastapi/tests/test_migration_lifecycle.py` (new) — **failing tests first**, using
        `InMemoryMigrator` from Phase 1 (no DB at all):
        `mode="off"` → `start()` never calls the migrator;
        `mode="check"` with pending revisions → raises `PendingMigrationsError` whose message lists
        them; with none → returns cleanly;
        `mode="upgrade"` → calls `upgrade(target)` once and logs the applied revisions;
        `on_failure="warn"` with a raising migrator → logs ERROR, does **not** raise;
        `on_failure="fail"` → raises;
        `skipped_locked=True` with an empty subsequent plan → returns cleanly;
        `skipped_locked=True` with a non-empty plan → raises `MigrationLockTimeout`;
        `timeout` exceeded → raises with the elapsed time in the message;
        `stop()` calls `close()` and is idempotent;
        two migrators run **sequentially in registration order** (a composite/dual-backend service).
40. [ ] `varco_fastapi/varco_fastapi/migrate.py` (new) — `MigrationLifecycle`:
        ```python
        class MigrationLifecycle:
            def __init__(
                self,
                *migrators: AbstractMigrator,
                settings: MigrationSettings | None = None,   # None → MigrationSettings.from_env()
            ) -> None: ...
            async def start(self) -> None: ...   # the whole algorithm above
            async def stop(self) -> None: ...    # close() each migrator, log-and-swallow
        ```
        Satisfies `AbstractLifecycle` structurally (`lifespan.py:70-88`) — no inheritance needed.
        `start()` wraps the run in `asyncio.timeout(settings.timeout)`; `stop()` never raises
        (matching `VarcoLifespan._stop_all`'s contract).
41. [ ] `varco_fastapi/tests/test_app_migrations.py` (new) — **failing tests first**, integration at
        the ASGI level with `TestClient`: `create_varco_app(migrations=InMemoryMigrator(...))` with
        `mode="upgrade"` runs the migrator before the first request is served (assert ordering against
        a recording event-bus stub registered after it); with a failing migrator and
        `on_failure="fail"`, entering the `TestClient` context **raises** and no request is ever
        served — this is the "refuse to serve traffic" guarantee and it needs an executable proof;
        `migrations=None` (default) leaves the lifespan component list byte-identical to today.
42. [ ] `varco_fastapi/varco_fastapi/app.py:92-121` — add
        `migrations: AbstractMigrator | Sequence[AbstractMigrator] | None = None` and
        `migration_settings: MigrationSettings | None = None` to `create_varco_app`. At `:304-311`,
        when `migrations` is not `None` and the resolved `mode != "off"`, **prepend**
        `MigrationLifecycle(...)` to `lifespan_components` before
        `_collect_lifecycle_components(container)`'s results. Default `None` → zero behaviour change.
        Update the numbered docstring at `:125-141` (step 4) and the `Args:` block.
43. [ ] `varco_fastapi/varco_fastapi/app.py` — when `migrations is None` but a `MigrationSettings`
        resolved from env has `mode != "off"`, log a single WARNING naming
        `VARCO_MIGRATE_MODE` and stating that no migrator was passed. Silently ignoring a set env var
        is the failure mode that wastes the most operator time.
44. [ ] `varco_fastapi/varco_fastapi/__init__.py` — export `MigrationLifecycle`.
        `varco_fastapi/pyproject.toml` — bump minor.
45. [ ] `varco_fastapi/varco_fastapi/composite.py` — no code change; add a docstring paragraph and a
        `technical_docs` cross-reference recording the two composite properties (startup time is the
        sum; same-database services converge on the default lock key automatically).
46. [ ] `varco_fastapi/tests/test_composite_migrations.py` (new) — two sub-apps each with their own
        `InMemoryMigrator`; assert both run, in mount order, and that one failing aborts the whole
        composite startup (the documented fail-fast composite behaviour).
47. [ ] `technical_docs/features/schema-migrations.md` (new) — **the deliverable doc**. Sections:
        the three modes and which to deploy; the auto-on-startup wiring in ten lines; the multi-pod
        algorithm diagram from D2 including the `idle_in_transaction_session_timeout` mechanic; the
        failure-semantics table; the two-branch Alembic model and `upgrade heads`; the
        `ensure_table()` reconciliation and the one-time `adopt` step; the MongoDB migration model,
        the lock document, and the D5 index warning **stated as a rule, not a footnote**; the
        composite section; the full `VARCO_MIGRATE_*` env table; a Kubernetes recipe showing
        `initContainer` running `varco migrate upgrade` with `mode=check` on the pod itself (the
        recommended production shape).
48. [ ] `CLAUDE.md` — new "Schema migrations" section under Key Abstractions; extend the decision tree
        ("Migration / schema-upgrade feature? → `varco_core.migration` contract + backend migrator");
        add pitfall rows:
        `mode="upgrade"` in a large multi-pod deployment → rolling deploys serialize on the lock, use
        `check` + a pre-deploy job;
        `ensure_table()` and migrations both active → run `adopt` once, then pick one;
        `upgrade head` (singular) with the framework branch → applies only one branch, use `heads`;
        `index_mode="create"` on a large collection → minutes-to-hours blocking build at pod startup;
        `VARCO_MIGRATE_MODE` set but no `migrations=` passed → nothing runs (now warns).
        `ARCHITECTURE.md`, `README.md`, `mkdocs.yml` (nav entry after "Postgres RLS"),
        `technical_docs/index.md`, root `CHANGELOG.md`.

**Migration:** none. **Backward compat:** with `migrations=None` (the default) `create_varco_app`
builds an identical `VarcoLifespan` — assert this in step 41.

---

### Phase 5 — The `varco` CLI — `varco_core` + per-backend subcommands

Depends on: Phase 1, plus at least one of Phase 2 / Phase 3. Independent of Phase 4.

**Design.** One console script, `argparse` (stdlib — no `click`/`typer` dependency added to `core`),
subcommands discovered via the `varco.commands` entry-point group so `varco_core` keeps zero sibling
dependencies. The migrator is resolved from a `module:callable` target, mirroring
`uvicorn app:app` and Alembic's `env.py` — the CLI does not invent config discovery.

```
varco migrate current   -t myapp.db:migrator
varco migrate history   -t myapp.db:migrator
varco migrate pending   -t myapp.db:migrator          # exit 1 if pending  → CI gate
varco migrate check     -t myapp.db:migrator          # pending + drift    → exit 1
varco migrate upgrade   -t myapp.db:migrator [--to heads] [--sql] [--no-lock]
varco migrate downgrade -t myapp.db:migrator --to <rev> [--yes]
varco migrate stamp     -t myapp.db:migrator [--to heads]
varco migrate adopt     -t myapp.db:migrator          # Phase 2's ensure_table bridge
varco migrate ddl       -t myapp.db:migrator          # offline CREATE TABLE dump (print_create_ddl)
```

49. [ ] `varco_core/tests/test_cli_migrate.py` (new) — **failing tests first**, calling `main(argv)`
        directly (no subprocess): `pending` exits `1` when revisions are pending and `0` when not;
        `upgrade` on an `InMemoryMigrator` target applies and exits `0`; a raising migrator exits `1`
        with the error on stderr; `downgrade` without `--yes` refuses and exits `2`; an unresolvable
        `-t` prints a message naming the `module:callable` form and exits `2`; `--json` emits parseable
        JSON for `current`/`pending` (CI consumption).
50. [ ] `varco_core/varco_core/cli/__init__.py` + `main.py` (new) — `main(argv=None) -> int`; builds
        the root parser, discovers subcommands via
        `importlib.metadata.entry_points(group="varco.commands")`, and prints a helpful message when
        a requested subcommand's package is not installed.
51. [ ] `varco_core/varco_core/cli/migrate.py` (new) — the `migrate` subcommand: `-t/--target`
        resolution (`importlib.import_module` + `getattr`, calling the attribute if callable and
        awaiting it if it returns a coroutine), `asyncio.run` of the chosen `AbstractMigrator` method,
        `--json` output, and exit-code mapping (`0` ok, `1` migration/drift error, `2` usage error).
52. [ ] `varco_core/pyproject.toml` — `[project.scripts] varco = "varco_core.cli.main:main"`; bump
        minor. This is the first console script in the workspace — verify it after `uv sync` with
        `uv run varco --help`.
53. [ ] `varco_sa/pyproject.toml` / `varco_beanie/pyproject.toml` —
        `[project.entry-points."varco.commands"]` registering any backend-specific extras (e.g.
        `varco migrate revision --autogenerate` for SA, which delegates to `alembic revision`, and
        `varco migrate index --create` for Beanie). Keep the shared verbs in core; only genuinely
        backend-specific verbs go here.
54. [ ] `varco_sa/varco_sa/migration/cli.py` (new) — `revision` (delegates to `alembic.command.revision`
        with varco's `include_object` pre-wired) and `heads`/`branches` passthroughs.
55. [ ] `varco_beanie/varco_beanie/migration/cli.py` (new) — `index` (`--check` default, `--create`
        opt-in) and `new` (scaffold a `Migration` file from a template with a timestamped `version`).
56. [ ] `technical_docs/features/schema-migrations.md` — add the full CLI reference, the CI-gate
        recipe (`varco migrate pending` in a pipeline step), and a pre-deploy-job manifest.
        `README.md` + root `CHANGELOG.md` — announce the `varco` command.

**Migration:** none.

---

### Phase 6 — RLS as a migration operation + operations guidance — `varco_sa`

Depends on: Phase 2. Smallest phase; deliberately last.

**Design.** RLS is decided **in scope, narrowly**: `enable_rls_ddl` (`varco_sa/rls.py:71`) emits DDL
that must be ordered after table creation and reviewed like any other schema change. Making it a
migration operation is the whole change; nothing auto-enables RLS, ever.

57. [ ] `varco_sa/tests/test_rls_migration_ops.py` (new) — **failing tests first**: `rls_upgrade`
        renders the same statements `enable_rls_ddl` produces today (compare strings — this is the
        regression guard that the `(SELECT current_setting(..., true))` InitPlan form documented in
        CLAUDE.md's pitfall table is preserved); `rls_downgrade` renders the matching
        `DROP POLICY` / `DISABLE ROW LEVEL SECURITY`; both are no-ops on a non-Postgres dialect with a
        logged warning rather than a crash.
58. [ ] `varco_sa/varco_sa/migration/ops.py` (new) — `rls_upgrade(op, table, *, tenant_column="tenant_id",
        policy_name=None, setting="varco.tenant_id")` and `rls_downgrade(op, table, *, policy_name=None)`,
        thin wrappers issuing `op.execute()` for each statement `enable_rls_ddl` builds. Reuse
        `rls.py`'s statement construction — do not duplicate the SQL.
59. [ ] `varco_sa/tests/test_rls_migration_integration.py` (`@pytest.mark.integration`) — **Postgres
        mandatory, SQLite has no RLS**: apply a revision using `rls_upgrade`, set the tenant GUC with
        `set_tenant_local`, assert cross-tenant rows are invisible, then `rls_downgrade` and assert
        they are visible again.
60. [ ] `technical_docs/features/postgres-rls.md` — new section: "Applying RLS via a migration",
        replacing any implication that RLS DDL is applied at startup; cross-link to
        `schema-migrations.md`. `technical_docs/features/schema-migrations.md` — an "Operations"
        section: the pre-deploy-job vs. init-container vs. startup decision table; what to do when a
        migration fails in production (the DB is at N-1, fix forward, never auto-downgrade); the
        `CREATE INDEX CONCURRENTLY` / `autocommit_block()` caveat and the INVALID-index cleanup;
        recommended `lock_timeout`/`statement_timeout` values; how to take a service out of the load
        balancer while a long migration runs.
61. [ ] `CLAUDE.md` — pitfall row: "RLS enabled by a startup hook" → put it in a reviewed revision with
        `rls_upgrade`. Root `CHANGELOG.md`.

---

## Edge cases

| Input / state | Expected behaviour |
|---|---|
| `VARCO_MIGRATE_MODE` unset | nothing registered, nothing runs — byte-identical to today |
| `VARCO_MIGRATE_MODE=upgrade`, `migrations=` not passed | one WARNING naming the env var; nothing runs (step 43) |
| `VARCO_MIGRATE_MODE=nonsense` | `ValueError` from `MigrationSettings.from_env()` naming the three legal values |
| Virgin database, `mode="upgrade"` | full `upgrade heads` from base; both branches applied |
| Virgin database, `mode="check"` | `PendingMigrationsError` listing every revision; startup fails |
| N pods start simultaneously, `mode="upgrade"` | exactly one holds the advisory lock and migrates; the others time out, re-plan, find nothing pending, and serve |
| N pods, one migration takes longer than `lock_timeout` | the waiters re-plan, still find pending, and raise `MigrationLockTimeout` → they exit and are restarted by the orchestrator, by which time the leader has finished |
| Pod OOM-killed mid-migration | the lock transaction dies with the connection → lock released immediately; `alembic_version` sits at the last committed revision; the next pod resumes |
| Server/role sets `idle_in_transaction_session_timeout` | overridden by `SET LOCAL … = 0` inside the lock transaction (regression-tested in step 23) |
| Revision 3 of 5 raises | revisions 1-2 committed, `alembic_version` at 2, process exits non-zero, re-run resumes at 3 |
| `CREATE INDEX CONCURRENTLY` revision fails | may leave an INVALID index; documented as requiring manual `DROP INDEX`; no automatic cleanup |
| Existing DB built by `ensure_table()` | framework baseline revision's `has_table()` guards make `upgrade` a no-op; `adopt` stamps the branch head |
| Alembic `upgrade head` (singular) with the framework branch present | applies one branch only; the docs and CLI help both say `heads` — the CLI's default `--to` is `heads` |
| `alembic` not installed, `varco_sa.migration` imported | `MigrationBackendUnavailable` naming `pip install "varco-sa[migrations]"` |
| SQLite target for `migration_lock` | short-circuits to "acquired" with one INFO log (SQLite is single-writer) |
| Mongo lock holder crashes | lock reclaimable after `ttl`; `release()` is owner-fenced so the dead holder cannot delete the new holder's lock |
| Mongo `index_mode="create"` on a 200M-doc collection | builds the index at startup and blocks the lifespan — documented as unsafe; default is `check` |
| Mongo migration with no `down()` | `downgrade` raises `IrreversibleMigrationError` |
| Mongo applied-record checksum mismatch | raises (a recorded migration's source changed); `verify_checksums=False` opts out |
| Composite app, service B's migration fails | whole process aborts startup — the documented composite fail-fast behaviour, and correct here |
| Composite app, two services, one database | both use the default `"varco:migrate"` key and serialize automatically |
| `on_failure="warn"` and migrations fail | ERROR logged, startup continues, pod serves against a stale schema — documented as the deliberately dangerous option |
| `dry_run=True` / `--sql` | Alembic offline mode renders SQL to the report; **no** connection-level writes; Mongo `dry_run` lists what would run and applies nothing |

---

## Verification

```bash
# Per phase (unit — no Docker)
uv run pytest varco_sa/tests/test_alembic_helpers.py varco_sa/tests/test_framework_metadata.py -q   # 0
uv run pytest varco_core/tests/test_migration_contracts.py -q                                        # 1
uv run pytest varco_sa/tests/test_alembic_migrator.py varco_sa/tests/test_framework_branch.py \
              varco_sa/tests/test_migration_di.py -q                                                 # 2
uv run pytest varco_beanie/tests/test_beanie_migrator.py -q                                          # 3
uv run pytest varco_fastapi/tests/test_migration_lifecycle.py \
              varco_fastapi/tests/test_app_migrations.py \
              varco_fastapi/tests/test_composite_migrations.py -q                                    # 4
uv run pytest varco_core/tests/test_cli_migrate.py -q                                                # 5
uv run pytest varco_sa/tests/test_rls_migration_ops.py -q                                            # 6

# Integration — Docker required. MANDATORY for the lock, the branch, and RLS:
# SQLite cannot express advisory locks, concurrent DDL, or row-level security.
uv run pytest varco_sa/tests/test_migration_lock.py -m integration
uv run pytest varco_sa/tests/test_rls_migration_integration.py -m integration
uv run pytest varco_beanie/tests/test_beanie_migration_lock.py -m integration

# Whole workspace + gates
make test && make lint && make type-check
uv build && unzip -l dist/varco_sa-*.whl | grep migrations/versions   # step 25: revisions must ship
uv run varco --help && uv run varco migrate --help                     # step 52: console script works
make docs                                                              # nav entry resolves
```

**Test-convention notes, per the repo's existing patterns:**
- `varco_sa` unit tests: in-memory SQLite (`aiosqlite`, already in the dev group) with a **fresh
  `DeclarativeBase` per test** (`varco_sa/tests/conftest.py:29-80`) — the Alembic tests need a fresh
  temp `alembic/` directory per test too, since `ScriptDirectory` caches.
- `varco_beanie` unit tests: monkeypatch `Document.get_pymongo_collection()` per
  `varco_beanie/tests/conftest.py:30-80`; the migrator's raw-pymongo `db` handle gets a hand-rolled
  fake exposing only `find`, `find_one_and_update`, `insert_one`, `delete_one`,
  `list_collection_names`, `create_indexes`, `index_information`.
- All tests are `async def` with no `@pytest.mark.asyncio` (`asyncio_mode = "auto"`).
- `InMemoryMigrator` (Phase 1, step 16) is the standard double for every `varco_fastapi` and CLI test,
  mirroring `InMemoryEventBus`/`InMemoryDeadLetterQueue`.
- Timing-sensitive lock tests: widen the sleep margin rather than `xfail` (CLAUDE.md test conventions).

---

## Risks

- **The held-open lock transaction (D2) is the single highest-risk mechanic.** Invariant: the lock
  transaction must remain open for the entire duration of the Alembic run, on a connection Alembic
  never touches. Failure modes: a stray `commit()`, connection-pool recycling (`pool_recycle`), or a
  server-side `idle_in_transaction_session_timeout` — any of which silently un-excludes concurrent DDL.
  Step 23's integration test covers the timeout case explicitly; the implementer must also use a
  `NullPool`/dedicated connection for the lock so `pool_recycle` cannot apply.
- **Shipping `.py` revision files inside a wheel** is easy to get wrong with hatchling and fails
  silently (the branch simply has no revisions, so `upgrade heads` succeeds and creates nothing).
  Invariant: step 25's `unzip -l` check must be in CI, not just run once by hand.
- **Two Alembic heads confuse operators** who have typed `alembic upgrade head` for years. Invariant:
  every varco-provided command and every doc example says `heads`. Mitigation if this proves too
  sharp: `include_framework_branch=False` + `get_target_metadata(include_framework=True)` is a
  fully-supported single-branch fallback.
- **`ensure_table()` and migrations both live in the same codebase** and a deployment can end up with
  a table created by one and tracked by neither. Invariant: framework baseline revisions are
  `has_table()`-guarded so they are always safe to run, and `adopt` is idempotent. The residual risk is
  a *column* added by a future framework revision to a table `ensure_table()` created and never
  stamped — `adopt` handles the stamp, so the documented order (adopt, then upgrade) must be followed.
- **Mongo lock TTL sizing** has the exact flaw that disqualified `RedisLock` for Postgres — it is
  unavoidable there. Invariant: the heartbeat interval must be ≤ `ttl / 3` and the heartbeat task must
  be cancelled in a `finally`, or a hung migration silently keeps the lock forever.
- **`mode="upgrade"` turns every rolling deploy into a lock convoy** at high replica counts (100 pods
  all waiting 30s). Invariant: the docs must lead with `check` + a pre-deploy job as the recommended
  production posture, with `upgrade` presented as the small-deployment/dev convenience it is.
- **`on_failure="warn"` is a foot-gun by construction.** Invariant: it must never become the default,
  and its docstring must state that the pod will serve traffic against a schema it may not understand.
- **Scope creep into a data-migration framework.** Invariant: `up(db)` and Alembic revisions can run
  arbitrary DML and that is where backfills live; if a step starts growing batching/resumability,
  stop and file it as a separate plan.
