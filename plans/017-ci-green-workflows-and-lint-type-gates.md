# Plan 017 — CI green: GitHub Actions workflows + workspace lint/type gates

BACKLOG.md **Phase 2 — CI green**, items RL-5 and RL-6 (`BACKLOG.md:62-71`).

## Goal

After this plan, every push and pull request to `main` runs a live GitHub Actions gate that
lints (ruff), type-checks (mypy) and unit-tests all **eleven** workspace members across Python
3.12 and 3.13, ending in one aggregate `all-green` job suitable as the sole branch-protection
required check. `ruff` and `mypy` are version-pinned dev dependencies with real root-level
`[tool.ruff]` / `[tool.mypy]` configuration, and both gates are **green on the commit that
introduces them**. A separate `integration.yml` runs the existing testcontainers suite
(`scripts/integration_tests.sh`) on main + nightly + manual dispatch. CI and `make` invoke the
same entry points.

## Non-goals

- **No `publish.yml`, trusted publishing, PEP 740 attestations, `dependabot.yml`, or OpenSSF
  Scorecard.** That is RL-10, Phase 5. `.github/workflows/publish.yml` stays exactly as it is
  (154 lines, fully commented) — this plan does not touch the file. See *Hands off to Phase 5*.
- **No broad ruff ruleset.** A wide/unconstrained select measures ~1596 errors (`I001` 530,
  `RUF100` 240, `PLC0415` 143, `UP037` 129, `RUF022` 107, `BLE001` 104). Phase 2 adopts
  providify's exact four-rule select only. `PLC0415` in particular would fight this repo's
  deliberate deferred-import idiom, and `RUF100` would be a 240-site cleanup with no gate value
  until `warn_unused_ignores`-style hygiene is in place.
- **No mypy strictness ramp.** `disallow_untyped_defs`, `check_untyped_defs`,
  `disallow_any_generics`, `disallow_incomplete_defs`, `no_implicit_reexport`,
  `warn_return_any` are all explicitly **deferred** (Design §RL-6-mypy) with a BACKLOG row.
- **No `ruff format` sweep and no `ruff format --check` gate.** Formatting is unmeasured churn
  across 439 source files immediately before the RL-8 API-surface audit.
- **No dependency migrations.** `aiomcache` (abandoned upstream) and Motor (EOL 2026-05-14, i.e.
  already past) are flagged by research 002 §4. If either blocks the 3.13 leg, Phase 2's answer
  is a skip marker + a BACKLOG row, never a driver swap.
- **No RT reliability work** (Phase 3). This plan *runs* the existing integration suite; it does
  not add coverage to it. RT2/RT3/RT4/RT5/RT7/RT8/RT9 are untouched.
- **No version bumps or classifier changes.** Adding `Programming Language :: Python :: 3.13`
  to ten `pyproject.toml`s belongs to RL-9/RL-13, which are already editing all ten.
- **No mypy over `tests/`.** The 117-error baseline was measured on source directories only;
  gating tests would add an unmeasured tail on the same commit.

---

## Corrections to BACKLOG.md's own evidence

RL-5 and RL-6 both cite figures that do not survive contact with the tree. State these in the
BACKLOG update (Step 24) so the register is not trusted on stale numbers — the same discipline
`UPSTREAM-GAPS.md`'s U-8 lesson imposes.

| BACKLOG claim | Reality |
|---|---|
| "`test.yml` is 81/92 lines commented, `integration.yml` 169/200, `publish.yml` 134/154" | All three are **100% commented — zero live lines**. Totals are 92 / 200 / 154. There is no partially-live workflow; nothing runs at all. |
| "All nine distributed packages already ship `py.typed`" | There are **ten** distributed packages and `py.typed` exists in **nine** of them. `varco_nats/varco_nats/py.typed` is **absent**, while `varco_nats/pyproject.toml:19` declares `"Typing :: Typed"`. The PEP 561 promise is made in metadata and unfulfilled on disk. |
| (implicit) workspace has 9–10 members | `pyproject.toml:10-22` lists **11** members — ten distributed packages plus `examples`. |
| (implicit) `make lint` / `make type-check` cover the workspace | `Makefile:30-39`'s `PACKAGES` list has **nine** entries and **omits `varco_casbin`**. `make lint`, `make format`, `make type-check`, `make test`, `make build` and `make publish` have all silently skipped `varco_casbin` — and none of them ever covered `examples` or `testkit`. |
| "both currently run on defaults" | Stronger than that: **neither tool is declared as a dependency anywhere**. `Makefile:56` uses `uvx ruff` (ephemeral, unpinned); `Makefile:57` uses `uv run mypy`, which **fails today** because mypy is not in the venv. There is no `[tool.ruff]` or `[tool.mypy]` in any `pyproject.toml` in the repo. |

Two live comments also become false and must be corrected in the same commit as the workflows:
`Makefile:11-18` and `scripts/integration_tests.sh:6-9` both assert "nothing here runs in CI, by
design".

---

## Design

### §RL-5-shape — workflow structure: **hybrid — one lint job + a 2-leg Python matrix + aggregate** ✅

Research 002 §5 is explicit: *"Avoid per-package jobs (would require 11 separate matrix runs per
test type). Cache hits improve when there are fewer job starts."* It recommends
lint-type-unit / integration / aggregate. The dead `test.yml`'s own DESIGN block argues the
opposite (per-package matrix, "package failures are isolated"). Both arguments are real; they
are reconcilable.

```
test.yml
  ┌─ lint ────────────────── ubuntu-latest, Python 3.12 only
  │    ruff check .          (version-independent: mypy pins python_version = "3.12")
  │    mypy <10 src dirs>
  │
  ├─ unit ────────────────── matrix: python-version [3.12, 3.13], fail-fast: false
  │    scripts/unit_tests.sh (runs ALL 11 members, accumulates failures, one summary)
  │
  └─ all-green ───────────── needs: [lint, unit], if: always()
       exits 1 unless every needs.*.result == 'success'
       ← the ONLY required check in branch protection

integration.yml   (separate workflow, NOT a required check)
  └─ integration ─────────── push:main + schedule(nightly) + workflow_dispatch
       make integration-test-clean
```

**The per-package failure-isolation argument is honoured without per-package jobs.** The dead
workflow bought isolation by paying 8 job startups; the same isolation comes free from making
the unit runner *accumulate* failures instead of aborting on the first one — exactly what
`scripts/integration_tests.sh:148-236` already does for integration. Today `Makefile:115-118`
does `|| exit 1`, so the first red package hides the other ten. A sibling
`scripts/unit_tests.sh` fixes that for `make` and for CI simultaneously (Design §RL-5-parity).

- ✅ 3 jobs + aggregate instead of 16–22; one `uv sync` per Python version feeds all 11 members
  (research 002 §5).
- ✅ Full per-package picture on every run, from the script's summary block.
- ✅ mypy runs once, not 2×11 times — it is version-independent given a pinned `python_version`.
- ❌ A single red package still turns the whole `unit` leg red (no green checkmark *per package*
  in the GitHub UI). Accepted: the script's summary names the failing packages in the log.
- ❌ Slightly longer wall time than a maximally-parallel fan-out. Accepted per research 002 §5.

**Rejected — providify's single unified job** (`/home/edoardo/projects/providify/.github/workflows/ci.yml`,
33 lines: matrix 3.12/3.13, `uv sync --locked --all-extras`, `ruff check .`, `pytest`). ✅
Minimal, proven, the template BACKLOG names. ❌ providify is *one* package with *one* test root;
varco needs eleven `cd`-into-the-member pytest invocations (each member's own
`[tool.pytest.ini_options]` supplies `asyncio_mode`, `testpaths`, `pythonpath = ["../testkit"]`
— see `scripts/integration_tests.sh:163-174` for why running from the root breaks collection).
✅ Its *shape* is still adopted wholesale: matrix values, `fail-fast: false`, `enable-cache`,
SHA pinning, and its `[tool.ruff]` config verbatim.

**Rejected — per-package matrix (the dead `test.yml`)** ✅ Per-package green checkmarks; failure
isolation in the UI. ❌ 8 packages × 2 Pythons = 16 job startups, each re-resolving the cache,
against research 002 §5's explicit guidance; and its list was already stale (8 of 11 members —
no `varco_nats`, no `varco_casbin`, no `examples`), which is precisely the drift a hard-coded
job list invites. Rejected.

**Aggregate semantics (research 002 §5).** A job listed in `needs` that fails *or is skipped*
skips its dependents unless the dependent carries `if: always()`. So `all-green` must have
`if: always()` **and** must assert on `needs.<job>.result` explicitly — `success()` alone would
pass on a skipped upstream. Required-check configuration lists `all-green` only, never
individual matrix legs, because a skipped matrix leg leaves a required check permanently pending
(research 002 §5, "Troubleshooting required status checks").

`integration.yml` is deliberately **outside** the aggregate. If it were a `needs:` of
`all-green`, its `schedule`/`push`-only triggers would make it skipped on every PR, forcing
`all-green` to accept `skipped` for that job — which then also accepts a genuinely skipped
integration run on main. Two workflows, one required check, no ambiguity.

### §RL-5-integration — testcontainers-only, no `services:` blocks ✅

**The dead `integration.yml` is obsolete, not merely disabled.** It declares
`services:` blocks (`bitnami/kafka:3.7` KRaft, `redis:7-alpine`, `mongo:7`,
`postgres:16-alpine`) and injects **bare** env vars: `KAFKA_BOOTSTRAP_SERVERS`,
`REDIS_URL`, `MONGODB_URL`, `DATABASE_URL` (uncommented copy, lines 81/120/158/200). Since it
was written, the repo moved to session-scoped testcontainers fixtures with a deliberately
namespaced override contract: `varco_kafka/tests/conftest.py:45` reads `VARCO_TEST_KAFKA_URL`
and nothing else; the nine conftests honour only `VARCO_TEST_{REDIS,KAFKA,NATS,MONGO,POSTGRES,MEMCACHED}_URL`.
CLAUDE.md §Test Conventions states bare names are **never** honoured, specifically so a stray
`DATABASE_URL` cannot point destructive schema-create/drop tests at a developer's own database.

Resurrecting it verbatim would boot four containers the tests silently ignore while starting
their own — double cost, zero benefit — and would cover 4 packages where
`scripts/integration_tests.sh:91,98` covers **nine** plus `examples/00-full-stack-post-api`.

**Chosen — (a) testcontainers-only.** CI runs `make integration-test-clean`, which is
`scripts/integration_tests.sh` with every `VARCO_TEST_*_URL` name explicitly unset
(`Makefile:139-148`). No `services:` block anywhere.

- ✅ Research 005's direct recommendation: *"Testcontainers is the fit — varco tests 6 distinct
  backends … and needs per-test cleanup, fixture-driven backend selection, and zero workflow YAML
  brittleness"*; *"Docker is pre-installed on GitHub Actions `ubuntu-latest` runners — no extra
  `services:` block needed"*; and the Librarian's Note: *"Uncomment the GitHub Actions workflow,
  add one test job … and `pytest -m integration`. No Docker Compose file needed for CI."*
- ✅ Coverage goes 4 → 9 packages + the example suite, for free, and never drifts again: the
  package list lives in one place (`scripts/integration_tests.sh:91`), not duplicated in YAML.
- ✅ CI is byte-identical to what a developer runs locally (§RL-5-parity).
- ✅ Every CI run is a genuine **clean-room** run; the script's own summary says so truthfully.
- ✅ Removes the fragile Kafka KRaft `advertised.listeners: localhost:9092` hand-configuration
  (uncommented copy, line 57) — `testcontainers.kafka.KafkaContainer` owns that.
- ❌ Container startup is serialized inside the job rather than overlapped with job setup;
  research 005's comparison table puts testcontainers at ~30 s–2 m vs ~30 s–1 m for `services:`.
  Accepted — this job is nightly/main-only, not on the PR critical path.
- ❌ Docker image layers are not cached between runs by default. Accepted; not worth a
  `docker save`/`actions/cache` dance in Phase 2.

**Rejected — (b) `services:` blocks wired through `VARCO_TEST_<SERVICE>_URL`.** ✅ Fastest
startup; containers boot in parallel with checkout; no double-start (the override *is* honoured,
so the fixture skips its container). ❌ **Every CI run would then report "NOT a clean-room run"**
(`scripts/integration_tests.sh:79-84,226-231`) — permanently disarming the one signal that
distinguishes a fresh-broker run from a run against pre-existing state, on the runs that matter
most. ❌ Six services do not decompose into one job's `services:` block without either one
enormous job or the old file's per-package fan-out, which is how it got stale in the first place.
❌ Duplicates the package/service topology into YAML, guaranteeing drift from
`ALL_INTEGRATION_PACKAGES`. Rejected.

**Rejected — (c) hybrid** (services for the cheap singles, testcontainers for Kafka/Mongo). ✅
Best-of-both startup time. ❌ Two mechanisms, two failure modes, and the local/CI parity argument
— the main reason CI is worth having at all — collapses immediately. Rejected.

### §RL-5-triggers — integration on main + nightly + dispatch, not on every PR

The dead file used `paths:` filters over four package directories. With one job covering nine
packages, per-package path filtering no longer maps; and research 002 §5 warns that a
path-skipped job skips its dependents. Triggers become `push: [main]`, `schedule` (nightly), and
`workflow_dispatch`.

- ✅ PR feedback stays fast; Actions minutes bounded; Docker churn off the hot path.
- ❌ A PR can break integration and only nightly catches it. Accepted for Phase 2 and stated in
  Risks; Phase 3's RT items are the place to revisit.

### §RL-5-pinning — adopt SHA pinning **now**, not in RL-10 ✅

RL-10 explicitly owns "all actions pinned by commit SHA". Pull it forward anyway:

- ✅ **Floating tags are not available for `setup-uv`.** Research 002 §1: *"v8.0.0 (2026,
  security): Eliminated moving major/minor tags (`@v8` no longer works); requires pinning to
  specific patch versions."* A version must be chosen regardless; choosing a SHA costs one extra
  lookup per action.
- ✅ Four `uses:` lines. Writing them floating guarantees rework of every one of them in RL-10.
- ✅ providify already does exactly this (`actions/checkout@…# v7`, `astral-sh/setup-uv@…# v10.0.1`).
- ❌ Manual bumps until `dependabot.yml` lands in RL-10. Accepted — dependabot understands the
  `SHA # vN` form and will open the bump PRs the moment it exists.

Also set workflow-level `permissions: contents: read` (least privilege). It is one line, it is
what Scorecard will check for in RL-10, and it is part of *writing* a workflow rather than
hardening one.

### §RL-5-py313 — run the 3.13 leg, pre-commit to a decision rule

Research 002 §3: *"A `uv.lock` resolved under Python 3.12 can be used with `--locked` under
Python 3.13 — the lockfile pins transitive versions independently of the interpreter. No special
flag needed; `UV_PYTHON` takes precedence."* So the matrix needs no re-lock and no
`requires-python` change (all 11 members are already `>=3.12`).

Research 002 §4 clears aiokafka, beanie, casbin, nats-py, sqlalchemy+asyncpg for 3.13, and flags
two: **aiomcache** (unmaintained, 3.13 unverified) and **Motor** (EOL 2026-05-14 — already
past). Neither is investigated here (Non-goal). Instead Step 12 *measures* 3.13 locally before
the matrix is written, and the plan pre-commits to the response:

| Step 12 outcome | Phase 2 response |
|---|---|
| Green | Matrix `[3.12, 3.13]`. Done. |
| Isolated failure traceable to an abandoned/EOL dependency | `@pytest.mark.skipif(sys.version_info >= (3, 13), reason="BUG: <dep> …")` on the specific tests + one BACKLOG row — mirroring the repo's existing "conformance failure → `xfail(strict=True)` + BACKLOG row, never an in-place production fix" rule. Matrix stays `[3.12, 3.13]`. |
| Broadly red | Matrix drops to `[3.12]` for Phase 2 + a BACKLOG row naming the blocker. **A 3.12-only gate that is green beats a two-version gate that is red** — RL-5's deliverable is "CI green", not "CI comprehensive". |

`continue-on-error: true` on the 3.13 leg is **rejected**: a leg that cannot fail is theatre, and
it would silently satisfy `all-green`.

### §RL-6-tooling — ruff and mypy become pinned dev dependencies

Research 002 §2: the documented CI pattern for a workspace is
`uv sync --locked --all-packages --all-extras`. For that to install the linters, they must be
*declared*. Add to the **root** `pyproject.toml` (which is a virtual workspace root — no
`[project]` table — and whose `dev` group is what already delivers `asyncpg`):

```toml
[dependency-groups]
lint = [
    "ruff==0.16.4",   # exact pin: a ruff release must never turn CI red overnight
    "mypy==2.3.1",
]
dev = [
    "asyncpg>=0.31.0",
    { include-group = "lint" },   # PEP 735 — `uv sync` installs lint tooling by default
]
```

Exact `==` pins rather than `>=`: a new ruff minor routinely adds rules and re-classifies
fixes, and a new mypy point release routinely finds new errors. With `>=`, CI goes red on a
commit that changed nothing. ❌ The cost is manual bumps; RL-10's dependabot will automate them.

`Makefile:56-57` changes from `RUFF := uvx ruff` / `MYPY := uv run mypy` to `RUFF := uv run ruff`
/ `MYPY := uv run mypy`. This is the whole point: `uvx` resolves the newest ruff at whatever
moment a developer runs it, so a local green says nothing about CI. After the change both read
the same pin from `uv.lock`.

### §RL-6-ruff — providify's config verbatim + a mechanical, per-rule autofix sweep ✅

Measured baselines:

| Config | Errors | Auto-fixable |
|---|---|---|
| ruff's own defaults (`E4,E7,E9,F`) | 9 | — |
| **providify's config ported** (`select E,F,I,UP` / `ignore E501,UP046,UP047` / `line-length 100` / `target-version py312`) | **987** | **969** |
| Broad/unconstrained | ~1596 | — |

Adopt providify's config verbatim, including its explanatory comment that the `UP046`/`UP047`
ignore is deferred PEP 695 generic-syntax migration, **not** silent drift. Rejecting the
9-error default set: it catches syntax errors and undefined names and essentially nothing else —
a gate that asserts nothing is theatre.

**Green on the same commit is reached by an autofix sweep, split per rule.** 969 of 987 are
mechanical, dominated by `I001` (import sorting) and `UP037` (quoted annotations). ~18 need
hand fixes.

**The sweep must be one commit per rule family, each followed by the full unit sweep**, because
two of the fixes touch things this repo is documented to be sensitive about:

- `UP037` **removes quotes from annotations**. That direction is *aligned* with two documented
  pitfalls ("Quoted `@Provider` return annotation", "Quoted `TypeAlias` used in an injected
  annotation") and with providify's runtime annotation resolution. But providify, pydantic and
  dataclass machinery **do** evaluate annotations at runtime, so an unquoted name that is only
  imported under `TYPE_CHECKING` is a live `NameError`. `from __future__ import annotations` (a
  documented house rule) makes this safe — but the rule is not machine-verified anywhere.
- `I001` **reorders imports**, and import order can matter at the edge of a circular-import
  graph — of which this repo has several (documented `TYPE_CHECKING` guards in `consumer.py` ↔
  `dlq.py`).

Per-rule commits mean a red sweep localizes to one rule instead of 969 changes. Add each sweep
commit to a new `.git-blame-ignore-revs` so `git blame` stays useful through the RL-8 audit.

**Scope: whole repo (`ruff check .`), not just source dirs.** `Makefile:97` currently lints
`$(_SRC_DIRS)` = `pkg/pkg` only, which skips every `tests/`, `testkit/`, `examples/` and
`scripts/` file — *and* skips `varco_casbin` entirely. providify runs `ruff check .`. ⚠️ The 987
figure's exact path scope is **unverified** (Risks); Step 3 re-measures both scopes before the
config lands, and the decision rule is: adopt whole-repo unless the test/testkit tail is not
also ≥95% auto-fixable, in which case add a `per-file-ignores` entry for `tests/**` naming the
specific rules — never a blanket `exclude`.

**Rejected — `ruff format` + `ruff format --check` gate.** ✅ Ends all formatting debate. ❌
Unmeasured whole-tree churn on top of 969 autofixes, immediately before RL-8 reads the API
surface; and `E501` is ignored anyway, so nothing currently depends on the formatter. Deferred.

### §RL-6-mypy — defaults + monorepo plumbing, granular `# type: ignore[code]`, no baseline tool ✅

Measured baseline: `mypy --ignore-missing-imports --no-site-packages <pkg>/<pkg>`, default
strictness, 439 source files → **117 errors**: varco_fastapi 50 (16 files), varco_core 38 (23
files), varco_beanie 14 (10), varco_sa 7 (4), varco_redis 3, varco_nats 2, varco_casbin 2,
varco_kafka 1, varco_memcached 0, varco_ws 0.

Config landing at root:

```toml
[tool.mypy]
python_version = "3.12"
explicit_package_bases = true    # research 003 §4 — mandatory for a 10-package monorepo
namespace_packages = true
ignore_missing_imports = true    # research 003 §2 — unblocks untyped third-party
warn_unused_ignores = true       # research 003 §5 — "low churn at first, high value over time"
mypy_path = "varco_core:varco_kafka:varco_nats:varco_redis:varco_sa:varco_beanie:varco_memcached:varco_ws:varco_fastapi:varco_casbin:testkit"
```

`explicit_package_bases = true` is the load-bearing line: research 003 §4 identifies "found
module twice under different names" as the characteristic monorepo failure, caused by mypy
resolving one source file via two import paths, and names this flag as the fix. Given the
`varco_X/varco_X/` layout, each outer `varco_X/` must be on `mypy_path` for the flag to derive
`varco_core.event` rather than `varco_core.varco_core.event`. ⚠️ The exact `mypy_path` form is
unverified (Risks) — Step 15 is where it is empirically settled.

Mypy 2.0's new defaults (`--local-partial-types`, `--strict-bytes`, per research 003 §1) are
**already** in the 117-error measurement, since it was taken with mypy 2.3.1. They are not an
additional surprise.

**Reaching green on the same commit — granular suppressions with a fix-first rule.** For each of
the 117: fix it if the fix is trivially local and obviously correct (a missing return
annotation, an absent `| None`); otherwise append `# type: ignore[<code>]` — always with the
specific error code, never a bare `# type: ignore`. `warn_unused_ignores = true` from day one
means each suppression fails loudly the moment it becomes unnecessary, so the debt is
self-cleaning rather than rotting.

- ✅ The gate then genuinely asserts **"no new type error, anywhere, in any file"** — every line
  of every module stays checked; only 117 named lines are exempt.
- ✅ Zero third-party tooling, zero new files, upstream-documented mechanisms only.
- ✅ The suppression list *is* the ramp backlog: `rg -c 'type: ignore' varco_*/varco_*` is the
  progress metric for the deferred strictness work.
- ❌ ~117 line edits, and 117 `# type: ignore` comments in a public 3.0.0 release tree.
- ❌ A suppressed line stops being checked for *that* code, so a genuine regression at that exact
  line stays invisible. Accepted; it is the narrowest possible blind spot of the four options.

**Rejected — fix all 117.** ✅ No debt at all, the honest end state. ❌ 117 semantic changes
across ten packages (50 of them in `varco_fastapi`) is not complexity **S**, and each is a
potential behaviour change landing immediately before RL-8 freezes the API. The right time to
burn this down is the strictness ramp, one package at a time. Rejected.

**Rejected — `mypy-baseline`.** ✅ Zero source churn; gates on new errors only; the tool research
003 §2 names. ❌ Explicitly *not* upstream mypy (research 003 §2: *"Mypy's official documentation
does not include a dedicated 'baseline' … feature"*); adds a third-party dev dependency and a
generated baseline file to the release tree; research 003's own Evidence Gaps admit no worked
CI integration exists for it. For 117 errors the granular-ignore route needs no new tool at all.
Rejected.

**Rejected — per-module `[[tool.mypy.overrides]] ignore_errors = true`.** ✅ Pure config, ~30
lines, zero source churn, fastest to green. ❌ Blinds *entire modules* — the 23 varco_core files
holding 38 errors would stop being checked wholesale, so a brand-new type error in
`varco_core/service/base.py` would never surface. That is materially worse than 117 pinpoint
suppressions and only marginally cheaper. Rejected.

**Deferred strictness flags, with research 003 §5's cost characterization** (record as one
BACKLOG row, do not enable):

| Flag | Cost (research 003 §5) | Why deferred |
|---|---|---|
| `disallow_untyped_defs` | **High** — "often 500+ locations" | Largest single jump; belongs to a dedicated ramp. |
| `check_untyped_defs` | Medium-high — "exposes type mismatches inside functions that were previously unchecked" | Would invalidate the measured 117 baseline entirely. |
| `disallow_any_generics` | Medium — bare `list`/`dict`/`tuple` | Cheap-ish; still unmeasured. |
| `no_implicit_reexport` | Medium — needs `__all__` everywhere | The one research 003 §3 says matters *most* for a `py.typed` library ("downstream strict-mode consumers will fail with no-reexport errors"), and the one whose blast radius across ten `__init__.py` files is least predictable. ⚠️ Whether the packages already define `__all__` is unverified. |
| `warn_return_any` | Medium | Real unsoundness catcher; unmeasured. |
| `disallow_untyped_calls` / `disallow_any_unimported` / `disallow_any_expr` | High | Research 003 §5 recommends skipping these outright ("saves ~20–30% effort"). |

Not deferred, adopted now: `warn_unused_ignores` — research 003 §5 rates it "low churn at first,
high value over time", and it is load-bearing for the suppression strategy above.

**Scope: the ten `varco_X/varco_X` source directories**, invoked in **one** mypy call
(research 003 §4: *"invoke mypy once over all source directories, not per-package"*). Not tests,
not `examples`, not `testkit` — all unmeasured. `Makefile:108` keeps its `PKG=` narrowing for
local iteration, and diverges from CI only in that CI always passes all ten (§RL-5-parity).

`.mypy_cache` archiving in CI (research 003 §4) is **not** adopted in Phase 2: 439 files is small,
and research 003's own Evidence Gaps note no benchmark exists for varco's scale. One less moving
part.

### §RL-6-pytyped — `varco_nats/varco_nats/py.typed` is fixed here, in RL-6 ✅

It is one empty file, and RL-6's own stated rationale is *"All nine distributed packages already
ship `py.typed`, which is a promise the gate should actually back"* — a premise that is false
for `varco_nats`, whose `pyproject.toml:19` nonetheless advertises `"Typing :: Typed"`. Shipping
a first stable release where one of ten packages silently degrades every downstream annotation to
`Any` (PEP 561, research 003 §3) while claiming otherwise in its classifiers is a defect, and the
commit that gives the type promise teeth is exactly where it belongs.

Hatchling ships it automatically: `varco_nats/pyproject.toml:64-65` is
`packages = ["varco_nats"]`, which includes every file in that directory — no artifact
declaration needed. Step 17 verifies by building the wheel and listing its contents rather than
assuming.

**Rejected — defer to RL-9/RL-13** (which are already editing all ten `pyproject.toml`s). ✅
Batches metadata work. ❌ The file is not metadata, costs nothing, and leaving a known-broken
PEP 561 promise in the tree for two more phases while adding a type gate that cannot see it is
indefensible. Rejected.

### §RL-5-parity — where `make` and CI agree, and where they must not

CI invokes the same entry points a developer runs, so a green CI means a green desk.

| Concern | Local (`make`) | CI | Divergence rationale |
|---|---|---|---|
| Install | `uv sync --all-packages --all-extras` | `uv sync --locked --all-packages --all-extras` | **Must diverge.** Research 002 §2: `--locked` *"Fails if lockfile is not up-to-date … Recommended for CI"*; plain `uv sync` *"Automatically updates lockfile … Not recommended for CI"*. Locally the auto-update is the ergonomic default; in CI a stale lock must be a hard failure. |
| Lint | `make lint` → `uv run ruff check .` | same command | Identical. |
| Type-check | `make type-check` → `uv run mypy <src dirs>` | same command, always all ten | Local keeps `PKG=` narrowing for iteration speed; CI never narrows. |
| Unit tests | `make test` → `scripts/unit_tests.sh` | same script | Identical. |
| Integration | `make integration-test-clean` | same target | Identical — and clean-room, per §RL-5-integration. |
| Python version | whatever is on the developer's path | matrix 3.12 + 3.13 | **Must diverge.** Nobody runs two interpreters locally by default. |

`scripts/unit_tests.sh` is the one new file. It mirrors `scripts/integration_tests.sh`'s proven
shape: a package array, `cd` into each member before invoking pytest (mandatory — see that
script's DESIGN block at `:163-174`), accumulate pass/fail/skip, print a summary, exit 1 if any
failed. It also fixes three live defects at once: `varco_casbin` and `examples` were never in
`Makefile`'s `PACKAGES`, and the first failure aborted the rest.

**Rejected — keep the `Makefile` `foreach` loop and have CI call `make test`.** ✅ No new file.
❌ `Makefile:115-118`'s `|| exit 1` aborts on the first red package, which is exactly the
failure-isolation property §RL-5-shape depends on to justify not fanning out per-package. Adding
accumulate-and-summarize logic to a Make recipe in shell-inside-`foreach` is far worse than a
script. Rejected.

---

## Steps

TDD-ordered where a behaviour is being changed. RL-6's "test" for a lint/type gate is the gate
itself run locally at zero errors — which is why the measurement steps are written explicitly
rather than a test being invented. **RL-6 lands before RL-5**: a workflow that runs `ruff` and
`mypy` must not be pushed before those commands are green, or the first CI run is red by
construction.

### Phase A — tooling declaration (RL-6, gates everything)

1. [ ] `pyproject.toml` (root) — add a `[dependency-groups] lint` group with `ruff==0.16.4` and
       `mypy==2.3.1`, and add `{ include-group = "lint" }` to the existing `dev` group
       (`:24-27`). Comment the exact-pin rationale (§RL-6-tooling).
2. [ ] `uv sync --all-packages --all-extras`; verify `uv run ruff --version` → `0.16.4` and
       `uv run mypy --version` → `2.3.1`. Commit `uv.lock`.
       ⚠️ Also verify here that `uv run pytest varco_core/tests/` still collects — this is the
       step that empirically settles whether `--all-packages` brings each member's own `dev`
       group along (Risks).
3. [ ] **Measurement step, no file changes.** Run the candidate ruff config at both scopes and
       record the numbers in the commit message of Step 4:
       ```bash
       uv run ruff check . --select E,F,I,UP --ignore E501,UP046,UP047 \
           --line-length 100 --target-version py312 --statistics
       uv run ruff check $(ls -d varco_*/varco_*) --select E,F,I,UP \
           --ignore E501,UP046,UP047 --line-length 100 --target-version py312 --statistics
       ```
       Apply §RL-6-ruff's decision rule: whole-repo unless the non-source tail is <95%
       auto-fixable, in which case add a `per-file-ignores` entry naming the specific rules.

### Phase B — ruff config + mechanical sweep (RL-6)

4. [ ] `pyproject.toml` (root) — add `[tool.ruff]` with `line-length = 100`,
       `target-version = "py312"`, `[tool.ruff.lint] select = ["E","F","I","UP"]`,
       `ignore = ["E501","UP046","UP047"]`. Carry providify's comment verbatim: the UP046/UP047
       ignore is deferred PEP 695 migration work, not silent drift. Do **not** add
       `[tool.ruff.format]` (Non-goal).
5. [ ] `uv run ruff check . --select I --fix` → **full unit sweep** → commit
       `chore(lint): ruff I001 import-sort sweep`. Isolated because import order can matter at
       the edge of this repo's `TYPE_CHECKING` circular-import guards (§RL-6-ruff).
6. [ ] `uv run ruff check . --select UP --fix` → **full unit sweep** → commit
       `chore(lint): ruff UP autofix sweep`. Isolated because `UP037` unquotes annotations that
       providify/pydantic evaluate at runtime (§RL-6-ruff). If red, the failing file is missing
       `from __future__ import annotations` — add it rather than re-quoting.
7. [ ] `uv run ruff check . --fix` (remaining `E`/`F`) → **full unit sweep** → commit.
8. [ ] Fix the ~18 non-auto-fixable findings by hand, one logical group per commit. Any finding
       that cannot be fixed without a behaviour change gets a `# noqa: <CODE>` **with an inline
       reason comment** and a BACKLOG row — never a widened `ignore` list.
9. [ ] `.git-blame-ignore-revs` (**new**, repo root) — list the Step 5/6/7 sweep commit SHAs with
       a one-line header explaining the file. Add `[blame] ignoreRevsFile` to the contributor
       instructions in `CLAUDE.md` §Commands.
10. [ ] `Makefile:56` — `RUFF := uvx ruff` → `uv run ruff`. `Makefile:97,102-103` — lint/format
        target the repo root (`.`) instead of `$(_SRC_DIRS)`, honouring `PKG=` only when set.
        `Makefile:30-39` — add `varco_casbin` to `PACKAGES` (it has been silently excluded from
        lint/format/type-check/test/build/publish).
11. [ ] **Verify:** `make lint` → 0 errors. Commit: `RL-6: root [tool.ruff] config + pinned ruff`.

### Phase C — Python 3.13 measurement (RL-5, blocks the matrix)

12. [ ] Run the whole unit suite under 3.13 before any workflow is written:
        ```bash
        uv run --python 3.13 --all-packages --all-extras pytest varco_core/tests/   # etc., all 11
        ```
        Record the outcome and apply §RL-5-py313's pre-committed decision rule. This is a
        measurement step: its only artifacts are either nothing, or skip markers + BACKLOG rows.

### Phase D — mypy config + suppression pass (RL-6)

13. [ ] `varco_nats/varco_nats/py.typed` (**new**) — empty file (§RL-6-pytyped).
14. [ ] **Verify the packaging claim, do not assume it:** `uv build --package varco-nats
        --out-dir /tmp/nats-whl && python -m zipfile -l /tmp/nats-whl/*.whl | grep py.typed`.
        Repeat for one already-correct package (`varco-core`) as the control.
15. [ ] `pyproject.toml` (root) — add `[tool.mypy]` exactly as in §RL-6-mypy. Then settle
        `mypy_path` empirically: run
        `uv run mypy varco_core/varco_core varco_kafka/varco_kafka … varco_casbin/varco_casbin`
        (all ten, one invocation) and confirm **zero** "found module twice under different
        names" errors before looking at any type error. Adjust `mypy_path` until that holds.
16. [ ] Capture the real baseline **in the synced venv** (`--no-site-packages` is *not* used —
        that was the pre-measurement's condition and the real number will differ):
        `uv run mypy <ten src dirs> --show-error-codes 2>&1 | tee /tmp/mypy-baseline.txt`.
        Record the count and per-package split in the Step 19 commit message; if it diverges
        materially from 117, say so in the BACKLOG update (Step 24).
17. [ ] Work through the baseline package-by-package in ascending error count
        (`varco_kafka` 1 → `varco_nats` 2 → `varco_casbin` 2 → `varco_redis` 3 → `varco_sa` 7 →
        `varco_beanie` 14 → `varco_core` 38 → `varco_fastapi` 50), applying §RL-6-mypy's
        fix-first rule: trivially-correct fix, else `# type: ignore[<code>]` with the specific
        code. **One commit per package**, each followed by that package's unit tests
        (`cd <pkg> && uv run pytest tests/`) — a "trivially correct" annotation fix that changes
        a runtime default is exactly what this catches.
18. [ ] `Makefile:108` — confirm `type-check` passes all ten source dirs when `PKG` is unset
        (it follows automatically from the Step 10 `PACKAGES` fix).
19. [ ] **Verify:** `make type-check` → `Success: no issues found in N source files`, with
        `warn_unused_ignores = true` active. Commit:
        `RL-6: root [tool.mypy] config, pinned mypy, varco_nats py.typed`.

### Phase E — unit-test runner (RL-5 prerequisite)

20. [ ] `scripts/unit_tests.sh` (**new**) — model on `scripts/integration_tests.sh`. Package
        array = all ten `varco_*` members; `EXTRA_SUITES=("examples/00-full-stack-post-api:example/tests")`
        with the same `run_from="root"` handling (that script's `:99-116` explains why the
        example suite cannot use `cd`); `cd` into each member otherwise (`:163-174`); accumulate
        PASSED/FAILED/SKIPPED; treat pytest exit code 5 as "no tests, not a failure" (`:193-201`);
        print a summary; exit 1 if any failed. Honour `PYTEST_EXTRA_ARGS`. It must **not** pass
        `-m integration`; integration tests are already skipped by their `VARCO_RUN_INTEGRATION`
        guard (`varco_kafka/tests/conftest.py:40-43`) but pass `-m "not integration"` explicitly
        as belt-and-braces.
21. [ ] `Makefile:113-118` — `test` target delegates to `scripts/unit_tests.sh` (with `$(PKG)`
        forwarded), replacing the `foreach … || exit 1` loop. Update the `Makefile:4-25` header
        block and the `help` target text.
22. [ ] **Verify:** `make test` runs all eleven suites, and a deliberately-broken test in
        `varco_kafka` still lets `varco_fastapi` run and shows up in the summary. Revert the
        deliberate break. Commit: `RL-5: accumulate-and-summarize unit test runner`.

### Phase F — the workflows (RL-5)

23. [ ] Resolve the pinned SHAs (⚠️ cannot be supplied by this plan — no network at planning
        time):
        ```bash
        gh api repos/actions/checkout/git/ref/tags/v5      --jq .object.sha   # confirm current major
        gh api repos/astral-sh/setup-uv/git/ref/tags/v10.0.1 --jq .object.sha
        gh api repos/actions/setup-python/git/ref/tags/v5  --jq .object.sha
        ```
        Cross-check against `/home/edoardo/projects/providify/.github/workflows/ci.yml`, which
        already carries verified `SHA # vN` pairs for `actions/checkout` and `astral-sh/setup-uv`
        — reuse those two verbatim rather than re-deriving them.
24. [ ] `.github/workflows/test.yml` — **replace the file wholesale** (it is 92 lines of 100%
        dead comments; there is nothing to uncomment). Per §RL-5-shape:
        - `name: Tests`; `on: push[main] + pull_request[main]`; the existing `concurrency`
          block (group `${{ github.workflow }}-${{ github.ref }}`, `cancel-in-progress: true`) is
          worth keeping verbatim from the dead file; `permissions: contents: read`.
        - Job `lint`: `ubuntu-latest`, `actions/setup-python` @3.12 → `astral-sh/setup-uv` with
          `enable-cache: true` and `cache-dependency-glob: "uv.lock"` (both **required
          explicitly** in v10+ — research 002 §1) → `uv sync --locked --all-packages --all-extras`
          → `uv run ruff check .` → `uv run mypy <ten src dirs>`.
        - Job `unit`: `strategy: { fail-fast: false, matrix: { python-version: ["3.12","3.13"] } }`
          (per Step 12's outcome), same setup, then `bash scripts/unit_tests.sh`.
        - Job `all-green`: `needs: [lint, unit]`, `if: always()`, a shell step asserting
          `needs.lint.result == 'success' && needs.unit.result == 'success'` — `success()` alone
          is insufficient because a *skipped* upstream must also fail the aggregate
          (research 002 §5).
        - Keep the dead file's DESIGN-comment habit: a header block recording §RL-5-shape's
          ✅/❌ and an inline note that `all-green` is the only required check.
25. [ ] `.github/workflows/integration.yml` — **replace the file wholesale** (200 lines, 100%
        dead, and obsolete per §RL-5-integration). One job, no `services:` blocks, no bare env
        vars: checkout → setup-python 3.12 → setup-uv → `uv sync --locked --all-packages
        --all-extras` → `make integration-test-clean`. Triggers `push: [main]`,
        `schedule` (nightly cron), `workflow_dispatch`; `permissions: contents: read`;
        `timeout-minutes` set generously (nine packages × testcontainers). Header DESIGN block
        records why `services:` blocks were deleted (the `VARCO_TEST_*` override contract) so
        nobody reintroduces them.
26. [ ] `.github/workflows/publish.yml` — **do not touch.** Add nothing, delete nothing. Its
        resurrection is RL-10.
27. [ ] `Makefile:11-18` and `scripts/integration_tests.sh:6-9` — delete/rewrite the "nothing
        here runs in CI, by design" claims and the `BACKLOG.md:50-56` citations. They are now
        false. Replace with a pointer to `.github/workflows/integration.yml` and a note that CI
        uses the **clean-room** entry point.
28. [ ] Commit: `RL-5: live test.yml + integration.yml workflows`. **Push and watch the first
        run.** A red first run is expected to be a workflow-syntax or SHA issue, not a code
        issue — Phases A–E already proved the commands green locally.
29. [ ] After the first green run: configure branch protection on `main` to require **only**
        `All tests passed` (the `all-green` job). ⚠️ Repository-settings change, outside the
        repo tree — record it in `CLAUDE.md` so it is not invisible.

### Phase G — docs and close-out (same commits as the code, per CLAUDE.md)

30. [ ] `CLAUDE.md` §*Commands* — the block currently documents `make lint`/`make type-check`
        as if they work. Update: `uv sync` → `uv sync --all-packages --all-extras`; state that
        ruff/mypy are pinned in the root `lint` dependency-group (so `uvx ruff` must not be used);
        add `make test` now runs `scripts/unit_tests.sh` and reports every package; add the
        `.git-blame-ignore-revs` setup line; add a short "CI" subsection naming the two workflows,
        the three jobs, and `all-green` as the single required check.
31. [ ] `CLAUDE.md` §*Test Conventions* — add that `scripts/integration_tests.sh` now **does**
        run in CI (nightly + main), always via the clean-room entry point, so every CI
        integration run is a genuine clean-room run; and that the `VARCO_TEST_<SERVICE>_URL`
        override contract is why CI uses testcontainers rather than Actions `services:` blocks.
32. [ ] `CLAUDE.md` §*Common Pitfalls* — add one row: **"linting with `uvx ruff`"** → symptom
        "local green, CI red (or vice versa) with no code change" → cause "`uvx` resolves the
        newest ruff at invocation time; CI resolves the pin in `uv.lock`" → fix "`uv run ruff`,
        always; the pin lives in the root `lint` dependency-group".
33. [ ] `README.md` — sweep for `uvx`, for any command block showing `uv sync` alone, and for
        any claim that CI does not exist; add a short badge/CI paragraph.
34. [ ] `CHANGELOG.md` — `## [Unreleased]`: live CI workflows; root ruff/mypy config; ruff and
        mypy pinned as dev dependencies; `varco_nats` now ships `py.typed` (**call this out
        explicitly — it changes what downstream type-checkers see from that package**);
        `varco_casbin` added to the Makefile package list.
35. [ ] `BACKLOG.md` — mark RL-5 and RL-6 done; correct the RL-5 Evidence cell (workflows were
        100% commented, not 81/92 etc.) and the RL-6 Evidence cell (`py.typed` was 9/10, not
        9/9; there are ten packages, eleven members); add rows for: (a) the deferred mypy
        strictness ramp with §RL-6-mypy's per-flag table, (b) any `# noqa`/`skipif`/xfail filed
        in Steps 8/12, (c) integration tests not gating PRs (§RL-5-triggers), (d) `ruff format`
        deferred.
36. [ ] Final: `make lint && make type-check && make test && make build`, then one manual
        `workflow_dispatch` of `integration.yml` to prove the Docker path end-to-end.

---

## Edge cases

| Input / state | Expected behaviour |
|---|---|
| `uv.lock` is stale relative to a changed `pyproject.toml` | CI's `uv sync --locked` fails the `lint` and `unit` jobs immediately, before any test runs (research 002 §2). Local `uv sync` would have silently re-locked. The fix is always `uv lock` + commit. |
| A ruff autofix in Step 5/6/7 breaks a test | The per-rule commit split localizes it to one rule. `UP037` regressions mean a file lacks `from __future__ import annotations` — add the import, do **not** re-quote and do **not** widen `ignore`. |
| A `# type: ignore[code]` becomes unnecessary later | `warn_unused_ignores = true` turns it into a hard mypy error naming the exact line. Delete the comment. This is the intended self-cleaning mechanism, not a nuisance. |
| The 3.13 matrix leg is red | §RL-5-py313's decision table. Never `continue-on-error: true`. |
| `integration.yml` nightly fails | It is **not** a required check; `main` stays mergeable. The failure is a BACKLOG/Phase-3 signal, not a merge blocker. Stated deliberately in §RL-5-triggers. |
| A developer has `VARCO_TEST_REDIS_URL` exported and runs `make integration-test` | Unchanged behaviour: the override is honoured and loudly reported as "NOT a clean-room run" (`scripts/integration_tests.sh:79-84`). CI is unaffected because it calls `integration-test-clean`, which `env -u`s all six names (`Makefile:139-148`). |
| A new workspace member is added later | It must be added to `scripts/unit_tests.sh`'s array and `Makefile`'s `PACKAGES` — the same two-place duplication that let `varco_casbin` go missing. ⚠️ Not solved by this plan; note it in the script header as a known maintenance point. |
| A matrix leg is skipped (e.g. a future `if:` condition) | `all-green` still runs (`if: always()`) and **fails**, because it asserts `result == 'success'` rather than calling `success()` (research 002 §5). |
| `varco_memcached` / `varco_ws` (0 mypy errors) | Nothing to suppress. They are the control group proving the config is not accidentally disabling checks. |

---

## Verification

```bash
cd /home/edoardo/projects/varco

# Phase A
uv sync --all-packages --all-extras
uv run ruff --version    # 0.16.4
uv run mypy --version    # 2.3.1

# Phase B — must reach zero
make lint                                   # uv run ruff check .

# Phase C
uv run --python 3.13 --all-packages --all-extras pytest varco_core/tests/   # ×11

# Phase D — must reach "Success: no issues found"
make type-check
uv build --package varco-nats --out-dir /tmp/nats-whl
python -m zipfile -l /tmp/nats-whl/*.whl | grep py.typed

# Phase E
make test                                   # all 11 suites, summary block, exit 0

# Phase F — after push
gh run watch                                # or the Actions tab
gh workflow run integration.yml             # manual Docker proof

# Phase G — close-out
make lint && make type-check && make test && make build
```

Per-phase pass conditions:

| Phase | Command | Pass condition |
|---|---|---|
| A | `uv run ruff --version`, `uv run mypy --version` | exact pins resolve; `uv run pytest varco_core/tests/` still collects |
| B | `make lint` | **0 errors**, whole repo, config from root `[tool.ruff]` |
| C | `uv run --python 3.13 … pytest` ×11 | green, or a recorded decision per §RL-5-py313 |
| D | `make type-check` | `Success: no issues found in N source files`; zero "found module twice"; `py.typed` present in the `varco-nats` wheel |
| E | `make test` | 11 suites run; a broken package does not abort the rest; exit 1 only if something failed |
| F | first Actions run | `lint` green, both `unit` legs green, `all-green` green; `integration.yml` green on manual dispatch |
| G | `make lint && make type-check && make test && make build` | all green; ten wheels build (now including `varco_casbin`) |

---

## Hands off to Phase 5 (RL-10) — do **not** implement here

Phase 2 makes three Phase-5 items cheaper without touching them:

- **SHA pinning is already done** (§RL-5-pinning). RL-10's "all actions pinned by commit SHA"
  reduces to auditing `publish.yml` and any new `scorecard.yml`.
- **`permissions: contents: read`** is already set on both new workflows — one of the checks
  OpenSSF Scorecard scores.
- **`dependabot.yml`** gets two ready-made ecosystems to watch: `github-actions` (the `SHA # vN`
  pins, which dependabot understands natively) and `uv` (the new exact `ruff==` / `mypy==` pins,
  which is the maintenance cost §RL-6-tooling accepted).
- **`Programming Language :: Python :: 3.13`** classifiers: once Step 12 + the 3.13 matrix leg
  prove it, adding the classifier to all ten `pyproject.toml`s is free work for RL-9/RL-13,
  which are already editing every one of those files.

---

## Risks

- ⚠️ **ASSUMPTION — `uv sync --all-packages` installs each workspace member's own `dev`
  dependency-group.** Research 002 §2 documents `--all-packages` as installing "all 11 members'
  dependencies" and `--all-groups` as "all custom groups", but does not state whether a member's
  `dev` group (e.g. `varco_nats/pyproject.toml:48-58`'s `pytest`, `testcontainers[nats]`) comes
  along. CLAUDE.md claims plain `uv sync` installs "all workspace members + dev deps"; research
  002 §2 says plain root `uv sync` *"does not install workspace members"*. **These two statements
  cannot both be right.** Step 2 settles it empirically. If member dev-groups are excluded, CI
  needs `--all-groups` added and CLAUDE.md's Commands block needs correcting.
- ⚠️ **ASSUMPTION — the 987-error ruff figure's path scope.** Measured as "providify's exact
  config ported to varco" without a recorded path argument. If it was source-only, the whole-repo
  number is higher and the ~18 manual fixes could be materially more. Step 3 re-measures both
  scopes before anything is committed.
- ⚠️ **ASSUMPTION — the 117-error mypy baseline transfers to the real venv.** It was measured
  with `--no-site-packages`, i.e. with **no third-party type information at all**. In the synced
  venv mypy will see pydantic's, SQLAlchemy's and FastAPI's real stubs and plugins; the count
  will change, plausibly upward in the packages that lean hardest on them (`varco_sa`,
  `varco_beanie`, `varco_fastapi`). Step 16 re-measures. If the real figure is materially larger
  (say >250), §RL-6-mypy's granular-suppression choice should be re-litigated against
  `mypy-baseline` before 250 comments are written — that is the tipping point, and it is a
  judgement call this plan cannot pre-make.
- ⚠️ **ASSUMPTION — the `mypy_path` form in §RL-6-mypy is correct.** Research 003 §4 establishes
  *that* `explicit_package_bases = true` plus a correct `mypy_path` is required, but the exact
  colon-separated ten-entry value for the `varco_X/varco_X/` layout is untested. Step 15 exists
  solely to settle it; expect iteration.
- ⚠️ **ASSUMPTION — `UP037` autofixes are runtime-safe because every file has
  `from __future__ import annotations`.** That is a CLAUDE.md house rule, not a machine-checked
  invariant, and providify/pydantic **do** evaluate annotations at runtime. The invariant that
  must hold: **no test may be loosened to make Step 6's sweep pass.** A red sweep means a real
  latent bug in annotation resolution, not a bad rule.
- ⚠️ **ASSUMPTION — the action SHAs.** This plan cannot supply verified commit SHAs (no network
  at planning time). Step 23 must resolve them, preferring the two already verified in
  providify's own `ci.yml`. Research 002 §1's version facts (setup-uv v10.0.1 current; v8+ has
  no moving tags; v10 disables caching by default on sensitive events; `enable-cache` must be
  explicit) are from a brief dated 2026-08-26 and are fresh, but the action's own release page is
  the authority at implementation time.
- ⚠️ **ASSUMPTION — testcontainers works unmodified on GitHub Actions runners for all six
  services.** Research 005 asserts Docker is preinstalled and the socket is usable, and lists no
  varco-specific verification. Its own Evidence Gaps admit *"no benchmarked data"* for varco's
  broker mix. Kafka's advertised-listener resolution and Mongo's replica-set init are the two
  most likely to behave differently on a runner than on a developer's machine. Step 36's manual
  dispatch is the proof; until it passes, `integration.yml` is unproven.
- **117 `# type: ignore` comments ship in the 3.0.0 tree.** Mitigated by `warn_unused_ignores`
  (they cannot rot silently) and by the BACKLOG ramp row, but it is real debt in a public
  release, and a reviewer will notice. The invariant: **every one carries a specific error code**;
  a bare `# type: ignore` is never acceptable.
- **Integration tests do not gate PRs.** A PR can break a broker path and only the nightly run
  catches it. Deliberate (§RL-5-triggers) but genuinely weaker than gating; revisit in Phase 3.
- **The package list is duplicated in three places** after this plan: `Makefile:30-39`,
  `scripts/unit_tests.sh`, and `scripts/integration_tests.sh:91`. That triplication is exactly
  how `varco_casbin` went missing from `make` in the first place. Not solved here (deriving the
  list from `pyproject.toml`'s `[tool.uv.workspace] members` is the obvious fix, and is scope
  creep); flagged in the script headers and in a BACKLOG row.
- **A green `all-green` after Phase F proves the *gates* run, not that they are strict.** Both
  gates land at their weakest defensible setting by design. The honest claim this plan supports
  is "no **new** lint or type error can land"; it is not "varco is type-safe".
