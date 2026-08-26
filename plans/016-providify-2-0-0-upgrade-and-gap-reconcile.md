# Plan 016 — providify 2.0.0 upgrade, compat-shim deletion, and gap-register reconcile

BACKLOG.md **Phase 1 — providify 2.0.0**, items RL-1, RL-2, RL-3, RL-4.

## Goal

After this plan, varco resolves `providify>=2.0.0` **from PyPI** (no vendored wheel, no
`[tool.uv.sources]` override) across all ten workspace members; `varco_core.providify_compat`
is deleted and every one of its call sites uses providify's native `@Provider(returns=…)` /
`container.provide(fn, returns=…)`; the four independent 2.0.0 deltas (`container.validate()`,
aggregated `ShutdownError`, priority direction, pytest plugin) each have an explicit,
documented adopt/decline decision backed by a test; and `UPSTREAM-GAPS.md` carries a
source-verified status for every entry, with U-20 closed.

## Non-goals

- **No version bump of the varco packages themselves.** Lockstep 3.0.0 is RL-9, Phase 5.
- **No CI/workflow work.** That is RL-5/RL-6, Phase 2.
- **No API-surface breaking cleanup.** RL-8, Phase 4. In particular: adopting
  `container.ashutdown()` inside `VarcoLifespan` is explicitly deferred to Phase 4 (see
  Design §RL-3b) — this phase only characterizes the new exception shape.
- **No fixing of latent wiring defects `container.validate()` newly surfaces.** Those are
  *findings*: recorded in BACKLOG.md, tolerated by an explicit allowlist in the health test.
  A mechanical upgrade phase must not smuggle in behaviour changes.
- **No edits to `plans/*.md` or `audits/*.md`.** Those are historical records; they may
  reference `provide_factory()` forever. Only live docs (`CLAUDE.md`, `README.md`,
  `UPSTREAM-GAPS.md`, `CHANGELOG.md`) and source/tests are updated.
- **No re-export of providify's pytest fixtures from varco's testkit** (see Design §RL-3d).

---

## Design

### Dependency order (state this up front — it drives everything below)

```
  RL-4 (gap reconcile)  ─────────────────────────────┐  independent, start any time
                                                     │
  RL-1 ── un-vendor @1.1.0 ── bump to 2.0.0 ─┬─ RL-2 (delete compat shim)  [depends on RL-1]
   (GATE: nothing else is testable until      │
    2.0.0 actually resolves)                  ├─ RL-3a validate()      ┐
                                              ├─ RL-3b ShutdownError   │ parallel
                                              ├─ RL-3c priority audit  │ after RL-1
                                              └─ RL-3d pytest plugin   ┘
```

- **RL-1 gates everything.** `@Provider(returns=…)`, `container.validate()`, `ShutdownError`
  and the `pytest11` plugin do not exist in the installed 1.1.0 wheel. No RL-2/RL-3 code can
  even import until `uv sync` resolves 2.0.0.
- **RL-2 depends on RL-1** (needs `returns=`).
- **RL-3's four sub-deltas are mutually independent** and parallel after RL-1. Only RL-3c
  (priority audit) is a pure read + docs sweep and could in principle start earlier; it is
  cheap enough not to bother.
- **RL-4 is fully independent of RL-1..RL-3** — it is a source-verification pass over
  `UPSTREAM-GAPS.md`. Its only coupling is that **closing U-20 is done by RL-2's step**, so
  RL-4 leaves U-20 alone and RL-2 owns it. Start RL-4 first if the upgrade is blocked.

### RL-1 — sequencing decision: **two steps, one branch, two commits** ✅

Do it in two commits:

| # | Commit | `[tool.uv.sources]` | constraints | sweep |
|---|--------|---------------------|-------------|-------|
| A | "un-vendor providify — resolve 1.1.0 from PyPI" | entry **deleted** | unchanged `>=1.1.0` | full |
| B | "upgrade to providify 2.0.0" | (already gone) | `>=2.0.0` everywhere | full |

**Why, not one commit:** the vendored wheel is documented at `pyproject.toml:27` as *"a
pre-built copy of `/home/edoardo/projects/providify` at 1.1.0"* — a **local build of a working
tree**, not a download of PyPI's 1.1.0 artifact. There is no guarantee the two are
byte-identical (a file present in the local tree but excluded from the published sdist/wheel —
`py.typed`, the `pytest11` entry point, a `MANIFEST`-shaped omission — is the classic failure).
Step A isolates exactly that class of failure. PyPI genuinely offers 1.1.0 (verified: the
release list contains both `1.1.0` and `2.0.0`), so the isolation is available and free.

- ✅ A red sweep at A means "PyPI's artifact ≠ our local build"; a red sweep at B means "2.0.0
  changed behaviour". Two very different fixes, and a conflated failure is expensive to
  diagnose across ten packages × ~19 DI/bootstrap test files.
- ✅ Bisectable: `git revert` of B alone returns to a *published, resolvable* 1.1.0.
- ❌ One extra `uv sync` + one extra full sweep (minutes, not hours).

**Rejected — one commit (bump + un-vendor together).** ✅ Fewer commits, faster. ❌ Conflates
two independent failure modes at exactly the moment the project is least able to absorb an
ambiguous red suite; the saving is a few minutes of sweep time against an unbounded debugging
cost. Rejected.

**Rejected — bump to 2.0.0 against a re-built local wheel first, un-vendor second.** ✅ Also
two-step. ❌ Inverts the isolation so the *last* step is the packaging change — meaning the
final state (PyPI resolution) is the least-tested one, and the vendored-wheel maintenance
burden RL-1 exists to delete would be paid one more time. Rejected.

**Correction to the BACKLOG and the scout report — `varco_ws` declares no `providify`
dependency at all.** BACKLOG.md:55 says "all nine `providify>=1.1.0` constraints"; the scout
reported varco_ws as "present, line not captured". Neither is right. Nine packages declare it
(`varco_core:28`, `varco_kafka:30`, `varco_nats:32`, `varco_redis:28`, `varco_beanie:34`,
`varco_sa:25`, `varco_memcached:26`, `varco_fastapi:31`, `varco_casbin:24`);
`varco_ws/pyproject.toml:20-23` declares only `varco-core` — yet `varco_ws/varco_ws/sse.py:79`
and `varco_ws/varco_ws/websocket.py:85` both do `from providify import Inject, Singleton`.
That is an **undeclared direct dependency** relying on a transitive from `varco-core`, and it
is a genuine publishing defect (varco-core could drop providify and silently break varco_ws).
RL-1 fixes it by *adding* `providify>=2.0.0` to `varco_ws`. So: **nine bumps + one addition**.

### RL-2 — delete the shim, adopt `@Provider(returns=…)`

Verified in providify source: `DIContainer.provide(self, fn, *, returns: Any = None)`
(`providify/container.py:989`), precedence *call-site `returns=` > `@Provider(returns=…)` >
resolved return annotation* (`container.py:995-998`), and CHANGELOG §2.0.0 lines 199-214
("removes the only reason a caller ever had to mutate `factory.__annotations__["return"]`").

Two mechanical shapes replace `provide_factory(container, f, returns=X, singleton=S, name=N)`:

```python
# before
provide_factory(container, _factory, returns=WebSocketEventBus, singleton=True)

# after
_factory.__name__ = "…"  # ONLY where name= was passed (see below)
container.provide(Provider(singleton=True)(_factory), returns=WebSocketEventBus)
```

`name=` is **not** subsumed by `returns=` — it exists so per-entity closures built in a loop
are distinguishable in `describe()` output. Keep the one-line `factory.__name__ = …`
assignment inline at the two sites that used it (`varco_sa/di.py`, `varco_beanie/di.py`);
it is not an annotation patch and providify has no equivalent parameter.

**`varco_beanie` stops being a documented exception.** `varco_beanie/varco_beanie/di.py:160-214`
is container-less by design (its tests import `_make_repo_provider()` and assert on the
*unregistered* function). With `@Provider(returns=…)` being a **decoration-time** override,
the container-less builder can now express its intent natively:

```python
return Provider(returns=AsyncRepository[entity_cls])(_repo_factory)  # replaces di.py:207
```

so `di.py:207`'s `_repo_factory.__annotations__["return"] = …` goes away too, and the
"deliberately not a caller" paragraph at `di.py:160-178` is rewritten to a plain description.
`varco_beanie/tests/test_beanie_di.py:324` (and any sibling test asserting on
`__annotations__["return"]`) must be rewritten to assert on the provider **metadata** instead
— see Steps 16-17.

**Alternative considered — keep `providify_compat` as a one-line delegating wrapper around
`container.provide(..., returns=…)`.** ✅ Zero call-site churn. ❌ Leaves a shim in the public
tree for a *closed* upstream gap at the exact moment of the first stable release; the module's
own docstring (`providify_compat.py:38-47`) says it exists "to be **deleted** in one place the
day providify resolves [this] natively" — that day is today. Rejected.

### RL-3a — `container.validate()`: **supplement the 18 `validate_bindings()` sites, do not replace them** ✅

CHANGELOG §2.0.0 lines 99-105 is explicit: *"`validate_bindings()` and `validate_all()` are
unchanged … `validate()` is purely additive on top of them and never instantiates anything"*.
`validate_bindings()` is the scope-leak tier; `validate()` adds MISSING_BINDING,
AMBIGUOUS_BINDING, CIRCULAR_DEPENDENCY, SCOPE_LEAK, LIVE_REQUIRED, UNRESOLVED_ANNOTATION
(`providify/container.py:5292`, `providify/validation.py`). Replacing would *drop* coverage.

⚠️ **The strictness trap.** Most of varco's 18 health tests scan **one package in isolation**
(`container.scan("varco_redis")` with no app bindings). A full-graph walk will legitimately
report `MISSING_BINDING` for interfaces the *application* is expected to supply
(`AsyncRepository[User]`, `AbstractAuthorizer`, app settings). So the adopted shape is:

```python
report = container.validate(raise_on_error=False)
structural = [i for i in report.errors if i.kind is not IssueKind.MISSING_BINDING]
assert not structural, "\n".join(i.message for i in structural)
```

i.e. **fail hard on structural defects** (cycles, ambiguity, unresolvable annotations, scope
leaks, `Live[T]` violations) and tolerate `MISSING_BINDING` only, since "an app must supply
this" is that kind's legitimate meaning for a package scanned alone. Rejected alternative:
a per-test hand-maintained allowlist of *specific* missing interfaces — ✅ tighter, ❌ 18
allowlists to maintain and a guaranteed source of churn on every new binding. Rejected.

### RL-3b — aggregated `ShutdownError`: **characterize, do not adopt** ✅

Verified: `varco_fastapi/varco_fastapi/lifespan.py:205-212` — `VarcoLifespan` calls
`component.stop()` per registered lifecycle component and `_stop_all()` "logs errors but does
not raise". There is **no `container.shutdown()` / `ashutdown()` call anywhere in varco**.
So the new aggregated `ShutdownError` (CHANGELOG lines 269-300) has no call site and **nothing
is broken by 2.0.0 here**. RL-3b is therefore an *adoption* decision, not a forced migration.

**Decline adoption in this phase.** Adding `await container.ashutdown()` to the lifespan would
newly fire every `@PreDestroy` hook in the container — and varco declares many
(`varco_kafka/bus.py:329`, `varco_nats/bus.py:288`, `varco_redis/bus.py:225`,
`varco_redis/cache.py:214`, `varco_redis/streams.py:326`, `varco_casbin/engine.py:211`,
`varco_kafka/channel.py:235`, `varco_nats/channel.py:276`, `varco_redis/channel.py:132`,
`varco_memcached/cache.py`, …). Hooks that have never run in a varco FastAPI app's lifetime
would start running, some of them double-stopping a component `_stop_all()` already stopped.
That is a behaviour change with real blast radius, in a phase whose entire premise is
"mechanical". Deliver instead: (1) a characterization test locking the `ShutdownError` /
`ShutdownFailure` shape so the future adoption is de-risked, and (2) a BACKLOG entry routing
the decision to **Phase 4 (RL-8)**, the deliberate breaking-change window.

⚠️ Name the suspected leak in the BACKLOG entry: a `@PreDestroy`-bearing singleton that is
**not** also a registered `VarcoLifespan` component is never torn down today.

### RL-3c — priority direction: full sweep, expect zero changes

CHANGELOG line 233-235: *"**Higher priority value wins**… The code (`max()` in
`_get_best_candidate`) was always correct; only the docs have been updated."* So no runtime
behaviour changed; the risk is varco code or prose **written against the old wrong wording**.
Every site found so far uses a very negative value for an overridable framework default
(`varco_sa/di.py:110`, `varco_sa/provider.py:25`, `varco_fastapi/di.py:75-104` ×3,
`varco_redis/config.py:142`, `varco_nats/config.py:295`) — consistent with "higher wins".
⚠️ The scout flagged that sweep as **not exhaustive** (GAP 1), so Step 24 is a full repo sweep
of `priority=` **and** of the prose ("lower … wins" / "lowest priority") in `CLAUDE.md`,
`README.md`, `ARCHITECTURE.md`, `technical_docs/`, and every source docstring.

### RL-3d — pytest plugin: **leave it to consumers; document, do not wrap** ✅

Verified in `providify/pytest_plugin.py:1-24`: it is a `pytest11` entry point active in every
project with providify installed; all four fixtures are **function-scoped, yield-based, and
non-autouse**, with the module's stated invariant that *"a project that never asks for
`di_container`/`di_overrides`/`di_global`/`di_acontainer` must see zero behavioural difference
from providify not being installed at all"*. Fixture-name collisions in varco: **zero**.

- ❌ **Re-export from varco's testkit** — rejected. `testkit/varco_conformance` is deliberately
  never packaged (reached via a `pythonpath = ["../testkit"]` line), so it cannot deliver
  fixtures to a downstream consumer anyway; and a second name for an identical fixture is pure
  confusion.
- ❌ **Define varco fixtures on top** (e.g. a `varco_container` pre-scanned with varco
  packages) — rejected for this phase: it would bake a "which packages should be scanned"
  opinion into the test surface right before an API freeze, with no demand behind it.
- ✅ **Document the four fixtures** — including **`di_acontainer`, which BACKLOG.md:57 omits**
  — in README's testing section and CLAUDE.md's Test Conventions, plus one inertness test.

⚠️ **GAP 3 (fixture scope/autouse interaction with varco's existing `conftest.py` files) is
resolved primarily by the full sweep in Step 8**: the plugin does not exist at 1.1.0, so a
green ten-package sweep *after* 2.0.0 resolves is itself the interaction test. Step 27 adds an
explicit inertness assertion on top.

### RL-4 — gap-register reconcile

`UPSTREAM-GAPS.md`'s own **U-8 lesson** ("Maintainer response — source corrections",
`UPSTREAM-GAPS.md:1020`) requires verifying claims **in source**, not from documentation.
Three entries currently read "✅ verified implemented (per CLAUDE.md)" — U-11, U-13, U-17 —
which is precisely the failure U-8 warns about. Eight entries need a real source pass:

| Entry | Why re-verify | Verify in |
|---|---|---|
| U-1 | P0, still ⚠️ unverified | `varco_core/encryption*`, `encryption_store` |
| U-3 | claimed closed by design decision D-9g | `varco_fastapi/router/a2a.py`, `router/skill.py` |
| U-11 | "per CLAUDE.md" only | `varco_core/job/base.py`, `varco_sa` job store |
| U-12 | is it closed by 2.0.0's `validate()`? | **providify 2.0.0 source**: `validation.py`, `container.py:5292` |
| U-13 | "per CLAUDE.md" only | `varco_core/jwt/`, `varco_fastapi` `JwtBearerAuth` |
| U-14 | report-only, never re-checked | `varco_core/auth/`, `varco_fastapi` deps |
| U-15 | report-only, never re-checked | `varco_fastapi/router/` |
| U-17 | "per CLAUDE.md" only | `varco_core/job/base.py` (`run_at`, `run_at_wall`) |

U-20 is closed by `@Provider(returns=…)` and its closure is **owned by RL-2** (Step 20), not
by RL-4, so the two passes do not collide.

---

## Steps

Checkbox-ordered. TDD ordering is applied where a behaviour is being changed; RL-1's steps are
dependency-resolution changes whose "test" is the existing full sweep, which is why the sweep
step is written explicitly rather than a new test being invented.

### Phase A — RL-1 step A: un-vendor against 1.1.0 (GATE)

1. [ ] `pyproject.toml:24-39` — delete the entire `[tool.uv.sources]` block (the header line
       plus the DESIGN comment at `:25-38` and the `providify = { path = … }` line at `:39`).
       **Do not delete `vendor/providify-1.1.0-py3-none-any.whl` from the tree yet** — it is
       the rollback escape hatch (see Rollback).
2. [ ] `varco_ws/pyproject.toml:20-23` — add `"providify>=1.1.0",` to `dependencies` with a
       comment naming the two direct importers (`varco_ws/sse.py:79`,
       `varco_ws/websocket.py:85`). This is the undeclared-dependency fix from Design §RL-1.
3. [ ] Run `uv sync` from the workspace root. Confirm `providify` now resolves from PyPI at
       `1.1.0`: `uv run python -c "import providify, providify.__file__ as _; print(providify.__version__ if hasattr(providify,'__version__') else 'n/a')"`
       and inspect `uv.lock` for the removed path source. Commit `uv.lock`.
4. [ ] **Full sweep** (definition in Verification). Must be green *before* Step 5. A red sweep
       here means "PyPI's 1.1.0 ≠ the local build" — fix that first, in isolation.
5. [ ] Commit A: `un-vendor providify — resolve 1.1.0 from PyPI`.

### Phase B — RL-1 step B: bump to 2.0.0

6. [ ] Bump `providify>=1.1.0` → `providify>=2.0.0` in all ten members:
       `varco_core/pyproject.toml:28`, `varco_kafka/pyproject.toml:30`,
       `varco_nats/pyproject.toml:32`, `varco_redis/pyproject.toml:28`,
       `varco_beanie/pyproject.toml:34`, `varco_sa/pyproject.toml:25`,
       `varco_memcached/pyproject.toml:26`, `varco_fastapi/pyproject.toml:31`,
       `varco_casbin/pyproject.toml:24`, and the line added in Step 2
       (`varco_ws/pyproject.toml`).
7. [ ] `uv sync`; confirm `2.0.0` resolves; commit the updated `uv.lock`.
8. [ ] **Full sweep.** This sweep is also the GAP-3 verification for RL-3d — the `pytest11`
       plugin is newly present and a green ten-package run proves it is inert against every
       existing `conftest.py`.
9. [ ] `CHANGELOG.md` — add an `## [Unreleased]` entry: providify constraint bumped to
       `>=2.0.0`, vendored wheel source removed, `varco_ws` gains its missing explicit
       `providify` dependency.
10. [ ] Commit B: `upgrade to providify 2.0.0`.

### Phase C — RL-2: delete `varco_core.providify_compat` (depends on Phase B)

11. [ ] `varco_ws/varco_ws/di.py` — migrate both sites. Remove the deferred imports at `:235`
        and `:361`; rewrite `:276` → `container.provide(Provider(singleton=True)(_ws_factory), returns=WebSocketEventBus)`
        and `:388` → the `SSEEventBus` equivalent. Update the explanatory prose at `:216`,
        `:263`, `:345`, `:378` that names `provide_factory()`. Keep the module-level presence
        probe semantics: the deferred `import providify` must still be what raises when
        providify is absent.
12. [ ] `varco_fastapi/varco_fastapi/di.py` — remove the import at `:152`; rewrite `:196`
        (`bind_clients()`'s per-router client provider, generic-alias target,
        `singleton=True`). Update the prose at `:40`, `:119`, `:175`.
13. [ ] `varco_fastapi/varco_fastapi/router/skill.py` — remove the import at `:1088`; rewrite
        the call at `:1161`. Update prose at `:1078`, `:1105`.
14. [ ] `varco_fastapi/varco_fastapi/router/mcp.py` — remove the import at `:827`; rewrite
        `:858` (`returns=MCPAdapter, singleton=True`). Update prose at `:816`, `:840`.
15. [ ] `varco_sa/varco_sa/di.py` — remove the import at `:63`; rewrite `:320-326` to
        `_repo_factory.__name__ = f"_repo_factory_{entity_cls.__name__}"` +
        `container.provide(Provider(singleton=False)(_repo_factory), returns=AsyncRepository[entity_cls])`.
        Rewrite the docstring at `:295-297` (it currently points at `provide_factory()`).
16. [ ] `varco_beanie/tests/test_beanie_di.py` — **failing test first.** Rewrite the tests that
        assert on `_make_repo_provider(...).__annotations__["return"]` (see `:324` and the
        `test_make_repo_provider_*` family) to assert on the `@Provider` **metadata** carrying
        the interface instead. Edge cases to cover: two entity classes produce two distinct
        interfaces; `__name__` still carries the entity name; the returned object is still an
        `async def`.
17. [ ] `varco_beanie/varco_beanie/di.py` — delete the annotation patch at `:207`; return
        `Provider(returns=AsyncRepository[entity_cls])(_repo_factory)` at `:214`. Keep
        `:210`'s `__name__` assignment. Rewrite the docstring at `:160-178` — it is no longer
        a "deliberate exception to `provide_factory()`", it is just a container-less builder.
18. [ ] `varco_core/tests/test_providify_compat.py` — **delete the file.** Its per-behaviour
        coverage moves to providify's own suite; its one varco-specific assertion
        (`test_module_registers_no_bindings_when_scanned`, `:175-181`) is moot once the module
        is gone. The remaining `validate_bindings()` call at `:181` is covered by Step 22.
19. [ ] `varco_core/varco_core/providify_compat.py` — **delete the module.** Confirm it is not
        in `varco_core/__init__.py` (it is not) and that `rg providify_compat` returns hits
        only in `plans/` and `audits/` (history — do not edit).
20. [ ] `UPSTREAM-GAPS.md` — close **U-20**: mark `✅ CLOSED — fixed upstream in providify
        2.0.0`, cite `providify/container.py:989-1019` and CHANGELOG §2.0.0 lines 199-214,
        note the interim shim was deleted in this plan. Update the summary row at `:61` and
        the mention at `:16`; update the body at `:950-1014`.
21. [ ] `CLAUDE.md` — delete the "deletable compat shim" paragraph in the *DI wiring verb
        taxonomy* section (the block at `:119` beginning "Several `bind_*` factories above
        register a binding whose interface is only known at call time…" through the "invisible
        to `container.scan("varco_core")`" sentence) and replace it with a two-line statement
        that such sites use providify's native `container.provide(fn, returns=…)` /
        `@Provider(returns=…)`. Also update `:488` (the providify-limitation workflow section,
        which cites `providify_compat` as the worked example of a sanctioned shim) — keep the
        rule, replace the example with "a shim filed under an open UPSTREAM-GAPS entry".
        `README.md`: sweep for `provide_factory` and update any occurrence. **Same commit as
        the code** — never a follow-up.
22. [ ] `uv run pytest varco_core/tests/ varco_sa/tests/ varco_ws/tests/ varco_beanie/tests/ varco_fastapi/tests/`
        then the **full sweep**. Commit: `delete varco_core.providify_compat; adopt @Provider(returns=)`.

### Phase D — RL-3, four independent sub-deltas (parallel after Phase B)

**D-a — `container.validate()` adoption (supplement)**

23. [ ] Add `container.validate(raise_on_error=False)` + the structural-errors assertion (see
        Design §RL-3a) immediately after the existing `validate_bindings()` call at each of the
        17 surviving sites (the 18th, `test_providify_compat.py:181`, was deleted in Step 18):
        `varco_core/tests/test_tenancy_di.py:16`; `varco_kafka/tests/test_kafka_di.py:109`;
        `varco_nats/tests/test_nats_di.py:108`; `varco_redis/tests/test_redis_di.py:50,68,87`;
        `varco_sa/tests/test_sa_di.py:85,119,139`, `test_sa_tenancy_di.py:14`,
        `test_migration_di.py:23`; `varco_beanie/tests/test_beanie_di.py:300,319`,
        `test_beanie_tenancy_di.py:14`, `test_beanie_dlq.py:89`;
        `varco_fastapi/tests/test_tenancy_di.py:14`, `test_client_front_door.py:73`.
        Factor the three-line assertion into one helper in `testkit/` (reachable via the
        existing `pythonpath = ["../testkit"]`) rather than copy-pasting it 17 times.
24. [ ] Record every `MISSING_BINDING` the new assertion tolerates, and every structural error
        it uncovers, as rows in BACKLOG.md under a new "Findings from Plan 016" table. Do
        **not** fix production code here (Non-goal). If a structural error makes a suite red,
        the fix is either an obviously-wrong test-side scan or a BACKLOG row + a narrowly
        scoped `pytest.mark.xfail(strict=True)` on that one assertion — matching the project's
        existing conformance-failure rule.
25. [ ] `CLAUDE.md` — in the pitfall row "A package's suite is green but its container won't
        bootstrap", update the Fix column from `validate_bindings()` alone to
        `validate_bindings()` **plus** `validate()`, naming what each tier catches. `README.md`
        — same update wherever `validate_bindings()` is shown.

**D-b — aggregated `ShutdownError` (characterize, decline adoption)**

26. [ ] `varco_fastapi/tests/test_lifespan_shutdown_characterization.py` (**new**) — a
        characterization test: build a `DIContainer`, register two singletons whose
        `@PreDestroy` hooks both raise, `await container.ashutdown()`, assert a single
        `ShutdownError` is raised, that `exc.failures` has **two** `ShutdownFailure` entries
        (each with `.owner` / `.exception`), that `exc.__cause__` chains to the
        *earliest-created* failing component, and that the singleton cache is cleared anyway.
        Add a module docstring stating this locks the shape for a **future** adoption and that
        `VarcoLifespan` deliberately does **not** call `ashutdown()` today.
27. [ ] `varco_fastapi/varco_fastapi/lifespan.py:175-181` — extend `__call__`'s `Edge cases:`
        docstring block with an explicit "`VarcoLifespan` does not call
        `container.shutdown()`/`ashutdown()`; `@PreDestroy` hooks on singletons that are not
        registered lifecycle components are not run" note, so the gap is documented rather
        than accidental. **No behaviour change.**
28. [ ] `BACKLOG.md` — add an RL-8/Phase 4 row: "decide whether `VarcoLifespan` should call
        `container.ashutdown()`", citing `lifespan.py:205-212`, the `@PreDestroy` inventory,
        and the double-stop risk.

**D-c — priority-direction audit**

29. [ ] Full repo sweep (`rg -n 'priority\s*=' varco_*/ testkit/ examples/ --glob '!.claude/**'`
        plus `rg -ni 'lower.{0,20}priority|lowest priority|priority.{0,20}wins'` across
        `CLAUDE.md`, `README.md`, `ARCHITECTURE.md`, `technical_docs/`, and all `*.py`
        docstrings). Every `priority=` site must be classified: framework-default-that-must-lose
        (very negative ✅) vs. override-that-must-win (positive). Correct any prose stating the
        old, wrong "lower value wins".
30. [ ] `varco_fastapi/tests/test_di_priority_direction.py` (**new**) — **failing test first**:
        parametrized over the framework-default families, assert that a plain app binding
        registered *after* `bootstrap()`/`scan()` wins over each `priority=-sys.maxsize - 1`
        default (`varco_fastapi/di.py:75-104` ×3), and that `get_all()` returns ascending by
        priority with the winner last (CHANGELOG line 235). This is the regression guard for
        "higher value wins".

**D-d — providify pytest plugin**

31. [ ] `varco_core/tests/test_providify_pytest_plugin.py` (**new**) — assert the plugin is
        active with **no conftest change**: a test taking `di_container` gets a fresh
        `DIContainer`; a second test gets a *different* object (function scope); `di_overrides`
        is a `ContainerOverrides` bound to that container and its overrides are undone at
        teardown; `di_global` makes `DIContainer.current()` return it; and `di_acontainer`
        works under the repo's `asyncio_mode = "auto"`. Add an explicit inertness assertion:
        a test requesting **none** of them observes no container-related global state.
32. [ ] `CLAUDE.md` §*Test Conventions* + `README.md` testing section — document the **four**
        fixtures (explicitly including `di_acontainer`, which BACKLOG.md:57 omits), state that
        varco deliberately does not re-export or wrap them, and note that a consumer conftest
        redefining `di_container` wins over the plugin default
        (`providify/pytest_plugin.py:33-37`). **Same commit as Step 31.**

### Phase E — RL-4: reconcile `UPSTREAM-GAPS.md` (independent; may start at any time)

33. [ ] `UPSTREAM-GAPS.md` — add a `**Last reconciled:** 2026-08-25 against providify 2.0.0`
        line under `## Summary` (`:32`), and adopt a per-entry convention that every Status
        line must name the **file:line it was verified in**, never a CLAUDE.md/README claim.
34. [ ] Re-verify **U-11** (`:323-370`) in source — read `varco_core/varco_core/job/base.py`
        for `try_claim`/`renew`/`reap_expired_leases`/`save(expected_epoch=)`/`StaleLeaseError`
        and the SA/Beanie job stores. Replace "per CLAUDE.md" with the real file:line evidence
        or reopen the entry.
35. [ ] Re-verify **U-13** (`:434-474`) in source — `varco_core/jwt/`, `varco_fastapi`'s
        `JwtBearerAuth` construction guard, `VARCO_JWT_AUDIENCE` /
        `VARCO_JWT_ALLOW_ANY_AUDIENCE` / `VARCO_JWT_ENFORCE_ISS` handling. Same treatment.
36. [ ] Re-verify **U-17** (`:542-596`) in source — `varco_core/varco_core/job/base.py`'s
        `run_at`, `run_at_wall`, `run_at_tz`, `run_at_fold`. Same treatment.
37. [ ] Re-verify **U-1** (`:67-116`, P0, still ⚠️ unverified) in source —
        `varco_core/encryption*` / `encryption_store`, specifically whether
        `ScopedEncryptorRegistry` / `manager.destroy_scope(scope)` satisfies the
        per-data-subject key ask. Set a real status.
38. [ ] Re-verify **U-3** (`:162-218`) in source — `varco_fastapi/router/a2a.py` +
        `router/skill.py` for `SkillSource` / `source=` / `ctx=`; confirm "closed by D-9g".
39. [ ] Re-verify **U-12** (`:909-949`) against **providify 2.0.0 source** — read
        `providify/validation.py` and `providify/container.py:5292+`. Determine explicitly
        whether `IssueKind` covers interface *conformance* (does the implementation actually
        satisfy the declared Protocol/ABC) or only wiring resolvability. Record the verdict;
        if still open, restate the ask in 2.0.0 terms.
40. [ ] Re-verify **U-14** (`:756-780`) and **U-15** (`:781-797`) in source; both are
        report-only P3 — confirm the described absences still hold, or close them.
41. [ ] `UPSTREAM-GAPS.md` summary table (`:32-64`) — regenerate Status/Priority for every one
        of the 20 rows so the table and the bodies cannot disagree. U-20's row comes from
        Step 20; do not touch it here.
42. [ ] `CLAUDE.md` §*When you hit a `providify` limitation or bug* — if any P0/P1 entry
        changed status, verify the prose that cites the register is still accurate. Commit:
        `reconcile UPSTREAM-GAPS.md against source`.

### Phase F — close out

43. [ ] Delete `vendor/providify-1.1.0-py3-none-any.whl` from the tree **only now**, after
        Phases A–E are all green. Check `.dockerignore`/`Dockerfile`/`Makefile`/`scripts/` for
        any reference to `vendor/` and remove it.
44. [ ] `BACKLOG.md` — mark RL-1..RL-4 done; record the two BACKLOG open questions this plan
        answered (RL-1 sequencing → two-step; RL-3d plugin adoption → leave to consumers,
        document only) so the answers are not relitigated. Remove those two bullets from
        "Open questions for `/plan`".
45. [ ] Final **full sweep** + `make lint` + `make type-check` + `make build`.

---

## Edge cases

| Input / state | Expected behaviour |
|---|---|
| PyPI 1.1.0 ≠ the vendored local build (Step 4 red) | Stop. Diagnose the artifact difference before touching version constraints. Rollback = restore `[tool.uv.sources]`, `uv sync`. |
| `providify` absent entirely (an app that never installed it) | Unchanged. Every `bootstrap()` still returns `None`; `varco_ws`/`varco_fastapi`'s deferred `import providify` presence probes still raise at the same points (Steps 11-14 must not move the probe). |
| `container.provide(fn, returns=X)` called twice with the same `fn`, different `X` | Two independent bindings, neither shadowing the other — the closure-capture hazard `provide_factory()`'s docstring called out is gone entirely, since `returns=` is evaluated per registration, not stored on `fn`. |
| `@Provider(returns=…)` on the container-less `varco_beanie` builder | The override is decoration-time (CHANGELOG line 202), so it survives being returned unregistered and handed to `container.provide()` later. This is what lets Step 17 drop the annotation patch. |
| `container.validate()` reports `MISSING_BINDING` for an app-supplied interface | Tolerated by design (Design §RL-3a) — a package scanned alone legitimately lacks app bindings. Recorded in BACKLOG, not fixed. |
| `container.validate()` reports `CIRCULAR_DEPENDENCY` / `AMBIGUOUS_BINDING` / `UNRESOLVED_ANNOTATION` | Hard failure. A real bootstrap defect — BACKLOG row + narrowly scoped `xfail(strict=True)`, never a silent tolerance widening. |
| An existing varco conftest defines `di_container` in future | The consumer definition wins (`providify/pytest_plugin.py:33-37`). Step 31's test documents this; no action needed today (zero collisions exist). |
| A `@PreDestroy` hook raises during a *future* `ashutdown()` adoption | All hooks still run, caches still clear, one aggregated `ShutdownError` at the end — locked by Step 26's characterization test. |
| `rg providify_compat` after Step 19 | Hits only in `plans/*.md` and `audits/*.md` (historical). Any hit in `varco_*/`, `CLAUDE.md`, `README.md`, `UPSTREAM-GAPS.md`, `CHANGELOG.md` is a missed step. |

---

## Verification

**"Full sweep" = all ten packages plus the examples member, unit tests only** (integration
tests need Docker and are a separate, explicitly-invoked run):

```bash
cd /home/edoardo/projects/varco
uv sync
uv run pytest varco_core/tests/
uv run pytest varco_kafka/tests/
uv run pytest varco_nats/tests/
uv run pytest varco_redis/tests/
uv run pytest varco_sa/tests/
uv run pytest varco_beanie/tests/
uv run pytest varco_memcached/tests/
uv run pytest varco_ws/tests/
uv run pytest varco_fastapi/tests/
uv run pytest varco_casbin/tests/
uv run pytest examples/00-full-stack-post-api/example/tests/
# equivalently, in one call:
make test
```

Per phase:

| Phase | Command | Pass condition |
|---|---|---|
| A (Step 4) | full sweep | green with PyPI 1.1.0, `uv.lock` free of the path source |
| B (Step 8) | full sweep | green with 2.0.0; also the RL-3d GAP-3 interaction check |
| C (Step 22) | `uv run pytest varco_core/tests/ varco_sa/tests/ varco_ws/tests/ varco_beanie/tests/ varco_fastapi/tests/` then full sweep | green; `rg providify_compat varco_* CLAUDE.md README.md UPSTREAM-GAPS.md CHANGELOG.md` → no hits |
| D-a (Step 23) | `make test` | green; every tolerated `MISSING_BINDING` recorded in BACKLOG |
| D-b (Step 26) | `uv run pytest varco_fastapi/tests/test_lifespan_shutdown_characterization.py -v` | green; `lifespan.py` behaviour unchanged |
| D-c (Step 30) | `uv run pytest varco_fastapi/tests/test_di_priority_direction.py -v` | green; sweep in Step 29 produces zero code changes |
| D-d (Step 31) | `uv run pytest varco_core/tests/test_providify_pytest_plugin.py -v` | green with **no** conftest edit |
| E | `rg -n 'per CLAUDE.md' UPSTREAM-GAPS.md` | no hits — every Status cites a file:line |
| F (Step 45) | `make test && make lint && make type-check && make build` | all green; ten wheels build |

Integration suites (Docker required, run once at the end of Phase B and once at Step 45):

```bash
make integration-test          # or: uv run pytest <pkg>/tests/ -m integration
```

---

## Rollback

**RL-1 is the only step with a non-source rollback, and the vendored wheel is the escape
hatch.** `vendor/providify-1.1.0-py3-none-any.whl` stays in the tree through Phases A–E and is
deleted only at **Step 43**, after every phase is green. To roll back at any point before
Step 43:

```toml
# restore in pyproject.toml
[tool.uv.sources]
providify = { path = "vendor/providify-1.1.0-py3-none-any.whl" }
```

then `uv sync` and commit the reverted `uv.lock`. Phases C/D/E are ordinary source changes and
roll back with `git revert` of their commits — but note **Phase C cannot be kept if RL-1 is
rolled back**, because `returns=` does not exist in 1.1.0.

---

## Risks

- ⚠️ **ASSUMPTION — "providify 2.0.0 contains no breaking API changes".** This is the
  CHANGELOG's own self-report (`/home/edoardo/projects/providify/CHANGELOG.md:20`). It is
  **not independently verified**; the only proof is a green ten-package sweep at Step 8. Note
  that §2.0.0 *consolidates* the unannounced 1.0.x–1.1.1 releases (`CHANGELOG.md:21-23`), and
  those entries include two behaviour changes that genuinely can break a downstream
  (`CHANGELOG.md:120-136` — an unresolvable annotation on an *injection point* now raises
  `AnnotationResolutionError`; `:218-231` — scope-leak validation raises instead of reporting
  clean). varco is *already* on 1.1.0 so it should have absorbed those, but the invariant that
  must hold is: **no test may be loosened to make the sweep pass.**
- ⚠️ **ASSUMPTION — the PyPI 1.1.0 artifact is behaviourally identical to the vendored local
  build.** The whole point of the two-step sequencing is that this is *unverified*. If Step 4
  is red, the plan's Phase A/B split has already paid for itself.
- ⚠️ **ASSUMPTION — the scout's `priority=` sweep is non-exhaustive** (its own GAP 1). Step 29
  is the exhaustive pass. All sites found so far look correct, so the expected outcome is zero
  code changes and possibly some prose corrections — but a positive-priority site written
  against the old wrong docs would be a live resolution bug.
- ⚠️ **ASSUMPTION — the pytest plugin is inert against varco's existing conftests.** Grounded
  in `providify/pytest_plugin.py:5-24` (no autouse, no hooks, no `pytest_configure`, all four
  fixtures function-scoped) and zero name collisions — but *behavioural* interaction is only
  proven by the Step 8 sweep. Invariant: no existing test's outcome may change at Step 8.
- ⚠️ **ASSUMPTION — eight `UPSTREAM-GAPS.md` entries (U-1, U-3, U-11, U-12, U-13, U-14, U-15,
  U-17) have unverified statuses.** Three of them (U-11/U-13/U-17) currently cite CLAUDE.md
  prose, which the register's own U-8 lesson (`UPSTREAM-GAPS.md:1020`) names as the exact
  failure mode. Until Steps 34-40 run, **treat all eight statuses as unknown**, not as stated.
- ⚠️ **ASSUMPTION — `@PreDestroy` hooks on non-lifecycle singletons never run today.** Derived
  from `lifespan.py:205-212` (only registered components get `stop()`) plus the `@PreDestroy`
  inventory, but not exhaustively cross-checked class-by-class against what
  `create_varco_app()` registers as a lifecycle component. Step 28's BACKLOG entry must state
  this as suspected, not proven.
- **`container.validate()` may be too strict for package-in-isolation tests.** The tolerance
  design (structural-only) is a judgement call; if it turns out `MISSING_BINDING_DEFAULTED` /
  `MISSING_BINDING_DEFERRED` carry `ERROR` severity rather than `WARNING`, Step 23's filter
  needs those kinds added. ⚠️ Their severity mapping is **unverified** — characterize it in
  Step 23 before writing the assertion.
- **`varco_ws` gaining an explicit `providify` dependency is a metadata change to a published
  package.** It is strictly additive (the dependency was already installed transitively), but
  it must land in `CHANGELOG.md` (Step 9) so a consumer diffing metadata sees why.
- **Scope creep into Phase 4.** Steps 24 and 28 are deliberately BACKLOG-only. The invariant:
  this phase changes *dependency resolution, one deleted shim, and tests/docs* — no production
  behaviour change beyond the shim's mechanical replacement.
