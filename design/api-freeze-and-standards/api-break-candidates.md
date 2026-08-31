# API break candidates for 3.0.0 — RL-8 checkpoint

**Plan 022 / Phase 1, Steps 8–9.** Format defined by §D-RANK.

> ## ✅ CHECKPOINT HELD — 2026-08-31
>
> Plan 022 §D-RANK: *"No Phase 3 step may execute until every row's Verdict
> column is filled by the user."* Every `Verdict` cell below is now filled and
> the log at the foot of this file records each one with its date. **Phase 3 is
> executable as of 2026-08-31.**
>
> The user reviewed all twelve rows plus the two riders and accepted each row's
> **Proposal** as its Verdict, unchanged. No row was rejected and no proposal
> was amended at the checkpoint — so the Proposal and Verdict columns agree by
> construction, which is a result, not a copy-paste.
>
> Scope of what this authorises: **three** renames that ship deprecated aliases
> (AB-1, AB-2, AB-4), **one** breaking default change (AB-5), and **eight**
> rows that land no code at all (AB-3 and AB-6…AB-12 are `leave-and-document`).
> §D-DEP's precondition is therefore met — Phase 2 builds the deprecation
> mechanism.

## How blast radius was measured

`rg -c` over the working tree, counted in **four** buckets:

* **src** — occurrences in non-test, non-Markdown files, excluding
  `plans/`, `audits/`, `design/`. This is the number of edits a rename costs.
* **tests** — occurrences under any `tests/` directory.
* **docs** — occurrences in `*.md`, excluding `plans/`, `audits/`, `design/`
  (i.e. README / CLAUDE.md / ARCHITECTURE.md / `technical_docs/`).
* **historical** — `plans/` + `audits/` + `design/`. Reported for context and
  **never edited**; excluded from every "cost" figure.

Counts are occurrences, not files; the file count follows in parentheses.

---

## Rows requiring a verdict

| ID | Symbol | Category | Proposal | Blast radius (src / tests / docs · historical) | Cost if deferred | **Verdict** |
|---|---|---|---|---|---|---|
| **AB-1** | `varco_sa.rls.enable_rls_ddl` — `varco_sa/varco_sa/rls.py:71-78` | `verb-taxonomy` | `rename+alias` → `render_rls_ddl()` | 15 (3 files) / 27 (5) / 27 (8) · 28 | Post-3.0.0 needs a full deprecate→warn→remove cycle across two minor releases for a purely cosmetic gain — i.e. it would almost certainly never be done, and CLAUDE.md keeps the "exists specifically to call out" wart forever. | rename+alias |
| **AB-2** | `varco_core.migration.MigrationError` — `varco_core/migration/errors.py:23`; `varco_core.migration.MigrationPlan` — `varco_core/migration/base.py:59` | `collision` | `rename+alias` → `SchemaMigrationError` / `SchemaMigrationPlan`, re-exported from `varco_core` | `MigrationError` 37 (6) / 18 (4) / 11 (5) · 15  —  `MigrationPlan` 41 (9) / 28 (4) / 18 (6) · 14 | The two deliberate holes in `varco_core/__init__.py` (the NOTE at `:243`, the `__all__` gap at `:631-633`) and CLAUDE.md's ⚠️ become permanent. Adding the re-export later without renaming is impossible — the names collide. | rename+alias |
| **AB-3** | `install_cache_metrics` / `install_reliability_metrics` / `install_middleware_stack` / `install_cors` | `collision` | `leave-and-document` (+ a free docs correction, see below) | `install_cache_metrics` 9 (2) / 25 (2) / 10 (4) · 24  —  `install_reliability_metrics` 13 (5) / 15 (2) / 13 (5) · 22  —  `install_middleware_stack` 28 (9) / 0 / 0 · 1  —  `install_cors` 23 (5) / 3 (1) / 0 · 5 | None — the proposal is to not break it. | None |
| **AB-4** | `varco_beanie.BeanieConfig` — `varco_beanie/bootstrap.py`; vs `varco_beanie.BeanieSettings` — `varco_beanie/config.py` | `duplicate-value-object` | `rename+alias` (collapse: keep `BeanieSettings`, `BeanieConfig` becomes a deprecated alias) | `BeanieConfig` 14 (2) / 5 (1) / 9 (5) · 14  —  `BeanieSettings` 47 (9) / 27 (5) / 9 (4) · 21 | BACKLOG's `BEANIE-CFG` row explicitly routes the decision here *"before the 3.0.0 version freeze"*. Deferring means KI-10's field-for-field mapping is carried indefinitely, and `varco_beanie/__init__.py:46` keeps exporting a duplicate concept. | rename+alias |
| **AB-5** 🔴 | `CORSConfig.allow_origins = ("*",)` **with** `allow_credentials = True` — `varco_fastapi/middleware/cors.py:65`, `:81`, `from_env()` `:109-112`/`:126` | `fail-open` | `change-default` → `allow_origins = ()` (the existing `CORSConfig.restrictive()` shape), **plus** correct the two false docstring claims at `cors.py:49-51` and `:56-58` | `CORSConfig` 43 (7) / 14 (2) / 3 (2) · 9  —  `allow_origins` 11 (1) / 8 (2) / 0 · 2  —  `VARCO_CORS_ORIGINS` 5 src (incl. 2 example apps) / 1 test / 1 doc | **This is the only row with a security consequence.** Deferring means every 3.0.0 app that sets no `VARCO_CORS_*` env var ships a reflect-any-origin-with-credentials CORS policy (measured against Starlette 1.0.0 — see `measurements/fail-open-defaults.md` F1). A post-3.0.0 fix is a *silent behaviour break* with no possible alias, which is strictly worse than doing it in the window. | change-default  |
| **AB-6** | `ErrorEnvelopeSettings.include_params = True` — `varco_core/exception/settings.py:49` | `fail-open` | `leave-and-document` | 3 (2) / 2 (1) / 0 · 5 | None if left. If flipped later it is a wire-format change to every built-in exception body. | leave-and-document |
| **AB-7** | `TenancySettings.enforce_rls = False` — `varco_core/tenancy/settings.py:111` | `fail-open` | `leave-and-document` | 5 (2) / 3 (1) / 9 (4) · 11 | None if left. Flipping it would break every non-Postgres deployment and every already-provisioned Postgres one that never ran the RLS DDL. | leave-and-document |
| **AB-8** | `TenancySettings.isolation = SHARED`, `fanout_framework_tables = False` — `varco_core/tenancy/settings.py:110`, `:117` | `fail-open` | `leave-and-document` | `TenantIsolation.SHARED` 9 (6) / 2 (1) / 8 (5) · —  ·  `fanout_framework_tables` 11 (3) / 6 (2) / 6 (3) | None. CLAUDE.md documents "byte-identical to pre-Plan-007 behaviour" as a *contract*; changing it would break that written promise. | leave-and-document |
| **AB-9** | `ConnectionSettings.ssl = None` (plaintext) — `varco_core/connection/base.py`, inherited by 5 subclasses; incl. `HttpConnectionSettings(port=443, ssl=None)` | `fail-open` | `leave-and-document` (docs-only note on the `port=443` + `ssl=None` inconsistency) | 1 declaration (`connection/base.py`), 5 inheriting classes across `varco_redis`/`varco_kafka`/`varco_nats`/`varco_sa`/`varco_fastapi` | None. TLS needs a CA path varco cannot invent, and every `host` default is `localhost`. | leave-and-document |
| **AB-10** | `BeanieSettings.transactional = False` / `BeanieConfig.transactional = False` — `varco_beanie/config.py:78` | `fail-open` | `leave-and-document` | 35 occurrences of `transactional=` across `varco_beanie` + `examples` | None. `transactional=True` **raises at runtime** on a standalone MongoDB node (`config.py:71`), so the opposite default would make the framework unusable on the commonest dev topology. Interacts with AB-4 — if AB-4 collapses the classes, this field is touched anyway. | leave-and-document |
| **AB-11** | `CasbinSettings.adapter = "memory"` — `varco_casbin/config.py:123` | `fail-open` | `leave-and-document` | 5 occurrences across 5 files (1 src decl, 1 src use, 1 test, 2 docs) | None. The durable adapters need an optional extra plus a DSN with no default. | leave-and-document |
| **AB-12** | `RedisEventBusSettings.use_streams = False` — `varco_redis/config.py:103` | `fail-open` | `leave-and-document` | 5 (3) / 0 / 0 · 2 | None. Flipping it changes the wire representation and mandates consumer-group management — a different product, not a hardened default.  | leave-and-document |

### AB-3's free, non-breaking rider (docs-only, needs no verdict)

The audit confirmed a genuine documentation defect that costs nothing to fix.
CLAUDE.md's *DI wiring verb taxonomy* says `install_*` is *"sync,
**container-free** … a process-global side effect"*. True of
`install_cache_metrics` (`varco_core/observability/cache.py:203`) and
`install_reliability_metrics` (`varco_core/observability/reliability.py:373`);
**false** of `install_middleware_stack`
(`varco_fastapi/middleware/__init__.py:75`) and `install_cors`
(`varco_fastapi/middleware/cors.py:212`), which take and mutate an ASGI `app`.
`install_*` is two shapes under one verb. Amend the row to state both. Do **not**
move the app-taking pair into the `mount_*` family — `mount_*` is documented as
"an opt-in privileged HTTP surface, always behind an explicit acknowledgement
kwarg", which middleware installation is not. (Plan Step 17, unconditional.)

---

## Non-breaking riders — **no verdict needed**, land in Phase 3 (plan Step 9 / Step 16)

These are bug fixes, not API changes. They are listed here only so the
checkpoint sees the complete Phase 3 workload. **Neither is implemented yet.**

| ID | Site | Defect | Fix | Breaking? |
|---|---|---|---|---|
| **RIDER-1** | `varco_redis/varco_redis/di.py:216` (inside `async_bootstrap()`, defined at `:168`) | `container = bootstrap(container, streams=streams)` — `bootstrap()` **returns `None`** when providify is absent (`di.py:141-144`, `except ImportError: return None`). The very next statement is `await container.ainstall(RedisCacheConfiguration)`, so with `setup_cache=True` and no providify installed the caller gets `AttributeError: 'NoneType' object has no attribute 'ainstall'` instead of the documented graceful no-op. | Add `if container is None: return None` immediately after the `bootstrap()` call, before the `setup_cache` branch, and document the `None` return in the `Returns:`/`Edge cases:` blocks so it matches `bootstrap()`'s own contract. Source: BACKLOG "Deferred follow-ups (Plan 014 / audit 001 Batch B)". | **No** — strictly removes a crash on a path that is currently guaranteed to fail. |
| **RIDER-2** | `varco_fastapi/varco_fastapi/tenancy/mount.py:44`, `:103`, `:119` **and** `varco_fastapi/varco_fastapi/admin/mount.py:34`, `:95`, `:143` | Both double-mount guards are `_MOUNTED_APPS: set[int]` keyed by `id(app)`. `id()` is only unique among *live* objects: once a `FastAPI` app is garbage-collected its address can be reused, so a later app can collide with a stale entry and have its admin surface **silently not mounted**. `admin/mount.py:81-84` already documents the `id(app)` keying as a known caveat. | Replace both with `weakref.WeakSet[FastAPI]` — entries vanish when the app is collected, so no stale id can ever be matched, and membership becomes identity-based without holding the app alive. **Change both files in the same commit** (`admin/mount.py:33` explicitly says it mirrors the tenancy one; fixing one alone re-introduces the drift the comment guards against). Source: same BACKLOG batch. | **No** — module-private name, no exported symbol changes, `api_surface.py --check` must show zero delta. |

---

## Verdict log

Held **2026-08-31**. One line per row.

| Row | Verdict | Date | Reason recorded at the checkpoint |
|---|---|---|---|
| AB-1 | `rename+alias` | 2026-08-31 | A documented wart is worth deleting while it is free; `render_*` states the shape truthfully and the alias keeps every caller working. |
| AB-2 | `rename+alias` | 2026-08-31 | Renaming the newer, narrower pair closes the two deliberate holes in `varco_core/__init__.py`; the re-export is impossible later without the rename. |
| AB-3 | `leave-and-document` | 2026-08-31 | Renaming four functions to fix a naming adjacency CLAUDE.md already resolves in one row is a poor use of the window. The free docs correction lands unconditionally. |
| AB-4 | `rename+alias` | 2026-08-31 | Deletes a duplicate value object and retires KI-10's field-for-field mapping at the one moment it costs nothing. |
| AB-5 | `change-default` | 2026-08-31 | The only row with a security consequence. Deferring ships reflect-any-origin-with-credentials by default, and a post-3.0.0 fix would be a silent behaviour break with no possible alias. |
| AB-6 | `leave-and-document` | 2026-08-31 | Flipping it later is a wire-format change to every built-in exception body; no evidence it is wrong today. |
| AB-7 | `leave-and-document` | 2026-08-31 | Flipping would break every non-Postgres deployment and every provisioned Postgres one that never ran the RLS DDL. |
| AB-8 | `leave-and-document` | 2026-08-31 | CLAUDE.md documents "byte-identical to pre-Plan-007 behaviour" as a written contract. |
| AB-9 | `leave-and-document` | 2026-08-31 | TLS needs a CA path varco cannot invent; every `host` default is `localhost`. Docs-only note on the `port=443` + `ssl=None` inconsistency. |
| AB-10 | `leave-and-document` | 2026-08-31 | `transactional=True` raises on a standalone MongoDB node, so the opposite default makes the framework unusable on the commonest dev topology. |
| AB-11 | `leave-and-document` | 2026-08-31 | The durable adapters need an optional extra plus a DSN with no default. |
| AB-12 | `leave-and-document` | 2026-08-31 | Flipping changes the wire representation and mandates consumer-group management — a different product, not a hardened default. |

**Riders** (no verdict required, per §D-RANK): RIDER-1 and RIDER-2 land in Phase 3
Step 16 as bug fixes.

**Tally: 4 accepted breaks (AB-1, AB-2, AB-4 with aliases; AB-5 without), 8
`leave-and-document`, 0 rejected, 0 amended.**
