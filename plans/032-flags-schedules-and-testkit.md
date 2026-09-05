# Plan 032 — Feature-flag seam (D7), recurring schedules (D6), `varco-testkit` (D8)

Covers the three 🟢 **nice** rows of BACKLOG's *"3.1 — API surface & interop (discover,
2026-09-04)"* cycle: **D7** (`AbstractFeatureFlags` seam + OpenFeature provider, S–M), **D6**
(recurring schedules, M), **D8** (ship `varco-testkit` to PyPI, M).

## Scope and siblings

One of four plans covering that cycle; see plan 029's *Scope and siblings* table.

⚠️ **This plan is explicitly the droppable one.** All three rows are 🟢, none blocks the release
(`BACKLOG.md:33` — only `N1` and `D1` are must-ship). Each phase is independent of the others and
of plans 029–031; any subset may ship, in any order, or none.

**Research brief backing this plan:** `design/research/004-flags-asyncapi-and-sbom-tooling.md` §1
(OpenFeature). D6 and D8 rest on in-repo evidence and settled patterns — no brief was spent, per
the research gate's "do not spend a brief to confirm what the repo already proves".

## Goal

A feature-flag seam exists that does not mortgage varco's API to a pre-1.0 spec. A `Schedule`
entity materializes `Job` rows, closing the loop the job subsystem's zoned fields were designed
for. A decision is made and recorded about packaging `testkit`.

## Non-goals

- **No OpenFeature provider.** The un-park trigger has not fired — verified, not assumed
  (§D-D7-trigger). This is the plan's most important negative result.
- **No RRULE engine.** §D-D6-cron.
- **No `Schedule` execution path.** A schedule *materializes* `Job` rows; the existing
  `AbstractJobRunner` runs them, unchanged. If this phase starts writing a second runner, it has
  gone wrong.
- **No packaging of `varco_chaos`.** §D-D8-narrow.

---

## Design

### Phase order

```
P0  D7   🟢 S    varco_core.flags — ABC + in-memory + DI, NO provider
P1  D6   🟢 M    Schedule entity + materializer + SA migration
P2  D8   🟢 M    ⛔ DECISION-GATED — package varco-testkit (or record the refusal)
```

---

## §D-D7-trigger — the trigger has NOT fired; ship the seam, defer the provider

The backlog was explicit that this needed checking and that prior evidence was confused: the
recorded un-park condition is *"the `openfeature-sdk` Python SDK reaches 1.0"*, while briefs 001
and 002 evidenced the **specification** at v0.9 — a different artifact. `/plan` was told to verify
the SDK itself.

**Verified (brief 004 §1, fetched 2026-09-04):**

| Artifact | Version | Date | 1.0? |
|---|---|---|---|
| `openfeature-sdk` (PyPI) | **0.10.0** | 2026-06-01 | ❌ **no** |
| OpenFeature specification | 0.9.0 | 2026-07-29 | ❌ no |

The trigger has **not** fired. Worse, brief 004 §1 records that v0.10.0 *itself* shipped a breaking
change inside a minor bump — `set_provider()` no longer blocks; callers must use
`set_provider_and_wait()` — and that the spec explicitly warns breaking changes will continue while
version < 1.0.

Plan 022's §D-OF rejected building against this "inside a version freeze". The freeze is over, but
the underlying objection is unchanged: an ABC shaped to a moving pre-1.0 spec, shipped under
lockstep versioning and the `api_surface.py` gate, is a liability.

**Decision — build the seam, do not build the provider.** This is exactly what the backlog
anticipated: *"If it is still pre-1.0, `D7` ships the seam and defers the provider."*

`varco_core/varco_core/flags/` gets `AbstractFeatureFlags` (an ABC varco designs for varco, **not**
a transcription of OpenFeature's `AbstractProvider`), a `FlagEvaluationContext` wired to
`RequestContext` and `current_tenant()`, an `InMemoryFeatureFlags` default, and a
`NullFeatureFlags` no-op bound by default so nothing changes for anyone who does not opt in.

DESIGN: a varco-shaped ABC now, an adapter later
  ✅ The seam is the valuable half — the backlog's own reasoning — and a tenant/user-aware
     evaluation context is useful with no provider at all.
  ✅ varco's ABC is stable under *our* SemVer because we own its shape; an OpenFeature-shaped ABC
     inherits a pre-1.0 spec's churn into a frozen public surface.
  ✅ When the SDK reaches 1.0, an `OpenFeatureFlags` adapter is additive — a new implementation of
     an existing ABC, the cheapest possible change.
  ✅ Brief 004 §1 notes the SDK ships **no** in-memory/no-op test provider, so an adopter would
     have to write one anyway. `InMemoryFeatureFlags` is that, and it is useful immediately.
  ❌ Our ABC will not match OpenFeature's method names, so the future adapter does real translation
     rather than pass-through. Accepted: four typed resolution methods is a small translation
     surface (brief 004 §1 enumerates them).
  ❌ Someone will ask why we did not just use OpenFeature. Answered in the docs, with the version
     evidence and the date.
  Rejected — **transcribing OpenFeature's `AbstractProvider` as our ABC**: ❌ imports pre-1.0 churn
  directly into a gated public API.
  Rejected — **waiting entirely**: ❌ the backlog re-opened this "on the strength of the seam being
  worth having regardless"; the seam does not depend on the trigger.

**DI**: bound opt-in via `enable_feature_flags(container)` following
`varco_casbin.di.enable_policy_authorizer` (CLAUDE.md's `enable_*` verb — an opt-in binding that
would otherwise shadow an app default). Never a scanned `@Configuration`.

**The BACKLOG Parked row must be amended** with the verified versions and the fetch date, so the
next cycle re-argues against a record rather than re-researching.

### §D-D6-cron — cron only; RRULE is parked; zero new dependencies

CLAUDE.md already sketches the shape: *"a future `Schedule` entity that produces `Job` rows exactly
like these"*, and the job model was built for it — `run_at_wall`/`run_at_tz`/`run_at_fold` exist
with the comment *"`run_at` is MATERIALIZED, not replaced. These three fields are the intent"*
(`varco_core/varco_core/job/base.py:258-273`).

**Cron only.** A 5-field cron expression is a small, well-understood parser (~150 lines, no
dependency). RRULE (RFC 5545) is a large spec whose complete implementation is `dateutil.rrule` —
a new runtime dependency for a 🟢 row, in a repo whose standing rule is zero new runtime
dependencies in `varco_core`. Parked with a trigger.

**DST handling is not new work** — it is the reason the zoned fields exist. The materializer
computes the next wall-clock occurrence from the cron expression in the schedule's timezone, then
calls `resolve_zoned()` with the schedule's `GapPolicy`/`OverlapPolicy`
(`varco_core/varco_core/tz/schedule.py:53-115`) to get the UTC instant. `run_at` receives the
materialization; `run_at_wall`/`run_at_tz`/`run_at_fold` receive the intent. This is the design the
fields were added for, used as intended.

**Catch-up policy** — what a materializer that was down for six hours does with missed
occurrences. Three behaviours, explicit per schedule, defaulting to `SKIP`:

| Policy | Behaviour |
|---|---|
| `SKIP` (default) | Materialize only the next future occurrence. Missed ones are lost |
| `FIRE_ONCE` | Materialize one job for the missed window, then resume |
| `BACKFILL_ALL` | Materialize every missed occurrence, bounded by `max_backfill` |

DESIGN: `SKIP` as the default
  ✅ A "send the nightly digest" schedule that missed three nights should send tonight's digest,
     not three at once — the surprising-and-expensive failure mode is the one to avoid by default.
  ❌ A schedule with real catch-up semantics (billing) must opt in. Correct: that is a decision the
     schedule's owner must make consciously.

**Materialization is leased.** The materializer must not double-produce when two instances run.
It reuses the job store's existing fenced-lease primitives (`try_claim`/`renew`/`save(expected_epoch=)`
→ `StaleLeaseError`) rather than introducing a second locking model, and a
`UNIQUE(schedule_id, run_at)` index makes double-materialization impossible rather than merely
unlikely.

### §D-D8-narrow — package `varco_conformance` only; hold `varco_chaos` back

The backlog states the trade-off precisely: *"Packaging it converts internal test scaffolding into
public API subject to the `api_surface.py` gate — that trade-off is the whole decision."*

**Recommendation: ship a narrowed `varco-testkit` containing `varco_conformance` only.**

The value is asymmetric across `testkit/`'s contents:

| Content | Ship? | Why |
|---|---|---|
| `varco_conformance/` (5 suites) | ✅ **yes** | Its entire purpose is to be subclassed by a backend implementation. An out-of-tree backend — the exact case CLAUDE.md's `isinstance()`-preserving rules protect — has no way to prove it satisfies `AbstractEventBus`/`AsyncCache`/`AbstractJobStore`/`AbstractDeadLetterQueue`/`ChannelManager` without it. This is a genuine public contract already documented as one |
| `varco_chaos/` | ❌ **no** | Wraps testcontainers and is the *only* sanctioned caller of `get_wrapped_container()`. Its API is unstable by design, it needs Docker, and no downstream has asked. Freezing it under the API gate buys nothing |
| `tls_fixtures.py`, `_tls_test_certs.py` | ❌ **no** | Internal helpers; the leading underscore says so |

DESIGN: narrow package over all-of-testkit or nothing
  ✅ The gate cost is paid only where there is a corresponding benefit. `varco_conformance`'s
     surface is already stable — five suite classes with abstract fixtures — and already
     effectively public via `COVERAGE.md`.
  ✅ In-repo consumption is unchanged: packages keep reaching it through
     `pythonpath = ["../testkit"]`, so no test wiring moves.
  ✅ `varco_chaos` stays free to change.
  ❌ An eleventh distribution: `scripts/packages.sh`, `scripts/bump.py` lockstep, `release.yml`
     matrix, a PyPI trusted publisher and a GitHub Environment (manual operator steps).
  ❌ Permanent: an `__all__` name, once published, is removable only at 4.0.0.
  Rejected — **ship all of `testkit`**: ❌ freezes `varco_chaos`'s deliberately-unstable surface.
  Rejected — **ship nothing**: ❌ every downstream backend rebuilds conformance fixtures that exist,
  and the row exists because that is a real cost.

⛔ **This phase is decision-gated.** Step 12 is an explicit go/no-go. If the answer is no, the plan
still delivers a written record of *why* — which is worth more than a rushed eleventh
distribution, and prevents the question being reopened from scratch next cycle.

---

## Steps

### Phase 0 — D7: the flags seam

1. [x] `varco_core/varco_core/flags/` — `base.py` (`AbstractFeatureFlags`, `FlagEvaluationContext`,
       `FlagResolution[T]`), `memory.py` (`InMemoryFeatureFlags`), `null.py` (`NullFeatureFlags`),
       `di.py` (`enable_feature_flags`). Four typed resolutions: bool, string, numeric, object.
2. [x] `FlagEvaluationContext` reads `current_tenant()` for the tenant (never `RequestContext` —
       CLAUDE.md's rule) and `RequestContext` for request-scoped attributes.
3. [x] `NullFeatureFlags` bound by default; `enable_feature_flags(container)` opts in
       (`varco_casbin/varco_casbin/di.py`'s `enable_policy_authorizer` precedent). **Never** a
       scanned `@Configuration`.
4. [x] Tests: each resolution type; default returned on unknown flag; tenant-scoped override;
       `NullFeatureFlags` returns the caller's default for everything.
5. [x] Docs: README section; a short `technical_docs/features/feature-flags.md` recording
       §D-D7-trigger's version evidence **with the fetch date**, and stating that an
       `OpenFeatureFlags` adapter is additive when the SDK reaches 1.0.
6. [x] Amend BACKLOG's OpenFeature Parked row: trigger checked 2026-09-04, SDK at 0.10.0, spec at
       0.9.0, **not fired**; seam shipped, provider deferred. Cite brief 004 §1.
7. [x] API surface snapshot + import budget (`--warn-only`; `varco_core/__init__.py` stays lazy).

⛔ **CHECKPOINT**

### Phase 1 — D6: recurring schedules

8. [x] `varco_core/varco_core/schedule/` — `Schedule` entity (cron expression, timezone,
       `GapPolicy`/`OverlapPolicy`, catch-up policy, payload template, enabled flag, last-materialized
       marker), `cron.py` (5-field parser + `next_after()`, zero dependencies),
       `materializer.py`.
9. [x] Materializer per §D-D6-cron: next wall-clock occurrence → `resolve_zoned()` → `run_at`, with
       `run_at_wall`/`run_at_tz`/`run_at_fold` carrying the intent
       (`varco_core/varco_core/job/base.py:258-273`). ⚠️ **Deviation from the plan's literal
       mechanism, documented in `materializer.py`'s DESIGN note**: a synthetic lease row saved
       through `AbstractJobStore` would itself count as a `Job` in every `list_by_status()`/
       `all_jobs()` caller — that broke the concurrent-materializer test's `store.all_jobs() ==
       1` assertion outright, not just stylistically. Implemented instead: a deterministic
       (`uuid5`-derived) occurrence `Job.job_id` (idempotent-upsert convergence across processes)
       plus a lazily-created, per-schedule-id `asyncio.Lock` (in-process exclusivity only). The
       cross-process backstop is `UNIQUE(schedule_id)` on the SA/Beanie `Schedule` row (Step 10)
       plus the deterministic id — see that step's note on why `UNIQUE(schedule_id, run_at)`
       literally does not apply (no `run_at` column exists on `Schedule`).
10. [x] `varco_sa` + `varco_beanie` repositories; SA migration revision +
        `register_framework_metadata()`. Added `AbstractScheduleRepository` +
        `InMemoryScheduleRepository` to `varco_core.schedule.repository` first (same "dedicated
        ABC over `AsyncRepository`" pattern as `WebhookSubscriptionRepository`) since the plan's
        Step 10 wording implies backend implementations of an ABC that did not yet exist. Unit
        tests added for both backends (sqlite in-memory for SA, mocked Beanie operations for
        Mongo) — the "repository shapes the test-writer deferred".
11. [x] Tests: cron parsing incl. ranges/steps/lists and **invalid expressions rejected loudly**;
        a **spring-forward gap** and a **fall-back ambiguity** resolved per `GapPolicy`/`OverlapPolicy`
        (the whole reason the zoned fields exist — these two tests are the phase's core);
        all three catch-up policies; concurrent materializers produce exactly one job per
        occurrence; a disabled schedule produces none. (Pre-existing red tests — all pass
        unmodified except one mechanical `ruff format`/unused-import cleanup.)
12. [x] Docs: `technical_docs/features/recurring-schedules.md` with a Pitfalls table (DST gaps,
        catch-up surprise, materializer downtime); README; a CLAUDE.md Decision-Tree branch under
        the existing `tz/schedule.py` line, replacing its "Non-goal" note for RRULE with a pointer.

⛔ **CHECKPOINT**

### Phase 2 — D8: `varco-testkit` (decision-gated)

⛔ **NOT STARTED — the go/no-go gate at Step 13 has not been put to the maintainer.** Per the
implementation instructions for this pass, Phase 2 is explicitly out of scope until that
question is asked and answered. Steps 13–21 remain unchecked below.

13. [ ] ⛔ **GO/NO-GO.** Confirm §D-D8-narrow with the maintainer: publish `varco_conformance` only,
        as `varco-testkit`, accepting the `api_surface.py` gate on it permanently. **If no, stop
        here and record the refusal and its reasoning in BACKLOG — that is this phase's deliverable
        in the negative case.**
14. [ ] `testkit/pyproject.toml` — distribution `varco-testkit`, package `varco_conformance` only,
        version 3.1.0 in lockstep, sibling requirement `varco-core~=3.0` (compatible-release, never
        `==` — CONTRIBUTING's policy).
15. [ ] Add to `[tool.uv.workspace]`; verify `scripts/packages.sh` picks it up (it is the RL-18
        derivation every script depends on) and that `scripts/bump.py --check` stays coherent.
16. [ ] Define `varco_conformance.__all__` deliberately — the five suite classes and nothing else.
        This is the frozen surface; anything not exported stays free to change.
17. [ ] Regenerate the API surface snapshot with the new package included.
18. [ ] `release.yml`: the `packages` matrix derives from `scripts/packages.sh`, so verify rather
        than hand-edit. Add the `pypi-varco-testkit` environment to the publish job's matrix.
19. [ ] **Manual operator steps** (not scriptable in-repo — record in
        `design/varco-1-0-release/release-runbook.md`): create the `pypi-varco-testkit` GitHub
        Environment (no deployment-branch restriction) and the PyPI trusted-publisher config
        (owner `edoardoscarpaci`, repo `varco`, workflow `release.yml`, environment
        `pypi-varco-testkit`).
20. [ ] Keep in-repo consumption on `pythonpath = ["../testkit"]` — no test wiring changes.
21. [ ] Docs: `testkit/README.md` on using the suites from a downstream backend; a
        `COVERAGE.md` note that the suites are now public; CHANGELOG entry for the new
        distribution.

⛔ **CHECKPOINT** — `make lint`, `make type-check`, `make test`, `scripts/bump.py --check`,
`api_surface.py --check` all green.

---

## Parked

| Item | Why | Un-park trigger |
|---|---|---|
| **OpenFeature provider** | SDK at 0.10.0, spec at 0.9.0, both pre-1.0 with breaking changes ongoing (brief 004 §1, checked 2026-09-04) | `openfeature-sdk` reaches **1.0.0** — the SDK, not the spec. Then the adapter is additive against §D-D7's ABC |
| **RRULE / RFC 5545 schedules** | Complete implementation means a `dateutil` runtime dependency for a 🟢 row | A consumer needs a recurrence cron cannot express, and accepts the dependency |
| **Packaging `varco_chaos`** | Unstable by design; wraps testcontainers; nobody asked | A downstream writes chaos tests against varco backends and asks |
| **Seconds-precision / 6-field cron** | 5-field covers the case; a job scheduler with second granularity is a different tool | A consumer needs sub-minute scheduling |
| **Flag-change streaming / provider events** | OpenFeature's event model is part of what is pre-1.0 | Ships with the provider |

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| varco's flags ABC diverges from OpenFeature's shape, making the future adapter awkward | Low | Deliberate (§D-D7-trigger). Four typed resolutions is a small translation surface; the alternative imports pre-1.0 churn into a gated API |
| ⚠️ **ASSUMPTION** — `openfeature-sdk` stays pre-1.0 for varco 3.1's life. If it hits 1.0 mid-cycle the deferral looks premature | Low | Adding the adapter is purely additive; nothing built here is wasted |
| Hand-rolled cron parser has edge-case bugs | Medium | Confine to the 5-field standard; reject anything unparseable loudly rather than guessing; table-driven tests incl. ranges/steps/lists |
| DST materialization produces a duplicate or a missing job | High | Exactly why `resolve_zoned()` and `GapPolicy`/`OverlapPolicy` already exist; the spring-forward and fall-back tests (Step 11) are the phase's core, plus `UNIQUE(schedule_id, run_at)` |
| Two materializers double-produce | Medium | Fenced lease reusing the job store's existing primitives (no second locking model) + the unique index |
| ⚠️ **ASSUMPTION** — `varco_conformance`'s surface is stable enough to freeze. Based on it being five classes with abstract fixtures, already documented as a contract — **not** on a formal API review | Medium | Step 16 defines `__all__` deliberately and narrowly; Step 13's go/no-go is the real control |
| An eleventh distribution adds permanent release/versioning overhead | Medium | Every script derives its package list from `scripts/packages.sh` (RL-18), so the marginal cost is genuinely small — verified at Step 15, not assumed |
| D8's manual operator steps are forgotten, and the release fails at publish | Medium | Step 19 records them in the runbook, which is the durable record for exactly this |

## Open questions

1. **Should `AbstractFeatureFlags` resolution be sync or async?** Async matches every other varco
   seam and permits a remote provider; sync matches how flags are read (hot, in a request path) and
   how most SDKs work. Decide at Step 1 — lean **async**, for consistency with the rest of varco
   and because a sync ABC forecloses a remote provider permanently, while an async one costs a
   local implementation nothing.
2. **Does `Schedule` belong to a tenant?** Almost certainly `TenantScope.TENANT`, but a
   platform-wide maintenance schedule is a real case for `GLOBAL`. Decide at Step 8 — lean
   `TENANT` by default with `GLOBAL` available via `Meta.tenant_scope`, which is the existing
   mechanism and needs no new one.
