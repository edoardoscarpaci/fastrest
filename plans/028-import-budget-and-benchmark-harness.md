# Plan 028 — Lazy `varco_core` import, an import budget, and a benchmark harness

**Prerequisites: Plans 026 and 027 should have landed first.** Not a hard technical dependency —
P1/P2 are independent of the TLS work — but this plan **rewrites `varco_core/varco_core/__init__.py`
wholesale**, and 026/027 add names near it. Landing 028 first guarantees a painful merge for
whoever goes second. If the cycle is reordered, 028 goes last regardless.

Covers BACKLOG 3.1 rows **P1** (🔴 must, M), **P2** (🟡 should, S–M), and **P3**/**P4**
(🟢 nice, M) as explicitly **P2-gated** phases that may not be implemented until the harness
produces a number.

## Goal

`import varco_core` stops costing 419 ms. A committed, baseline-normalised import budget makes a
regression visible in CI. A CodSpeed benchmark harness exists and comments on PRs, so the two
remaining perf ideas (`slots`, reflection caching) can be argued from measurements instead of
intuition — and if the measurements do not support them, they are dropped, on the record.

## Non-goals

- **No behaviour change.** Every name importable from `varco_core` today remains importable, with
  the same object identity, and every import-time side effect that exists today still happens by
  the time any consumer could observe it (§D-P1-sideeffects).
- **No `__all__` change.** Not one name is added or removed by P1. `scripts/api_surface.py --check`
  must pass **without regenerating the snapshot** — that is the strongest available proof that P1
  is invisible.
- **P3 and P4 are not implemented in this plan unless P2's harness produces a qualifying number.**
  This is a locked decision (`BACKLOG.md:39`, `:82`): "neither may be implemented until P2 can
  measure it". The phases below carry the *gate*, the *threshold* and the *stop rule*, decided in
  advance so they cannot be rationalised after the fact.
- **No PEP 810 (`lazy import`).** Python 3.15; this repo's matrix is 3.12/3.13 (brief 002 §1,
  `BACKLOG.md:83`).
- **No benchmark gate.** P2 is comment-only, forever in this cycle (`BACKLOG.md:71`). Note the
  deliberate asymmetry with the import budget, explained in §D-P1-oq4.
- **No lazy `varco_fastapi`/backend `__init__.py`.** Measured target is `varco_core`. A follow-up
  row, not this plan (§D-P1-scope).

---

## Design

### Phase order

```
P0  P1a  🔴 M  varco_core/__init__.py → PEP 562 lazy, provably equivalent
P1  P1b  🔴 S  scripts/import_budget.py + committed budgets, WARN-ONLY
P2  P1c  🔴 S  flip the budget to a gate after N green CI runs
P3  P2   🟡 M  pytest-codspeed harness + benchmarks/ + bench.yml (comment-only)
P4  P4   🟢 M  ⛔ GATED — reflection caching, only if P2 measures ≥10%
P5  P3   🟢 M  ⛔ GATED — slots sweep, only if P2 measures ≥20% memory
```

P4 sorts before P3 despite the row order because it has a single concrete lead
(`QueryParser._parser`) and a cheaper benchmark; P3 needs a memory-measurement harness P2 does not
otherwise require.

### §D-P1-mechanism — PEP 562 module `__getattr__` + a `TYPE_CHECKING` eager block

`varco_core/varco_core/__init__.py` is 656 lines: ~330 lines of eager `from varco_core.X import
(...)` followed by `__all__` at `:383-655`. There is **no other code in the module** — no logging
setup, no registration, no side effect at module scope. That is what makes the conversion
mechanical.

| ID | Choice | Consequence |
|---|---|---|
| D-P1-mechanism | A `_LAZY: dict[str, str]` mapping every `__all__` name → its defining submodule, a module-level `__getattr__`/`__dir__`, and an `if TYPE_CHECKING:` block holding the **current** eager imports verbatim | Runtime is lazy; mypy, IDEs and `scripts/api_surface.py` all keep working unchanged |

```python
if TYPE_CHECKING:                      # unchanged, verbatim, the current import block
    from varco_core.model import DomainModel
    ...

_LAZY: Final[dict[str, str]] = {"DomainModel": "varco_core.model", ...}

def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module 'varco_core' has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value            # cache — __getattr__ is not consulted again
    return value

def __dir__() -> list[str]:
    return sorted(__all__)
```

**DESIGN: PEP 562, with the eager block kept under `TYPE_CHECKING`**

✅ PEP 562 is Final since 3.7 and is the *shipped* mechanism (brief 002 §1); PEP 690 was rejected
and PEP 810 needs 3.15. There is no third option.
✅ `globals()[name] = value` means the `__getattr__` cost is paid **once per name per process** —
brief 002 §1's "runtime overhead on every lazy access, not suitable for tight loops" caveat does
not apply, because after the first access the name is a normal module global.
✅ The `TYPE_CHECKING` block keeps mypy `strict` (root `pyproject.toml:163`) fully informed; without
it, `__getattr__ -> Any` would silently make every `from varco_core import X` an `Any` and quietly
erase type checking across the repo. **This is the single most important detail of the phase.**
✅ `scripts/api_surface.py` does `getattr(module, name)` for every `__all__` entry
(`scripts/api_surface.py:253-266`) and reads `obj.__module__` for the defining module
(`:162,172`) — a lazy attribute resolves to the identical object, so the snapshot is unchanged and
the CI gate stays green with no regeneration.
✅ PEP 562 is already in this repo: `varco_beanie/varco_beanie/__init__.py:125` and
`varco_beanie/varco_beanie/bootstrap.py:173` (`deprecated_alias`). Not a new pattern here.
❌ An `ImportError` inside a submodule now surfaces at **first attribute access**, not at
`import varco_core`. That is a genuine ergonomic loss (a typo'd optional dependency fails later
and further from its cause). Mitigated by Step 3's test, which resolves **every** name in `__all__`
and would catch a broken submodule in CI on every run.
❌ `from varco_core import *` still materialises everything. Fine — nobody does that in production
code, and it remains correct.

### §D-P1-sideeffects — the real risk, and how it is bounded

Making imports lazy is only safe if no module currently relies on being imported *as a side effect*
of `import varco_core`. Two known families of import-time side effect exist in this repo: framework
metadata registration and DI/serializer registration. `register_framework_metadata()` lives in
`varco_sa` (`varco_sa/varco_sa/metadata.py:55`, called e.g. at
`varco_sa/varco_sa/tenancy/models.py:53`) — **not** in `varco_core`, and `varco_sa` imports its own
modules, so P1 cannot affect it. That must be **verified, not assumed**, along with everything else.

| ID | Choice | Consequence |
|---|---|---|
| D-P1-sideeffects | Before the conversion, enumerate every module-scope statement in `varco_core` that is not a definition, an import or a constant. Any module with a real side effect is **pinned eager** in a documented `_EAGER` list at the top of `__init__.py`, with the reason inline | The optimisation is bounded by evidence; a genuinely side-effecting module keeps its guarantee and costs its import time |

Detection procedure (Step 1, and it is the gating step of the phase):

```bash
# candidate side effects: module-scope calls, registry mutations, decorator applications
rg -n "^[A-Za-z_][A-Za-z_0-9.]*\(" varco_core/varco_core --glob '*.py' | rg -v "^\S+:\d+:(def|class) "
rg -n "^_?[A-Z_]+\s*=\s*\w+\(" varco_core/varco_core --glob '*.py'
```
plus a differential: `sys.modules` after `import varco_core` on `main` vs. after `import varco_core`
+ touching every `__all__` name on the branch. The **set difference must be empty** — any module
imported eagerly today but never reachable through an `__all__` name is exactly a module that was
being imported for its side effect, and it goes in `_EAGER`.

### §D-P1-scope — `varco_core` only

✅ 419 ms vs a 7 ms baseline is the measured number, and it is `varco_core`'s
(`BACKLOG.md:53-57`). Every other package's `__init__` is smaller and, crucially, unmeasured — and
this cycle's own locked posture is "harness + the one measured win" (`BACKLOG.md:39`).
✅ `varco_core` is the package a CLI, a sidecar or a serverless cold start pays for; it is also
what Plans 025-027's watch/TLS subsystem imports, which is the BACKLOG's own argument for P1
serving this cycle's feature work (`BACKLOG.md:70`).
❌ `import varco_fastapi` stays expensive. It pulls FastAPI/Starlette regardless, so the ceiling on
any win there is much lower. Step 8 **measures** it and records the number in the budget file as an
observation with a generous ceiling, so a future plan starts from data.

### §D-P1-oq4 — BACKLOG open question 4: a **hard ceiling on a baseline-normalised delta**, ratcheted in only once

| ID | Choice | Consequence |
|---|---|---|
| D-P1-oq4 | `scripts/import_budget.py` measures `best-of-5` subprocess `-X importtime` for each target **and** for a bare interpreter, and compares the **delta** against a hard per-target ceiling committed in `design/async-performance-patterns/measurements/import-budget.json`. Not a ratchet | Runner-speed-independent, deterministic, reviewable in a diff, and impossible to silently tighten |

**DESIGN: hard number over ratchet — but a *normalised* hard number**

✅ The objection to a hard number is that a slow runner fails it. Subtracting a bare-interpreter
baseline measured **in the same job** removes most of that: the BACKLOG's own measurement is
already expressed that way (419 ms *against a 7 ms baseline*), so the metric is the one already in
use.
✅ A hard number is a single reviewable line in a JSON file. Raising it requires a diff a reviewer
sees, with a commit message justifying it. That is the whole value of a budget.
✅ `best-of-5` in separate subprocesses is `importtime-waterfall`'s own methodology (brief 002 §1),
not an invention.
❌ **A ratchet is rejected** on three grounds: (a) it must rewrite a committed number on every
improving PR, guaranteeing merge conflicts on a file every PR touches; (b) one lucky-fast CI run
permanently lowers the bar and every subsequent honest run fails — an unfixable red with no code
defect behind it; (c) "cannot be gamed by a slow runner" is answered better by baseline
normalisation than by a moving target.
❌ A hard number needs headroom, so it catches *structural* regressions (a new eager top-level
import of pydantic) and not 20% noise. Ceilings are set at **~2× the measured post-P1 delta**,
and the measured value is committed alongside the ceiling in the same JSON, so the headroom is
visible rather than implied.

**The budget is a gate; the benchmarks are not.** The asymmetry is deliberate and must be stated in
CLAUDE.md: import time is a structural property measured in a fresh subprocess with best-of-N,
whose failure mode is "someone added a top-level import" — reproducible and actionable. A
microbenchmark on a shared GitHub runner is neither (brief 002 §5: "GitHub-hosted runner noise is
acknowledged but unquantified"; almost no major project gates on it).

**And even the budget earns its gate.** Step 9 lands `import_budget.py` in **warn-only** mode
(prints, exits 0). Step 11 flips it to failing **only after** at least 10 CI runs across both
matrix legs, with the observed min/max deltas recorded in the JSON file's `observations` array.
That is U-8 evidence discipline applied to our own gate, exactly as `BACKLOG.md:39` demands of the
perf claims themselves.

### §D-P2-harness — CodSpeed, comment-only, never in `all-green`

| ID | Choice | Consequence |
|---|---|---|
| D-P2-harness | `pytest-codspeed` in a new root `bench` dependency-group; benchmarks in a top-level `benchmarks/` directory; `make bench`; a **separate** `.github/workflows/bench.yml` that is never in `test.yml`'s `needs:` and never a required check | Numbers on PRs; zero merge-blocking risk; `all-green` (the only required check) is untouched |

✅ Brief 002 §5: CodSpeed is the 2025/2026 table stakes (pydantic, FastAPI, polars) and its
"simulation" mode runs in an instrumented VM for reproducible timing; comment-not-gate is the
mainstream posture.
✅ A separate workflow keeps `test.yml`'s three-job/`all-green` contract (`test.yml:86-100`)
byte-identical. CLAUDE.md is explicit that `all-green` is the only required check and that adding
others is a footgun.
✅ `pytest-codspeed` is API-compatible with `pytest-benchmark`, so benchmarks are plain pytest
tests locally (`make bench` runs them as tests with no instrumentation).
❌ CodSpeed needs a `CODSPEED_TOKEN` repository secret — an **out-of-repo operator step**, in the
same class as the ten PyPI environments. It is recorded in the runbook (Step 18) rather than
assumed; the workflow must be conditioned so a fork PR (no secret) **skips**, never fails.
❌ Benchmarks that are never gated can rot. Mitigated by running them, uninstrumented, in `make
bench` and by keeping them under `benchmarks/` where `scripts/unit_tests.sh` does not collect them
(so they never slow the unit legs).

Seed benchmark set (all in-process, no Docker, deterministic):

| Benchmark | Path exercised | Why |
|---|---|---|
| `bench_query_parse` | `QueryParser.parse()` on a fixed filter string | P4's direct target |
| `bench_query_ast_build` | `QueryBuilder` → AST → SA compile | P3's direct target |
| `bench_dto_roundtrip` | `CreateDTO`/`ReadDTO` validate + dump | pydantic hot path |
| `bench_service_create` | `AsyncService.create()` over an in-memory repo | the composite path users actually pay |
| `bench_event_publish` | `InMemoryEventBus.publish()` + `drain()` | fan-out cost |
| `bench_cache_get_set` | `InMemoryCache` | cheapest baseline; detects harness drift |
| `bench_import_varco_core` | subprocess import | ties P1 to the same dashboard |

### §D-P3P4-gate — what "P2-gated" means operationally

Neither phase may be implemented on intuition. Both begin with a measurement step that **writes a
file**, and both have a pre-committed threshold and a stop rule:

| Row | Gate | Threshold to proceed | If below threshold |
|---|---|---|---|
| P4 (reflection caching) | `bench_query_parse` measured before/after on a throwaway branch | **≥10%** improvement on the benchmark | Write the measurement to `design/async-performance-patterns/measurements/p4-reflection.md`, close the BACKLOG row as **measured, not worth it**, and stop. That is a successful outcome. |
| P3 (`slots=True`) | A memory measurement (`tracemalloc` peak, or `sys.getsizeof` × allocation count) over `bench_query_ast_build` | **≥20%** per-instance memory reduction on the AST node population | Same: record and close the row. |

✅ Locked (`BACKLOG.md:39`, `:82`): "This is U-8 evidence discipline applied to our own perf
claims." Brief 002 §3 says outright that "plan at registration, not call time" has **no published
micro-benchmarks** — the claim varco would be acting on is unsupported, which is exactly why P4 is
🟢 and gated.
❌ Two 🟢 rows may ship as documentation instead of code. That is the intended outcome when the
number says so, and the plan must make writing that document feel like completing the work, not
abandoning it.

**P4's concrete lead and its hidden correctness question.** `QueryParser._parser` is a
`@cached_property` (`varco_core/varco_core/query/parser.py:80-98`), so a per-request `QueryParser`
rebuilds the Lark grammar every time; `_grammar_text` is already a `ClassVar` read once
(`:78`). But the cached object is `Lark(..., transformer=QueryTransformer())` — the parser owns a
transformer instance, and the existing comment at `:92-93` says a fresh one is used to "avoid any
accidental state sharing between parser instances". **Sharing one `Lark` across requests therefore
shares that transformer.** So P4's step order is: (1) prove `QueryTransformer` is stateless, or
(2) cache a **transformer-less** `Lark` at module level and run the transform per call. Option (2)
is correct regardless and is the recommended shape. Zero uses of `functools.cache`/`lru_cache`
exist in the repo today, so this is also the file that sets the convention.

**P3's scope fence.** Only **internal** types: the query AST nodes (`varco_core/varco_core/query/type.py`
— `TransformerNode:95`, `ComparisonNode:112`, `BinaryNode:166`, `AndNode`/`OrNode:177-178`,
`NotNode:192`, `SortField:211`) are the candidate population. Exclusions, non-negotiable: anything
listed in any package's `__all__` (adding `__slots__` forbids attribute assignment — arguably
breaking, and 3.1 is additive, `BACKLOG.md:72`); anything in an MRO with a mixin (this codebase is
deliberately mixin-heavy and slot layouts conflict — brief 002 §2's inheritance caveat); anything
pydantic. ⚠️ `SortField` is **exported** from `varco_core` (`varco_core/varco_core/__init__.py:22`
documents `from varco_core import SortField`) — it is therefore **out of scope** for P3 despite
being an AST node. That exclusion is the plan's own worked example of the fence.

### Alternatives considered

- **`importguard` as the CI mechanism** (brief 002 §1) — ❌ rejected: a new dependency and a new
  config language for something `-X importtime` plus 60 lines of Python does, in a repo whose
  script conventions (`scripts/api_surface.py`, `scripts/bump.py`, deriving their package list by
  executing `scripts/packages.sh` per RL-18) are already established.
- **`pytest-benchmark` instead of CodSpeed** — ❌ rejected: brief 002 §5 has it as "not a
  regression gate by default" with no PR integration; CodSpeed subsumes its API.
- **Gate on benchmarks too** — ❌ rejected: `BACKLOG.md:71` locks comment-not-gate, and brief 002 §5
  reports only pydantic/FastAPI gate at all.
- **Split the giant `__init__.py` into sub-namespaces and tell users to import from those** — ❌
  rejected: that is an API break in an additive release, and the whole point of `__all__` at the
  top level is the documented flat import surface (`varco_core/__init__.py:6-25`).
- **Do P1 by deleting exports** — ❌ rejected for the same reason, and `api_surface.py --check`
  would fail it (as designed).

---

## Steps

### Phase 0 — P1a: lazy `varco_core/__init__.py` (🔴 must, M)

1. [ ] **Side-effect audit (gating).** Run §D-P1-sideeffects' two `rg` sweeps plus the `sys.modules`
       differential. Record the result in
       `design/async-performance-patterns/measurements/p1-side-effect-audit.md`: every module-scope
       non-definition statement found, and the verdict (harmless / must be `_EAGER`). Explicitly
       confirm `register_framework_metadata` is `varco_sa`-only (`varco_sa/varco_sa/metadata.py:55`)
       and therefore out of P1's blast radius. **Do not write code before this file exists.**
2. [ ] `uv run python -X importtime -c "import varco_core" 2>&1 | tail -1` and the same for
       `-c "import sys"` — reproduce the BACKLOG's 419 ms / 7 ms baseline on the implementer's
       machine and record both in the audit file. A materially different starting number means the
       premise changed and the plan needs re-reading before proceeding.
3. [ ] `varco_core/tests/test_lazy_init.py` (new, **failing first** — it fails on `main` only in
       its cold-set assertions) —
       (a) every name in `varco_core.__all__` is resolvable via `getattr` and via
       `from varco_core import <name>`, and is the **same object** as the direct submodule import;
       (b) `dir(varco_core) == sorted(varco_core.__all__)`;
       (c) `varco_core.NoSuchName` raises `AttributeError` with a message naming the module;
       (d) **the cold-set assertion**: in a fresh subprocess, after `import varco_core`,
       `sys.modules` contains none of `lark`, `jwt`, `psutil`, `opentelemetry.sdk` (the measured
       contributors, `BACKLOG.md:56-57`); after touching one name from each owning subsystem, each
       appears. This test is the whole phase's specification.
4. [ ] `varco_core/varco_core/__init__.py` — rewrite per §D-P1-mechanism. Keep the existing module
       docstring and `__all__` (`:383-655`) **byte-identical**. The `_LAZY` map is generated from
       the current import block (a throwaway script is fine; the map is committed, not generated at
       runtime). `_EAGER` holds whatever Step 1 found, each entry with an inline reason. Add a
       `DESIGN:` block with §D-P1-mechanism's ✅/❌ verbatim, including the `TYPE_CHECKING`
       explanation.
5. [ ] `uv run python scripts/api_surface.py --check` — **must pass with no regeneration.** If it
       does not, P1 changed the public surface and the diff must be understood before proceeding,
       not committed.
6. [ ] `make type-check` — mypy `strict` must report the **same** error count as before (expected:
       zero). If new `Any`-related errors appear, the `TYPE_CHECKING` block is incomplete.
7. [ ] `make test` — the full eleven-suite run. Any failure here is a side effect Step 1 missed;
       add it to `_EAGER` **and** to the audit file, never "fix" the test.
8. [ ] Re-measure and record in the audit file: `import varco_core` delta after the change, plus
       `import varco_fastapi` and one backend (`varco_redis`) as observations for §D-P1-scope.

### Phase 1 — P1b: the import budget, warn-only (🔴 must, S)

9. [ ] `scripts/import_budget.py` (new) — for each target: run `python -X importtime -c "import
       <target>"` in a subprocess 5 times, take the min of the cumulative total, subtract a
       same-methodology `import sys` baseline, and compare to the ceiling from
       `design/async-performance-patterns/measurements/import-budget.json`. Flags: `--check`
       (compare), `--update` (rewrite measured values, never ceilings), `--warn-only` (print,
       exit 0). Derives its package list by **executing `scripts/packages.sh`**, per RL-18 and the
       precedent set by `scripts/api_surface.py` and `scripts/bump.py` (CLAUDE.md).
10. [ ] `design/async-performance-patterns/measurements/import-budget.json` (new) — per target:
        `{"measured_ms": ..., "ceiling_ms": ..., "observations": []}`. Ceilings ≈ 2× the Step 8
        measurement, with a header comment (in a sibling `.md`) explaining the normalisation and
        the headroom rule.
11. [ ] `Makefile` — `make import-budget` target + a `make help` line; wire `--warn-only` into
        `make lint`'s **no-`PKG`** path only, beside `api-check` (`Makefile:151-167` shows the
        existing `ifeq ($(strip $(PKG)),)` pattern). `make lint PKG=<one>` must stay narrow and
        fast — same rule §D-C5 established for `api-check` in Plan 024.
12. [ ] `.github/workflows/test.yml` — a step in the `lint` job after `api surface --check`
        (`:64-65`): `uv run python scripts/import_budget.py --check --warn-only`. Explicitly
        warn-only for now (§D-P1-oq4), with a comment naming Step 14 as the flip.

### Phase 2 — P1c: flip the budget to a gate (🔴 must, S — a *separate commit*, later)

13. [ ] Collect ≥10 CI runs across both matrix legs. Append each observed delta to the JSON's
        `observations` array (a small commit, or one commit recording all ten).
14. [ ] If observed max < ceiling with margin: drop `--warn-only` from `test.yml` and from `make
        lint`. If not: raise the ceilings **once**, with the observations as the justification in
        the commit message, and only then flip. Record the decision in the measurements `.md`.
15. [ ] `CLAUDE.md` — a short "Import-time budget" subsection under Commands: what
        `scripts/import_budget.py` does, that it **is** a gate (contrast with the benchmark harness,
        which never is — §D-P1-oq4), and the rule: **a new top-level `import` in a `varco_*`
        `__init__.py` needs a budget check, not a hunch.**

### Phase 3 — P2: the benchmark harness (🟡 should, M)

16. [ ] `pyproject.toml` (root) `[dependency-groups]` — a `bench` group with
        `pytest-codspeed>=3`, **not** included in `dev` (it must not be installed for a normal
        `uv sync`, so it never affects the unit legs). Comment cites brief 002 §5.
17. [ ] `benchmarks/` (new) — `conftest.py` plus one module per §D-P2-harness's seed table. Every
        benchmark is deterministic, in-process, Docker-free, and asserts nothing about time (that
        is CodSpeed's job). A `README.md` in the directory states: **these are not tests, they are
        never a gate, and they must never import a backend that needs a container.**
18. [ ] `Makefile` — `make bench` (`uv run --group bench pytest benchmarks/`) and a `make help`
        line. Confirm `scripts/unit_tests.sh` does **not** pick `benchmarks/` up (it iterates an
        explicit `SUITES` list — verify, do not assume).
19. [ ] `.github/workflows/bench.yml` (new) — `pull_request` + `push: [main]`;
        `permissions: {}` at top level with the minimum the action needs on the job; a
        `concurrency` group including `github.event_name` (the lesson Plan 024 recorded for
        `integration.yml`, `design/research/001-github-actions-concurrency-semantics.md` §3);
        `if: github.event.pull_request.head.repo.full_name == github.repository` so fork PRs skip
        rather than fail on the missing secret; `CodSpeedHQ/action` running
        `uv run --group bench pytest benchmarks/ --codspeed`. **Not** in `test.yml`'s `needs:`.
20. [ ] `design/varco-1-0-release/release-runbook.md` — a new operator section: add the
        `CODSPEED_TOKEN` repository secret and connect the repo on codspeed.io. Same treatment as
        the PyPI environments: the runbook is the durable record of an out-of-repo step.
21. [ ] `CLAUDE.md` + `CONTRIBUTING.md` — one paragraph each: `make bench`, where benchmarks live,
        and the standing rule that `bench` is **never** a required check and must never appear in
        `all-green`'s `needs:`.
22. [ ] `CHANGELOG.md` — `### Changed`/`### Added` entries for the lazy import, the budget script
        and the benchmark harness, referencing "Plan 028 / P1" and "Plan 028 / P2".

### Phase 4 — P4 ⛔ GATED: reflection caching (🟢 nice, M)

> **Do not start this phase until Phase 3 is merged and `make bench` produces numbers.**

23. [ ] Measure first: record `bench_query_parse` on `main` in
        `design/async-performance-patterns/measurements/p4-reflection.md`, including how many
        `QueryParser` instances a single request creates today (grep the call sites — if the answer
        is "one per process", the entire premise evaporates and the row closes here).
24. [ ] Prove or sidestep the transformer-sharing question (§D-P3P4-gate): either demonstrate
        `QueryTransformer` holds no per-parse state, or adopt the transformer-less `Lark` +
        per-call transform shape. **Prefer the latter.**
25. [ ] Implement on a branch: a module-level `@functools.cache`d parser factory keyed by grammar
        text, replacing the per-instance `@cached_property` (`parser.py:80-98`). Keep
        `QueryParser`'s public behaviour identical, including its documented Edge cases
        (`parser.py:69-72`).
26. [ ] Re-measure. **≥10% or stop.** If below: write the number into the measurements file, close
        the BACKLOG P4 row as *measured, not worth it*, revert the branch, and record the reason in
        `CHANGELOG.md`'s `### Notes`-equivalent or simply in the BACKLOG row. That is the successful
        outcome of a gated phase.
27. [ ] If it proceeds: unit tests asserting the cache is shared across `QueryParser` instances,
        that two grammars would not collide, and that concurrent `parse()` from multiple tasks
        produces correct ASTs (the thread/async-safety question the current per-instance design was
        avoiding). Plus a `CLAUDE.md` note establishing the `functools.cache` convention, since this
        would be its first use in the repo.

### Phase 5 — P3 ⛔ GATED: `slots=True` on internal value objects (🟢 nice, M)

> **Do not start this phase until Phase 3 is merged.** Same rules as Phase 4.

28. [ ] Measure first: per-instance memory for the AST node population under
        `bench_query_ast_build`, recorded in
        `design/async-performance-patterns/measurements/p3-slots.md`.
29. [ ] Enumerate the candidate set and apply §D-P3P4-gate's fence: internal only; exclude every
        `__all__`-exported name (**including `SortField`**, `type.py:211`); exclude anything with a
        mixin in its MRO; exclude pydantic models. Commit the enumeration *with its exclusions and
        reasons* before touching code.
30. [ ] Apply `slots=True` to the surviving set, one commit. `frozen=True` + `slots=True` compose
        cleanly (brief 002 §2).
31. [ ] Re-measure. **≥20% or stop**, with the same write-it-down-and-close outcome as Step 26.
32. [ ] If it proceeds: `make test` must be green with no changes to any test — a `slots` change
        that requires a test edit is a change to observable behaviour and must be reverted.

---

## Edge cases

- **`from varco_core import *`** → materialises every name (Python consults `__all__`, then
  `__getattr__` per name). Correct, just not lazy. Asserted in Step 3.
- **`import varco_core; varco_core.__dict__["DomainModel"]`** → `KeyError` before first access,
  where it previously succeeded. Nobody does this; documented in the module docstring's `DESIGN:`
  block as an accepted incompatibility.
- **`pickle` of a lazily-resolved class** → unaffected: `__module__` still points at the defining
  submodule (`api_surface.py:162` relies on the same fact).
- **A circular import between two `varco_core` submodules** → previously masked by the eager block's
  ordering, now surfaces on first access. Step 7's full `make test` is the detector; the fix is to
  break the cycle (or `_EAGER`-pin with a written reason), never to reorder `_LAZY`.
- **Budget script on a machine without `scripts/packages.sh`'s assumptions** → it must fail with a
  clear message, not silently measure an empty target list (the RL-18 failure mode).
- **A benchmark that imports a backend needing Docker** → forbidden by `benchmarks/README.md`;
  `bench.yml` runs without services and would fail loudly.
- **Fork PR with no `CODSPEED_TOKEN`** → the job is skipped by its `if:` (Step 19), not failed.
  Because `bench.yml` is never a required check, a skip is inert.

## Verification

```bash
# P1
uv run pytest varco_core/tests/test_lazy_init.py -q
uv run python scripts/api_surface.py --check          # must pass WITHOUT regenerating
make type-check                                       # same error count as before (zero)
make test                                             # all eleven suites
uv run python -X importtime -c "import varco_core" 2>&1 | tail -1   # record the new delta

# P1b/P1c
uv run python scripts/import_budget.py --check --warn-only
make lint

# P2
uv run --group bench pytest benchmarks/ -q            # runs as plain tests, no instrumentation
make bench

# P4 / P3 — only after Phase 3, and only if the thresholds are met
```

**DoD (P1):** `api_surface.py --check` green **without** a snapshot regeneration; the cold-set
assertion green; the audit file committed; the measured delta recorded. **DoD (P2):** `make bench`
runs locally with no token; `bench.yml` present, never in `all-green`'s `needs:`, and the operator
step recorded in the runbook. **DoD (P3/P4):** either a merged change with its measurement, or a
committed measurement file and a closed BACKLOG row — both are complete.

## Risks

- **A missed import-time side effect.** The highest-severity risk in the plan: the failure mode is
  silent (something is not registered) rather than loud. Invariant that must hold: **the set of
  modules imported after touching every `__all__` name equals the set imported eagerly today.**
  Step 1's differential is the guard and it must be run, not reasoned about.
- **`TYPE_CHECKING` block drift.** If a future contributor adds a name to `_LAZY` but not to the
  `TYPE_CHECKING` block, mypy silently degrades that name to `Any` across the repo. Mitigation: a
  test asserting `set(_LAZY) == set(__all__)`, plus the same check for the `TYPE_CHECKING` names
  parsed out of the file with `ast` — cheap, and it makes the drift impossible.
- **⚠️ ASSUMPTION — the ~2× ceiling headroom is enough for GitHub-runner variance.** Brief 002 §5
  states runner noise is "acknowledged but unquantified"; no source gives a variance figure for
  `-X importtime` on `ubuntu-latest`. This is precisely why Steps 13-14 gather ten observations
  before the gate turns on. If the observed spread exceeds the headroom, the honest response is to
  raise the ceiling once with the data, or to leave the check warn-only — **not** to switch to a
  ratchet (§D-P1-oq4's rejection stands on other grounds).
- **⚠️ ASSUMPTION — P4's premise that `QueryParser` is constructed per request.** The BACKLOG says
  "a per-request parser rebuilds the Lark parser every time" (`BACKLOG.md:73`), but the construction
  frequency was not verified by the scout. Step 23 verifies it **first**; if parsers are process-
  singletons, P4 closes immediately with no code change, which is a fine outcome.
- **P3's MRO/slot-layout constraint.** Brief 002 §2's inheritance caveat plus this repo's
  deliberate mixin-heaviness means a blanket sweep would produce `TypeError: multiple bases have
  instance lay-out conflict` at class-definition time — loud, but potentially in a rarely-imported
  module. The fence in Step 29 (commit the enumeration and exclusions *before* editing) is what
  keeps that from being discovered by a user.
- **Scope creep: "while I'm here, make `varco_fastapi` lazy too."** §D-P1-scope forbids it. The
  number is unmeasured there, and the locked posture for this cycle is one measured win.
