# Audit 001 — DI / wiring integration points (providify) — 2026-08-21

## Summary
The per-package wiring is individually well-documented (every `di.py`/`bootstrap()`/`bind_*`/`enable_*`/`mount_*` carries a thorough docstring), but there is no single index reconciling the ~6 verb families in use, and two of the findings below are live, confirmed contradictions rather than just naming friction. The biggest single risk is F1: three event-bus packages stamp `@Singleton` directly on a pydantic `BaseSettings` subclass — the exact anti-pattern their own sibling classes' docstrings (in the same files) warn raises `LookupError`, and no test actually constructs the singleton through the container to prove it's safe.

## Findings (ranked by severity, then effort ascending)

### F1 · `@Singleton` on pydantic `BaseSettings` — contradicts CLAUDE.md's own rule, and the sibling class three lines away
- **Where:**
  - `varco_kafka/varco_kafka/config.py:113-114` — `@Singleton(priority=-sys.maxsize) class KafkaEventBusSettings(EventBusSettings)`
  - `varco_nats/varco_nats/config.py:109-110` — same pattern, `NatsEventBusSettings`
  - `varco_redis/varco_redis/config.py:61-62` — same pattern, `RedisEventBusSettings`; `varco_redis/varco_redis/di.py:12` even asserts in prose "settings are self-registering via `@Singleton` on the Pydantic `BaseSettings` subclass"
  - Contradicting sibling in the **same package/file family**: `varco_kafka/varco_kafka/channel.py:132-146` — `kafka_channel_manager_settings()` is a `@Provider` factory whose docstring explains *why* `@Singleton` on this exact shape (`__init__(self, **values: Any)`) raises `LookupError: Cannot resolve 'values: typing.Any'`, and cites this as "same precedent as `varco_casbin/di.py` and `varco_fastapi/di.py`" — i.e. `varco_kafka` documents the forbidden pattern in one file while using it in another.
  - `EventBusSettings` is confirmed to be a pydantic `BaseSettings` subclass: `varco_core/varco_core/event/config.py:32` → `class EventBusSettings(VarcoSettings)` → `varco_core/varco_core/config.py:64` → `class VarcoSettings(BaseSettings)`.
  - CLAUDE.md pitfall table row: *"`@Singleton` on pydantic `BaseSettings`" → `LookupError: Cannot resolve 'values'` → "Register settings via a `@Provider`... not `@Singleton`."*
- **Smell:** Contradictory convention / untested assumption presented as documented fact.
- **Severity:** 🔴 high
- **Effort:** S (verify + document or fix, isolated to 3 config classes)
- **Evidence:** `varco_redis/tests/test_redis_di.py:19-20` says explicitly *"No Redis server is required: only container registration and annotation resolution are exercised — nothing is instantiated."* — `validate_bindings()` checks that type hints resolve, not that the class can actually be constructed through the container. No test anywhere calls `container.get(KafkaEventBusSettings)` / `container.get(NatsEventBusSettings)` / `container.get(RedisEventBusSettings)`. The three classes likely work today only because every field is defaulted (pydantic-settings needs zero injected kwargs), which is exactly the kind of accidental-not-guaranteed behavior a contributor adding one *required* field to `KafkaEventBusSettings` would silently break, reproducing the `LookupError` the sibling class's own docstring warns about three functions away.
- **Suggested direction:** Either (a) add a regression test that actually constructs each of the three settings classes via `container.get()`/`aget()` to prove the accidental-safe case really holds, or (b) convert all three to the `@Provider` factory pattern already used by every other settings class in the same three packages, for consistency and to remove the live contradiction.
- **Risk of fixing:** Low — `@Provider` factories are already the majority pattern in these packages; switching is mechanical and covered by `validate_bindings()`.

### F2 · Forgetting a `bootstrap()`/`setup_*()` call has no signal — bare `except Exception: pass` swallows it
- **Where:** `varco_fastapi/varco_fastapi/app.py:705-766` (`_try_resolve_component`, used by `_collect_lifecycle_components` at lines 662-702) — catches `Exception` (not just `ModuleNotFoundError`/`LookupError`) around scan+resolve and silently skips on *any* failure, per its own comment at 763-766: *"Any failure (import error, binding missing, scan error) is silently skipped — lifecycle components are optional infrastructure."*
- **Smell:** Exactly the pattern CLAUDE.md's own guidance warns about elsewhere ("functions the app must remember to call manually, with no compile-time signal if forgotten") — here it's not just "forgot to call `redis_bootstrap()`", it's that forgetting produces **zero warning at any layer**: no log line, no exception, the event bus is simply absent from the lifespan.
- **Severity:** 🔴 high
- **Effort:** S (narrow the except clause + add one WARNING log)
- **Evidence:** A developer who forgets to call e.g. `redis_bootstrap(container)` before `create_varco_app(container)` gets an app that starts cleanly, serves `/health` as 200, and only fails much later — either a raw `LookupError` deep inside application code the first time something does `container.get(AbstractEventBus)`, or (per `varco_fastapi/varco_fastapi/di.py:592-597`, `setup_event_producer`'s own documented edge case) a silently-installed `NoopEventProducer` that drops every event with no error at all. This directly matches the user's named concern about opt-in wiring functions with no compile-time signal.
- **Suggested direction:** At minimum, log a single WARNING per skipped component naming the missing binding (mirrors the existing `VARCO_MIGRATE_MODE`-set-but-no-`migrations=` warning pattern already present at `app.py:341-350` and the `VARCO_TENANCY_ISOLATION`-set-but-no-`tenancy=` warning at `app.py:359-367` — i.e. this file already has the right pattern for two other subsystems, just not for event bus/job runner/ws lifecycle discovery).
- **Risk of fixing:** None — purely additive logging; does not change control flow.

### F3 · `install_cache_metrics()` has no automated wiring path; `install_reliability_metrics()` (same shape, same author intent) does
- **Where:**
  - `varco_core/varco_core/observability/cache.py:11-14` — docstring states this is *"the same shape as `install_reliability_metrics()`"*.
  - `varco_core/varco_core/observability/reliability.py:8-12` — sibling installer.
  - `varco_fastapi/varco_fastapi/reliability.py:70-83` — `ReliabilityLifecycle.startup()` calls `install_reliability_metrics(...)` automatically.
  - `varco_fastapi/varco_fastapi/app.py:369-386` — `create_varco_app(reliability=...)` wires `ReliabilityLifecycle` into the lifespan automatically.
  - No equivalent exists anywhere for `install_cache_metrics()` — no `CacheLifecycle`, no `create_varco_app(cache_metrics=...)` kwarg; grepping the whole tree for `install_cache_metrics` outside `varco_core/varco_core/observability/cache.py` finds only its own test files.
- **Smell:** Two functions the codebase itself describes as structurally identical ("same shape") diverge in how discoverable/automatic their wiring is, with no doc explaining why.
- **Severity:** 🟡 medium
- **Effort:** S (either add a thin `CacheMetricsLifecycle` + `create_varco_app` kwarg, or add one line to `install_cache_metrics()`'s docstring cross-referencing that it is manual-only by design)
- **Evidence:** A team that adopted `reliability=ReliabilityPreset.durable(...)` and assumed "the framework wires observability packs for me" will reasonably expect the same for cache metrics and get silent zero-data dashboards instead — this exact failure mode is independently called out in CLAUDE.md's own pitfall table for `install_cache_metrics` ("Cache metrics never appear... `install_cache_metrics()` was never called").
- **Suggested direction:** Either give it parity (a lifecycle wrapper reachable from `create_varco_app`) or add one sentence to both docstrings cross-referencing the deliberate asymmetry.
- **Risk of fixing:** None if doc-only; low if adding a lifecycle wrapper (additive, opt-in).

### F4 · `mount_tenant_admin()` guards against double-mount; `mount_reliability_admin()` (same contract shape) does not
- **Where:**
  - `varco_fastapi/varco_fastapi/tenancy/mount.py:43` (`_MOUNTED_APPS: set[int] = set()`) and `:102-106` (raises `ValueError` on a second mount for the same app).
  - `varco_fastapi/varco_fastapi/admin/mount.py:32-114` (`mount_reliability_admin`) — identical `acknowledge_bundled_admin`/`server_auth`/`admin_role`/`prefix`/`dependencies` contract, explicitly modeled on the tenant admin surface ("Mirrors Plan 007 RD-9... verbatim", line 7), but has no equivalent guard — a second call duplicates routes (FastAPI raises on the actual route conflict, but only if paths collide identically; a different `prefix=` on the second call silently mounts a second, live admin surface).
- **Smell:** Two functions documented as deliberately mirroring each other's privilege-gating contract diverge on idempotency, with no test covering the reliability admin surface's double-mount behavior.
- **Severity:** 🟡 medium
- **Effort:** S (port the `_MOUNTED_APPS` guard)
- **Evidence:** `mount_reliability_admin` can replay bus messages and delete audit/DLQ records (per its own docstring, "at least as privileged as the tenant control plane") — an accidental second mount (e.g. a re-entrant startup hook, a hot-reload in dev) silently doubling this surface is a materially worse outcome than doubling a read-mostly router.
- **Suggested direction:** Add the same `id(app)` tracking set used by `mount_tenant_admin`.
- **Risk of fixing:** None — purely additive guard; only changes behavior for the (already-buggy) double-mount case.

### F5 · No package-level index of the wiring-verb taxonomy (docs gap, not a bug)
- **Where:** Spread across `varco_*/varco_*/di.py` (bootstrap), `varco_redis/varco_redis/di.py` + `varco_memcached/varco_memcached/di.py` (`async_bootstrap`), `varco_casbin/varco_casbin/di.py:127` (`enable_policy_authorizer`), `varco_sa/varco_sa/rls.py:71` (`enable_rls_ddl`), `varco_fastapi/varco_fastapi/tenancy/mount.py` + `varco_fastapi/varco_fastapi/admin/mount.py` (`mount_*`), `varco_sa/varco_sa/di.py:225` + `varco_beanie/varco_beanie/di.py:103` + `varco_fastapi/varco_fastapi/di.py:106,195` + `varco_fastapi/varco_fastapi/client/peer.py:324` + `varco_ws/varco_ws/di.py:150,279` (`bind_*`), `varco_core/varco_core/observability/cache.py:204` + `varco_core/varco_core/observability/reliability.py:370` (`install_*`).
- **Smell:** Each function is individually excellent (docstrings explain "why this verb, why opt-in, why not scanned"), but nothing reconciles them. In practice the taxonomy that emerges from reading all ten `di.py` files is real and mostly consistent:
  - `bootstrap(container=None, ...)` — sync `container.scan(pkg)` wrapper, one per package, returns the container (or `None` if providify absent).
  - `async_bootstrap(...)` — `bootstrap()` + an async `ainstall(SomeConfiguration)` step, only where an async connection must open before the singleton is usable (redis cache, memcached cache).
  - `bind_*(container, ...)` — register N *typed, per-item* generic bindings (`AsyncRepository[D]`, `VarcoClient[Router]`, per-channel event-bus adapters) that can't be known until app startup.
  - `enable_*(container)` — flip on an opt-in DI *binding* that would otherwise shadow an app default if auto-registered (`enable_policy_authorizer`).
  - `mount_*(app, ...)` — flip on an opt-in privileged *HTTP surface*, always gated by an explicit acknowledgement kwarg.
  - `install_*(...)` — a **container-free** process-global side effect (OTel instrument registration); despite the verb, unrelated to `container.install(SomeConfiguration)`.
  But this taxonomy exists only as an inference from reading every file — it is not written down anywhere the way the rest of CLAUDE.md's decision trees are.
- **Severity:** 🟡 medium (docs)
- **Effort:** M (one new section, cross-linking the existing docstrings — no code change)
- **Evidence:** The user's own framing of this task ("dual pattern is genuinely hard to follow") is the evidence — every individual piece is documented, but the meta-pattern is not, which is exactly the CLAUDE.md-style gap this file's own conventions are good at closing everywhere else.
- **Suggested direction:** Add a "DI wiring verb taxonomy" subsection to CLAUDE.md (same style as the existing "Decision Tree" section) listing the six verb families above with one example each.
- **Risk of fixing:** None — documentation only.

### F6 · `install_cache_metrics()`/`install_reliability_metrics()` take no `container` argument, unlike every other wiring verb
- **Where:** `varco_core/varco_core/observability/cache.py:204` (`def install_cache_metrics(*, config: CacheMetricsConfig | None = None) -> None`) and `varco_core/varco_core/observability/reliability.py:370` (`def install_reliability_metrics(*, dlq=..., dlq_name=..., outbox_repo=..., config=...) -> None`) — both mutate module-level globals (`_enabled`/`_config` in cache.py; `_depth_targets`/`_owner_loop`/etc. in reliability.py) directly.
- **Smell:** Naming collision with providify's own `container.install(SomeConfiguration)` verb. Every other wiring function in the inventory (`bootstrap`, `bind_*`, `enable_*`) takes `container` as its first positional argument; these two do not, because they are deliberately *not* container-based (per their own docstrings: "deliberately not a scanned `@Configuration`"). The design choice is sound and documented in isolation — the confusion is purely that the verb "install" is already provify-reserved elsewhere in the same codebase for a different mechanism.
- **Severity:** 🟢 low (docs)
- **Effort:** S
- **Evidence:** A reader skimming call sites for `container.install(X)` vs. `install_cache_metrics()` has to open the function to learn the second one takes no container at all — the two "install"s look identical at a call site (`install_cache_metrics(config=...)` vs `container.install(OtelConfiguration)`) but are unrelated mechanisms.
- **Suggested direction:** Rename to `enable_cache_metrics()`/`enable_reliability_metrics()` (matching the `enable_*` = "opt-in process-wide side effect, no container" family used by `enable_policy_authorizer`) — or, cheaper, add one line to both docstrings explicitly disambiguating from `container.install()`.
- **Risk of fixing:** A rename would be a breaking API change for the two call sites in `varco_fastapi/varco_fastapi/reliability.py:71` and any app code calling `install_cache_metrics` directly — doc-only fix is lower risk and sufficient.

### F7 · `varco_redis`/`varco_memcached` solve the identical "sync scan + optional async setup" problem with different `async_bootstrap()` contracts
- **Where:** `varco_redis/varco_redis/di.py:162-217` — `async_bootstrap(container=None, *, streams=False, setup_cache=False)`; cache install is opt-in, defaulting to **off** (calling `async_bootstrap()` with no kwargs behaves exactly like calling sync `bootstrap()`). `varco_memcached/varco_memcached/di.py:141-198` — `async_bootstrap(container=None)`; cache install is **unconditional** — there is no toggle equivalent to `setup_cache`.
- **Smell:** Same conceptual step (open an async connection pool) exposed with divergent defaults across two backend packages that otherwise mirror each other closely (both packages' `bootstrap()`/`async_bootstrap()` docstrings use nearly identical prose).
- **Severity:** 🟡 medium
- **Effort:** S
- **Evidence:** A developer who has internalized "`async_bootstrap()` alone doesn't start the cache, pass `setup_cache=True`" from working with `varco_redis` and then adds `varco_memcached` to the same app will be surprised that the memcached cache pool opens unconditionally on the very same-shaped call.
- **Suggested direction:** Either give `varco_memcached.async_bootstrap()` a `setup_cache: bool = True` parameter (for symmetry, with the current behavior as default) or document the divergence explicitly in both docstrings with a cross-reference.
- **Risk of fixing:** Low if additive (`setup_cache: bool = True` preserves current default behavior).

### F8 · Duplicated `Provider`-return-annotation-patch closures across 4 packages
- **Where:** `varco_ws/varco_ws/di.py:248-273` (`bind_websocket_adapter`'s `_ws_factory`) and `:362-383` (`bind_sse_adapter`'s `_sse_factory`); `varco_fastapi/varco_fastapi/router/mcp.py` (`bind_mcp_adapter`, cross-referenced by name at `varco_ws/varco_ws/di.py:257,367` as "same pattern"); `varco_sa/varco_sa/di.py:285-329` (`_make_repo_provider`, sync) and `varco_beanie/varco_beanie/di.py:160-203` (`_make_repo_provider`, async — otherwise near-identical, including the docstring's DESIGN block wording).
- **Smell:** The same workaround (patch `factory.__annotations__["return"]` after defining a closure, because `from __future__ import annotations` turns `Inject[T]`/generic-alias hints into unresolvable strings) is independently reimplemented four-plus times with copy-pasted DESIGN comments rather than a shared helper.
- **Severity:** 🟡 medium
- **Effort:** M (extract a `_provide_singleton(container, factory, *, returns=, async_=False)` helper into a small shared module, e.g. `varco_core.di_helpers` — but note `varco_core` is otherwise providify-free by design, per `varco_fastapi/varco_fastapi/di.py:96-98`, so this would need to land in `varco_fastapi` or a new tiny leaf module instead).
- **Evidence:** `varco_sa/varco_sa/di.py:293-297` and `varco_beanie/varco_beanie/di.py:167-172`'s DESIGN blocks are word-for-word identical except for "sync"/"async" — a change to the workaround (e.g. if a future providify version fixes the PEP-563 resolution) requires editing 4+ call sites instead of 1.
- **Suggested direction:** Extract one shared factory-builder; keep it out of `varco_core` (which is deliberately providify-free — see `varco_sa/varco_sa/di.py`'s own DESIGN note about avoiding a circular import) and put it somewhere neutral like `varco_fastapi.di` or a new tiny `varco_core.providify_compat`-style leaf if `varco_core` staying import-clean of providify is not actually load-bearing here.
- **Risk of fixing:** Low-medium — behavior-preserving refactor, but touches four packages' DI surface; needs the existing `validate_bindings()` tests in each package re-run as the safety net (already present per F9's inventory).

### F9 · Two packages lack the documented "container actually bootstraps" regression test
- **Where:** `varco_beanie/tests/test_beanie_di.py:1-20` — docstring states outright *"No actual container resolution is performed... `bind_repositories` is tested against a mock container"* — there is no `DIContainer(); container.scan("varco_beanie"); container.validate_bindings()` anywhere covering the package's core `BeanieModule`/`bind_repositories` wiring (only `test_beanie_tenancy_di.py` and `test_beanie_dlq.py` apply that pattern to unrelated sub-areas). `varco_sa` has no `test_sa_di.py` at all (confirmed via directory listing of `varco_sa/tests/`) — only `test_migration_di.py` and `test_sa_tenancy_di.py` exercise `validate_bindings()`, neither of which covers the core `SAModule`/`bind_repositories`/`sa_advisory_lock` wiring documented in `varco_sa/varco_sa/di.py`.
- **Smell:** Direct, named-in-CLAUDE.md pitfall: *"A package's suite is green but its container won't bootstrap... Add a `container.scan(pkg); container.validate_bindings()` test per package (see `varco_redis/tests/test_redis_di.py`)."*
- **Severity:** 🟡 medium
- **Effort:** S (one small file per package, following the existing `test_redis_di.py`/`test_kafka_di.py`/`test_nats_di.py`/`test_observability_di.py`/`test_di.py` (casbin, memcached, ws) template already present in 7 of the other 9 packages)
- **Evidence:** `varco_kafka/tests/test_kafka_di.py`, `varco_nats/tests/test_nats_di.py`, `varco_redis/tests/test_redis_di.py`, `varco_core/tests/test_observability_di.py`, `varco_casbin/tests/test_di.py`, `varco_memcached/tests/test_di.py`, `varco_ws/tests/test_di.py` all exist and do exactly this — `varco_sa` and `varco_beanie` (arguably the two packages with the most complex DI surface, given per-entity repository binding + async init) are the two gaps.
- **Suggested direction:** Add `varco_sa/tests/test_sa_di.py` and extend/rename `varco_beanie/tests/test_beanie_di.py` to include a real `container.scan("varco_beanie"); container.validate_bindings()` block alongside its existing mock-based unit tests.
- **Risk of fixing:** None — additive test only.

### F10 · `enable_rls_ddl()` shares a verb with the DI opt-in family but does something unrelated
- **Where:** `varco_sa/varco_sa/rls.py:71-78` — pure function, no I/O, no container, returns `list[str]` of raw SQL DDL for the caller to embed in an Alembic revision. Contrast `varco_casbin/varco_casbin/di.py:127-160` (`enable_policy_authorizer(container)` — mutates a DI container's bindings) and `varco_fastapi/varco_fastapi/tenancy/mount.py` / `admin/mount.py` (`mount_*` — the actual "opt-in privileged surface" family this function's *name* suggests it belongs to).
- **Smell:** Naming collision with the documented `enable_*` = "opt-in DI activation, deliberately not scanned" convention (CLAUDE.md's own pitfall table: *"Policy authorizer silently active... The authorizer is opt-in via `enable_policy_authorizer(container)`"*). `enable_rls_ddl` has nothing to do with DI at all.
- **Severity:** 🟢 low — this is a legitimate, intentional design (RLS DDL generation genuinely doesn't belong in the container; it belongs in a reviewed Alembic revision, per CLAUDE.md's own "RLS enabled by a startup hook" pitfall row explicitly warning *against* wiring RLS into any runtime hook). Classify as **docs**, not refactor.
- **Effort:** S (docs-only)
- **Evidence:** None reported — no test or call site treats `enable_rls_ddl` as a container-mutating function; this is purely a naming-pattern-matching risk for a reader who has just learned the `enable_policy_authorizer` convention from CLAUDE.md and assumes the verb always means the same thing.
- **Suggested direction:** A one-line docstring note in `rls.py` cross-referencing that this `enable_*` is unrelated to the DI opt-in family (i.e. "no container is touched here — see `enable_policy_authorizer` for that pattern").
- **Risk of fixing:** None (docs only); renaming was considered but rejected — `enable_rls_ddl` is referenced throughout `technical_docs/features/postgres-rls.md` and multiple migration recipes in CLAUDE.md itself, so a rename is pure churn for a non-bug.

## Not-findings (deliberate, leave alone)
- `mount_tenant_admin`/`mount_reliability_admin`'s `acknowledge_bundled_admin=True` friction and lack of any `VARCO_*_MOUNT_ADMIN` env var (`varco_fastapi/varco_fastapi/tenancy/mount.py:7-23`, `admin/mount.py:1-15`) — explicitly documented, intentional (RD-9), and consistent between the two.
- `enable_policy_authorizer` being a free function rather than a scanned `@Configuration` (`varco_casbin/varco_casbin/di.py:127-160`) — explicitly justified against the "silently shadows the app's own authorizer" failure mode, and the reasoning is sound and consistent with `install_cache_metrics`/`install_reliability_metrics`'s identical justification.
- `priority=-sys.maxsize - 1` used consistently for every framework-default `@Provider` across `varco_sa/varco_sa/di.py`, `varco_fastapi/varco_fastapi/di.py` — this is the one piece of the taxonomy that is genuinely uniform across all packages inspected.
- `bootstrap()` returning `None` on `ImportError` for providify absence, repeated in every backend package's `bootstrap()` — consistent, deliberate graceful-degradation pattern (`varco_kafka/di.py:119-122`, `varco_nats/di.py:121-124`, `varco_redis/di.py:134-137`, `varco_memcached/di.py:110-121`, `varco_sa/di.py:401-404`, `varco_beanie/di.py:278-281`, `varco_ws/di.py:130-133`, `varco_casbin/di.py:112-115`, `varco_fastapi/di.py:699-702`).

## Suggested batches
- **Batch A (quick wins, no behavior change):** F5 (taxonomy doc section), F6 (docstring disambiguation), F10 (docstring note), F9 (two new test files) — all additive, no production code path changes, safe to land together.
- **Batch B (small, additive fixes):** F2 (narrow the except + log), F4 (port the double-mount guard), F7 (add `setup_cache=` to memcached for symmetry) — each is an isolated, low-risk change to a single function.
- **Batch C (needs care — verify before touching):** F1 (pydantic `@Singleton` on event-bus settings) — write the missing regression test *first* to establish current actual behavior, then decide fix-vs-document; this is the one finding where the "intended" behavior is genuinely ambiguous from the code alone.
- **Batch D (structural, own plan):** F8 (extract the shared `Provider`-annotation-patch helper) — touches four packages' DI surfaces; should be scoped as its own small refactor with the existing `validate_bindings()` suites as the regression net.
