# Plan 013 — DI wiring taxonomy docs + missing bootstrap regression tests (Audit 001, Batch A)

## Goal
Close the four "quick win" findings of `audits/001-audit-di-wiring.md` Batch A — **F5**
(no written index of the DI wiring-verb taxonomy), **F6** (`install_*` metrics functions
collide in name with `container.install()`), **F9** (`varco_sa`/`varco_beanie` lack the
canonical "container actually bootstraps" regression test), **F10** (`enable_rls_ddl()`
collides in name with the `enable_*` DI opt-in family). After this plan: CLAUDE.md carries
a "DI wiring verb taxonomy" subsection, three docstrings carry one disambiguating line
each, and `varco_sa`/`varco_beanie` each have a named, canonically-located DI bootstrap
test matching the seven other packages' template.

## Non-goals
- **No production code path changes.** Batch A is additive docs + tests only.
- **No renames.** `install_cache_metrics`/`install_reliability_metrics` are NOT renamed to
  `enable_*` (breaking API change for `varco_fastapi/varco_fastapi/reliability.py:71` and
  any app call site — audit F6 "Risk of fixing"). `enable_rls_ddl` is NOT renamed
  (referenced throughout `technical_docs/features/postgres-rls.md` and CLAUDE.md's own
  migration recipes — audit F10 explicitly rejects the rename as "pure churn for a non-bug").
- **Every other audit finding is out of scope**: F1 (pydantic `@Singleton` on event-bus
  settings — Batch C, needs its own characterization plan), F2 (bare `except Exception` in
  `_try_resolve_component`), F3 (`install_cache_metrics` has no lifecycle parity), F4
  (`mount_reliability_admin` double-mount guard), F7 (`async_bootstrap` contract divergence),
  F8 (duplicated `Provider`-annotation-patch closures — Batch D).
- No new `CacheMetricsLifecycle`, no `create_varco_app(cache_metrics=...)` kwarg (that is
  F3's option (a), out of scope).
- No changes to `varco_beanie`'s existing mock-based unit tests — the new block is *added
  alongside* them, nothing is deleted or rewritten.

## Design

Four independent, non-interacting edits. Three are pure prose; one adds tests. They share
one theme — the codebase's DI wiring verbs are individually well documented but the
*meta-pattern* and its two name collisions are not written down anywhere.

```
F5 ─ CLAUDE.md      + "### DI wiring verb taxonomy" (6 families, 1 example each)
                        │  cross-links ↓
F6 ─ observability/cache.py       install_cache_metrics()      + 1 line: "not container.install()"
     observability/reliability.py install_reliability_metrics() + 1 line: same
F10 ─ varco_sa/rls.py             enable_rls_ddl()             + 1 line: "not the DI enable_* family"

F9 ─ varco_sa/tests/test_sa_di.py           (new)   ┐ scan + validate_bindings, matching the
     varco_beanie/tests/test_beanie_di.py    (extend) ┘ test_redis_di.py / test_kafka_di.py template
```

The six verb families (verbatim from audit F5's "Smell" section — this is the content of
the new CLAUDE.md subsection, do not re-derive it):

| Verb | Shape | Meaning | Example |
|---|---|---|---|
| `bootstrap(container=None, ...)` | sync, returns container or `None` | one per package; wraps `container.scan(pkg)`; returns `None` if providify is absent | `varco_kafka.di.bootstrap` |
| `async_bootstrap(...)` | async, returns container | `bootstrap()` + an `await container.ainstall(SomeConfiguration)` step, only where an async connection must open before the singleton is usable | `varco_redis.di.async_bootstrap(setup_cache=True)`, `varco_memcached.di.async_bootstrap` |
| `bind_*(container, ...)` | sync, mutates container | registers N *typed, per-item* generic bindings unknowable before app startup | `varco_sa.di.bind_repositories`, `varco_fastapi.client.bind_clients_from`, `varco_ws.di.bind_websocket_adapter` |
| `enable_*(container)` | sync, mutates container | flips on an opt-in DI **binding** that would shadow an app default if auto-registered | `varco_casbin.di.enable_policy_authorizer` |
| `mount_*(app, ...)` | sync, mutates the ASGI app | flips on an opt-in privileged **HTTP surface**, always behind an explicit acknowledgement kwarg | `varco_fastapi.tenancy.mount_tenant_admin`, `varco_fastapi.admin.mount_reliability_admin` |
| `install_*(...)` | sync, **container-free** | a process-global side effect (OTel instrument registration) — despite the verb, unrelated to `container.install(SomeConfiguration)` | `install_cache_metrics`, `install_reliability_metrics` |

Plus the two collision warnings the subsection must call out explicitly, because they are
exactly what F6/F10 are about:
- `install_*` in this taxonomy takes **no container** — `container.install(X)` is providify's
  unrelated `@Configuration`-install verb.
- `enable_rls_ddl()` (`varco_sa/varco_sa/rls.py`) is **not** in the `enable_*` family — it is a
  pure DDL-string generator, touches no container, performs no I/O.

### Alternatives considered
- **Rename `install_cache_metrics`/`install_reliability_metrics` → `enable_*`** (audit F6's
  first suggestion): ✅ removes the collision at the source, makes the taxonomy uniform.
  ❌ breaking API change for `varco_fastapi/varco_fastapi/reliability.py:71` and every app
  calling it directly; the audit itself scores doc-only as "lower risk and sufficient", and
  Batch A is explicitly "no behavior change". Rejected.
- **Rename `enable_rls_ddl` → `rls_ddl`/`build_rls_ddl`** (audit F10): ✅ removes the second
  collision. ❌ the name appears throughout `technical_docs/features/postgres-rls.md` and
  several CLAUDE.md migration recipes — a rename is documentation churn for a non-bug, and
  the audit explicitly records it as "considered but rejected". Rejected.
- **Put the taxonomy in a new `technical_docs/features/di-wiring.md` instead of CLAUDE.md**:
  ✅ keeps CLAUDE.md shorter. ❌ the audit's own framing is that this gap is "exactly the
  CLAUDE.md-style gap this file's own conventions are good at closing everywhere else" —
  the taxonomy's value is being in the file an agent/contributor already reads before
  wiring anything. Rejected; CLAUDE.md it is.
- **Give `varco_sa`/`varco_beanie` the F9 tests by just relying on the existing
  `test_sa_tenancy_di.py` / `test_beanie_tenancy_di.py` recursive scans** (see Spot-check
  finding below): ✅ zero new files. ❌ the coverage is incidental — it lives in a file
  named for an unrelated sub-area, so deleting/refactoring that sub-area's test silently
  drops the whole package's bootstrap safety net, and no test asserts the *core* wiring
  (`SAModule`, `bind_repositories` against a real container, `bootstrap()`'s return
  contract) by name. Rejected.

### Spot-check finding (recorded so the implementer isn't surprised)
Audit F9 says `varco_sa`/`varco_beanie` "lack the documented regression test". Verified more
precisely:
- `varco_sa/tests/test_sa_tenancy_di.py:12-14` and `varco_sa/tests/test_migration_di.py:20-23`
  **already** call `container.scan("varco_sa", recursive=True); container.validate_bindings()`.
- `varco_beanie/tests/test_beanie_tenancy_di.py:12-14` and
  `varco_beanie/tests/test_beanie_dlq.py:87-89` do the same for `varco_beanie`.

So raw annotation-resolution coverage of these two packages exists today; what is missing is
a **named, canonically-located** test (`test_sa_di.py` / a block in `test_beanie_di.py`)
asserting the *core* wiring explicitly. Step 6/7 below must therefore state this in the new
files' docstrings rather than claiming to close a coverage hole that is already incidentally
covered. `varco_beanie/tests/test_beanie_di.py:1-20`'s "No actual container resolution is
performed" docstring **is** accurate for that file and must be updated by step 7.

## Steps

### Phase 0 — characterization (required by the refactor workflow)

1. [ ] **F5 / F6 / F10 — no behaviour to characterize; record why.** These three steps change
   only Markdown prose and Python docstrings. No executable code path, no signature, no
   return value, and no import graph is touched, so there is no behaviour a characterization
   test could pin. This requirement is explicitly **satisfied by this note**, not skipped:
   the "before" and "after" states are byte-identical to every runtime caller. Verification
   for these three is therefore lint/type-check/docs-build only (see Verification).

2. [ ] `varco_beanie/tests/test_beanie_di.py` — **characterize the current mock-only nature**
   before extending it. Run `uv run pytest varco_beanie/tests/test_beanie_di.py -q` and record
   the pass count; confirm by reading lines 1-40 that (a) the module docstring asserts "No
   actual container resolution is performed", (b) the only container used is
   `MagicMock()` (lines 217, 227, 239, 258), (c) `providify.DIContainer` is not imported.
   Do not change the file in this step — this is the "before" snapshot the step-7 extension
   is measured against.

3. [ ] `varco_sa/tests/` — **characterize the absence.** Confirm via `ls varco_sa/tests/` that
   no `test_sa_di.py` exists, and confirm (grep `validate_bindings`) that the only two files
   exercising the container are `test_migration_di.py` and `test_sa_tenancy_di.py`, neither
   named for nor asserting anything about `SAModule`/`bind_repositories`/`bootstrap`. Record
   this as the "before" state. No file change in this step.

4. [ ] Baseline the two suites so Phase 2 has a green reference:
   `uv run pytest varco_sa/tests/ varco_beanie/tests/ -q` — record pass/fail/skip counts.
   Any pre-existing failure here is **not** in scope for this plan; note it and proceed.

### Phase 1 — docs (F5, F6, F10)

5. [ ] `CLAUDE.md` — insert a new `### DI wiring verb taxonomy` subsection **immediately after
   the existing `### DI wiring (providify)` subsection (currently lines 135-146) and before
   `### Resilience (varco_core.resilience)` (currently line 147)**. Content = the six-row
   table in the Design section above, in the same Markdown table style already used
   throughout CLAUDE.md, followed by the two explicit collision warnings
   (`install_*` ≠ `container.install()`; `enable_rls_ddl` ∉ the `enable_*` family). Each row
   names at least one real, importable example so a reader can jump straight to the
   authoritative docstring. Do not restate the individual docstrings' reasoning — cross-link
   to them ("see that function's docstring for why it is opt-in / not scanned").

6. [ ] `varco_core/varco_core/observability/cache.py` — add one line to
   `install_cache_metrics()`'s docstring (function at line 204) disambiguating it from
   providify's `container.install()`: this function takes **no container**, mutates
   module-level globals, and is deliberately not a scanned `@Configuration`. Reference the
   new CLAUDE.md taxonomy subsection by name. Do **not** rename the function.

7. [ ] `varco_core/varco_core/observability/reliability.py` — add the same one-line
   disambiguation to `install_reliability_metrics()`'s docstring (function at line 370),
   worded consistently with step 6 so the two siblings stay symmetric (they already describe
   each other as "the same shape"). Do **not** rename the function.

8. [ ] `varco_sa/varco_sa/rls.py` — add one line to `enable_rls_ddl()`'s docstring (function
   at line 71, docstring starting line 79) noting that this `enable_*` is **unrelated** to
   the DI opt-in family: no container is touched, no binding is registered, no I/O is
   performed — see `varco_casbin.di.enable_policy_authorizer` for that pattern. Place it near
   the existing "**Nothing is applied here** — this function performs no I/O." sentence
   (lines 84-85), which it complements. Do **not** rename the function.

### Phase 2 — tests (F9)

9. [ ] `varco_sa/tests/test_sa_di.py` — **new file**, modelled on
   `varco_redis/tests/test_redis_di.py` (module docstring explaining *why this file exists*,
   one `Test*` class, one docstring per test naming the user-visible symptom). Must contain,
   at minimum:
   - a test doing `container = DIContainer(); container.scan("varco_sa", recursive=True);
     container.validate_bindings()` — the canonical pattern;
   - a test asserting the core `SAModule`-contributed implementations are actually discovered
     by that scan (mirror `test_redis_di.py:52-58`'s `implementations` set built from
     `container._bindings`; assert on the `SQLAlchemyRepositoryProvider` /
     `SAAdvisoryLock` / `SAXactAdvisoryLock` names registered per
     `varco_sa/varco_sa/di.py:77-180`);
   - a test that `bind_repositories(container, _Entity)` against a **real** `DIContainer`
     (not a `MagicMock`) still leaves `validate_bindings()` green — this is the part no
     existing `varco_sa` test covers at all.
   Module docstring must state the Spot-check finding above: the recursive scan was already
   incidentally exercised by `test_migration_di.py`/`test_sa_tenancy_di.py`; this file makes
   it explicit, named, and independent of those sub-areas' lifetimes. Follow CLAUDE.md test
   conventions: `from __future__ import annotations`, plain `def` for these (no I/O, nothing
   to await), `async def` only if a test genuinely awaits — no `@pytest.mark.asyncio` either way.
   No database, no Docker, no `@pytest.mark.integration` — nothing is instantiated.

10. [ ] `varco_beanie/tests/test_beanie_di.py` — **extend, do not rewrite**. Append a
    `class TestBeanieContainerValidates:` block at the end of the file containing the real
    `DIContainer(); container.scan("varco_beanie", recursive=True); validate_bindings()` test
    plus a `bind_repositories()`-against-a-real-container test (same two shapes as step 9).
    Add `from providify import DIContainer` to the imports. Every existing mock-based test in
    the file stays exactly as it is.

11. [ ] `varco_beanie/tests/test_beanie_di.py` — update the **module docstring** (lines 1-20),
    whose current claim "No actual container resolution is performed" becomes false after
    step 10. Reword to: the mock-based tests below assert the pure-function/`@Provider`
    mechanics, **and** a final section performs real container resolution. Keep the existing
    "Coverage:" list and extend it with the new block. Keep `Thread safety:` / `Async safety:`
    footer lines.

### Phase 3 — verify

12. [ ] Run the two package suites and confirm the counts moved only by the number of tests
    added in steps 9-10, with no pre-existing test newly failing (compare against step 4's
    baseline).

13. [ ] Run `make lint` and `make type-check`; both must be clean for the touched files.

## Edge cases
- `providify` not installed in the dev env → steps 9/10's tests would error on import. It is a
  workspace dev dependency and the seven sibling `test_*_di.py` files import it unguarded, so
  match them: import it at module scope, no `pytest.importorskip`.
- `container.scan("varco_sa", recursive=True)` imports every submodule including
  `varco_sa.migration` (needs the optional `alembic` extra) → if the scan raises `ImportError`
  in a bare env, that is a **pre-existing** condition already hit by `test_migration_di.py` and
  `test_sa_tenancy_di.py`; do not add a guard the existing tests don't have. If it does fail,
  record it as an out-of-scope finding, do not "fix" it inside this plan.
- `container._bindings` is a private attribute → acceptable, `test_redis_di.py:53-56` already
  does exactly this; matching the established template beats inventing a public accessor here.
- A future contributor adds a required (non-defaulted) field to an `SAModule` provider's
  injected type → step 9's `validate_bindings()` test fails loudly, which is the entire point.
  Note this in the new file's docstring so the failure is self-explaining.
- CLAUDE.md line numbers cited in step 5 will shift if any other edit lands first → anchor on
  the **section headings** (`### DI wiring (providify)` / `### Resilience (varco_core.resilience)`),
  not on the line numbers.
- `enable_rls_ddl`'s docstring is long and structured (`Args:`/`Returns:`) → the new line is
  prose in the leading description block, not a new `Args:` entry.

## Verification
```bash
# Phase 0 baseline (before any edit) and Phase 3 comparison (after)
uv run pytest varco_sa/tests/ varco_beanie/tests/ -q

# The two new/extended F9 targets specifically
uv run pytest varco_sa/tests/test_sa_di.py -v
uv run pytest varco_beanie/tests/test_beanie_di.py -v

# Regression net for the docstring edits (F6/F10) — the modules must still import
# and their existing suites stay green
uv run pytest varco_core/tests/ -q -k "observability or reliability or cache"
uv run pytest varco_sa/tests/ -q -k "rls"

# Repo-wide gates
make lint
make type-check
```
Docs-only steps (5-8) have no runtime assertion; their gate is `make lint` (ruff also checks
docstring formatting configured for this repo) plus a human read of the rendered CLAUDE.md
table.

## Risks
- **Risk: the CLAUDE.md taxonomy drifts from the code.** Invariant that must hold — every
  example named in the new table must be a real, importable symbol at the path given. If a
  future plan renames one, the table is part of that plan's blast radius. Mitigation: name the
  module path (`varco_casbin.di.enable_policy_authorizer`), not just the bare function name,
  so a grep finds the doc.
- **Risk: step 10/11 accidentally invalidates a mock-based test** by importing the real
  `DIContainer` into a module that patches `varco_beanie.provider.*`. Invariant: the new class
  must not use `patch(...)` on anything the existing tests patch, and must not run at module
  import time. The existing tests' `with patch(...)` blocks are function-scoped, so appending
  an independent class is safe — but confirm the step-4 baseline count is preserved exactly.
- **Risk: `scan("varco_sa", recursive=True)` has import side effects** (e.g. framework metadata
  self-registration via `register_framework_metadata()`) that could leak into other tests in
  the same session. Invariant: this already happens today in `test_sa_tenancy_di.py`/
  `test_migration_di.py`, so the new file adds no *new* side effect — but if the full-suite run
  in step 12 shows an ordering-dependent failure that the isolated run doesn't, that is the
  cause; do not paper over it with a `sys.modules` hack, record it as a new finding.
- **Risk: someone reads this plan as license to rename.** Invariant: three functions
  (`install_cache_metrics`, `install_reliability_metrics`, `enable_rls_ddl`) keep their exact
  current names and signatures. Any diff touching a `def` line for those three is out of scope.
