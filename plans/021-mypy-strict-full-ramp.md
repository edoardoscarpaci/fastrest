# Plan 021 — Complete the mypy strictness ramp (RL-14 / RL-14b / RL-14c / RL-14d)

## Goal

`[tool.mypy]` in the root `pyproject.toml` carries **`strict = true`** plus
`disallow_any_unimported = true`, and `make type-check` is green over all ten
`varco_*/varco_*` source dirs with **zero** new blanket suppressions. BACKLOG rows RL-14,
RL-14b, RL-14c and RL-14d are all closed. The ten `[[tool.mypy.overrides]]`
`check_untyped_defs` blocks are deleted — the overrides section is empty, which is exactly
RL-14b's stated completion criterion.

## Non-goals

- **`disallow_any_expr` stays off, permanently.** Brief 004
  (`design/plan-017-findings/research/004-mypy-strict-remediation.md` §1) confirms it is *not*
  part of `--strict` in mypy 2.3.1. Brief 001's "never" verdict survives for this flag alone.
- **`warn_unreachable` stays off** — also not in `--strict` (brief 004 §1); unmeasured, not in scope.
- **No behaviour changes.** This is an annotation and configuration sweep. Any place where the
  correct annotation would require changing what the code *does* is filed as a BACKLOG row and
  suppressed with a narrow, reasoned `# type: ignore[<code>]`, never freelance-fixed. (Precedent:
  Plan 020's KI-9/KI-10/KI-12 discipline.)
- **No mypy version bump.** `mypy==2.3.1` stays pinned in `[dependency-groups] lint`.
- **No test-suite / `testkit/` / `examples/` type-checking.** Out of scope for `make type-check`
  today and out of scope here — same ten dirs, unchanged.
- **No new runtime dependency**, in particular not `typing_extensions` (see §PEP 696 below).

---

## Scope call (state this in the commit message)

The user asked to fix **every** strictness ramp error. That is the whole of `--strict`, plus the
one cheap non-strict flag whose "never" verdict is now measurably stale.

| Target | Measured today (mypy 2.3.1, pinned) | In `--strict`? | In scope |
|---|---|---|---|
| `disallow_any_generics` (`type-arg`) | **176** | ✅ | ✅ RL-14c |
| `warn_return_any` (`no-any-return`) | **64** (BACKLOG says 67 — **stale**) | ✅ | ✅ RL-14d |
| `disallow_untyped_defs` (`no-untyped-def`) | **22** | ✅ | ✅ RL-14b |
| `disallow_incomplete_defs` | 16 — **subsumed** by the 22 above | ✅ | ✅ RL-14b |
| `disallow_untyped_calls` (`no-untyped-call`) | **6** | ✅ | ✅ — "never" verdict **re-opened** |
| `strict_equality` (`comparison-overlap`) | **4** | ✅ | ✅ (never previously measured) |
| `extra_checks` | 0 (inferred — see Risks) | ✅ | ✅ |
| `disallow_any_unimported` | **7** | ❌ | ✅ — "never" verdict **re-opened** |
| `disallow_any_expr` | not measured | ❌ | ❌ **never** |

**`--strict` total: 272 errors in 100 files.** Plus `disallow_any_unimported`'s 7 = **279**.
Full baseline: `design/plan-017-findings/measurements/rl-14-strict-baseline.txt` (measured
2026-08-30 against the pinned toolchain; current config is `Success: no issues found in 439
source files`).

**On §RL-14-stop (the 50-error-per-flag budget).** That rule is not being silently overridden.
It was a *stopping* rule scoped to Plan 020's remit — "a flag that surfaces >50 errors is not a
config change, it is a refactor wearing a config change's clothing — and the release path
(Phase 4/5) does not depend on it"
(`plans/020-plan-017-findings-remediation.md:586-591`). The user has now explicitly
authorized that refactor as its own unit of work. §RL-14-stop is therefore **retired for this
plan and this plan only**, and replaced by a per-phase stop rule (below) that preserves its real
intent — never grind blindly.

**Replacement stop rule (§021-stop).** Within a phase, if a *single file* needs more than **3**
new `# type: ignore` comments to go green, stop working that file, revert it, and file a BACKLOG
row naming the file, the error code and the count. A file needing four suppressions is telling
you the annotation is not the problem.

**Two re-opened "never" verdicts, per U-8 evidence discipline.** `[tool.mypy]:153-155` records
`disallow_untyped_calls` and `disallow_any_unimported` as DECIDED NEVER on brief 001's advice
("Re-opening this needs a fresh brief, not just enthusiasm"). Two fresh briefs (004, 005) and a
fresh measurement now exist, and the measurement says 6 and 7 errors respectively — a combined
13, not the hundreds the verdict assumed. The verdicts are **stale**; this plan re-opens them on
the evidence and says so in `pyproject.toml`'s comment block, rather than deleting the comment
as if it had never been written.

---

## Design

### D1 — End state is literally `strict = true`, not an enumerated flag list

`[tool.mypy]` ends with:

```toml
strict = true                    # mypy 2.3.1's 13-flag bundle — see comment block
disallow_any_unimported = true   # NOT in --strict; landed separately (Phase 8)
# disallow_any_expr: NEVER. Not in --strict (brief 004 §1). Off deliberately.
# warn_unreachable: not in --strict; unmeasured; out of scope.
```

DESIGN: `strict = true` over the enumerated list
- ✅ One line replaces ~45 lines of G1–G5 ramp archaeology that describes a ramp that is over.
- ✅ Self-maintaining: if a future mypy adds a flag to `--strict`, we inherit it at the moment we
  bump the pin — a deliberate, reviewed event — rather than silently missing it forever.
- ✅ It is mypy's own documented terminal state for a staged monorepo ramp (brief 004 §6 "Phase 3
  — Global strict (aspirational state)").
- ✅ Makes future relaxations explicit and auditable: any exemption must appear as a named
  `[[tool.mypy.overrides]]` block, which is loud, rather than as a flag quietly never enabled.
- ❌ A pin bump can turn CI red with no code change. Mitigated: mypy is pinned exactly
  (`mypy==2.3.1`), and `varco_core/tests/test_repo_tooling_pins.py` already exists as the place
  that guards tooling pins.
- ❌ Less explicit to a reader who does not know the 13 flags. Mitigated by a comment block above
  `strict = true` enumerating them as of 2.3.1, citing brief 004 §1 — documentation, not config.

#### Alternatives considered

- **Enumerate all 13 flags individually instead of `strict = true`**: rejected — ✅ maximally
  explicit, immune to a pin bump changing behaviour; ❌ 13 lines that must be manually
  reconciled against every mypy release forever, and the failure mode of *not* reconciling them
  is silent (a new strict flag we never adopt), which is exactly the class of silent drift RL-18
  was filed about. The `strict = true` failure mode is loud (CI red at pin-bump time).
- **Per-package `strict = true` overrides, ramping package by package** (brief 004 §6 Phase 2):
  rejected — ✅ the documented incremental path, each package independently landable; ❌ the
  errors here do not partition by package, they partition by *error code* (176 of 279 are one
  code, spread across 73 files in 8 packages). A per-package ramp would apply the same
  remediation pattern ten separate times with ten separate context reloads. Phasing by code is
  strictly cheaper here. Per-package remains the right shape for a *different* codebase.
- **`disable_error_code = ["type-arg"]` for the SQLAlchemy/motor seams** (brief 004 §7 sanctions
  config-level disables for "entire modules that will never be fully typed"): rejected — these
  are our own modules that merely *use* third-party generics; the generics are knowable
  (brief 005's table, corrected per §D5). A module-wide disable would also hide our own bare
  `dict`/`list` in the same file.
- **Land everything in one commit**: rejected — ❌ a 279-error single diff is unreviewable and
  unbisectable; a semantic regression could not be attributed. Each phase must leave
  `make type-check` and `make test` green.

### D2 — Phase by error code, not by package

Each `--strict` error code has exactly one remediation pattern in brief 004. A phase is
therefore "apply one pattern N times", which is reviewable at a glance and mechanically
verifiable. Every phase ends by **landing its flag in `[tool.mypy]`**, so the phase's work can
never silently regress afterwards, and `make type-check` is green at every phase boundary.

```
Phase 1  config-only    G3 hoist: check_untyped_defs global, delete 10 override blocks   0 errors
Phase 2  comparison-overlap  strict_equality                                              4
Phase 3  no-untyped-def      disallow_untyped_defs + disallow_incomplete_defs            22
Phase 4  no-untyped-call     disallow_untyped_calls (2 of 6 evaporate in Phase 3)         4
Phase 5  type-arg            disallow_any_generics — 3 sub-landings (5a/5b/5c)          176
Phase 6  no-any-return       warn_return_any                                             64
Phase 7  config-only    collapse landed flags → strict = true; verify 0 delta             0
Phase 8  no-any-unimported   disallow_any_unimported                                      7
Phase 9  M1 re-measure + docs + BACKLOG closure + CHANGELOG                               —
```

Ordering rationale: ascending count, so each phase's pattern is proven on a small population
before the 176-error one; and Phase 3 **must** precede Phase 4 (two of the six
`no-untyped-call` errors are calls into our own untyped defs and disappear once those defs are
annotated — see §D6).

### D3 — Public API / BREAKING assessment: **not breaking. No CHANGELOG BREAKING entry.**

This is the highest-risk item in the request, and it resolves cleanly.

Every `type-arg` error is a bare generic **in varco's own annotations** — a use site, not a
declaration. `AsyncVarcoClient`, `VarcoRouter`, `AsyncCache`, `ClientConfigurator`,
`AsyncRepository`, `AbstractMapper` keep their exact current declarations:

- `varco_fastapi/varco_fastapi/client/base.py:602` — `class AsyncVarcoClient(Generic[R], ...)`
- `varco_fastapi/varco_fastapi/router/base.py:419` — `class VarcoRouter(Generic[D, PK, C, R, U])`
- `varco_core/varco_core/cache/base.py:66` — `class AsyncCache(Protocol[K, V])`
- `varco_fastapi/varco_fastapi/client/configurator.py:58` — `class ClientConfigurator(Generic[R])`
- `varco_core/varco_core/repository.py:31` — `class AsyncRepository(ABC, Generic[D, PK])`
- `varco_core/varco_core/mapper.py:56` — `class AbstractMapper(ABC, Generic[D, O])`

No type parameter is added, removed, re-bounded or re-ordered. Downstream code writing bare
`AsyncVarcoClient` continues to mean `AsyncVarcoClient[Any]` — unchanged, because
`disallow_any_generics` is **our** configuration and has never applied to a consumer's own
tree. Downstream subclassing (`class MyCache(AsyncCache)`) is untouched. Therefore: no BREAKING
entry; CHANGELOG records this under an internal/typing heading.

**The one real hazard** is a *use-site* parameterization that accidentally narrows a public
signature — e.g. annotating a parameter `AsyncCache[str, bytes]` where the function genuinely
accepts any cache. Rule for the implementer: **when in doubt at a public boundary, use `[Any]`
(or `[Any, Any]`).** `Foo[Any]` is byte-identical in meaning to today's bare `Foo` and satisfies
the flag. Tightening beyond `Any` is a separate, opt-in improvement — allowed only where the
surrounding code already proves the concrete type, and never on a `def` that is exported in a
package's `__all__`.

#### §PEP 696 — considered and rejected

Brief 004 §2 confirms mypy 2.3.1 fully supports PEP 696 type-parameter defaults, and that a
default does keep a bare generic `disallow-any-generics`-clean. Rejected anyway:

- ❌ `typing.TypeVar(default=...)` requires Python 3.13; `varco_core` is `requires-python = ">=3.12"`
  (`varco_core/pyproject.toml:8`) and CI's unit matrix is `[3.12, 3.13]`. Supporting 3.12 means a
  new **runtime** `typing_extensions` dependency in `varco_core` and `varco_fastapi` — a real
  packaging cost for a lint-only benefit.
- ❌ It would permanently bake "bare `AsyncVarcoClient` means `[Any]`" into the *declaration*,
  which is harder to tighten later than the status quo (where bareness is merely an unannotated
  use site we are now fixing).
- ✅ The only thing it buys is downstream ergonomics for consumers who run
  `disallow_any_generics` themselves — and per §D3 they are no worse off than today.

Recorded as a one-line note in `pyproject.toml`'s comment block so the next person does not
re-derive it.

### D4 — `varco_core/event/consumer.py`'s 4 `comparison-overlap` errors are an annotation bug, not a runtime bug

Verified in source. `ListenEntry.retry_policy` and `.dlq` default to a private `_UNSET` sentinel
but are annotated as if they could not (`consumer.py:199`, `:218`), each papered over with
`# type: ignore[assignment]`:

```python
retry_policy: RetryPolicy | None = _UNSET  # type: ignore[assignment]
dlq: AbstractDeadLetterQueue | None = _UNSET  # type: ignore[assignment]
```

`register_to()` then does `entry.retry_policy is not _UNSET` (`consumer.py:868`, `:871`, `:873`)
— which mypy correctly reports as a non-overlapping identity check, because the declared type
excludes `_Unset`. The sentinel is load-bearing and deliberate (Plan 009 / R5 / RD-7: an
explicit `retry_policy=None` must opt *out* of the process-wide default, so omission must be
distinguishable from `None`). **The runtime is correct; the annotations are wrong.**

Fix: widen both annotations to `RetryPolicy | None | _Unset` and
`AbstractDeadLetterQueue | None | _Unset`, and **delete both `# type: ignore[assignment]`**.
This closes 4 `comparison-overlap` errors and removes 2 suppressions (M1 −2) in one edit, with
zero behaviour change. Do not touch the `_UNSET` mechanism.

### D5 — ⚠️ Brief 005 §SQLAlchemy is contradicted by measurement — trust the measurement

Brief 005 (`design/plan-017-findings/research/005-third-party-generic-params.md:16-26`) lists
`Select`, `Column`, `MappedColumn`, `async_sessionmaker`, `Row` and `TypeEngine` under **"NOT
generic (do not parameterize)"**. The measured baseline shows mypy 2.3.1 emitting `type-arg` on
every one of them, 20 times:

| Symbol | Brief 005 says | mypy 2.3.1 says | Sites |
|---|---|---|---|
| `Select` | not generic | **generic** | 7 (`varco_sa/query/applicator.py`, `aggregation.py`) |
| `Column` | not generic | **generic** | 5 (`varco_sa/factory.py`) |
| `async_sessionmaker` | not generic | **generic** | 4 (`sqlalchemy_session.py`, `outbox.py`, `inbox.py`, `audit.py`) |
| `TypeEngine` | (not listed) | **generic** | 2 (`varco_sa/migrations/versions/0003`, `0004`) |
| `MappedColumn` | not generic | **generic** | 1 (`varco_sa/query/compiler.py:299`) |
| `Row` | not generic | **generic** | 1 (`varco_sa/tenancy/catalog.py:103`) |

Brief 005 flags this itself in its own Evidence gaps ("requires source inspection to verify") —
the section was derived from prose documentation, not from the installed distribution. This is
precisely the U-8 failure mode. **Resolution: the running, pinned mypy against the resolved
SQLAlchemy 2.0.48 is ground truth.** The implementer derives each parameter from SQLAlchemy's
own annotations (`uv run python -c "import sqlalchemy, inspect; ..."`, or read the installed
`.py`), not from brief 005's table. Brief 005's *other* sections (motor/pymongo/pydantic
generics; casbin/aiokafka/nats-py being untyped) are corroborated by the measurement and stand.

Phase 9 adds an in-tree correction banner to brief 005 pointing here — the same convention
research 002 §1 already uses in this repo.

### D6 — Remediation patterns, per error code

**`type-arg` — bare builtins (94 of 176: `dict` 41, `list` 27, `Callable` 24, `tuple` 2).**
Brief 004 §2. Order of preference:
1. **The real type**, when the surrounding code proves it (`dict[str, str]`, `list[Event]`).
2. **`dict[str, Any]` / `list[Any]`** for genuinely JSON-shaped or FastAPI-passthrough data.
   Brief 004 §2: "use when the value type is genuinely unknown, or when accepting arbitrary
   JSON-like data."
3. **`Mapping[str, object]`** when the function only *forwards* the value and never inspects it
   — `object` states "I intentionally do not depend on this shape" without `Any`'s escape hatch
   (brief 004 §2, and note its own evidence gap #1 flagging this as inferred, not an explicit
   mypy guideline). Use it where it costs nothing; do not churn a public signature to get it.
4. **A `TypeVar`** only when two annotations in the same signature must agree (brief 004 §2).
   In practice this is the right answer for the `_run_in_span` family — see `no-any-return`.

`Callable` bare → `Callable[..., Any]` is acceptable and correct where the decorator genuinely
accepts any signature (all 24 sites are resilience decorators / `@listen` plumbing); prefer
`Callable[..., Awaitable[T]]` where the code already requires awaitability.

**`type-arg` — stdlib (16).** `asyncio.Task` ×8 → `asyncio.Task[None]` (verify each: these are
background-loop handles). `argparse._SubParsersAction` ×7 → `_SubParsersAction[ArgumentParser]`.
`AbstractAsyncContextManager` ×1 → `AbstractAsyncContextManager[AsyncSession]`.

**`type-arg` — varco's own generics (40).** Per §D3: `[Any]` at public boundaries; the concrete
parameter only where already proven locally.

**`type-arg` — third-party (26).** Per §D5, derive from the installed distribution. Brief 005's
corroborated guidance: motor `AsyncIOMotorDatabase[dict[str, Any]]`,
pymongo `AsyncMongoClient[dict[str, Any]]` / `AsyncCollection[dict[str, Any]]` (brief 005
§Beanie+motor+pymongo), pydantic `Field` (`varco_core/meta.py:990`).

**`no-any-return` (64).** Brief 004 §3's documented ordering, in order:
1. **Annotate the intermediate local**, then return it. Preferred; documents intent.
2. **`typing.cast()`** only when there is no intermediate to annotate. Brief 004 §3 calls it a
   "last resort … bypasses all type checking on that expression" — every `cast()` added by this
   plan carries a one-line `# why:` comment.
3. `# type: ignore[no-any-return]` — only if both above are infeasible.

**Two structural fixes cover ~20 of the 64 and should be done first**, because they are one edit
each rather than N call-site edits:
- `varco_core/observability/repository_mixin.py:231` —
  `async def _run_in_span(self, operation: str, coro_fn, *args: Any) -> Any:` returns `Any`,
  which is why its eight callers (lines 142–182) all report `no-any-return`. Make it generic:
  `_T = TypeVar("_T")`, `coro_fn: Callable[..., Awaitable[_T]]`, `-> _T`. Fixes 8
  `no-any-return` + 1 `no-untyped-def` in one edit. Leave the existing
  `# type: ignore[safe-super]` comments alone — mypy's "Error code not covered by" notes in the
  baseline are informational, and those ignores remain load-bearing.
- `varco_core/observability/mixin.py:153` — same helper, fully unannotated
  (`async def _run_in_span(self, operation, coro_fn, *args):`). Same fix. Kills 5
  `no-any-return` + 2 `no-untyped-def`.
- Check `varco_core/cache/mixin.py` (5 `no-any-return` at :351, :366, :418, :421, :436) for the
  same shape before touching call sites individually.

**`no-untyped-def` (22) / `disallow_incomplete_defs`.** Brief 004 §4: `__init__` needs an
explicit `-> None` even with no parameters; `*args`/`**kwargs` must be annotated (`Any` is
acceptable and documents the escape point). The 16 `disallow_incomplete_defs` errors are a
strict subset of these 22 — fixing the 22 closes both flags.

**`no-untyped-call` (6).** Brief 004 §4. Two are ours and evaporate once Phase 3 annotates the
callee (`varco_fastapi/middleware/session.py:140` → `_generate_session_id` at `:52`;
`varco_fastapi/router/health.py:125` → `_resolve_checks` at `:108`). The other four are
third-party seams and are the plan's *expected* suppression sites:
`varco_redis/job_store.py:295` (`reset`) and `:302` (`multi`) — redis-py pipeline;
`varco_fastapi/router/metrics.py:216` (`generate_latest`) — prometheus_client;
`varco_beanie/inbox.py:350` (`Set`) — beanie query operator.

**Genuinely-untypable third-party seams.** Where an error is irreducible, use a **narrow**
`# type: ignore[<code>]` with a one-line reason on the same or preceding line — never a bare
`# type: ignore` (brief 004 §7). Format, matching the three suppressions Plan 020's G4 already
landed:

```python
# prometheus_client ships no py.typed — generate_latest() is untyped upstream.
return generate_latest(registry)  # type: ignore[no-untyped-call]
```

Per §RL-14-metric these count as **third-party seam suppressions, not debt**, and are listed
individually in Phase 9's M1 report rather than folded into the raw count.

### D7 — G3 hoist is free and goes first

All ten packages already carry a `check_untyped_defs = true` override
(`pyproject.toml:170-208`) — verified; the flag is globally complete in practice, just written
ten times. (A scout claim that `varco_redis` lacks an override is **false**; `pyproject.toml:198-200`.)
Phase 1 hoists it to a single global `check_untyped_defs = true` and deletes all ten
`[[tool.mypy.overrides]]` blocks. Zero expected error delta — it must be verified as zero, which
is what makes it a safe first commit and what empties the overrides section RL-14b wants empty.

---

## Steps

Every phase ends green: `make type-check` **and** `make lint` **and** `make test` pass before the
phase is committed. `PKG=` narrows `make type-check` while iterating
(`Makefile:147-149`); the final check of each phase is always the unnarrowed run.

### Phase 0 — re-baseline

1. [x] `design/plan-017-findings/measurements/` — re-run and record, appending to (not
   overwriting) `rl-14-strict-baseline.txt` or writing `rl-14-strict-baseline-rerun.txt`:
   `uv run mypy --strict $(make print-packages | ...)` — in practice
   `uv run mypy --strict varco_core/varco_core varco_kafka/varco_kafka varco_nats/varco_nats
   varco_redis/varco_redis varco_sa/varco_sa varco_beanie/varco_beanie
   varco_memcached/varco_memcached varco_ws/varco_ws varco_fastapi/varco_fastapi
   varco_casbin/varco_casbin`. Confirm 272/100 still holds; if it drifted, the per-code counts in
   this plan's tables are advisory and the new run is ground truth.
2. [x] Record M1: `rg -o 'type: ignore' varco_*/varco_* | wc -l` — expected **327**. This is the
   number Phase 9 compares against.
3. [x] `uv run mypy --disallow-any-unimported <ten dirs> > .../rl-14-any-unimported.txt` — the 7
   errors are not enumerated anywhere yet; Phase 8 needs the file list.

### Phase 1 — G3 hoist (config only, 0 errors)

4. [x] `pyproject.toml` — add `check_untyped_defs = true` to `[tool.mypy]` and **delete all ten**
   `[[tool.mypy.overrides]]` blocks (lines ~161–208, including the G3 comment header).
5. [x] Verify `make type-check` reports zero new errors. A non-zero delta means an override was
   doing something other than what its name says — stop and investigate before proceeding.

### Phase 2 — `strict_equality` (4 errors, 1 file)

6. [x] `varco_core/varco_core/event/consumer.py:199` — widen `retry_policy` to
   `RetryPolicy | None | _Unset`, delete `# type: ignore[assignment]`. Update the field docstring
   to state the annotation includes the sentinel (the docstring already explains *why* the
   sentinel exists — keep that text).
7. [x] `varco_core/varco_core/event/consumer.py:218` — same for `dlq`
   (`AbstractDeadLetterQueue | None | _Unset`), delete `# type: ignore[assignment]`.
8. [x] `varco_core/varco_core/event/consumer.py:868-873` — confirm the four `is not _UNSET` /
   `is _UNSET` checks now type-check unchanged, and that `effective_retry_policy` /
   `effective_dlq` still narrow to `RetryPolicy | None` / `AbstractDeadLetterQueue | None` after
   the ternaries. Add an explicit local annotation if narrowing does not carry.
9. [x] `varco_core/tests/test_event*.py` — confirm existing coverage already distinguishes
   "omitted" from "explicitly `None`" for `retry_policy`/`dlq`; if it does **not**, add that test
   *before* step 6 (TDD order) — it is the invariant this edit must not break.
10. [x] `pyproject.toml` `[tool.mypy]` — add `strict_equality = true`. `make type-check` green.

### Phase 3 — `no-untyped-def` + `disallow_incomplete_defs` (22 errors, 8 files)

11. [x] `varco_sa/varco_sa/sqlalchemy_session.py` — annotate lines 18, 22, 32, 37, 39 (6 errors;
    also fixes the `async_sessionmaker` bare at :32 — do that here, it is the same line).
12. [x] `varco_fastapi/varco_fastapi/exceptions.py:242,246,250,254,258` — 5 missing return
    annotations (exception-handler factories; expect `-> JSONResponse` / `-> Response`).
13. [x] `varco_fastapi/varco_fastapi/auth/server_auth.py:660,663,670` — 3.
14. [x] `varco_fastapi/varco_fastapi/middleware/tracing.py:117,125` — 2 (parameter annotations).
15. [x] `varco_core/varco_core/observability/mixin.py:153` — annotate `_run_in_span` **and** make
    it generic per §D6 (this also pre-pays 5 Phase-6 errors).
16. [x] `varco_core/varco_core/observability/repository_mixin.py:231` — same, generic
    `_run_in_span` (pre-pays 8 Phase-6 errors).
17. [x] `varco_sa/varco_sa/tenancy/provisioner.py:48` — 1.
18. [x] `varco_fastapi/varco_fastapi/middleware/session.py:52` — 1 (`_generate_session_id`).
19. [x] `varco_fastapi/varco_fastapi/router/health.py:108` — 1 (`_resolve_checks`; mypy's own note
    says `-> None` if it returns nothing — check, it feeds `:125`).
20. [x] `pyproject.toml` — add `disallow_untyped_defs = true` **and**
    `disallow_incomplete_defs = true`. `make type-check` green.

### Phase 4 — `no-untyped-call` (4 remaining after Phase 3)

21. [x] Re-measure: `uv run mypy --disallow-untyped-calls <ten dirs>` — expect 4, not 6 (steps 18
    and 19 removed two). If 6, Phase 3 missed something.
22. [x] `varco_redis/varco_redis/job_store.py:295,302` — redis-py pipeline `reset()`/`multi()`.
    Attempt an annotated intermediate first (brief 004 §4 option 1); if the upstream symbol is
    genuinely untyped, narrow-suppress with a reason comment.
23. [x] `varco_fastapi/varco_fastapi/router/metrics.py:216` — `generate_latest`, prometheus_client.
24. [x] `varco_beanie/varco_beanie/inbox.py:350` — beanie `Set`.
25. [x] `pyproject.toml` — add `disallow_untyped_calls = true`; **rewrite** the "DECIDED NEVER"
    comment block to record that this verdict was re-opened on the 2026-08-30 measurement
    (6 errors, not hundreds), citing brief 004 §1. `make type-check` green.

### Phase 5a — `type-arg`, builtins + stdlib (110 errors)

26. [x] `varco_core` builtins: `i18n/catalog.py:30`, `meta.py:94,890`, `profiling/report.py:122`,
    `query/transformer.py:43,56,68,80,96,108,120,140,152,164,176` (11× bare `list`).
27. [x] `varco_core` bare `Callable` (17): `resilience/timeout.py:89`, `retry.py:353,403`,
    `rate_limit.py:435,476`, `hedge.py:237,273`, `circuit_breaker.py:413,617,658`,
    `bulkhead.py:349,405,439`, `event/base.py:654,683`, `event/consumer.py:311,429`.
28. [x] Backend builtins: `varco_nats/dlq.py:676`, `varco_kafka/dlq.py:663`,
    `varco_redis/stream_dlq.py:670`, `varco_redis/dlq.py:523`, `varco_redis/bulkhead.py:320`,
    `varco_sa/factory.py:453,467`, `varco_sa/migrations/versions/0004_job_zoned_schedule.py:46`.
29. [x] `varco_beanie` builtins (16): `saga.py:116,203,245`, `outbox.py:179,315,369,428`,
    `job_store.py:236`, `dlq.py:100`, `deduplication.py:292,353`, `audit.py:189`,
    `inbox.py:173,315,346,378`.
30. [x] `varco_fastapi` builtins (33): `router/endpoint.py:87,88,143,144,163,259,333,364,422,450`,
    `router/mixins.py:78,79,132,133,177,178,231,232,279,280,350,351` (six `dict`/`list` ClassVar
    pairs — these are FastAPI `responses=`/`dependencies=` passthroughs: `dict[str | int, dict[str, Any]]`
    and `list[Any]` are the honest annotations), `router/crud.py:465,488,503,522,541`,
    `tenancy/router.py:138,156,162,174,175,212,223,234,245,274`.
31. [x] `asyncio.Task` (8): `varco_core/job/reschedule.py:60`, `varco_core/service/inbox.py:402`,
    `varco_core/event/memory.py:176,291`, `varco_core/tenancy/control/readiness.py:147`,
    `varco_fastapi/job/poller.py:127`, `varco_kafka/bus.py:212`, `varco_redis/bus.py:176`.
32. [x] `argparse._SubParsersAction` (7): `varco_core/cli/{retention.py:39,migrate.py:41,tenant.py:29,dlq.py:37}`,
    `varco_sa/migration/cli.py:32`, `varco_beanie/migration/cli.py:50`,
    `varco_fastapi/contract/cli.py:32`.
33. [x] `varco_sa/varco_sa/sqlalchemy_session.py:11` — `AbstractAsyncContextManager[...]`.

### Phase 5b — `type-arg`, varco's own generics (40 errors)

34. [x] `AsyncVarcoClient` (14) → `AsyncVarcoClient[Any]` per §D3 unless the concrete `R` is
    proven locally: `client/sync.py:92`, `client/handle.py:114`, `client/base.py:995`,
    `client/generic.py:34`, `client/front_door.py:39,42,91`, `client/peer.py:116,255`,
    `router/a2a/router_source.py:129,222`, `router/skill.py:174`, `router/mcp.py:520`,
    `contract/runtime.py:64`.
35. [x] `VarcoRouter` (9): `router/base.py:900,951,985,1021,1061,1095,1392,1523`,
    `client/configurator.py:306` → `VarcoRouter[Any, Any, Any, Any, Any]`.
36. [x] `AsyncCache` (9): `varco_core/cache/readthrough.py:71,128,177,212,250,257,310,487,495` →
    `AsyncCache[Any, Any]`, **or** the module's existing `K`/`V` TypeVars if `readthrough.py`
    already declares them — check first; a TypeVar is strictly better here if the signature
    already relates key and value types.
37. [x] `ClientConfigurator` (3): `client/base.py:649,660`, `varco_fastapi/di.py:220`.
38. [x] `AsyncRepository` (2): `varco_beanie/di.py:188`, `varco_sa/di.py:311`. ⚠️ These are DI
    binding sites — re-read the "Quoted `@Provider` return annotation" pitfall in CLAUDE.md
    before editing, and re-run `varco_fastapi/tests/test_di_binding_health.py` plus each
    package's `validate_bindings()` test after.
39. [x] `AbstractMapper` (2): `varco_sa/factory.py:227`, `varco_beanie/factory.py:134`.
    `Transformer` (1): `varco_core/query/transformer.py:28`.

### Phase 5c — `type-arg`, third-party (26 errors)

40. [x] **Before editing**: derive each parameter from the *installed* distribution per §D5, not
    from brief 005's table. Record the six SQLAlchemy answers in the commit message.
41. [x] SQLAlchemy `Select` (7): `varco_sa/query/applicator.py:106,110,155,160,182,186`,
    `varco_sa/query/aggregation.py:80`. Note lines 106/155/182 already carry
    `# type: ignore[override]` — mypy's baseline note says `type-arg` is *not* covered by it, so
    those ignores stay and the annotation is fixed alongside.
42. [x] SQLAlchemy `Column` (5): `varco_sa/factory.py:345,373,381,398,409`.
43. [x] SQLAlchemy `async_sessionmaker` (3 remaining after step 11): `varco_sa/outbox.py:539`,
    `varco_sa/inbox.py:473`, `varco_sa/audit.py:257`.
44. [x] SQLAlchemy `TypeEngine` (2): `varco_sa/migrations/versions/0003_audit_hash_chain.py:31`,
    `0004_job_zoned_schedule.py:46`. ⚠️ These are checked-in Alembic revisions — annotate them,
    but add a one-line note to whatever generates/reviews new revisions that autogen output needs
    the same treatment (see Edge cases).
45. [x] SQLAlchemy `MappedColumn` (1): `varco_sa/query/compiler.py:299`. `Row` (1):
    `varco_sa/tenancy/catalog.py:103`.
46. [x] motor/pymongo (5): `varco_beanie/index_guard.py:257,282,396` (`AsyncIOMotorDatabase`),
    `varco_beanie/uow.py:50` (`AsyncMongoClient`), `varco_beanie/tenancy/catalog.py:115`
    (`AsyncCollection`). ⚠️ `uow.py` is where BACKLOG's **KI-12** lives (`_begin()` awaits a
    non-coroutine, `uow.py:62`) — **do not fix KI-12 here.** Annotation only.
47. [x] pydantic `Field` (1): `varco_core/meta.py:990`.
48. [x] `pyproject.toml` — add `disallow_any_generics = true`; delete the "STOPPED per
    §RL-14-stop" comment block (lines ~139-145) and replace it with a one-line landed note.
    `make type-check` green, `make test` green.

### Phase 6 — `warn_return_any` (64, minus ~13 already paid by steps 15–16)

49. [x] Re-measure `uv run mypy --warn-return-any <ten dirs>`; expect ~51. Work the file list from
    the re-measurement, densest first.
50. [x] `varco_core/varco_core/cache/mixin.py:351,366,418,421,436` — check for a shared
    `Any`-returning helper first (§D6); one generic helper beats five casts.
51. [x] `varco_fastapi` middleware (9): `middleware/profiling.py:159,165,196`,
    `middleware/metrics.py:345,351,370`, `middleware/tracing.py:123,195`,
    `middleware/localization.py:160`. All are `call_next(request)` returning `Any` under
    Starlette's `RequestResponseEndpoint` — annotate the local
    (`response: Response = await call_next(request)`), do not cast.
52. [x] `varco_fastapi/client/generic.py:146,179,213,247` (4), `client/base.py:951,1040`,
    `client/handle.py:145`, `client/middleware.py:380`, `client/openapi_gen.py:113`,
    `client/method.py:131`, `contract/runtime.py:90`, `router/crud.py:354`,
    `auth/server_auth.py:677,682`, `tenancy/router.py:153`.
53. [x] `varco_core` remainder: `encryption_store.py:923,930,937`, `query/transformer.py:54`,
    `cache/readthrough.py:461`, `cache/service.py:363`, `profiling/engine.py:242`,
    `service/base.py:1406`, `service/tenant.py:546`.
54. [x] Backends: `varco_sa/repository.py:495`, `varco_sa/query/compiler.py:337,396`,
    `varco_sa/query/aggregation.py:174,243`, `varco_sa/outbox.py:761`,
    `varco_beanie/repository.py:217,247,389`, `varco_beanie/migration/store.py:57`,
    `varco_redis/stream_dlq.py:549`, `varco_redis/dlq.py:459`, `varco_nats/channel.py:515`,
    `varco_casbin/beanie_adapter.py:566`.
55. [x] `pyproject.toml` — add `warn_return_any = true`; delete the G5 "STOPPED" comment block
    (lines ~147-151). `make type-check` green, `make test` green.

### Phase 7 — collapse to `strict = true` (config only, must be a 0-delta)

56. [x] `pyproject.toml` `[tool.mypy]` — replace the accumulated individual flags
    (`warn_redundant_casts`, `no_implicit_reexport`, `check_untyped_defs`,
    `disallow_subclassing_any`, `disallow_untyped_decorators`, `disallow_any_generics`,
    `warn_return_any`, `warn_unused_ignores`, `strict_equality`, `disallow_untyped_defs`,
    `disallow_incomplete_defs`, `disallow_untyped_calls`) with a single `strict = true`.
    **Keep** `warn_unused_configs` (in strict, but harmless to state), `python_version`,
    `explicit_package_bases`, `namespace_packages`, `ignore_missing_imports`, `mypy_path`.
57. [x] Write the replacement comment block above `strict = true`: enumerate the 13 flags
    `--strict` comprises as of mypy 2.3.1 citing brief 004 §1; state `disallow_any_expr` and
    `warn_unreachable` are NOT in it and stay off deliberately; record the §PEP 696 rejection
    (§D3) in one line.
58. [x] Verify `make type-check` is **still** zero errors. Any error here means a strict flag was
    never individually landed — `extra_checks` is the prime suspect (see Risks).

### Phase 8 — `disallow_any_unimported` (7)

59. [x] Work the file list captured in step 3. Expect these to be annotations derived from
    casbin / aiokafka / nats-py, all confirmed untyped (brief 005 §casbin, §aiokafka, §nats-py,
    and its Options-compared table). Where a real type exists, use it; otherwise narrow-suppress
    `# type: ignore[no-any-unimported]` with a one-line reason naming the untyped library.
60. [x] §021-stop check: if this phase needs more than **7** suppressions total, do not land the
    flag — commit the annotation improvements, leave the flag off, and file a BACKLOG row.
61. [x] `pyproject.toml` — add `disallow_any_unimported = true` below `strict = true`, with a
    comment noting it is NOT part of `--strict` and that the previous "DECIDED NEVER" verdict was
    re-opened on measurement (7 errors), per U-8.

### Phase 9 — measure, document, close

62. [x] Re-measure M1 (`rg -o 'type: ignore' varco_*/varco_* | wc -l`) and produce a
    **two-line** report in the BACKLOG row: raw M1, and M1 minus third-party-seam suppressions
    (which §RL-14-metric excludes from "debt"). List every suppression added by this plan with
    its file, code and library.
63. [x] `pyproject.toml` — final read-through: the `[[tool.mypy.overrides]]` section is empty
    (RL-14b's completion criterion) and no "STOPPED"/"OUT OF SCOPE"/"DECIDED NEVER (three flags)"
    comment survives except the `disallow_any_expr` one.
64. [x] `CLAUDE.md` — rewrite the "**mypy strictness ramp**" bullet list (the Landed / Stopped /
    Decided-never / Out-of-scope four-way split) as a short "ramp complete" paragraph: config is
    `strict = true` + `disallow_any_unimported`; `disallow_any_expr` is the only permanent
    exclusion and why; M1/M2 definitions stay (M2 is now trivially the gate); point at this plan.
65. [x] `BACKLOG.md` — mark **RL-14 ✅ DONE (Plan 021)**, **RL-14b ✅**, **RL-14c ✅**, **RL-14d ✅**.
    Correct RL-14d's stale count (67 → measured 64) in the same edit, per U-8 — do not silently
    close a row over a number that was never true.
66. [x] `design/plan-017-findings/research/005-third-party-generic-params.md` — add an in-tree
    correction banner at the top of the SQLAlchemy section per §D5, pointing at this plan and at
    the measurement file. Follow research 002 §1's existing banner convention.
67. [x] `CHANGELOG.md` — one entry under the current version, **non-breaking**, internal/typing:
    "mypy `strict = true` across all ten packages; no public signature gained or lost a type
    parameter (Plan 021 §D3)". Explicitly state it is not a BREAKING change so a reader does not
    have to infer it.
68. [x] Final full verification (see below) on a clean checkout.

---

## Edge cases

- **A `no-any-return` "fix" that changes behaviour** → not allowed. If the honest fix is a real
  runtime coercion, file a BACKLOG row and suppress. `make test` red at a phase boundary is the
  detector.
- **Annotating narrows a public signature** (e.g. `AsyncCache[str, bytes]` where any cache was
  accepted) → use `[Any]`; §D3.
- **Alembic autogen reintroduces bare `TypeEngine`/`dict`** in a future revision file → the new
  revision fails `make type-check` in CI before merge. That is the intended behaviour, not a
  problem; the fix is to annotate the new revision. Do **not** add a
  `[[tool.mypy.overrides]]` exemption for `varco_sa/migrations/versions/*` — an exemption grows
  silently, which is the RL-18 failure mode.
- **`warn_unused_ignores` strands a suppression mid-phase** → delete it in the same commit as the
  flag that made it unused (§RL-14-ignores, `plans/020-...md:593-599`). Never carry a
  suppression whose reason evaporated.
- **A suppression's error code changes** (mypy's baseline already emits
  `Error code "no-any-return" not covered by "type: ignore[safe-super]"` notes at
  `repository_mixin.py:142-182`) → widen to the multi-code form
  `# type: ignore[safe-super, no-any-return]` only if the structural fix in step 16 does not
  remove the need. Prefer the structural fix.
- **`varco_beanie/uow.py`** carries the open KI-12 bug at `:62`; step 46 touches `:50` only.
  Annotation only — do not fix KI-12 (scope-guard discipline).
- **DI binding sites** (`varco_sa/di.py:311`, `varco_beanie/di.py:188`) — providify reads real
  return annotations. Never quote them; keep the referenced type imported at module scope
  (CLAUDE.md's two annotation pitfalls). Re-run the `validate_bindings()` tests.
- **`AsyncCache` is `runtime_checkable`** (`cache/base.py:66`) — parameterizing *use sites* does
  not touch the protocol's method set, so `isinstance()` semantics are unchanged. Do not add or
  remove a method.

## Verification

Run at every phase boundary, and in full at step 68:

```bash
# 1. Types — the gate. Must print "Success: no issues found in 439 source files".
make type-check

# 2. Lint + format gate (Plan 020 / RL-17). Never `uvx ruff`.
make lint

# 3. All eleven unit suites, accumulated. Annotations must not change behaviour;
#    a red suite means a real semantic change crept in — revert, do not patch.
make test

# 4. Targeted regression guards for the riskiest edits
uv run pytest varco_core/tests/test_event.py            # Phase 2, _UNSET sentinel
uv run pytest varco_fastapi/tests/test_di_binding_health.py   # Phase 5b, DI annotations
uv run pytest varco_sa/tests/ varco_beanie/tests/ varco_redis/tests/   # Phases 5c / 6

# 5. Per-flag confirmations while iterating
uv run mypy --strict <ten dirs>                # Phases 0–7: watch this go 272 → 0
uv run mypy --disallow-any-unimported <ten dirs>   # Phases 3, 8

# 6. Suppression accounting (Phase 9)
rg -o 'type: ignore' varco_*/varco_* | wc -l   # M1; baseline 327
rg -n 'type: ignore\]?\s*$' varco_*/varco_*    # must return nothing new: no bare ignores
```

**Python 3.13**: no separate mypy run is needed. `[tool.mypy]` pins `python_version = "3.12"`,
so mypy's result is interpreter-independent — which is exactly why CI's `lint` job runs 3.12
only (CLAUDE.md §CI). What *does* need both versions is `make test`, and CI's `unit` matrix
`[3.12, 3.13]` already provides it; do not merge on a green local 3.12 run alone.

## Risks

- **A `cast()` hides a real type mismatch that only fails at runtime.** Invariant: `make test`
  green at every phase boundary, and `cast()` is second choice behind annotating a local
  (brief 004 §3). Every `cast()` added carries a one-line reason. Highest exposure: Phase 6's
  `client/generic.py` and `client/base.py` sites, which sit on the deserialization boundary.
- **A use-site parameterization silently narrows a public signature.** Invariant: `[Any]` at any
  boundary exported in a package's `__all__` (§D3). Reviewer check: no `def` in a public module
  gains a parameter type *narrower* than what it accepted before.
- **The DI container stops resolving a binding** because an annotation changed shape
  (`AsyncRepository[Any, Any]` vs `AsyncRepository`). Invariant: every package's
  `validate_bindings()` test stays green; `varco_fastapi/tests/test_di_binding_health.py` is the
  canary. This is the one place where an annotation *is* runtime behaviour in this repo.
- **M1 rises.** Budget: net-new suppressions ≤ **11** (4 `no-untyped-call` + 7
  `no-any-unimported`), each narrow, each with a reason, each a genuinely-untyped third-party
  seam. Exceeding it means a phase was landed wrong. Note M1 also *falls* by 2 in Phase 2 (§D4).
- ⚠️ **ASSUMPTION — `extra_checks` produces 0 errors.** Inferred, not measured: the five measured
  error codes sum to exactly 272, which is the total `--strict` count, leaving nothing for
  `extra_checks`. Step 58 is the check. If Phase 7 turns red, land `extra_checks = true` as its
  own mini-phase before flipping `strict = true`.
- ⚠️ **ASSUMPTION — brief 005 §SQLAlchemy is wrong about six symbols** (§D5). This is
  *measurement-backed* (mypy emits `type-arg` on all six), so the assumption is really the
  reverse: that the brief's *other* sections are still trustworthy. Mitigation: step 40 derives
  every third-party parameter from the installed distribution regardless of what the brief says.
- ⚠️ **ASSUMPTION (unverified, and moot) — brief 005's "Starlette 1.0.0 introduced a generic
  `WebSocket[T]`"** (`005-...md:59-62`). Not verified against our tree, and the measurement found
  **zero** Starlette generics among the 176 `type-arg` errors — `varco_ws` contributes 0 errors
  in total. Nothing in this plan depends on it. If `varco_ws` ever does surface a `WebSocket`
  `type-arg` error, verify against the installed Starlette before believing the brief.
- ⚠️ **ASSUMPTION (unverified, and moot) — mypy 2.3.1's PEP 696 support** (brief 004 §2). The
  plan rejects the PEP 696 route (§D3), so nothing depends on the claim being right.
- **A future mypy pin bump adds a flag to `--strict` and turns CI red.** Accepted, and preferred
  to the silent alternative (§D1). The red arrives attached to a deliberate pin-bump commit, and
  the fix is either to fix the errors or to add one named `[[tool.mypy.overrides]]` exemption.
- **The `varco_fastapi/router/mixins.py` ClassVar pairs are FastAPI passthroughs** — the correct
  `responses=` type is `dict[int | str, dict[str, Any]]`, which is easy to get subtly wrong and
  which FastAPI validates only at route-registration time. Invariant: `varco_fastapi`'s router
  tests must exercise a route with a non-empty `_create_responses` after the edit; if none does,
  that is a missing test, not a licence to guess.
