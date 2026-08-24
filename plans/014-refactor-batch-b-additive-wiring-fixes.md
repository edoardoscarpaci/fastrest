# Plan 014 — Audit 001 Batch B: three additive DI/wiring fixes (F2, F4, F7)

**Status: DONE** — all 23 steps (Phases 1–5) complete and green.

## Goal
Land the three "small, additive fixes" from `audits/001-audit-di-wiring.md` Batch B, each
behind characterization tests written first:

1. **F2** — `_try_resolve_component()` (`varco_fastapi/app.py`) stops swallowing every
   failure into `except Exception: pass`; each skipped lifecycle component produces exactly
   one log line naming the module/class (WARNING when a binding is genuinely missing after a
   successful import, DEBUG when the package simply isn't installed). **Control flow is
   unchanged — nothing new propagates out of the function.**
2. **F4** — `mount_reliability_admin()` gains the same `id(app)` double-mount guard
   `mount_tenant_admin()` already has, raising `ValueError` on a second mount for the same app.
3. **F7** — `varco_memcached.di.async_bootstrap()` gains `setup_cache: bool = True`, mirroring
   `varco_redis.di.async_bootstrap(..., setup_cache=...)` in *shape* while preserving
   memcached's current unconditional-install behaviour as the default.

After this plan, no existing correct call site changes behaviour: the only behavioural deltas
are (a) new log lines, (b) a `ValueError` on an already-buggy double mount, and (c) a new
opt-out kwarg that defaults to today's behaviour.

## Non-goals
- **Findings F1, F3, F5, F6, F8, F9, F10 are out of scope.** Do not touch
  `varco_kafka/varco_nats/varco_redis` `config.py` `@Singleton` settings (F1), do not add a
  `CacheMetricsLifecycle` (F3), do not write the taxonomy doc section (F5), do not rename
  `install_*` (F6), do not extract the shared `@Provider`-annotation helper (F8), do not add
  the missing `varco_sa`/`varco_beanie` DI bootstrap tests (F9), do not touch
  `enable_rls_ddl` (F10).
- Do **not** make lifecycle-component discovery fail fast. A missing/broken binding must still
  be skipped, exactly as today — this plan only adds the missing signal.
- Do **not** change `varco_redis.di.async_bootstrap()`'s `setup_cache=False` default
  (changing it would silently start a Redis cache pool for existing callers). Redis is the
  *reference shape*, not a target for behaviour change here.
- Do **not** fix `varco_redis.di.async_bootstrap()`'s missing `container is None` guard
  (`bootstrap()` returns `None` when providify is absent, then `container.ainstall(...)` would
  raise `AttributeError` — `varco_memcached` guards this, redis does not). It is a real,
  separate defect; note it in BACKLOG.md rather than fixing it inside this refactor.
- No new abstractions, no new modules, no signature changes to public constructors.

## Design

### F2 — tiered, always-logged, never-propagating skip

Current shape (`varco_fastapi/varco_fastapi/app.py:745-766`):

```
try:
    container.scan(module)
    mod = importlib.import_module(module)
    cls = getattr(mod, class_name)
    if not container.is_resolvable(cls):   # ← silent return, not even an exception
        return
    out.append(container.get(cls))
except Exception:                          # ← everything, silently
    pass
```

Two distinct silences: the `is_resolvable() is False` early `return` (the case the audit is
actually about — "you forgot `redis_bootstrap(container)`") and the blanket `except`.

New shape — same control flow, four labelled outcomes:

```
scan + import + getattr
 ├─ ModuleNotFoundError ................ DEBUG  "package not installed — skipping"   → return
 ├─ AttributeError ..................... WARNING "module present but has no <class>"  → return
 └─ ok
     ├─ is_resolvable() is False ....... WARNING (or DEBUG if warn_if_missing=False)  → return
     ├─ get() raises LookupError ....... WARNING "binding vanished between check and get"
     ├─ get() raises Exception ......... WARNING + exc_info=True (construction failed)
     └─ ok ............................. append to out
```

- `_try_resolve_component()` gains one keyword-only parameter,
  `warn_if_missing: bool = True`. `_collect_lifecycle_components()` passes `True` for
  `AbstractEventBus` / `AbstractJobRunner` (core infra you almost certainly meant to wire) and
  `False` for the two `varco_ws` push adapters (documented at `app.py:695-696` as "only added
  when the caller explicitly registered them" — warning on those would be pure noise for every
  app that does not use `varco_ws`).
- Every WARNING message names the module, the class, and the remedy
  (`call <package>.bootstrap(container) before create_varco_app()`), and mentions the kill
  switch — the same shape as the existing `VARCO_MIGRATE_MODE` warning at `app.py:341-350`
  and the `VARCO_TENANCY_ISOLATION` warning at `app.py:359-367`.
- Kill switch: `VARCO_LIFECYCLE_DISCOVERY_WARN` (default `true`). When falsy, the
  missing-binding WARNINGs are emitted at DEBUG instead. Read per call via a tiny module-level
  helper (`_lifecycle_discovery_warns() -> bool`) so tests can `monkeypatch.setenv` it, and so
  an app that genuinely has no event bus can silence one line without silencing its whole
  logger.
- The final catch-all stays broad (`except Exception as exc:  # noqa: BLE001`) but **logs**
  with `exc_info=True`. That is the established pattern in this very file
  (`app.py:640-641`, `app.py:657-658`) — the smell F2 names is `except ...: pass`, not a
  broad catch that reports.

### F4 — port the `_MOUNTED_APPS` guard, with one deliberate deviation

Reference implementation, `varco_fastapi/varco_fastapi/tenancy/mount.py:43,102-106,118`:
a module-level `_MOUNTED_APPS: set[int]`, checked after the acknowledgement check and
populated after `include_router()`.

`mount_reliability_admin()` (`varco_fastapi/varco_fastapi/admin/mount.py:32-114`) is
copied in shape with **one difference**: it can legitimately mount *nothing*
(`audit_repo=None` **and** `dlq=None` is not an error today). So the app id is recorded
**only when at least one router was actually included**. Recording a no-op call would poison
the app and refuse a later, legitimate mount — a new bug, not a fix.

```
mount_reliability_admin(app, ...)
  ├─ not acknowledge_bundled_admin ......... ValueError            (unchanged)
  ├─ id(app) in _MOUNTED_APPS .............. ValueError            (NEW)
  ├─ server_auth is None ................... WARNING               (unchanged)
  ├─ audit_repo → include audit router      ─┐
  ├─ dlq        → include dlq router        ─┴─ mounted_any
  └─ if mounted_any: _MOUNTED_APPS.add(id(app))                    (NEW)
```

The `ValueError` message names the same remedy shape as the tenancy one and adds why it
matters here specifically (this surface replays bus messages and deletes audit/DLQ records —
its own docstring already says "at least as privileged as the tenant control plane").

### F7 — `setup_cache: bool = True` on memcached's `async_bootstrap`

```python
async def async_bootstrap(container: Any = None, *, setup_cache: bool = True) -> Any:
```

`setup_cache=True` (default) → identical to today: `bootstrap(container)` then
`await container.ainstall(MemcachedCacheConfiguration)`. `setup_cache=False` → sync scan only,
no `ainstall`, no connection pool — i.e. exactly `bootstrap(container)`, matching how
`varco_redis.async_bootstrap(setup_cache=False)` behaves. `bootstrap()` is still called as
`bootstrap(container)` with no extra argument (`varco_memcached/tests/test_di.py:190` asserts
that call shape — it must keep passing untouched).

The docstring gains a short cross-reference paragraph explaining the *defaults* still differ
between the two packages and why (redis's `async_bootstrap` also serves the streams/event-bus
path where no cache is wanted; memcached's only reason to exist is the cache), so the
asymmetry is documented rather than merely reduced.

### Alternatives considered

- **F2: narrow the `except` so unexpected exceptions propagate (fail fast at startup).**
  ✅ Loudest possible signal for a genuinely broken binding. ❌ Changes control flow — an app
  that starts today (e.g. its `AbstractEventBus` singleton throws while connecting) would stop
  starting. The audit explicitly scopes this fix as "purely additive logging; does not change
  control flow". Rejected; logged-with-`exc_info` instead.
- **F2: WARNING on every unresolvable component, including the `varco_ws` adapters.**
  ✅ Literally what the audit's one-liner says. ❌ Two guaranteed WARNINGs at every startup for
  the ~majority of apps that never use `varco_ws` push adapters — the fastest route to a
  warning everybody learns to ignore. Rejected in favour of `warn_if_missing=False` for the
  opt-in adapters plus the env kill switch.
- **F2: put the notice behind a `create_varco_app(warn_missing_lifecycle=...)` kwarg.**
  ✅ Per-app control, no env var. ❌ New public API surface on the framework's most-used
  function for a logging concern, and it would have to be threaded through two private
  helpers. Rejected; env kill switch is cheaper and matches `VARCO_OTEL_CAPTURE_PARAMS`.
- **F4: use a `weakref.WeakSet[FastAPI]` instead of `set[int]` in both mount modules.**
  ✅ Immune to `id()` reuse after the first app is garbage-collected (a real, if rare, source
  of a spurious `ValueError` and of cross-test coupling). ❌ Diverges from the reference
  implementation the audit asked to port "verbatim in shape", and silently changes
  `mount_tenant_admin`'s existing semantics if applied to both. Rejected for this plan;
  recorded in BACKLOG.md as a follow-up that should change *both* modules together.
- **F7: document the divergence in both docstrings instead of adding the parameter** (the
  audit's alternative). ✅ Zero code change. ❌ Leaves the two same-shaped calls behaving
  differently, which is the actual reported surprise. Rejected — the additive parameter costs
  three lines and gets both symmetry *and* the doc note.
- **F7: flip memcached's default to `setup_cache=False` for exact redis parity.**
  ✅ Perfectly symmetric defaults. ❌ Silently stops opening the cache pool for every existing
  `await async_bootstrap(container)` caller — `CacheBackend` would become unresolvable at
  runtime with no error at bootstrap. Breaking; rejected.

## Steps

TDD order: every characterization step (1–12) lands and passes **against unmodified
production code** before any of steps 13+ run. Steps marked ⟳ are the characterization
assertions that are *deliberately inverted* by a later step — each names its flipping step.

### Phase 1 — characterization (no production code changes)

1. [x] `varco_fastapi/tests/test_lifecycle_component_discovery.py` — new file
   (`from __future__ import annotations`, module docstring naming Plan 014 / audit F2).
   Add a `_fake_container(*, resolvable: dict[type, object])` helper built on `MagicMock`
   following the existing pattern in
   `varco_fastapi/tests/milestone_f/test_app_factory.py:329-378` (stub
   `importlib.import_module` via `monkeypatch`, stub `scan`/`is_resolvable`/`get`).
2. [x] `varco_fastapi/tests/test_lifecycle_component_discovery.py` — characterize the
   happy path: a container where `AbstractEventBus` is resolvable →
   `_collect_lifecycle_components(container)` contains that instance. (No test covers the
   event-bus branch today; only the two `varco_ws` branches are covered.)
3. [x] Same file — characterize `container is None` → `[]`, and a container where nothing is
   resolvable → `[]` and **no exception**.
4. [x] Same file — characterize failure isolation: `container.scan` raising
   `ModuleNotFoundError` for `varco_ws.*`, and `container.get` raising `RuntimeError` for one
   component, must both leave the *other* components collected and must not raise. **This
   assertion must remain green after step 13** — it is the "control flow unchanged" contract.
5. [x] ⟳ Same file — characterize the defect: with `caplog.at_level(logging.DEBUG)` and a
   container where `AbstractEventBus` is importable but **not** resolvable,
   `_collect_lifecycle_components()` currently emits **zero** log records mentioning
   `AbstractEventBus`. Assert that, with a comment marking it as current-behaviour
   documentation. *Inverted by step 14.*
6. [x] ⟳ Same file — characterize the second defect: `container.get()` raising a non-`Lookup`
   exception currently emits **zero** records. *Inverted by step 14.*
7. [x] `varco_fastapi/tests/test_mount_reliability_admin.py` — new file. Autouse fixture that
   clears `varco_fastapi.admin.mount._MOUNTED_APPS` before **and** after each test (the set is
   process-global and `id()` values are reused after GC — without this the file is
   order-dependent). Reuse the `InMemoryDeadLetterQueue` fixture shape from
   `varco_fastapi/tests/test_dlq_router.py:21-49`.
8. [x] Same file — characterize the working contract that must not regress: mount without
   `acknowledge_bundled_admin` → `ValueError`; mount with it → `GET {prefix}/dlq/entries` is
   not 404; `server_auth=None` logs exactly one WARNING.
9. [x] ⟳ Same file — characterize the bug: calling `mount_reliability_admin(app, dlq=dlq,
   acknowledge_bundled_admin=True)` **twice with the same prefix** does not raise today, and
   the number of `app.routes` whose path starts with `/reliability/dlq` doubles.
   *Inverted by step 16.*
10. [x] ⟳ Same file — characterize the worse variant: a second mount with a *different*
    `prefix="/reliability2"` silently produces a second live admin surface (both prefixes
    answer non-404). *Inverted by step 16.*
11. [x] Same file — characterize two invariants the guard must preserve: (a) mounting on two
    **different** `FastAPI` apps succeeds; (b) `mount_reliability_admin(app,
    acknowledge_bundled_admin=True)` with neither `audit_repo` nor `dlq` mounts nothing and
    does not raise — and a subsequent real mount on that same app still succeeds (this is the
    deviation from `mount_tenant_admin` described in Design; it must be green both before and
    after step 16).
12. [x] `varco_memcached/tests/test_di.py` — extend `TestAsyncBootstrap` with a
    characterization of today's unconditional install: `await async_bootstrap(container)` (no
    kwargs) awaits `container.ainstall(MemcachedCacheConfiguration)` exactly once. Mirror it
    with a new `varco_redis/tests/test_redis_async_bootstrap.py` pinning the reference
    contract: `await async_bootstrap(mock_container)` with **no kwargs** does **not** call
    `ainstall`, while `setup_cache=True` does (no redis test covers `async_bootstrap` at all
    today). Both files must be green before step 18.

### Phase 2 — F2 implementation

13. [x] `varco_fastapi/varco_fastapi/app.py` — add module-private
    `_lifecycle_discovery_warns() -> bool` reading `VARCO_LIFECYCLE_DISCOVERY_WARN`
    (default `True`; accepts `0/false/no/off` case-insensitively — reuse whatever bool-parsing
    helper the file/package already has rather than adding a new one). Full docstring
    (Args/Returns/Edge cases).
14. [x] `varco_fastapi/varco_fastapi/app.py:705-766` — rewrite `_try_resolve_component()` per
    the Design tiering: keyword-only `warn_if_missing: bool = True`; separate
    `except ModuleNotFoundError` (DEBUG) / `except AttributeError` (WARNING) /
    `except LookupError` (WARNING) / `except Exception as exc:  # noqa: BLE001` (WARNING with
    `exc_info=True`); WARNING on `is_resolvable() is False` gated by `warn_if_missing` **and**
    `_lifecycle_discovery_warns()`. Update the docstring's `Edge cases:` block — it currently
    documents "silently skipped" three times and would otherwise become a lie. Then invert the
    step-5 and step-6 assertions to require exactly one matching record each.
15. [x] `varco_fastapi/varco_fastapi/app.py:689-700` — pass `warn_if_missing=False` for the two
    `varco_ws` components; leave `AbstractEventBus`/`AbstractJobRunner` at the default. Update
    `_collect_lifecycle_components()`'s docstring ("Missing bindings are silently skipped" →
    "logged and skipped"). Add one test asserting a missing `WebSocketEventBus` binding emits
    **no** WARNING while a missing `AbstractEventBus` emits one, and one asserting
    `VARCO_LIFECYCLE_DISCOVERY_WARN=false` demotes the latter to DEBUG.

### Phase 3 — F4 implementation

16. [x] `varco_fastapi/varco_fastapi/admin/mount.py` — add module-level
    `_MOUNTED_APPS: set[int] = set()` with the same explanatory comment as
    `tenancy/mount.py:41-43`; raise `ValueError` when `id(app) in _MOUNTED_APPS` immediately
    after the `acknowledge_bundled_admin` check; track `mounted_any` across the two
    `include_router()` blocks and `_MOUNTED_APPS.add(id(app))` only when it is `True`. Then
    invert the step-9 and step-10 assertions to `pytest.raises(ValueError)`.
17. [x] `varco_fastapi/varco_fastapi/admin/mount.py` — update the docstring: add the
    already-mounted case to `Raises:`, and add two `Edge cases:` bullets — the
    mount-nothing-does-not-poison-the-app rule, and the `id()`-based tracking caveat (an app
    that has been garbage-collected releases its id).

### Phase 4 — F7 implementation

18. [x] `varco_memcached/varco_memcached/di.py:141-198` — add keyword-only
    `setup_cache: bool = True`; wrap the local import + `await container.ainstall(...)` in
    `if setup_cache:`; keep the `container is None` providify guard *before* that branch and
    keep `bootstrap(container)` called with exactly one positional argument. Docstring: new
    `Args:` entry, an `Edge cases:` bullet ("`setup_cache=False` is equivalent to
    :func:`bootstrap` — no connection pool is opened"), and the cross-reference paragraph to
    `varco_redis.di.async_bootstrap`'s different default and why.
19. [x] `varco_memcached/tests/test_di.py` — add tests for the new parameter:
    `setup_cache=False` does **not** await `ainstall` and still returns the container;
    `setup_cache=False` with providify absent still returns `None`; the default (`True`) path
    is unchanged (step 12's characterization stays as-is).
20. [x] `varco_memcached/varco_memcached/__init__.py` — verify no re-export signature/`__all__`
    change is needed (the function object is re-exported, not its signature). No edit expected;
    confirm `TestPackageReexport` still passes.

### Phase 5 — docs & backlog

21. [x] `CHANGELOG.md` — one `### Fixed` / `### Added` block under `[Unreleased]` covering all
    three: the new lifecycle-discovery warnings + `VARCO_LIFECYCLE_DISCOVERY_WARN`, the
    `mount_reliability_admin()` double-mount `ValueError`, and
    `varco_memcached.async_bootstrap(setup_cache=)`. Explicitly state that the memcached
    default is unchanged.
22. [x] `CLAUDE.md` — three pitfall-table rows, matching the existing row style: (a) "Forgot
    `<pkg>.bootstrap(container)`" → symptom "app starts, `AbstractEventBus` silently absent" →
    fix "one WARNING now names the missing binding at startup; silence with
    `VARCO_LIFECYCLE_DISCOVERY_WARN=false` if the app genuinely has no bus"; (b)
    "`mount_reliability_admin()` called twice" → `ValueError`, same rule as
    `mount_tenant_admin()`; (c) "`varco_memcached.async_bootstrap()` opens a pool you didn't
    want" → pass `setup_cache=False` (and note redis defaults the other way).
23. [x] `BACKLOG.md` — two one-line entries for the deliberate deferrals: the
    `weakref.WeakSet` upgrade for *both* mount modules, and
    `varco_redis.di.async_bootstrap()`'s missing `container is None` guard
    (`AttributeError: 'NoneType' object has no attribute 'ainstall'` when providify is absent
    and `setup_cache=True`).

## Edge cases
- **F2 / package absent** (`varco_ws` not installed) → `ModuleNotFoundError` → one DEBUG line,
  no WARNING, component skipped. Must stay quiet — this is the common case.
- **F2 / module present, class renamed** (version skew between `varco_fastapi` and `varco_ws`)
  → `AttributeError` → WARNING naming module + class. Previously indistinguishable from
  "not installed".
- **F2 / binding resolvable but construction throws** (e.g. `RedisEventBus.__init__` opens a
  socket) → WARNING with `exc_info=True`, component skipped, **app still starts** exactly as
  today.
- **F2 / `container is None`** → `[]`, no logging at all (nothing was asked for).
- **F2 / kill switch set to a garbage value** (`VARCO_LIFECYCLE_DISCOVERY_WARN=maybe`) → treat
  as truthy (warn). Never raise from a logging-configuration read.
- **F4 / same app, both `audit_repo` and `dlq` given, called once** → both routers mounted, one
  id recorded, a second call raises.
- **F4 / same app, neither given** → nothing mounted, id **not** recorded, a later real mount
  succeeds.
- **F4 / two different apps in one process** (composite deployments, `create_composite_app`)
  → both mount successfully; the guard is per-app, never per-process.
- **F4 / same app, second call with a different `prefix`** → `ValueError`. This is the case the
  audit calls out as silently producing a second live privileged surface.
- **F7 / `setup_cache=False` and providify absent** → returns `None` (the existing guard runs
  before the branch), no `AttributeError`.
- **F7 / `setup_cache=True` (default) and Memcached unreachable** → `ConnectionRefusedError`
  still propagates from `ainstall()`, exactly as documented today.

## Verification
```bash
# Phase 1 must be green against unmodified production code:
uv run pytest varco_fastapi/tests/test_lifecycle_component_discovery.py \
              varco_fastapi/tests/test_mount_reliability_admin.py \
              varco_memcached/tests/test_di.py \
              varco_redis/tests/test_redis_async_bootstrap.py

# Regression nets for the touched surfaces (must stay green throughout):
uv run pytest varco_fastapi/tests/milestone_f/test_app_factory.py \
              varco_fastapi/tests/test_mount_tenant_admin.py \
              varco_fastapi/tests/test_dlq_router.py

# Full package suites (unit only — no Docker):
uv run pytest varco_fastapi/tests/
uv run pytest varco_memcached/tests/
uv run pytest varco_redis/tests/

# Lint + types across the workspace:
make lint
make type-check
```
Manual smoke for F2 (one command, expects exactly one WARNING naming `AbstractEventBus`):
```bash
uv run python -c "
import logging; logging.basicConfig(level=logging.DEBUG)
from providify import DIContainer
from varco_fastapi import create_varco_app
create_varco_app(DIContainer(), routers=[])
"
```

## Risks
- **New WARNINGs break a test that counts log records.** Surveyed: `varco_fastapi` tests use
  `any(...)`/substring matching over `caplog.records`, and the one exact-count assertion
  (`test_mount_tenant_admin.py:114-127`) covers `mount_tenant_admin`, not app creation — so
  the blast radius should be nil. Invariant: run the whole `varco_fastapi` suite after step 15
  and fix any count-based assertion by scoping it to its own logger, never by dropping the new
  warning.
- **Warning fatigue.** An app with no event bus and no job runner now logs two startup
  WARNINGs. Invariant: each message must name the exact missing binding *and* the kill switch,
  or it is noise. If review judges it still too noisy, demote `AbstractJobRunner` to
  `warn_if_missing=False` — do **not** demote `AbstractEventBus`, which is the finding.
- **F2 must not become fail-fast by accident.** Invariant: `_try_resolve_component()` has no
  `raise` and no bare `raise` re-throw on any path; step 4's isolation test is the guard.
- **`id()` reuse in `_MOUNTED_APPS`.** A collected `FastAPI` can free its id for a new object,
  producing a spurious `ValueError`. Pre-existing in `mount_tenant_admin`; consciously
  replicated for shape parity (see Alternatives). Invariant: every test touching a `mount_*`
  function clears the relevant `_MOUNTED_APPS` in an autouse fixture and keeps its app alive
  in a local variable for the test's duration.
- **F7 default drift.** Invariant: `varco_memcached.async_bootstrap()` with no kwargs must
  still `await ainstall(MemcachedCacheConfiguration)` — step 12's characterization test is
  written before the parameter exists precisely to pin this, and must never be edited by the
  implementer of step 18.
