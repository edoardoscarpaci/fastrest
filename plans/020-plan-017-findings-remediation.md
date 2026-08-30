# Plan 020 — Plan 017 findings: package-list SSOT, formatter gate, Beanie/MCP fixes, mypy ramp

BACKLOG.md §*"Plan 017 findings — new rows filed during CI-green implementation"*
(`BACKLOG.md:74-133`), nine rows: **KI-9**, **KI-10**, **KI-11** (🔴 must), **RL-14**, **RL-16**,
**RL-18** (🟡 should), **RL-15**, **RL-17**, **RL-19** (🟢 nice).

Grounded in research briefs
[001 — mypy strictness ramp](../design/plan-017-findings/research/001-mypy-strictness-ramp.md),
[002 — StrEnum vs `(str, Enum)`](../design/plan-017-findings/research/002-strenum-vs-str-enum-migration.md),
[003 — FastMCP `add_tool()` schema](../design/plan-017-findings/research/003-fastmcp-add-tool-schema.md).

---

## Goal

After this plan:

1. **Three real defects are fixed, each with tests** — `BeanieAuditRepository.list_for_entity`
   honours `tenant_id` (KI-9); `BeanieFastrestApp`'s non-DI construction path actually constructs
   (KI-10); `MCPAdapter.to_mcp_server()` advertises varco's own JSON Schemas to MCP clients and
   its `xfail(strict=True)` marker is gone (KI-11).
2. **The package list stops being copy-pasted** — one derivation from `[tool.uv.workspace]
   members`, consumed by four call sites, guarded by a test that fails on drift (RL-18). This
   also fixes a *live* drift the scout did not report: `scripts/gen_ref_pages.py` is missing
   `varco_casbin`, so `make docs` has never rendered that package's API reference.
3. **`ruff format` is a CI gate** at genuinely zero code churn (RL-17), and the nine deferred
   `# noqa: UP042` sites become eight `StrEnum` classes plus one example, spent deliberately
   inside the 3.0.0 breaking-change window (RL-15).
4. **The mypy ramp is sequenced and started**, five flags landed under a measured stop rule, with
   the annotation-enforcement tier explicitly re-filed rather than half-done (RL-14).
5. **Two rows close with zero code** (RL-19 already done; RL-16's disposition already decided) —
   recorded as such rather than turned into invented work.

**Every `xfail(strict=True)` marker filed by Plan 017 is removed in the same step as the fix that
makes it pass.** There is exactly one:

| Marker | File | Removed by |
|---|---|---|
| KI-11 | `varco_fastapi/tests/milestone_f/test_mcp_adapter.py:492-501` | Step 33 |

---

## Evidence corrections (U-8 discipline — write these back to BACKLOG.md)

Three claims in the BACKLOG rows did not survive contact with the tree. They are corrected here
*before* the design, because two of them change what the design has to do.

### EC-1 — RL-17's premise is wrong: `ruff format` is not "unmeasured whole-tree churn"

RL-17's Rationale cell says *"A future adoption is unmeasured whole-tree churn across 439+ source
files."* Measured this session with the pinned `ruff==0.16.4`:

```
uv run ruff format --check .   →  1107 files already formatted, 0 .py files would change
```

**Zero** `.py` files across all ten packages plus `examples` would be reformatted. The only two
reported diffs in the whole tree are fenced Python blocks inside `varco_nats/README.md` and
`plans/019-plan-018-findings-remediation.md` — neither is source, and neither is touched by a
`.py`-scoped formatter gate. RL-17 is therefore an **S with no churn**, not an unmeasured L.

### EC-2 — RL-14's progress metric and its number are two different quantities

RL-14's Rationale cell says *"`rg -c 'type: ignore' varco_*/varco_*` (currently 219) is the
progress metric."* That command does not yield 219. Measured this session:

| Package | `type: ignore` occurrences |
|---|---:|
| varco_core | 99 |
| varco_fastapi | 83 |
| varco_redis | 48 |
| varco_beanie | 36 |
| varco_sa | 34 |
| varco_nats | 10 |
| varco_memcached | 8 |
| varco_kafka | 7 |
| varco_casbin | 2 |
| varco_ws | 0 |
| **total** | **327** across 114 source files |

**219 was RL-6's *mypy error* baseline** (BACKLOG.md:72 states it as such), a different quantity
measured under a different command. §RL-14-metric below picks one metric per purpose and names
both explicitly so this cannot recur.

### EC-3 — RL-15's blast radius is smaller than brief 002's worst case, and the class KI-10 names does not exist

- **Blast radius.** Brief 002 §"Serialization Boundaries" flags three boundaries where migration
  changes the wire format, all of which depend on `f"{member}"` / `str(member)` / `%s` reaching
  the wire. Every interpolation of the eight library enums in this repo already goes through
  `.value` or `!r` — verified at `varco_ws/websocket.py:340`, `varco_kafka/bus.py:648`,
  `varco_nats/bus.py:846`, `varco_core/event/memory.py:631-632`, `varco_core/health.py:142,316`,
  `varco_redis/job_store.py:243`. **Zero bare `{member}` interpolations exist.** The residual
  risk is confined to direct `json.dumps(member)` and pydantic serialization — brief 002 §5 and
  §"Pydantic v2 Behaviour" both report *no observable difference* at those two boundaries, and
  Step 20 verifies rather than assumes it.
- **Class name.** KI-10 and the scout report both call the class `BeanieApp`. **No such name
  exists.** The class is `BeanieFastrestApp` (`varco_beanie/varco_beanie/bootstrap.py:115`,
  exported at `varco_beanie/varco_beanie/__init__.py:46,82`). `grep` for `BeanieFastrestApp`
  across every `*.py` in the repo returns **only source and no test file** — confirming KI-10's
  "not exercised by any test" claim in the strongest form: the class has zero coverage.

---

## Non-goals

- **No `disallow_untyped_defs` / `disallow_incomplete_defs`.** Brief 001 §"Recommended Ramp
  Order" tier 4 and §"Librarian's Note" item 4 both place these last and call them "high effort;
  per-package ramp recommended". They are re-filed as RL-14b (§reconciliation), not attempted.
- **No `disallow_untyped_calls` / `disallow_any_unimported` / `disallow_any_expr`, ever on the
  current dependency set.** Brief 001 §"Recommended Ramp Order" — "Skip these entirely". Recorded
  as a decision, not a deferral.
- **No PR-gating for integration tests** (RL-16). The disposition is already decided in
  BACKLOG.md:126 and Plan 018 §RT7-ci; §RL-16 restates it with a trigger condition and stops.
  The `chaos` job must never be promoted, on any schedule.
- **No `mcp` v2 migration.** Brief 003 §"Options compared" row 3: decorators removed, handlers
  rewritten, protocol stateless. Out of scope for a release-hardening pass; filed forward.
- **No new MCP capability.** `output_schema` / structured output (brief 003, v1.29.1) is not
  wired. KI-11 fixes what `to_mcp_server()` already claims to do, nothing more.
- **No audit conformance module.** `testkit/varco_conformance` stays at five modules. KI-9 is a
  single-backend fix, not a contract gap — `varco_sa`'s implementation already satisfies the ABC.
- **No `AuditRepository` ABC signature change.** KI-9 makes the Beanie subclass match the base;
  the base (`varco_core/varco_core/service/audit.py:267-275`) is untouched.
- **No un-ignoring of `E501`** — see §RL-17-e501.
- **No changes to `varco_core/tests/test_serializer.py:195,200`, `varco_core/meta.py:967`, or
  `varco_beanie/factory.py:270`.** All four `# noqa` are permanent by design — §RL-15-keep.
- **No release engineering, no version bumps** (RL-8…RL-13, Phase 4/5). This plan's output is a
  prerequisite for the 3.0.0 freeze, not part of it.

---

## Design

### §phase-order — why this order

```
Phase A (no code)      RL-19 close, RL-16 disposition
   │
Phase B  RL-18 ────────── package-list SSOT + drift guard
   │        (first: every later phase edits or is run by these scripts;
   │         landing it first means later phases inherit the guard)
   │
Phase C  RL-17 ────────── ruff format gate      (independent of B, but B's
   │                                             Makefile edit is in flight —
   │                                             serialise to keep diffs clean)
   │
Phase D  KI-9, KI-10 ──── varco_beanie behaviour fixes  (independent of B/C)
   │
Phase E  KI-11 ────────── MCP low-level Server + version pin
   │
Phase F  RL-15 ────────── StrEnum migration      (after D+E: both delete
   │                                              `# type: ignore`s, and F's
   │                                              lint sweep should see the
   │                                              final tree)
   │
Phase G  RL-14 ────────── mypy ramp, five flags, measured stop rule
            (LAST — D, E and F all change the error surface. Ramping first
             would measure a baseline that three later phases invalidate.)
```

Each phase is independently shippable and independently revertible. Phases D and E may be
parallelised across worktrees; F and G may not (both re-measure the whole tree).

---

### §RL-19 — close as done, add a pin-parity guard (🟢, Phase A)

RL-19 records an *unplanned but correct* change already in the tree: `.pre-commit-config.yaml`'s
ruff rev was bumped `v0.4.1` → `v0.16.4` because `v0.4.1` predates the `UP046`/`UP047` codes now
in `[tool.ruff.lint] ignore` and could not parse the config.

**Decision: close the row. Add one guard, nothing else.**

DESIGN: assert `.pre-commit-config.yaml`'s ruff rev equals the `[dependency-groups] lint` pin
- ✅ The failure RL-19 fixed was *silent* — the hook broke only at commit time, on one developer's
  machine, with a config-parse error rather than a lint error. A test catches the next divergence
  in CI instead.
- ✅ Costs one test function; the pin already exists (`ruff==0.16.4`, root `pyproject.toml:30`).
- ❌ Couples two files that could legitimately diverge for a transition window. Accepted — the
  transition window is a deliberate act and the test names the exact reason it exists.
- **Rejected**: leave it unguarded. That is the status quo that produced RL-19.

### §RL-16 — no work, standing decision with a trigger (🟡, Phase A)

BACKLOG.md:126 already carries the disposition, decided across Plan 017 §RL-5-triggers and Plan
018 §RT7-ci. **Nothing in this plan changes it.** The row is restated, not closed:

- The `integration` job **may** be promoted to a required/PR-gating check — but only after a
  measured flake rate over real nightly runs. §RL-16-trigger fixes the missing quantity: **≥30
  consecutive nightly `integration` runs with ≤1 non-code-caused failure.** Below 30 runs there
  is no measurement, only anecdote — the exact error RL-20 and RL-21 were filed to stop repeating.
- The `chaos` job must **never** be promoted, on any schedule, for any reason. It exercises
  genuine races (black-holed sockets, container-restart port remaps) by design.

No file changes. Step 3 writes the trigger number into BACKLOG.md so the next reader inherits a
decidable condition rather than "after a measured flake rate".

---

### §RL-18 — one derivation, four consumers, one guard (🟡, Phase B)

**Today: four hand-written copies, and one is already wrong.**

| Consumer | Members | Delta vs. workspace |
|---|---:|---|
| root `pyproject.toml:10-22` `[tool.uv.workspace] members` | 11 | **the source of truth** (10 packages + `examples`) |
| `Makefile:45-55` `PACKAGES` | 10 | members − `examples` |
| `scripts/unit_tests.sh:55` `ALL_PACKAGES` | 10 | members − `examples` (+ `EXTRA_SUITES` for the example suite) |
| `scripts/integration_tests.sh:109` `ALL_INTEGRATION_PACKAGES` | 9 | members − `examples` − `varco_core` — **deliberate**, varco_core has no broker |
| `scripts/gen_ref_pages.py:27-37` `PACKAGES` | 9 | members − `examples` − **`varco_casbin` — LIVE DRIFT, a bug** |

`scripts/gen_ref_pages.py` is a fourth copy the RL-18 row does not mention, and it is missing
`varco_casbin` — the same package RL-6 found missing from `make lint`. `make docs` has therefore
never rendered `varco_casbin`'s API reference. This is the row's own thesis demonstrated a second
time, and fixing it is in scope.

DESIGN: derive a **base list** from `members`, apply **locally-declared, named exclusions**
- ✅ A new workspace member is picked up by all four consumers from one edit to `members`.
- ✅ The 9-vs-10 asymmetry stays *visible and reasoned* at the site that needs it. A naive "derive
  all three from `members`" would silently start running `varco_core` through the integration
  runner — which has no broker-facing tests and would report a spurious skip forever.
- ✅ A stale exclusion (naming a package that no longer exists) becomes a loud test failure, not a
  silently-ignored line.
- ❌ Two mechanisms instead of one (derivation + exclusion). Accepted: the alternative is
  encoding the exclusion as a second hand-written list, which is the defect being removed.
- ❌ Makefile now shells out on every invocation (~30 ms). Negligible.

**Mechanism.** A new `scripts/packages.sh`:
- Prints the base list (one package per line, `members` order preserved minus non-distribution
  entries) by invoking `python3 -c 'import tomllib; ...'` against the root `pyproject.toml`.
- Uses **stdlib `tomllib`** (Python ≥3.11) via bare `python3` — not `uv run python`.
  - ✅ No venv dependency: `Makefile:lint` and `gen_ref_pages.py` must work before/without a sync.
  - ✅ Correct TOML parsing, no regex.
  - ❌ Requires `python3 ≥ 3.11` on PATH. Accepted — the repo targets 3.12/3.13 and CI provides it.
  - **Rejected**: `sed`/`awk` on the `members = [...]` block. ❌ Breaks on a comment, a trailing
    comma on the closing line, or an inline-table member.
- Distinguishes "distribution package" from "workspace member" by one rule: **a member is a
  distribution iff `<member>/<member>/__init__.py` exists.** This excludes `examples`
  structurally rather than by name, so a future non-distribution member needs no edit.
  - ⚠️ ASSUMPTION: this rule holds for all ten today (`varco_core/varco_core/__init__.py`, …).
    Step 5's guard test asserts the derived list equals the ten literal names, so a rule that
    stops holding fails loudly on the next member added.

**Consumers.**
- `Makefile` — `PACKAGES := $(shell $(CURDIR)/scripts/packages.sh)`, plus a `print-packages`
  phony target (used by the guard test; also useful for humans).
- `scripts/unit_tests.sh` — `mapfile -t ALL_PACKAGES < <("$ROOT/scripts/packages.sh")`.
  `EXTRA_SUITES` stays hand-written: it is a *shape* declaration (`<dir>:<testpath>`), not a
  package list, and its one entry is not a workspace member.
- `scripts/integration_tests.sh` — same `mapfile`, then subtract a locally-declared
  `INTEGRATION_EXCLUDE=("varco_core")` carrying its own reason comment.
- `scripts/gen_ref_pages.py` — read `members` directly with `tomllib` (it is already Python), same
  `<member>/<member>/__init__.py` rule. **This adds `varco_casbin` to the docs build.**

**Guard.** `varco_core/tests/test_repo_package_lists.py`.

DESIGN: a repo-infrastructure test living inside `varco_core`'s suite
- ✅ `varco_core` is the first suite `scripts/unit_tests.sh` runs and the one CI's `unit` job runs
  on both 3.12 and 3.13 — the guard cannot be skipped.
- ❌ Repo infrastructure asserted from a package suite is off-layer. Accepted: there is no
  repo-level pytest suite and creating one means an eleventh entry in the very list this row
  exists to stop duplicating.
- Guards: derived base == the ten literal names; `make -s print-packages` == base (skipped if
  `make` is absent); every name in `INTEGRATION_EXCLUDE` is a member of base; `gen_ref_pages`'s
  derived list == base.

---

### §RL-17 — adopt `ruff format` as a gate (🟢, Phase C)

Premise corrected in EC-1: measured churn is **zero `.py` files**. This is the cheapest moment
this will ever be — every day without the gate is a day the tree can drift away from formatted.

DESIGN: add `[tool.ruff.format]` with `docstring-code-format = false`
- ✅ Zero-churn adoption, verified by measurement rather than estimated.
- ✅ `docstring-code-format = false` (ruff's default, set **explicitly**) keeps the formatter out
  of varco's `Usage::` docstring blocks. Those blocks are hand-wrapped to read well at 100
  columns inside an indented docstring and follow CLAUDE.md's docstring conventions; letting the
  formatter rewrite them is exactly the "unmeasured whole-tree churn" RL-17 feared, just
  relocated. Setting it explicitly makes the decision visible instead of relying on a default.
- ❌ One more gate that can go red. Mitigated: `make format` fixes it, and the pin (`ruff==0.16.4`)
  means a new ruff release cannot change formatting under CI without a deliberate bump.
- **Rejected**: formatter as a `--fix`-only local convenience with no CI gate. ❌ That is the
  status quo; nothing stops the tree drifting, and the zero-churn window closes.

#### §RL-17-e501 — `E501` stays ignored

`[tool.ruff.lint] ignore` currently carries `E501` with the comment *"line-length is enforced by
the formatter, not the linter (and this repo does not run a formatter gate yet)"*. The
parenthetical becomes false in this phase and must be rewritten — but the **decision does not
change**: `E501` stays ignored. The formatter cannot split a long URL, a long string literal, or
a long comment, so un-ignoring `E501` would flag lines nothing can fix. Step 12 rewrites the
comment to state the real reason.

⚠️ ASSUMPTION: the zero-churn measurement was taken this session on this tree. Step 10 re-measures
before the config lands; if any `.py` file now differs, the diff is reviewed rather than blindly
applied.

---

### §RL-15 — migrate the eight library enums to `StrEnum`, spend the 3.0.0 window (🟢, Phase F)

**Decision: migrate now.** Nine `# noqa: UP042` sites (eight library enums + one example) become
`StrEnum`; the four unrelated `# noqa` sites stay and get permanent reasons.

DESIGN: migrate inside the 3.0.0 breaking-change window rather than defer again
- ✅ **The window argument is decisive.** BACKLOG.md's Locked decisions table: *"Breaking-change
  appetite — Deliberately spend the window. 3.0.0 is the last cheap moment before the SemVer
  contract binds."* After 3.0.0 this becomes a major-version-only change to eight public types.
- ✅ **Measured internal blast radius is zero** (EC-3): no bare `{member}` interpolation exists in
  the repo. Brief 002's three "wire format changes" rows all require exactly that construct.
- ✅ **Both no-change boundaries are documented, not assumed**: stdlib `json.dumps` treats both
  identically because `JSONEncoder` does an `isinstance(obj, str)` check (brief 002 §5); pydantic
  v2 serializes by *value* for both forms, and `BaseSettings` env parsing is identical (brief 002
  §"Pydantic v2 Behaviour"). Step 20 verifies rather than trusts.
- ✅ **The change is a repair, not a regression.** `f"{HealthStatus.HEALTHY}"` yields
  `"HealthStatus.HEALTHY"` today — the Python 3.11 `Enum.__format__` change (brief 002 §1, CPython
  #100458). `StrEnum` exists specifically to undo it (brief 002 §"Authoritative Guidance" item 1).
  A downstream consumer formatting a varco enum today gets a value that surprises them.
- ✅ All nine sites assign explicit string values, so `auto()`'s lowercasing behaviour (brief 002
  §6) is not in play.
- ❌ **It is a real breaking change to eight public types** for any downstream consumer that
  `str()`s or `%s`-logs them. Ruff marks UP042 an *unsafe* fix for precisely this reason (brief
  002 §"Ruff's UP042 Rule Position"). Mitigated by: the 3.0.0 major bump, an explicit
  **BREAKING** CHANGELOG entry naming all eight types and the before/after strings, and the fact
  that no varco-internal caller is affected.
- ❌ Log-line text changes (`HealthStatus.HEALTHY` → `healthy`) for anyone `%s`-logging these
  outside varco. A log-scraping regex could break. Named in §Risks; not blocking.
- **Rejected — defer again and widen `[tool.ruff.lint] ignore` to include UP042.** ❌ Converts a
  visible, per-site, reasoned deferral into an invisible global suppression, and permanently
  forfeits the only cheap window. Explicitly what RL-15's own Rationale cell warns against.
- **Rejected — migrate only the enums with no external surface (`PKStrategy`, `ErrorPolicy`,
  `DispatchMode`) and keep the wire-facing three (`HealthStatus`, `CircuitState`, the two
  delivery-semantics).** ❌ Leaves the codebase with two idioms for the same construct and no
  rule a reader can apply; the split would need re-litigating at every new enum.

**Sites (all nine `# noqa: UP042` removed):**

| File:line | Class | Members |
|---|---|---|
| `varco_core/varco_core/meta.py:199` | `PKStrategy` | INT_AUTO / UUID_AUTO / STR_ASSIGNED / CUSTOM |
| `varco_core/varco_core/health.py:76` | `HealthStatus` | HEALTHY / DEGRADED / UNHEALTHY |
| `varco_core/varco_core/event/base.py:101` | `ErrorPolicy` | — |
| `varco_core/varco_core/event/base.py:140` | `DispatchMode` | FIRE_FORGET / COLLECT_ALL / FAIL_FAST |
| `varco_core/varco_core/resilience/circuit_breaker.py:78` | `CircuitState` | CLOSED / OPEN / HALF_OPEN |
| `varco_kafka/varco_kafka/config.py:67` | `KafkaDeliverySemantics` | — |
| `varco_nats/varco_nats/config.py:65` | `NatsDeliverySemantics` | — |
| `varco_ws/varco_ws/websocket.py:102` | `BackpressurePolicy` | — |
| `examples/19-resilience-payment-gateway/stub.py:26` | `FailMode` | — |

⚠️ `varco_core/health.py:64-72` carries a module-level `_HEALTH_SEVERITY` dict with a comment
explaining it lives outside the class because *"Python's Enum metaclass processes all class-body
assignments … and may coerce dict values to strings when the class extends str."* `StrEnum` uses
the same metaclass, so this constraint is unchanged and the dict stays where it is. Step 19 must
not "clean this up".

#### §RL-15-keep — four `# noqa` that are permanent, not debt

RL-15's own Rationale already flags two of these; the other two are verified here. **None are
migrated.** Step 21 rewrites each comment from a deferral to a permanent reason.

| Site | Rule | Why permanent |
|---|---|---|
| `varco_core/varco_core/meta.py:967` | UP007 | `typing.Union[tuple(non_none)]` is built from a **variable-length tuple at runtime**. `X \| Y` requires literal operands — the rewrite is not expressible. The existing comment already says this. |
| `varco_beanie/varco_beanie/factory.py:270` | UP045 | `Optional[int]` returned as a **runtime typing object** for pydantic field construction, not a static annotation. Rewriting to `int \| None` is *probably* accepted by pydantic v2, but the benefit is zero and the risk is a silent field-schema change in the model factory. Not worth it. |
| `varco_core/tests/test_serializer.py:195,200` | UP045 ×2 | The test's *subject* is `Optional[]` backward-compat handling. Rewriting the construct deletes the test's reason to exist. |

---

### §KI-9 — Beanie audit trail is not tenant-scoped (🔴, Phase D)

`varco_beanie/varco_beanie/audit.py:493-508` declares:

```python
async def list_for_entity(  # type: ignore[override]
    self, entity_type: str, entity_id: str, *, limit: int = 100,
) -> list[AuditEntry]:
```

The ABC (`varco_core/varco_core/service/audit.py:267-275`) declares
`tenant_id: str | None = None`, documented at :283-289 as a *"**breaking** keyword-only
addition … An out-of-tree subclass that does not accept this parameter breaks loudly
(`TypeError`) at call time rather than silently ignoring the tenant filter, **which is the
security bug this change exists to fix**."*

`BeanieAuditRepository` is an in-tree subclass that does exactly what the ABC docstring calls the
security bug: any caller passing `tenant_id=` gets a `TypeError`, and the Beanie audit query at
:537-548 filters only on `(entity_type, entity_id)`.

DESIGN: add the parameter **and** the filter, mirroring `varco_sa` exactly
- ✅ The reference implementation already exists and is the contract's intent:
  `varco_sa/varco_sa/audit.py:469-516` does `if tenant_id is not None: stmt =
  stmt.where(AuditEntryModel.tenant_id == tenant_id)`. `None` → no filter → pre-existing
  unscoped behaviour preserved.
- ✅ `AuditDocument` already persists `tenant_id` (written at `audit.py:477`, read back at :567) —
  no schema change, no migration, no backfill.
- ✅ Purely additive at the call site: every existing caller omits `tenant_id` and is unaffected.
- ❌ Adds a third field to a query with no supporting compound index. The existing docstring
  already warns about O(N) in-memory sorts without
  `(entity_type, entity_id, occurred_at)`; the recommendation becomes
  `(tenant_id, entity_type, entity_id, occurred_at)`. Documentation change only — this plan does
  **not** create the index (that is an operator decision on a live collection).
- **Rejected — accept `tenant_id` and ignore it** to silence mypy. ❌ The ABC docstring names
  this as the exact failure mode it was designed to prevent, and the existing inline comment at
  `audit.py:503-505` already says so.
- **Rejected — relax the ABC** so the Beanie signature is legal. ❌ Weakens the contract for all
  backends to accommodate one incomplete implementation.

### §KI-10 — `BeanieFastrestApp`'s constructor does not match the provider's (🔴, Phase D)

`bootstrap.py:176-180` calls `BeanieRepositoryProvider(mongo_client=…, db_name=…,
transactional=…)`. The real signature (`provider.py:53-56`) is
`__init__(self, settings: Inject[BeanieSettings])`. This raises `TypeError` on every call. It is
undetected because the class has **zero test coverage** (EC-3).

The proximate cause is a stale docstring: `provider.py:33-34` still documents the deleted call
shape —

```
provider = BeanieRepositoryProvider(mongo_client=client, db_name="myapp")
```

— two lines above a paragraph (:42-44) that correctly says connection details come from the
injected `BeanieSettings`. The bootstrap was written against the docstring, not the signature.

DESIGN: build a `BeanieSettings` from `BeanieConfig` and pass `settings=`
- ✅ One construction shape for the provider. The DI path and the non-DI path converge instead of
  diverging further.
- ✅ `BeanieConfig` and `BeanieSettings` are field-for-field compatible already —
  `mongo_client` / `db_name` / `entity_classes` / `transactional`
  (`config.py:75-78` vs `bootstrap.py:99-109`). No new fields, no lossy mapping.
- ✅ `BeanieSettings` is a plain frozen dataclass (`config.py:40`), directly constructible with no
  container — the non-DI path stays genuinely non-DI.
- ❌ `BeanieConfig` and `BeanieSettings` are now near-duplicate value objects. Accepted for this
  plan: collapsing them is a public-API deletion (`BeanieConfig` is exported at
  `varco_beanie/__init__.py:46`) and belongs in the RL-8 API-surface audit, not here. Filed
  forward (§reconciliation).
- **Rejected — add optional `mongo_client=`/`db_name=`/`transactional=` kwargs to
  `BeanieRepositoryProvider.__init__` alongside `settings`.** ❌ Two construction shapes is the
  defect, not the fix; and making `settings` optional in a `@Singleton`-decorated `__init__` puts
  an `Inject[...]`-annotated parameter behind a default, which is precisely the annotation shape
  CLAUDE.md's DI pitfall table warns about.
- **Rejected — delete `BeanieFastrestApp`.** ❌ It is exported public API with a documented
  `SAFastrestApp` parallel (`bootstrap.py:33-41`). Deleting it is an RL-8 decision.

Because `BeanieSettings.entity_classes` is registered by the provider's own `__init__`
(`provider.py:70-71`), the now-redundant `self._provider.register(*config.entity_classes)` at
`bootstrap.py:183` is **removed** — one registration path, not two. It is idempotent today
(`provider.py:75-76` guards on `cls not in self._built`), so removal is behaviour-preserving.

### §KI-11 — MCP tools advertise the wrong schema (🔴, Phase E)

Two coupled problems, both currently fatal:

1. `mcp.py:700-711` calls `server.add_tool(..., input_schema=_tool.input_schema)`. `FastMCP.add_tool()`
   has **never** accepted that parameter — it derives schemas from the handler's type hints
   (brief 003 §finding 1; SDK issue #761 open since May 2025 with no resolution). The call raises
   `TypeError`.
2. `mcp.py:715-764` `mount()` calls `to_mcp_server()` and then `server.sse_app()` — so `mount()`
   is broken by (1) too. **Both public entry points are dead today**; there is no working
   behaviour to regress.

Varco already holds a complete JSON Schema dict per tool
(`MCPToolDefinition.input_schema`, built by `_build_input_schema()` at `mcp.py:190`), and its
handler is deliberately an untyped `**kwargs` shim because dispatch is generic
(`MCPAdapter.execute(tool_name, arguments)` at `mcp.py:551`). Signature-derived schemas and
generic dispatch are fundamentally incompatible.

**Decision: drop `to_mcp_server()` to the low-level `Server` API and pin `mcp>=1.28.1,<2`.**
This is brief 003's option 1 and its §"Librarian's note" recommendation.

DESIGN: low-level `Server` + `mcp.types.Tool` carrying varco's schema verbatim
- ✅ **Works today with what varco already has.** `Tool(name=…, description=…, input_schema={…})`
  accepts a plain dict; no synthesis, no post-processing (brief 003 §finding 2, §note items 1-2).
- ✅ **Portable across v1 and v2.** The low-level `Tool`-with-schema path exists in both; only
  handler *registration* differs (brief 003 §finding 3). A future v2 migration touches the
  registration lines, not the schema lines.
- ✅ **Reversible.** If SDK issue #761 ever lands `input_schema=` on the high-level server,
  reverting to decorators is a small diff (brief 003 §note item 3).
- ❌ **More verbose**, and varco loses FastMCP's automatic argument validation. In v2 that
  validation is gone anyway ("schemas are advertised but not validated", brief 003 §finding 3),
  so this is a cost varco pays one release early rather than a cost it avoids.
- ❌ **`mount()` must be rewritten** — the low-level `Server` has no `sse_app()`. Cost is bounded:
  `mount()` is already broken, so this is construction, not repair.
- ❌ **Pins onto a maintenance-only branch.** v1.x receives critical fixes only; v1.29.1 is the
  last v1 release (brief 003 §"Version/compatibility notes"). Filed forward as an explicit row.
- **Rejected — synthesize a typed handler signature (or a pydantic model) from the JSON Schema so
  FastMCP derives the right schema.** ❌ Brief 003 §"Options compared" row 2: *"Fragile; no
  robust off-the-shelf tool … post-processing/monkey-patching Tool objects is version-brittle"*,
  and issue #761 itself records that this workaround is unreliable. It also duplicates metadata
  varco already has in the exact form the SDK wants.
- **Rejected — migrate to `mcp` v2 now.** ❌ Brief 003 §"Options compared" row 3: decorators
  removed, every handler rewritten, protocol stateless. A whole-subsystem rewrite to fix one call
  site during a release-hardening pass.
- **Rejected — drop `input_schema=` and let FastMCP derive from `**kwargs`.** ❌ Every tool would
  advertise a `kwargs`-shaped schema to the LLM. Silently-wrong tool contracts are worse than a
  loud `TypeError`.
- **Rejected — reach into `FastMCP._tool_manager` / `._mcp_server` to inject correct schemas.**
  ❌ Private-attribute workaround around an upstream gap — the same discipline CLAUDE.md imposes
  for providify ("don't paper over it"). File the gap, use the sanctioned API.

#### §KI-11-pin — the version pin is part of the fix, not housekeeping

`varco_fastapi/pyproject.toml:44` declares `mcp = ["mcp>=1.0"]`, unbounded. Brief 003
§"Version/compatibility notes": *"Varco's `mcp>=1.0` (no upper bound) is unsafe — it will
auto-upgrade to v2.1.1 on first install after 2026-07-28, breaking any v1-dependent code."*
Today is 2026-08-28. **Change to `mcp = ["mcp>=1.28.1,<2"]`** — brief 003's stated official
guidance for v1.x users. Lower bound 1.28.1 because that is the version brief 003 names as the
stable v1 floor for this API.

⚠️ ASSUMPTION: `uv.lock` may already have resolved `mcp` to a 2.x release under the unbounded
constraint, in which case `mcp.server.fastmcp` does not exist in the current venv at all and the
xfail is xfailing for a *different* reason than its `reason=` string states. **Step 27 records
the currently-resolved version before changing anything** — if it is 2.x, that is a second
evidence correction to write back.

#### §KI-11-testability — extract the tool-object builder

DESIGN: a module-level `_to_mcp_tools(tools: Sequence[MCPToolDefinition]) -> list[Tool]`
- ✅ The schema assertion — the *entire point* of KI-11 — becomes a pure function test with no
  SDK-internal poking (no reaching into `server.request_handlers[...]` or similar).
- ✅ Keeps `to_mcp_server()` down to: construct `Server`, register two handlers, return.
- ❌ One more private module-level function. Trivial.

⚠️ ASSUMPTION (three, all resolved by Step 26's read of the resolved SDK, **before** any code is
written): (a) the exact `Tool` field spelling in the pinned v1.x — brief 003 §finding 6 says the
`inputSchema` → `input_schema` rename happened in **v2**, so v1 likely wants `inputSchema`;
(b) the `@server.list_tools()` / `@server.call_tool()` decorator names and their handler
signatures; (c) the `SseServerTransport` wiring shape for `mount()`. All three are read out of
the installed `mcp/types.py`, `mcp/server/lowlevel/server.py` and `mcp/server/sse.py` rather than
recalled.

⚠️ `varco_fastapi/tests/milestone_f/test_mcp_auth_middleware.py:311-319,342-349` install a
`_fake_to_mcp_server` returning an object with an `sse_app()` method. Those fakes encode the old
return contract and **must be updated in the same step** as `mount()`, or they will pass while
asserting nothing true.

---

### §RL-14 — a sequenced, measured mypy ramp (🟡, Phase G)

#### §RL-14-metric — two metrics, named (fixes EC-2)

| Name | Command | Role | Today |
|---|---|---|---|
| **M1 — suppression debt** | `rg -o 'type: ignore' varco_*/varco_* \| wc -l` | Directional gauge only. **Never a gate** — a legitimate suppression on a genuinely untypable third-party call is not debt. | **327** (per-package table in EC-2) |
| **M2 — flag fallout** | `uv run mypy <ten dirs>` with the candidate flag enabled, per package | **The gate.** A flag is not committed until M2 == 0 for the packages it applies to. | 0 under the current config |

RL-6's 219 was neither of these — it was M2 measured under the *initial* config before the
suppression sweep. Step 36 writes this table into BACKLOG.md.

#### §RL-14-order — the sequence

Brief 001 §"Recommended Ramp Order" (mypy's own *Using mypy with an existing codebase* page) and
§"Librarian's Note" give the ordering. One flag per commit; each commit re-measures M2.

| # | Flag(s) | Scope | Why here | Brief 001 basis |
|---|---|---|---|---|
| G1 | `warn_unused_configs`, `warn_redundant_casts` | global | Foundation tier — "Getting this passing should be easy" | §Ramp Order tier 1 |
| G2 | `no_implicit_reexport` | global | **Near-zero cost** for varco specifically: a name in `__all__` is already treated as explicitly re-exported, and all ten top-level `__init__.py` define `__all__`. Enabled early *because it is cheap*, not because ROI is high | §"`no_implicit_reexport` for `py.typed` Libraries" — "Blast radius for varco: ~0" |
| G3 | `check_untyped_defs` | **per-package**, ascending debt | The single highest-ROI flag — "Strongly recommend enabling this one as soon as you can". Type-checks bodies previously skipped entirely, so it is the one flag that finds *runtime* bugs | §Ramp Order tier 2; §Note item 2 |
| G4 | `disallow_subclassing_any`, `disallow_untyped_decorators`, `disallow_any_generics` | global | Intermediate tier — "shouldn't be too much additional work" | §Ramp Order tier 3 |
| G5 | `warn_return_any` | global | Real unsoundness catcher; last because it "can be tricky with untyped libraries" | §Ramp Order tier 5 |
| — | `disallow_incomplete_defs`, `disallow_untyped_defs` | — | **OUT OF SCOPE**, re-filed as RL-14b | §Ramp Order tier 4 — "High effort" |
| — | `disallow_untyped_calls`, `disallow_any_unimported`, `disallow_any_expr` | — | **DECIDED: never**, on the current dependency set | §"Skip these entirely"; §Note item 6 |

**G3 is per-package via `[[tool.mypy.overrides]]`** — brief 001 §"Per-Module Strictness Support"
confirms mypy 2.3.1 supports it in `pyproject.toml` with glob patterns, and calls per-package the
"natural granularity for a workspace monorepo".

DESIGN: per-package ramp for G3, global for everything else
- ✅ A package whose fallout is large does not block the nine that are clean.
- ✅ Each override block is a visible, dated to-do: a package **absent** from the `check_untyped_defs`
  overrides is a package with known unmeasured debt.
- ❌ `[tool.mypy]` grows an overrides section that must eventually be deleted when the flag goes
  global. Accepted — that deletion is RL-14b's completion criterion.

**Package order for G3** (ascending M1, as a *proxy* for fallout — M1 measures whole-package
suppression debt, not `check_untyped_defs` fallout specifically, so the order is a starting
heuristic and the implementer re-sorts on the first real measurement):
`varco_ws` (0) → `varco_casbin` (2) → `varco_kafka` (7) → `varco_memcached` (8) →
`varco_nats` (10) → `varco_sa` (34) → `varco_beanie` (36) → `varco_redis` (48) →
`varco_fastapi` (83) → `varco_core` (99).

#### §RL-14-stop — the stop rule

**If any single flag's measured M2 fallout exceeds 50 errors, stop. Do not grind.** Commit the
flags already landed, file the remainder as a BACKLOG row naming the flag, the package, and the
count. This mirrors Plan 017's own 250-error re-litigate threshold, scaled to a per-flag budget.

Rationale: a flag that surfaces >50 errors is not a config change, it is a refactor wearing a
config change's clothing — and the release path (Phase 4/5) does not depend on it.

#### §RL-14-ignores — newly-unused suppressions

`warn_unused_ignores=true` is already on. Brief 001 §"`warn_unused_ignores` Interaction": enabling
a new flag can make an existing suppression newly *unused*, and mypy will report it. **Delete
newly-unused ignores in the same commit as the flag that made them unused** — never carry a
suppression whose reason has evaporated. Statically-unreachable-code ignores are exempt from the
warning and are not a concern here (same section).

No mypy version change is involved: 2.0's default changes (`--local-partial-types`,
`--strict-bytes`, per brief 001 §"Version-Specific Changes") are already in effect at the pinned
2.3.1, so the ramp inherits no surprise from them.

---

## Steps

Each step is atomic and independently verifiable. `[ ]` = not started.

### Phase A — zero-code closures

1. [x] `BACKLOG.md` — mark **RL-19 ✅ DONE**, citing `.pre-commit-config.yaml`'s `v0.16.4` rev and
       the `ruff==0.16.4` pin at root `pyproject.toml:30`.
2. [x] `varco_core/tests/test_repo_tooling_pins.py` (new) — failing test first:
       parse `.pre-commit-config.yaml`'s ruff `rev` and root `pyproject.toml`'s
       `[dependency-groups] lint` ruff pin, assert they name the same version (`v0.16.4` ↔
       `ruff==0.16.4`, normalising the `v` prefix). Skip if `.pre-commit-config.yaml` is absent.
3. [x] `BACKLOG.md` — RL-16: keep the row **open**, append the trigger from §RL-16:
       "promotion candidate only after ≥30 consecutive nightly `integration` runs with ≤1
       non-code-caused failure; the `chaos` job is never a promotion candidate."

### Phase B — RL-18, package-list single source of truth

4. [x] `varco_core/tests/test_repo_package_lists.py` (new) — **failing tests first**, four
       assertions per §RL-18's Guard paragraph. All four fail today (`scripts/packages.sh` and
       the `print-packages` target do not exist; `gen_ref_pages.PACKAGES` is missing
       `varco_casbin`).
5. [x] `scripts/packages.sh` (new) — derive the base list from root `pyproject.toml`'s
       `[tool.uv.workspace] members` via `python3 -c 'import tomllib; …'`, filtering to members
       where `<member>/<member>/__init__.py` exists. Print one name per line, `members` order.
       Include the DESIGN block from §RL-18 as a header comment.
6. [x] `Makefile` — replace the literal `PACKAGES := \ …` block (lines 45-55) with
       `PACKAGES := $(shell $(CURDIR)/scripts/packages.sh)`; add a `.PHONY: print-packages`
       target echoing `$(PACKAGES)`; add it to `make help`.
7. [x] `scripts/unit_tests.sh` — replace the `ALL_PACKAGES=(…)` literal (line 55) with a
       `mapfile -t ALL_PACKAGES < <("$ROOT/scripts/packages.sh")`. **Delete** the "KNOWN
       MAINTENANCE POINT" comment at lines 13-17 — it describes the defect this step removes —
       and replace it with one line pointing at `scripts/packages.sh`. Leave `EXTRA_SUITES`
       hand-written (§RL-18).
8. [x] `scripts/integration_tests.sh` — same `mapfile`, then subtract a locally-declared
       `INTEGRATION_EXCLUDE=("varco_core")` with its own reason comment ("varco_core has no
       broker-facing tests; it is deliberately not 10 members here"). Leave `EXTRA_SUITES` alone.
9. [x] `scripts/gen_ref_pages.py` — replace the literal `PACKAGES` tuple (lines 27-37) with a
       `tomllib` read of the root `pyproject.toml`, same existence rule. **This adds
       `varco_casbin` to the docs build** — note it in the CHANGELOG as a fix, not a feature.

### Phase C — RL-17, `ruff format` gate

10. [x] Re-measure: `uv run ruff format --check .` and record the `.py` diff count in the commit
        message. Expected 0 (EC-1). If non-zero, review the diff before proceeding.
11. [x] Root `pyproject.toml` — add `[tool.ruff.format]` with `docstring-code-format = false`
        set **explicitly** and a comment carrying §RL-17's reasoning.
12. [x] Root `pyproject.toml` — rewrite the `E501` comment in `[tool.ruff.lint] ignore`: delete
        "this repo does not run a formatter gate yet" and state §RL-17-e501's real reason (the
        formatter cannot split long URLs / string literals / comments).
13. [x] `Makefile` — `lint` target gains `uv run ruff format --check $(_SRC_DIRS)`; `format`
        target already runs `ruff format` (verify) and stays as the fixer.
14. [x] `.github/workflows/test.yml` — `lint` job gains a `ruff format --check .` step, adjacent
        to the existing `ruff check .` step.
15. [x] `.pre-commit-config.yaml` — add the `ruff-format` hook alongside the existing `ruff` hook,
        same `rev`.
16. [x] `CLAUDE.md` — §Commands: `make format` / `make lint` descriptions gain the formatter gate;
        the Common Pitfalls "Linting with `uvx ruff`" row applies verbatim to `ruff format` too —
        extend its Symptom cell.

### Phase D — KI-9 and KI-10 (`varco_beanie`)

17. [x] `varco_beanie/tests/test_beanie_audit_tenant_scoping.py` (new) — **failing tests first**:
        - (a) Docker-free: `inspect.signature(BeanieAuditRepository.list_for_entity)` exposes
          `tenant_id` as KEYWORD_ONLY with default `None`, and its parameter names/kinds match
          `AuditRepository.list_for_entity`'s.
        - (b) `@pytest.mark.integration`, session-scoped `mongo_url` fixture: save three
          `AuditEntry` rows for one entity — two with `tenant_id="t-a"`, one with `"t-b"` — then
          assert `list_for_entity(..., tenant_id="t-a")` returns exactly the two,
          `tenant_id="t-b"` exactly the one, and `tenant_id=None` all three.
          **Per-test namespacing (CLAUDE.md §shared containers): use a `uuid4().hex[:8]`-suffixed
          `entity_type` string**, not a fresh database — the container is shared.
18. [x] `varco_beanie/varco_beanie/audit.py:493-548` — add `tenant_id: str | None = None`
        keyword-only; add `AuditDocument.tenant_id == tenant_id` to the `find()` call **only when
        not `None`** (mirroring `varco_sa/varco_sa/audit.py:469-516`). Remove the
        `# type: ignore[override]` and the whole BUG comment block at :499-507. Update the
        docstring: `Args:` gains `tenant_id` (reuse the ABC's wording at
        `varco_core/varco_core/service/audit.py:283-289`), and the `Edge cases:` index note at
        :524-526 becomes `(tenant_id, entity_type, entity_id, occurred_at)`.
19. [x] Verify callers: `rg -n 'list_for_entity' --type py` — confirm no in-tree caller (including
        `varco_fastapi.admin.mount_reliability_admin`'s handlers) passes positionally in a way the
        new keyword-only parameter disturbs. Record the caller list in the commit message.
20. [x] `varco_beanie/tests/test_beanie_bootstrap.py` (new) — **failing tests first**:
        - (a) Docker-free: `BeanieFastrestApp(BeanieConfig(mongo_client=<fake>, db_name="x",
          entity_classes=(SomeModel,), transactional=True))` constructs without raising, and
          `app.uow_provider.get_repository(SomeModel)` returns an `AsyncBeanieRepository`
          (proves entity registration happened, no I/O required).
        - (b) `@pytest.mark.integration`, `mongo_url`: construct against a real `AsyncMongoClient`,
          `await app.init()`, then a save/get round-trip through `app.uow_provider.make_uow()`.
          Namespace by `db_name=f"varco_bootstrap_{uuid4().hex[:8]}"`.
        Test (a) fails today with `TypeError` — that *is* KI-10.
21. [x] `varco_beanie/varco_beanie/bootstrap.py:166-183` — build
        `BeanieSettings(mongo_client=config.mongo_client, db_name=config.db_name,
        entity_classes=config.entity_classes, transactional=config.transactional)` and pass
        `settings=`. Remove the `# type: ignore[call-arg]` and the BUG comment at :166-175.
        Remove the now-redundant `self._provider.register(*config.entity_classes)` at :183 with a
        one-line comment pointing at `provider.py:70-71`.
22. [x] `varco_beanie/varco_beanie/provider.py:33-34` — fix the stale docstring that caused this
        bug: replace `BeanieRepositoryProvider(mongo_client=client, db_name="myapp")` with the
        real `BeanieRepositoryProvider(settings=BeanieSettings(mongo_client=client,
        db_name="myapp"))` shape. **This is the root-cause fix**; without it the next author
        repeats KI-10.
23. [x] `CHANGELOG.md` — two entries under Fixed: the Beanie audit tenant scope (name it a
        **security-relevant** fix — an unscoped audit query in a multi-tenant deployment) and the
        `BeanieFastrestApp` constructor.
24. [x] `README.md` / `technical_docs/features/database-auditing.md` — if either documents
        `list_for_entity`'s parameters, add `tenant_id`. (`rg -n 'list_for_entity' -g '*.md'`.)

### Phase E — KI-11 (MCP)

25. [x] Record the currently-resolved `mcp` version from `uv.lock` **before any change**
        (§KI-11-pin's ⚠️). If it is 2.x, write that back to BACKLOG.md as a third evidence
        correction — the xfail's stated `reason=` would then be wrong.
26. [x] Read the resolved SDK's `mcp/types.py`, `mcp/server/lowlevel/server.py` and
        `mcp/server/sse.py`. Record in the commit message: the `Tool` schema field spelling
        (`inputSchema` vs `input_schema`), the low-level decorator names and handler signatures,
        and the `SseServerTransport` wiring shape. **No code before this step.**
27. [x] `varco_fastapi/pyproject.toml:44` — `mcp = ["mcp>=1.28.1,<2"]` (§KI-11-pin, brief 003
        §"Recommended pinning"). Run `uv lock` and commit the lockfile change.
28. [x] `varco_fastapi/tests/milestone_f/test_mcp_adapter.py` — **new failing test first**:
        `test_mcp_tool_objects_carry_varco_input_schemas` — `importorskip("mcp")`, build an
        `MCPAdapter(OrderRouter, client=AsyncMock())`, call `_to_mcp_tools(adapter.tools)`, and
        assert each returned `Tool`'s schema field is `is`-identical in content to the
        corresponding `MCPToolDefinition.input_schema`, and that names/descriptions round-trip.
29. [x] `varco_fastapi/varco_fastapi/router/mcp.py` — add module-level
        `_to_mcp_tools(tools) -> list[Tool]` (§KI-11-testability), with the `mcp` import deferred
        inside the function to preserve the existing "mcp SDK import deferred" design
        (`mcp.py:42`).
30. [x] `varco_fastapi/varco_fastapi/router/mcp.py:689-713` — rewrite `to_mcp_server()`: construct
        the low-level `Server`, register a `list_tools` handler returning `_to_mcp_tools(self._tools)`
        and a `call_tool` handler dispatching to `await self.execute(name, arguments)` and wrapping
        the result in a `TextContent` with `json.dumps(result, default=str)`. Delete the
        `input_schema=` line and its `# type: ignore[call-arg]` and BUG comment (:704-710).
31. [x] `varco_fastapi/varco_fastapi/router/mcp.py:656-675` — update the docstring: it currently
        promises "A configured `mcp.FastMCP` instance". Return type stays `Any` (keeps `mcp` out
        of the type-check surface); add a DESIGN block carrying §KI-11's ✅/❌ and the brief-003
        citation.
32. [x] `varco_fastapi/varco_fastapi/router/mcp.py:757-764` — rewrite `mount()`'s ASGI-app
        construction: the `try: server.sse_app() except AttributeError: server.asgi_app()` dance
        is dead code against a low-level `Server`. Build the SSE ASGI app per Step 26's recorded
        shape. Keep the `server_auth` middleware behaviour and the existing log line unchanged.
33. [x] `varco_fastapi/tests/milestone_f/test_mcp_adapter.py:492-501` — **remove the
        `xfail(strict=True)` marker**; rename the test to reflect what it now asserts
        (`to_mcp_server()` returns a low-level `Server` and does not raise). It must pass.
34. [x] `varco_fastapi/tests/milestone_f/test_mcp_auth_middleware.py:311-319,342-349` — update
        both `_fake_to_mcp_server` doubles: they return an object with `sse_app()`, which no
        longer matches the contract. Without this they pass while asserting nothing true.
35. [x] `varco_fastapi/tests/milestone_f/test_mcp_adapter.py` — add a `mount()` test:
        `importorskip("mcp")`, `adapter.mount(app, path="/mcp")`, assert a route/mount exists at
        `/mcp` on the FastAPI app. (Deterministic; a live SSE stream is not asserted.)
36. [x] `CHANGELOG.md` + `ARCHITECTURE.md` — record the `to_mcp_server()` return-type change
        (`FastMCP` → low-level `Server`) as **BREAKING for anyone calling `to_mcp_server()`
        directly**, and the `mcp` extra's new upper bound.

### Phase F — RL-15 (StrEnum)

37. [x] `varco_core/tests/test_strenum_serialization.py` (new) — **characterization tests first,
        written against the CURRENT `(str, Enum)` form and expected to keep passing after
        migration**, verifying the two boundaries brief 002 reports as unchanged (EC-3):
        `json.dumps({"k": HealthStatus.HEALTHY}) == '{"k": "healthy"}'`, and a pydantic v2 model
        with a `HealthStatus` field round-trips `model_dump(mode="json")` to `"healthy"`. Add the
        same for one `BaseSettings`-parsed enum (`KafkaDeliverySemantics` from an env var).
        **If any of these fails before migration, stop and re-litigate §RL-15.**
38. [x] Migrate the eight library enums to `enum.StrEnum` (§RL-15's table rows 1-8), removing each
        `# noqa: UP042` and each deferral comment. Do **not** move
        `varco_core/health.py:64-72`'s `_HEALTH_SEVERITY` (§RL-15's ⚠️).
39. [x] `examples/19-resilience-payment-gateway/stub.py:26` — migrate `FailMode`, remove its noqa.
40. [x] `varco_core/meta.py:967`, `varco_beanie/factory.py:270`,
        `varco_core/tests/test_serializer.py:195,200` — rewrite each `# noqa` comment from a
        deferral to a permanent reason per §RL-15-keep's table. **No code change at these four
        sites.**
41. [x] `CHANGELOG.md` — one **BREAKING** entry naming all eight public types with the before/after
        string for each (`f"{HealthStatus.HEALTHY}"`: `"HealthStatus.HEALTHY"` → `"healthy"`), and
        stating explicitly that `.value`, `json.dumps`, and pydantic serialization are unchanged
        (brief 002 §5, §"Pydantic v2 Behaviour").
42. [x] `CLAUDE.md` — add a Common Pitfalls row: "**Assuming `f"{SomeVarcoEnum.MEMBER}"` still
        yields `ClassName.MEMBER`**" → root cause (3.11 `Enum.__format__`, undone by `StrEnum`) →
        fix (it now yields the value; that is intended as of 3.0.0).

### Phase G — RL-14 (mypy ramp)

43. [x] Measure and record M1 and M2 baselines per §RL-14-metric, **after** Phases D/E/F have
        landed (they delete `# type: ignore`s and change the error surface).
44. [x] Root `pyproject.toml` `[tool.mypy]` — **G1**: add `warn_unused_configs = true` and
        `warn_redundant_casts = true`, each with a brief-001 citation comment. Fix fallout. M2 → 0.
45. [x] Root `pyproject.toml` `[tool.mypy]` — **G2**: add `no_implicit_reexport = true`. Measure
        first: brief 001 predicts ~0 for the ten top-level `__init__.py`, but the flag applies to
        **every** module, so sub-package `__init__.py` files are the real unknown. Apply
        §RL-14-stop if >50.
46. [x] Root `pyproject.toml` — **G3**: add one `[[tool.mypy.overrides]]` block per package with
        `check_untyped_defs = true`, in the §RL-14-order sequence, **one commit per package**.
        Re-sort the sequence after the first two real measurements. Apply §RL-14-stop per package.
47. [x] Root `pyproject.toml` `[tool.mypy]` — **G4**: `disallow_subclassing_any`,
        `disallow_untyped_decorators`, `disallow_any_generics`, one commit each, §RL-14-stop each.
48. [x] Root `pyproject.toml` `[tool.mypy]` — **G5**: `warn_return_any`. §RL-14-stop.
49. [x] Root `pyproject.toml` `[tool.mypy]` — add a comment block recording the **decided-never**
        set (`disallow_untyped_calls`, `disallow_any_unimported`, `disallow_any_expr`) with the
        brief-001 §"Skip these entirely" citation, so a future contributor does not re-open it.
50. [x] Delete every newly-unused `# type: ignore` surfaced by `warn_unused_ignores` **in the same
        commit as the flag that made it unused** (§RL-14-ignores).
51. [x] `CLAUDE.md` — §Commands / CI subsection: record which strictness flags are on, which are
        per-package, and which are decided-never. Re-measure and record final M1.

### Phase H — reconciliation

52. [x] `BACKLOG.md` — apply §BACKLOG reconciliation below, in full.
53. [x] Full sweep: `make lint`, `make type-check`, `make test`, then
        `make integration-test-clean` for `varco_beanie` and `varco_fastapi` at minimum.

---

## Edge cases

| Input / state | Expected behaviour |
|---|---|
| `scripts/packages.sh` run with `python3` < 3.11 on PATH | Fails loudly with a message naming the requirement — never falls back to a hard-coded list. |
| A new workspace member added to `members` that is **not** a distribution (no `<m>/<m>/__init__.py`) | Excluded from the base list automatically; the guard test's literal-ten assertion fails and forces a deliberate update. |
| `INTEGRATION_EXCLUDE` names a package that no longer exists | Guard test fails (Step 4 assertion 3). Never silently ignored. |
| `make` absent when the guard test runs | That one assertion `pytest.skip`s; the other three still run. |
| `ruff format --check` finds a diff in a Markdown fenced block | Not a `.py` file; the gate is `.py`-scoped and unaffected (EC-1). |
| `BeanieAuditRepository.list_for_entity(tenant_id=None)` | No tenant filter — byte-identical to today's behaviour. Additive, non-breaking. |
| `BeanieAuditRepository.list_for_entity(tenant_id="t")` where no entry carries `tenant_id` | Empty list. Correct: an unstamped entry belongs to no tenant. |
| `BeanieFastrestApp(BeanieConfig(entity_classes=()))` | Constructs; provider registers nothing; `make_uow()` yields a UoW with no repos. Same as today's documented edge case (`config.py:69-70`). |
| `to_mcp_server()` with zero MCP-enabled routes | Returns a `Server` whose `list_tools` handler yields `[]`. No exception. |
| `mcp` not installed | `ImportError` with the existing "pip install 'varco-fastapi[mcp]'" message — the Plan 017 import fix is preserved. |
| `mcp` resolves to 2.x despite the new `<2` bound (stale lock) | `uv lock` in Step 27 corrects it; Step 25's recorded version makes the before/after visible. |
| `f"{HealthStatus.HEALTHY}"` after Phase F | `"healthy"` (was `"HealthStatus.HEALTHY"`). Intended, BREAKING, in CHANGELOG. |
| `json.dumps(HealthStatus.HEALTHY)` after Phase F | `'"healthy"'` — unchanged (brief 002 §5). Asserted by Step 37. |
| A mypy flag's fallout is 51 errors | §RL-14-stop: do not land it. Commit what is green, file the rest with the flag name, package, and count. |

---

## Verification

```bash
# Phase B
scripts/packages.sh                     # 10 lines, Makefile order
make -s print-packages                  # same 10, space-separated
uv run pytest varco_core/tests/test_repo_package_lists.py -v
make docs                               # varco_casbin reference now present under site/

# Phase C
uv run ruff format --check .            # must report 0 files would be reformatted
make lint                               # ruff check + ruff format --check

# Phase D
uv run pytest varco_beanie/tests/test_beanie_audit_tenant_scoping.py \
              varco_beanie/tests/test_beanie_bootstrap.py -v
uv run pytest varco_beanie/tests/ -m integration      # requires Docker
make type-check PKG=varco_beanie        # the two removed `# type: ignore`s must not resurface

# Phase E
uv run pytest varco_fastapi/tests/milestone_f/test_mcp_adapter.py -v
uv run pytest varco_fastapi/tests/milestone_f/test_mcp_auth_middleware.py -v
# no strict-xfail may remain from Plan 017:
rg -n 'KI-11' varco_fastapi/tests/      # expect: no xfail marker hits
make type-check PKG=varco_fastapi

# Phase F
uv run pytest varco_core/tests/test_strenum_serialization.py -v
rg -n 'noqa: UP042' -- varco_* examples/    # expect: zero hits
rg -n 'noqa: UP0' -- varco_* examples/      # expect: exactly 4 (the permanent set)

# Phase G — after EACH flag commit
uv run mypy varco_core/varco_core varco_kafka/varco_kafka varco_nats/varco_nats \
            varco_redis/varco_redis varco_sa/varco_sa varco_beanie/varco_beanie \
            varco_memcached/varco_memcached varco_ws/varco_ws \
            varco_fastapi/varco_fastapi varco_casbin/varco_casbin
rg -o 'type: ignore' varco_*/varco_* | wc -l    # M1, directional only

# Whole-plan gate
make lint && make type-check && make test
make integration-test-clean PKG=varco_beanie
make integration-test-clean PKG=varco_fastapi
```

CI equivalence: `.github/workflows/test.yml`'s `all-green` must be green on both 3.12 and 3.13.

---

## Risks

| Risk | Invariant that must hold | Mitigation |
|---|---|---|
| **RL-15 breaks an out-of-tree consumer** that `str()`s or `%s`-logs a varco enum | The change lands **inside** the 3.0.0 window, before the SemVer contract binds (BACKLOG Locked decisions) | Explicit BREAKING CHANGELOG entry naming all eight types with before/after strings; CLAUDE.md pitfall row; measured zero in-tree callers (EC-3) |
| **RL-15 changes a wire format after all** via a `json.dumps`/pydantic path brief 002 got wrong | `json.dumps(member)` and `model_dump(mode="json")` emit the member **value** under both forms | Step 37 writes those assertions **before** migrating; a red test there stops the phase |
| **KI-11's SSE rewrite is wrong in a way tests do not catch** — `mount()` is hard to assert end-to-end | `mount()` is **already broken today**; any working state is strictly better | Step 26 reads the real SDK source before coding; Step 35 asserts route registration; the SSE stream itself is an ⚠️ ASSUMPTION carried into a follow-up manual smoke test |
| **`mcp<2` pins onto a maintenance-only branch** (brief 003 §Version notes: v1.29.1 is the last v1 release) | varco's MCP surface keeps working on a supported SDK | Filed forward as an explicit BACKLOG row with the v2 migration shape already scoped by brief 003 §"Options compared" row 3 |
| **KI-9's new filter degrades an unindexed query** on a large audit collection | The filter is opt-in (`tenant_id=None` → no filter) so no existing query plan changes | Docstring records the recommended compound index; index creation is left to the operator |
| **RL-18's `python3`-on-PATH dependency** breaks `make lint` in a minimal container | The derivation fails loudly, never silently falls back | Step 5's script exits non-zero with a named requirement; the guard test asserts the derived list |
| **RL-14 lands a flag whose fallout is fixed with new `# type: ignore`s** — trading one debt for another | M1 must not increase across Phase G | Step 51 re-measures M1; a net increase means the flag was landed wrong and should be reverted per §RL-14-stop |
| **Phase G measures a baseline invalidated by D/E/F** | Phase G runs last (§phase-order) | Step 43 explicitly re-measures after D/E/F land |
| **`ruff format` gate goes red on a ruff bump** | ruff is `==`-pinned in `[dependency-groups] lint` and CI resolves `uv.lock` | Never `uvx ruff` (CLAUDE.md pitfall table); Step 2's pin-parity guard covers the pre-commit copy |

### Remaining ⚠️ ASSUMPTIONs (all resolved by a named step)

1. `<member>/<member>/__init__.py` identifies a distribution for all ten packages → Step 4's
   literal-ten assertion.
2. `ruff format --check` still reports zero `.py` churn at implementation time → Step 10.
3. The pinned `mcp` v1.x `Tool` schema field is `inputSchema`, not `input_schema` → Step 26.
4. The low-level `Server` decorator names and handler signatures in the resolved v1.x → Step 26.
5. The `SseServerTransport` wiring shape for `mount()` → Step 26.
6. `uv.lock` may already resolve `mcp` to 2.x under the unbounded `>=1.0` → Step 25.
7. `no_implicit_reexport`'s blast radius on **sub-package** `__init__.py` files (brief 001 only
   measured the ten top-level ones) → Step 45's measure-first instruction.
8. pydantic v2 would accept `int | None` at `varco_beanie/factory.py:270` — **untested and
   deliberately not tested**; §RL-15-keep declines the rewrite on zero-benefit grounds.

---

## BACKLOG reconciliation (Step 52, in full)

### Rows this plan CLOSES ✅

| Row | Closure evidence to write into the row |
|---|---|
| **KI-9** | `varco_beanie/varco_beanie/audit.py` `list_for_entity(tenant_id=)` implemented + filtered; `# type: ignore[override]` removed; `varco_beanie/tests/test_beanie_audit_tenant_scoping.py` (signature conformance + real-Mongo tenant isolation) |
| **KI-10** | `varco_beanie/varco_beanie/bootstrap.py` constructs `BeanieSettings` and passes `settings=`; `# type: ignore[call-arg]` removed; `provider.py:33-34`'s stale docstring (the root cause) fixed; `varco_beanie/tests/test_beanie_bootstrap.py` — first-ever coverage of this class |
| **KI-11** | `to_mcp_server()` on the low-level `Server` API with varco's schemas verbatim; `mount()` rebuilt; `mcp` pinned `>=1.28.1,<2`; **`xfail(strict=True)` removed and passing** |
| **RL-15** | 8 library enums + 1 example migrated to `StrEnum`; 9 `# noqa: UP042` gone; 4 unrelated `# noqa` reclassified permanent with reasons; BREAKING CHANGELOG entry |
| **RL-17** | `[tool.ruff.format]` + `ruff format --check` in `make lint`, CI `lint` job, and pre-commit; **premise corrected** (EC-1) |
| **RL-18** | `scripts/packages.sh` single derivation, four consumers, `varco_core/tests/test_repo_package_lists.py` guard; **`scripts/gen_ref_pages.py`'s missing `varco_casbin` fixed** (a fourth copy the row did not know about) |
| **RL-19** | Already done; closed with the pin-parity guard added (Step 2) |

### Rows this plan PARTIALLY closes 🟠

| Row | What lands | What is re-filed |
|---|---|---|
| **RL-14** | G1-G5: `warn_unused_configs`, `warn_redundant_casts`, `no_implicit_reexport`, per-package `check_untyped_defs`, `disallow_subclassing_any`, `disallow_untyped_decorators`, `disallow_any_generics`, `warn_return_any`. Metric corrected (EC-2). Decided-never set recorded in config. | **RL-14b** (below) |

### Rows this plan does NOT touch, by design

| Row | Disposition |
|---|---|
| **RL-16** | Stays open as a standing decision. Step 3 adds the missing trigger quantity (≥30 nightly runs, ≤1 non-code failure). `chaos` is never a promotion candidate. |

### New rows to FILE

| ID | Row | Severity | Complexity |
|---|---|---|---|
| **RL-14b** | **mypy annotation-enforcement tier** — `disallow_incomplete_defs` then `disallow_untyped_defs`, per-package via `[[tool.mypy.overrides]]`, deleting each package's override block as it goes global. Brief 001 §Ramp Order tier 4: "High effort … per-package ramp recommended". Completion criterion: the `[[tool.mypy.overrides]]` section is empty and both flags are global. | 🟡 should | L |
| **MCP-v2** | **Migrate off `mcp` v1.x** — v1.29.1 is the last v1 release and the branch is maintenance-only (brief 003 §"Version/compatibility notes"). Scope already mapped by brief 003 §"Options compared" row 3: low-level decorators removed → constructor-based `Server(list_tools_handler=…, call_tool_handler=…)`, handler shape `async (ctx, params) -> result`, `inputSchema` → `input_schema`, stateless protocol. Blocked on nothing; not urgent. | 🟡 should | M |
| **MCP-761** | **Watch SDK issue #761** (`input_schema=` on the high-level server, open since May 2025). If it lands, revert §KI-11's low-level detour — brief 003 §"Librarian's note" item 3 says the return trip is small. | 🟢 nice | S |
| **BEANIE-CFG** | **`BeanieConfig` and `BeanieSettings` are near-duplicate value objects** — surfaced by KI-10's fix, which now maps one onto the other field-for-field. Collapsing them deletes exported public API (`varco_beanie/__init__.py:46`) and belongs in the **RL-8 API-surface audit**, before the 3.0.0 version freeze. | 🟡 should | S |

### Evidence corrections to write back

| Row | Correction |
|---|---|
| **RL-17** | Rationale cell's "unmeasured whole-tree churn across 439+ source files" → replace with EC-1's measurement: 0 `.py` files, 1107 already formatted. |
| **RL-14** | Rationale cell's "`rg -c 'type: ignore' …` (currently 219)" → replace with EC-2: that command yields **327** across 114 files; **219 was RL-6's mypy-error baseline**, a different quantity. Record both metrics (M1/M2) with their distinct roles. |
| **RL-15** | Append EC-3's finding: zero bare `{member}` interpolations exist in-tree; every site already uses `.value` or `!r`. Brief 002's three wire-format rows do not fire inside varco. |
| **KI-10** | Row names the class `BeanieApp`; the real name is **`BeanieFastrestApp`** (`bootstrap.py:115`). Also record: `grep` across every `*.py` finds it in **source only, no test file** — the "not exercised by any test" claim holds in its strongest form. |
| **RL-18** | Row lists three copies; there are **four** — `scripts/gen_ref_pages.py:27-37`, which was already drifted (missing `varco_casbin`, so `make docs` never rendered it). |
| **KI-11** | If Step 25 finds `uv.lock` resolving `mcp` to 2.x, record that the xfail's `reason=` string described a v1-specific defect while the venv held v2 — a third evidence correction. |
