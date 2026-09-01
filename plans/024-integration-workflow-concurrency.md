# Plan 024 — `integration.yml` concurrency: only the newest merge to `main` consumes a full run

Originating request (verbatim):

> "I would like to modify the integration test into the github action, that if multiple PR are
> merged only the last one will trigger the action so it just use the time needed"

Sole external grounding: `design/research/001-github-actions-concurrency-semantics.md` (dated
2026-09-01, freshness-sensitive). **Every** GitHub Actions behavioural claim below cites a section
of that brief. Nothing about Actions semantics in this plan is asserted from memory.

## Goal

After this plan, `.github/workflows/integration.yml` carries exactly one new workflow-level
`concurrency` stanza, event-name-scoped, with `cancel-in-progress: true`. Consequence: when several
PRs merge to `main` in quick succession, only the newest merge commit's integration run reaches
completion — earlier in-flight/pending runs are cancelled — while the nightly `schedule` run and
any `workflow_dispatch` run remain in *separate* concurrency groups and can never be cancelled by,
or cancel, a merge.

## Non-goals

- **No trigger changes.** `push: [main]` + nightly `schedule` + `workflow_dispatch` stay exactly as
  they are (`integration.yml:63-68`). Plan 017 §RL-5-triggers owns that decision and it is not
  relitigated here.
- **No change to the `chaos` job's gating.** `if: github.event_name != 'push'`
  (`integration.yml:95`) is untouched (Plan 018 §RT7-ci).
- **No `needs:` between `integration` and `chaos`.** They stay fully independent
  (`integration.yml` header, §RT7-ci ✅ bullet 2).
- **Neither job becomes a required status check.** Not now, not as a side effect. See §D-REQUIRED —
  this change makes that *more* load-bearing, not less.
- **No change to `test.yml`, `docs.yml`, `release.yml`, or `scorecard.yml`.** In particular
  `test.yml:31-33`'s existing stanza is left alone (it is correct *for that file* — see §D-WHYNOT-A).
- **No new lint/validation tooling.** No `actionlint` in `make lint`, no `.pre-commit` hook, no new
  CI job. The repo has no workflow-linting convention today and this change does not justify
  inventing one (§VERIFY).
- **No `if: always()` Docker-cleanup step.** Deliberate, argued in §D-LEAK.
- **No `queue:` key.** Deliberate, argued in §D-WHYNOT-QUEUE.
- **No changes to `scripts/integration_tests.sh`, `scripts/unit_tests.sh`, or the `Makefile`
  targets.** The testcontainers lifecycle is per-suite via conftest fixtures; there is no global
  trap or cleanup hook that a mid-run cancellation would skip, so there is nothing to harden.

---

## Design

### §D-SHAPE — the chosen stanza

Added at workflow level, immediately after the `on:` block and before `permissions:` (the same
position `test.yml:31-33` and `docs.yml:49-51` use), preceded by a `DESIGN §…` comment block in this
file's house style:

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.event_name }}-${{ github.ref }}
  cancel-in-progress: true
```

Resulting group names (brief §3 for the `github.ref` values, §4 for the pattern):

| Trigger | `github.event_name` | `github.ref` | Group |
|---|---|---|---|
| merge to `main` | `push` | `refs/heads/main` | `Integration Tests-push-refs/heads/main` |
| nightly cron | `schedule` | `refs/heads/main` (default branch) | `Integration Tests-schedule-refs/heads/main` |
| manual run | `workflow_dispatch` | selected branch, usually `refs/heads/main` | `Integration Tests-workflow_dispatch-refs/heads/main` |

`${{ github.workflow }}` expands to the workflow's `name:` — `Integration Tests`
(`integration.yml:61`) — which is structurally distinct from `test.yml`'s `Tests` (`test.yml:23`).
**Invariant 4 (must not share `test.yml`'s group) is satisfied by construction**, not by
coincidence: the two files can only collide if someone renames one workflow to the other's exact
name.

Where cancellation can *actually* occur, per group:

- **`push` group** — the target. Two merges inside the 45-minute window: the older run is cancelled,
  the newer runs. This is the user's request, literally.
- **`schedule` group** — effectively inert. A nightly cron fires once per day and the job is capped
  at `timeout-minutes: 45` (`integration.yml:77`, `:96`), so two scheduled runs cannot overlap in
  normal operation. `cancel-in-progress: true` here is a safety net against a duplicated/stuck cron,
  nothing more.
- **`workflow_dispatch` group** — a second manual dispatch on the same ref cancels the first.
  Deliberate operator action, visible in the UI, cheap to redo. A dispatch on a *different* branch is
  a different `github.ref` and therefore a different group.

### §D-LEVEL — workflow-level, not job-level

Rejected alternative: job-level `concurrency` on `integration` only, leaving `chaos` in no group at
all (brief §1 confirms both placements are legal and take the same fields).

- ✅ (for job-level) `chaos` would be structurally, provably outside any concurrency group —
  invariant 2 ("the concurrency design must not implicitly couple them") would be guaranteed by
  shape rather than by argument.
- ❌ The guarantee buys nothing that is ever exercised. Cancellation only actually happens in the
  `push` group (§D-SHAPE), and `chaos` **does not run on `push`** at all (`integration.yml:95`,
  Plan 018 §RT7-ci). So under the workflow-level stanza, a push-triggered cancellation can only ever
  cancel an `integration` job — the theoretical coupling is never realized.
- ❌ Job-level would still need the identical `github.event_name`-scoped expression (the `push`/
  `schedule`/`workflow_dispatch` ref collision of brief §3 applies to the `integration` job just the
  same), so it is the same expression written in a more fragile place: a third job added later would
  silently be ungrouped unless someone remembers to add a stanza to it.
- ❌ Two stanzas instead of one, for a file whose header already argues (§RT7-ci ❌ bullet 1) that
  duplication in this file is accepted only where the alternative is *more* machinery.

**Decision: workflow-level.** Invariant 2 is preserved in the sense that matters — no `needs:`
edge is created, neither job's `if:` changes, and the only trigger on which cancellation fires is
one where `chaos` is skipped anyway. The residual case (a second manual dispatch cancels a run that
*does* include `chaos`) is a deliberate human action, and is listed in §EDGE.

### §D-WHYNOT-A — why `test.yml`'s exact stanza is wrong *for this file*

`test.yml:31-33` uses `group: ${{ github.workflow }}-${{ github.ref }}`, `cancel-in-progress: true`,
with no explanatory comment. Copying it here would be a defect:

- Brief §3: on `push: main`, `schedule`, and `workflow_dispatch`, `github.ref` **all** evaluate to
  `refs/heads/main`. Brief §3 states the consequence explicitly — a group keyed only on
  `github.ref` "will **collide** — a scheduled cron run and a push-to-main run share the same group
  and will cancel each other if `cancel-in-progress: true`".
- Concretely: a merge landing at 05:02 would cancel the nightly run that started at 05:00, and with
  it the **`chaos` job** — the only trigger on which chaos runs at all. The nightly chaos signal
  (Plan 018 §RT7-ci: "Nightly gives a real, attributable signal with a real owner and no merge
  pressure") would be silently deleted by an unrelated merge, on a workflow that is not a required
  check and that nobody is watching. That is the worst failure shape available here: an invisible
  loss of the repo's only comprehensive daily broker signal.
- Symmetrically, the nightly cron would cancel an in-flight merge run.

`test.yml`'s stanza is nonetheless *correct where it is*: `test.yml` runs only on `push: [main]` and
`pull_request: [main]` (`test.yml:25-29`) — there is no `schedule` and no `workflow_dispatch`, so
brief §3's collision cannot arise, and on `pull_request` `github.ref` is the per-PR merge ref, which
groups PRs correctly. **Do not "harmonize" the two files.** The DESIGN block must say this, so the
next reader does not treat the difference as drift.

### §D-WHYNOT-C — why not `docs.yml`'s shape (serialize, don't cancel)

`docs.yml:49-51` uses `group: docs-deploy`, `cancel-in-progress: false` — a deliberate
serialization, because two concurrent `mike` deploys would race on the `gh-pages` branch (Plan 023
§Edge cases). That is the opposite need. Brief's Strategy C: all runs queue sequentially, no
cancellation.

- ✅ Every merge commit eventually gets a completed integration run — strictly better coverage.
- ❌ It is exactly the behaviour the user asked to remove. Three merges = three sequential 45-minute
  runs, and the tip's result arrives ~135 minutes late instead of ~45.

Rejected: it maximizes, rather than minimizes, "the time needed".

### §D-WHYNOT-QUEUE — why not `queue: max`

Brief §1 (GitHub Changelog, May 2026): `queue: max` allows up to 100 pending runs in FIFO order,
and **cannot** be combined with `cancel-in-progress: true` (validation error). Brief §2: with
`queue: max`, "the middle run is preserved and will eventually execute".

- ✅ No run is ever lost; every merge commit gets its own attributable result.
- ❌ It is the same trade as §D-WHYNOT-C, amplified: `queue: max` exists to stop losing queued runs,
  which is the precise thing the user asked us to start doing. It would raise total runner time, not
  lower it.
- ❌ It is mutually exclusive with the mechanism that satisfies the request.

Rejected. Recorded here only so a future reader knows the newer option was considered and dismissed
on purpose rather than missed.

### §D-WHYNOT-D — why not a conditional `cancel-in-progress`

Brief §4 offers Strategy D: one group keyed on `github.ref`, with
`cancel-in-progress: ${{ github.event_name == 'push' }}`.

- ✅ One group, arguably simpler to read.
- ❌ Brief §4 flags it as "not an official documented pattern".
- ❌ Brief §Evidence Gaps 4: whether a conditional `cancel-in-progress` is evaluated at workflow-queue
  time or at execution time "is not officially documented", and the brief's own advice is to "test
  it empirically in your repo". If it evaluates at the *running* run's queue time rather than the
  *arriving* run's, a merge could still cancel a nightly — the exact failure §D-WHYNOT-A exists to
  prevent, reintroduced through an undocumented evaluation rule.

Rejected: an undocumented evaluation order is not an acceptable foundation for an invariant we
depend on. The event-scoped **group** achieves the same isolation using only documented,
queue-time-evaluated expression interpolation (brief §4, §5).

### §D-WHYNOT-RUNID — why not force manual dispatches to never cancel each other

A hybrid such as
`group: ${{ github.workflow }}-${{ github.event_name }}-${{ github.event_name == 'push' && github.ref || github.run_id }}`
would give every dispatch a unique group (brief §5's "truly unique groups per event" idiom).

- ✅ Two rapid manual dispatches would both complete.
- ❌ It trades a one-line, canonical expression (brief §4's "canonical pattern") for an
  unreadable one, to protect a case where the operator is knowingly pressing the button twice.
- ❌ It reintroduces a conditional into the group expression, adjacent to §D-WHYNOT-D's evidence gap.

Rejected. The behaviour is documented in §EDGE instead; the workaround if it ever bites is "wait for
the first dispatch, or dispatch from a different ref".

### §D-MIDDLE — what happens to three back-to-back merges

Brief §2 is the source, and it is **internally inconsistent** for the `cancel-in-progress: true`
case — this must be recorded honestly rather than smoothed over:

- Its bullet narrative says run A keeps executing while B becomes pending and C then replaces B.
- Its quoted line, in the same section, says "A new trigger immediately cancels any running
  instance. At most, only one job or workflow run can be pending in a concurrency group at a time."

Under *either* reading the operationally relevant outcome is identical and is what we ship on:

> **Only the newest merge commit's run reaches completion. Intermediate merge commits do not get a
> completed integration run.** At most one run is pending per group (brief §2, default `queue:
> single` — brief §Version Notes).

What differs between the readings is only *when* the older run stops (immediately, or after the
newest arrives), which changes the savings magnitude, not the contract. The DESIGN block states the
guaranteed part and does not claim the ungrounded part. See §RISK-BRIEF2.

The ❌ that must be written down: a commit whose run was cancelled is never individually retested.
This widens an accepted hole rather than opening a new one — Plan 017 §RL-5-triggers already accepts
that "a PR can break integration and only nightly catches it", since `integration.yml` never runs on
PRs. After this change, `main` is verified at the tip on merge, plus in full every night. A
regression is still attributable by bisect; it is just not attributed automatically.

### §D-LEAK — cancelled testcontainers runs: no cleanup step. Position taken.

Brief §6: on cancellation the runner sends SIGINT, waits 7.5 s, sends SIGTERM, waits 2.5 s, then
force-kills the process tree, with a 5-minute total cancellation timeout. Brief §6/§7: bash "does not
propagate SIGINT/SIGTERM to child processes while it is blocked waiting for them to exit", so
"Docker containers started by your test suite (testcontainers) may not receive the cancellation
signal and may remain running on the runner".

**Decision: add no cleanup step.** Reasoning:

- Brief §7, citing actions/runner#926: "For GitHub-hosted runners: GitHub automatically cleans up
  ephemeral runners after each job, so lingering containers are destroyed with the runner instance.
  No manual cleanup is required for cost purposes, but self-hosted runners require explicit cleanup."
  Both jobs are `runs-on: ubuntu-latest` (`integration.yml:76`, `:94`) — GitHub-hosted, ephemeral.
- A leaked container on an ephemeral runner cannot outlive the run, cannot affect the next run
  (a fresh VM), and cannot affect a developer's machine. There is no state to corrupt and no cost to
  recover.
- An `if: always()` `docker system prune --force --volumes` step would run on **every** run
  (including the ~365 nightly runs a year that are never cancelled) to protect against a condition
  that cannot persist. That is machinery without a beneficiary — and this repo's stated posture
  (Plan 018 §RT7-ci ❌ bullet 1) is to refuse machinery whose only justification is symmetry.
- Brief §7 also notes testcontainers' Ryuk daemon attempts cleanup but "relying on this alone is not
  safe". We rely on neither Ryuk nor a prune step; we rely on runner ephemerality, which brief §7
  states directly.

**The one trigger that reverses this:** if the repo ever adopts a self-hosted or persistent runner
for `integration.yml`, an `if: always()` cleanup step becomes mandatory (brief §6 "Cleanup guarantee
for `if: always()` steps", brief §7's marketplace-action precedent). That trigger condition goes
into the DESIGN comment block so the reversal is discoverable at the point of change.

Secondary consequence to state, not fix: brief §6's 5-minute cancellation ceiling means a cancelled
run may linger for up to ~5 minutes before the server force-terminates it. That trims, but does not
negate, the savings (a cancelled run costs minutes, not 45).

### §D-BILLING — does this actually "use just the time needed"?

The user's motivation is time. Two honest answers, separated:

1. **Wall-clock / feedback time — yes, unambiguously.** Under §D-MIDDLE, N rapid merges produce one
   completing run instead of N, so the tip's result arrives after ~45 minutes rather than after
   ~N×45 (serialized) minutes. This follows from brief §2 alone and needs no billing claim.
2. **Billable minutes — almost certainly, but not officially guaranteed.** Brief §9: "Official
   documentation is silent on billable-minute behavior upon cancellation"; industry evidence
   suggests 20–30% savings; brief's conclusion is "Cancellation almost certainly saves billable
   minutes … but GitHub does not publicly document the exact semantics" (also brief §Evidence
   Gaps 1). The DESIGN block must phrase this as "almost certainly", never as a guarantee.

⚠️ ASSUMPTION (not in brief 001, not verified in-tree): this repository appears to be public
(OpenSSF Scorecard workflow, PyPI trusted publishing, and Plan 023 §Non-goals' "free **for a public
repository**"), and GitHub-hosted standard runners are free for public repositories — in which case
the *monetary* saving is zero and the entire benefit is (1). The DESIGN block should therefore lead
with wall-clock/feedback time and concurrency-slot contention, and mention billing only as the
secondary, brief-§9-hedged point. See §RISK-PUBLIC.

### §D-REQUIRED — this stanza is a new reason `integration` must never be required

Invariant 3 (neither job is ever a required check) is already repo policy (`integration.yml:26-28`,
`CLAUDE.md:160-169`, Plan 017 §RL-5-triggers). This change adds an independent, mechanical reason:

Brief §8: a cancelled run's final status is `cancelled`, and if that run is a *required* status
check "the PR's merge state may become **stuck** in 'waiting for status to be reported' because a
cancelled run does not resolve as either success or failure". Brief §8 closes: "Since
`integration.yml` is explicitly **not** a required check, cancellation poses no branch-protection
risk."

Consequence to record explicitly, because BACKLOG's RL-16 row and Plan 017 §RL-5-triggers both
contemplate eventually promoting `integration` to a required check: **that promotion is now coupled
to this stanza.** Whoever promotes it must, in the same change, either drop `cancel-in-progress:
true` or accept brief §8's stuck-merge failure mode. This goes in the DESIGN block *and* in the
BACKLOG RL-16 row (Step 6), not only here — a coupling recorded only in a plan file is a coupling
nobody will find.

### §D-COMMENT — the DESIGN comment block is part of the deliverable

`integration.yml`'s header is 59 lines of `DESIGN §<tag>` prose with ✅/❌ bullets
(`integration.yml:1-59`), and every non-obvious choice in this repo carries one (CLAUDE.md, Coding
Standards). `test.yml:31-33`'s bare, uncommented stanza is the counter-example this plan explicitly
does not follow: a reader of `test.yml` today cannot tell whether the group expression was chosen or
copied.

The new block is tagged **`§CONC`** (a new tag — it belongs to no prior plan) and must carry, at
minimum: the group table from §D-SHAPE, the §D-WHYNOT-A "do not copy `test.yml`'s form here"
warning with its brief §3 citation, the §D-MIDDLE guarantee (newest completes; intermediates get no
completed run), the §D-LEAK no-cleanup position *with its self-hosted-runner reversal trigger*, and
the §D-REQUIRED coupling to any future required-check promotion.

### Alternatives considered (summary)

- **Strategy A — `${{ github.workflow }}-${{ github.ref }}`, cancel (i.e. copy `test.yml`)**:
  rejected — ✅ one-line, matches an in-repo precedent / ❌ brief §3: `push`/`schedule`/`dispatch`
  share `refs/heads/main`, so a merge cancels the nightly (and its `chaos` job) and vice versa.
- **Strategy C — serialize like `docs.yml` (`cancel-in-progress: false`)**: rejected — ✅ every
  commit keeps full coverage / ❌ the exact opposite of the request; three merges cost three runs.
- **`queue: max`**: rejected — ✅ preserves middle runs / ❌ brief §1: mutually exclusive with
  `cancel-in-progress: true`, and preserving middle runs is the thing we were asked to stop doing.
- **Strategy D — conditional `cancel-in-progress`**: rejected — ✅ single group / ❌ brief §4 "not
  an official documented pattern" + brief §Evidence Gaps 4 undocumented evaluation timing.
- **Job-level stanza on `integration` only**: rejected — ✅ `chaos` provably ungrouped / ❌ the
  guarantee is never exercised (`chaos` is skipped on `push`, the only trigger where cancellation
  fires), needs the same expression, and silently misses any future third job (§D-LEVEL).
- **`github.run_id` in the group for non-push events**: rejected — ✅ manual dispatches never cancel
  each other / ❌ unreadable conditional expression for a knowingly-repeated human action (§D-WHYNOT-RUNID).
- **Adding an `if: always()` docker-prune step**: rejected — ✅ defends against brief §6's
  signal-propagation gap / ❌ brief §7: GitHub-hosted runners are ephemeral and auto-cleaned, so the
  step protects nothing (§D-LEAK).
- **Adding `actionlint` to `make lint` to validate the stanza**: rejected — ✅ would catch YAML/expression
  errors mechanically / ❌ new tooling, a new pin, a new CI gate, and a new contributor obligation for
  a three-line change; §VERIFY's cheaper checks cover this one edit (offered as an optional one-shot).

---

## Steps

Small, single-commit change. Steps 1–3 are the change; 4–7 are the same-commit docs sync
(CLAUDE.md's rule + memory `feedback_keep_docs_updated`: docs land in the SAME change, never a
follow-up); 8–9 are verification.

1. [x] `.github/workflows/integration.yml` — insert a `DESIGN §CONC` comment block at the end of the
   existing header (after the `chaos` block ending at `:59`, before `name: Integration Tests` at
   `:61`), in the file's established `#`-prefixed ✅/❌ style. Required content per §D-COMMENT:
   the §D-SHAPE group table; the §D-WHYNOT-A warning ("`test.yml:31-33`'s form is correct there and
   wrong here — on `push`/`schedule`/`workflow_dispatch` `github.ref` is `refs/heads/main` for all
   three (research 001 §3), so a merge would cancel the nightly run *and its `chaos` job*; do not
   harmonize the two files"); the §D-MIDDLE guarantee and its ❌; the §D-LEAK position plus its
   self-hosted-runner reversal trigger; the §D-REQUIRED coupling. Cite
   `design/research/001-github-actions-concurrency-semantics.md` by section, the way the header
   already cites `plans/017-…` and `plans/018-…`.

2. [x] `.github/workflows/integration.yml` — add the stanza itself, between the `on:` block
   (ends `:68`) and `permissions:` (`:70`), matching the placement used in `test.yml:31-33` and
   `docs.yml:49-51`:
   ```yaml
   concurrency:
     group: ${{ github.workflow }}-${{ github.event_name }}-${{ github.ref }}
     cancel-in-progress: true
   ```

3. [x] `.github/workflows/integration.yml` — confirm by re-reading the diff that **nothing else
   changed**: `on:` triggers identical, `chaos`'s `if: github.event_name != 'push'` identical, no
   `needs:` added, both `timeout-minutes: 45` identical, no step added or removed, no action SHA
   touched. (Invariants 1–3. This is a review step, not an edit.)

4. [x] `CLAUDE.md:160-169` — extend the `integration.yml` bullet with one sentence on the new
   behaviour and one clause on why it differs from `test.yml`'s. Suggested shape, to be adapted to
   the surrounding prose: *"Since Plan 024 the workflow carries a `concurrency` group scoped by
   `github.event_name` as well as `github.ref` (`${{ github.workflow }}-${{ github.event_name }}-${{
   github.ref }}`, `cancel-in-progress: true`) — several merges landing in quick succession leave
   only the newest commit's run running, while the nightly `schedule` and any `workflow_dispatch`
   run sit in separate groups and can never be cancelled by a merge (which would take the `chaos`
   job down with it). It deliberately does **not** use `test.yml`'s simpler `${{ github.workflow
   }}-${{ github.ref }}` group: this workflow has three triggers that all report `github.ref ==
   refs/heads/main`."* Keep it in the existing bullet; do not create a new section.

5. [x] `CLAUDE.md` — in the same bullet (or the sentence immediately after), reinforce invariant 3
   with the new mechanical reason from §D-REQUIRED: a cancelled run resolves as neither success nor
   failure, so promoting `integration` to a required check while `cancel-in-progress: true` is set
   can leave a PR permanently pending (research 001 §8).

6. [x] `BACKLOG.md` — the **RL-16** row (integration-as-required-check promotion, ~`:132`): append a
   one-clause note that promotion is now coupled to `integration.yml`'s `concurrency` stanza and
   must drop `cancel-in-progress: true` or accept research 001 §8's stuck-merge mode. Do not change
   the row's status. This is the discoverability half of §D-REQUIRED.

7. [x] `CHANGELOG.md` — add a bullet under `[Unreleased]` → `### Changed` (create the subsection if
   `[Unreleased]` has only `### Fixed` today, matching the file's Keep-a-Changelog convention at
   `:1-20`). CI-only entries are in-convention here — `:378-380` records the `chaos` job the same
   way. State: the new group expression, the "newest merge wins" behaviour, the explicit
   *non*-cancellation of nightly/dispatch runs, and that no package changed. Mirror the "Test-only
   release — no runtime package changed" framing used at `:364-365`.

8. [x] **Docs-sync files deliberately NOT touched** — record this verdict rather than leaving the
   next reader to wonder (review step, no edit):
   - `Makefile:13-33` — its header describes which `make` target CI invokes and that
     `integration.yml` is not a required check. Both statements remain true; concurrency is a
     workflow-scheduling concern with no `make`-level counterpart. No edit.
   - `plans/017-…md` §RL-5-triggers and `plans/018-…md` §RT7-ci — historical design records.
     Neither is *contradicted*: triggers are unchanged (§RL-5-triggers) and chaos gating is
     unchanged (§RT7-ci). The repo's convention for a genuinely superseded plan/brief section is an
     in-tree banner (cf. `plans/022`'s CLOSED banner, research 002 §1's superseded banner); no
     banner is warranted because nothing is superseded. No edit.
   - `technical_docs/features/` — no CI/workflow feature doc exists. No edit.
   - `README.md` — documents library usage, not CI. No edit.

9. [x] Run §VERIFY's checks A–C and record their output in the PR/commit description. (A and B run
   and recorded below; C is optional/one-shot and skipped per instruction.)

10. [ ] After merge: perform §VERIFY's observation D (the only real proof) and, if it reveals the
    behaviour is not as designed, open a BACKLOG row rather than silently re-tuning the expression.

---

## Edge cases

- **Three merges land inside 45 minutes** → only the newest commit's run completes; the intermediate
  run is discarded and is never re-run for that commit (§D-MIDDLE, brief §2). Expected, documented,
  not a bug.
- **A merge lands at 04:59, nightly cron fires at 05:00** → different groups (`push` vs `schedule`,
  brief §4). Both run to completion, concurrently, on separate runners with separate
  testcontainers. Nothing is cancelled. This is the case §D-WHYNOT-A exists to protect.
- **A merge lands while a `workflow_dispatch` run is in flight** → different groups. Both complete.
- **Two `workflow_dispatch` runs on `main` within 45 minutes** → same group; the first is cancelled,
  including its `chaos` job. Deliberate (§D-WHYNOT-RUNID); the workaround is to wait, or to dispatch
  from a different ref (a different `github.ref` ⇒ a different group).
- **A `workflow_dispatch` on a non-`main` branch while a `main` dispatch runs** → different
  `github.ref`, different groups, both complete (brief §3: `workflow_dispatch`'s `github.ref` is the
  branch selected in the UI).
- **A cancelled run leaks testcontainers on the runner** → nothing is done; the ephemeral
  GitHub-hosted runner is destroyed with them (brief §7). No cleanup step, by design (§D-LEAK).
- **A cancelled run takes up to ~5 minutes to actually stop** → expected: brief §6's SIGINT → 7.5 s →
  SIGTERM → 2.5 s → force-kill sequence with a 5-minute server-side cancellation ceiling. The saving
  is "minutes instead of 45", not "instantaneous".
- **Someone renames the workflow's `name:`** → `${{ github.workflow }}` changes, so the group name
  changes and one in-flight run under the old name will not be cancelled by the first run under the
  new one. Harmless, one-time.
- **Someone later adds `pull_request` to this workflow's triggers** → the event-scoped group handles
  it correctly with no edit: PR runs land in a `…-pull_request-refs/pull/N/merge` group, one per PR,
  isolated from `push`. (Adding the trigger itself is out of scope — Plan 017 §RL-5-triggers.)
- **Someone later adds a third job to this file** → it inherits the workflow-level group
  automatically; no per-job stanza to forget (§D-LEVEL).
- **`integration` is promoted to a required check without revisiting this stanza** → a cancelled run
  can leave a PR stuck in "waiting for status to be reported" (brief §8). Guarded by Steps 5–6.

---

## Verification

There is **no workflow-linting convention in this repo today** (no `actionlint`, no
`.pre-commit-config.yaml` hook for YAML workflows, no CI job that validates `.github/workflows/*`).
This plan does not create one (§Non-goals). The checks below are ordered cheapest-first; the honest
statement is that **only D is real proof**, and A–C exist to make D's outcome interpretable.

**A. YAML parses and the stanza is exactly as designed** (seconds, no new dependency in `uv.lock`):

```bash
cd /home/edoardo/projects/varco

uv run --with pyyaml python - <<'PY'
import pathlib, yaml
wf = yaml.safe_load(pathlib.Path(".github/workflows/integration.yml").read_text())
c = wf["concurrency"]
assert c["group"] == "${{ github.workflow }}-${{ github.event_name }}-${{ github.ref }}", c
assert c["cancel-in-progress"] is True, c
# Invariants 1-3, re-asserted mechanically:
assert wf["jobs"]["chaos"]["if"] == "github.event_name != 'push'"
assert "needs" not in wf["jobs"]["integration"] and "needs" not in wf["jobs"]["chaos"]
assert set(wf[True]) == {"push", "schedule", "workflow_dispatch"}   # PyYAML parses `on:` as True
print("OK: concurrency stanza + invariants 1-3")
PY
```

**B. The group cannot collide with `test.yml`'s** (invariant 4) — a name-level check, since the
group string embeds `${{ github.workflow }}`:

```bash
grep -n '^name:' .github/workflows/integration.yml .github/workflows/test.yml
# expect: integration.yml -> "Integration Tests"; test.yml -> "Tests"  (distinct)
grep -n -A2 '^concurrency:' .github/workflows/integration.yml .github/workflows/test.yml
# expect: the two group expressions differ (event_name segment present only in integration.yml)
```

**C. Optional one-shot expression/schema lint** — a container invocation, adopted for this review
only, deliberately *not* wired into `make lint` or CI:

```bash
docker run --rm -v "$(pwd):/repo" -w /repo rhysd/actionlint:latest -color
# expect: no findings for .github/workflows/integration.yml
```

**D. The real proof — observation, not a test.** In descending order of cost/fidelity:

1. *Cheapest deliberate proof of the push path* (**post-merge, requires two merges**): the next two
   PRs merged to `main` within 45 minutes of each other. Expected in the Actions UI: the older
   `Integration Tests` run ends **Cancelled**, the newer ends **Success**/**Failure**. Record the two
   run URLs in the PR description or the commit message.
2. *Proof the dispatch group is isolated and active* (**on demand, ~1 cancelled run + 1 full run of
   runner time**): trigger `workflow_dispatch` on `main` twice within a minute. Expected: the first
   run is **Cancelled**, the second completes — which proves the stanza is live. Do this only if (1)
   has not occurred naturally within a week.
3. *Proof the nightly is not collateral damage* (**free, next morning**): after any day on which a
   merge landed to `main` after 05:00 UTC, confirm the 05:00 `schedule` run completed and its
   `chaos` job ran. This is the §D-WHYNOT-A guarantee, observed.

⚠️ There is no way to assert a concurrency group's *name* from inside a run — the group is a
scheduling-side construct, not exposed in the `github` context. D(2) and D(3) are the only available
evidence that the event-name scoping works; do not invent an in-run assertion for it.

---

## Risks

- **§RISK-INTENT — ⚠️ ASSUMPTION (interpretation of user intent, not stated by the user).** The user
  asked only about *merged PRs*: "if multiple PR are merged only the last one will trigger the
  action". They said nothing about the nightly `schedule` or `workflow_dispatch`. The design's
  central claim — *a merge must never cancel the nightly run* — is **our reading**, justified by
  in-tree facts (the `chaos` job runs only on non-`push` triggers, `integration.yml:95`; Plan 018
  §RT7-ci makes the nightly the sole owner of that signal) but not by anything the user said. If the
  user actually wants "newest run of any kind wins, everywhere", the correct change is brief
  Strategy A — `group: ${{ github.workflow }}-${{ github.ref }}` — and this plan's §D-WHYNOT-A is
  the argument to overrule. **Surface this reading to the user before or at implementation; it is
  the one decision here that a reasonable person could make differently.**
- **§RISK-BRIEF2 — the brief's own §2 is internally inconsistent** for `cancel-in-progress: true`
  (bullet narrative vs. quoted line — see §D-MIDDLE). The plan therefore commits only to the
  outcome both readings share ("newest completes, intermediates do not") and to "at most one pending
  per group". Any DESIGN-comment or CHANGELOG wording that claims a *precise* moment of cancellation
  would be asserting something the brief does not settle. Invariant that must hold: the committed
  wording never states more than "the newest run is the one that completes".
- **§RISK-COVERAGE — intermediate commits lose their integration run.** Bisecting a broker
  regression across a burst of merges gets harder. Mitigated by: the nightly full run, `integration`
  not being a required check, and Plan 017 §RL-5-triggers already accepting that PRs get no
  integration coverage at all. If this ever bites, the escape hatch is `workflow_dispatch` on the
  suspect commit — not removing the stanza.
- **§RISK-PUBLIC — ⚠️ ASSUMPTION (not in brief 001, not verified in-tree): the repository is public
  and therefore GitHub-hosted standard-runner minutes are free.** If true, the *monetary* saving of
  this change is zero and the benefit is entirely wall-clock/feedback time plus concurrency-slot
  contention. Do not write a cost-savings claim into the CHANGELOG or the DESIGN block without
  verifying the repository's visibility and plan. Brief §9 is itself hedged ("almost certainly …
  GitHub does not publicly document the exact semantics"; §Evidence Gaps 1).
- **§RISK-REQUIRED — a future required-check promotion silently breaks merges.** Brief §8: a
  `cancelled` conclusion resolves as neither success nor failure and can leave a PR permanently
  pending. Invariant that must hold: `integration` and `chaos` are never required checks while
  `cancel-in-progress: true` is set. Guarded by Steps 5 and 6, not by code — nothing mechanical
  enforces it.
- **§RISK-FRESHNESS — brief 001 is dated 2026-09-01 and self-labels "Freshness matters: **yes** …
  recheck annually".** The `queue:` key is four months old (May 2026, brief §1) and the runner
  pricing change is from January 2026 (brief §9). If this plan is executed materially later than its
  writing date, re-verify §1's `queue`/`cancel-in-progress` mutual exclusion and §3's `github.ref`
  values before trusting §D-WHYNOT-QUEUE and §D-WHYNOT-A.
- **§RISK-LEAK — the no-cleanup position is conditional on runner ephemerality** (brief §7). It
  becomes wrong the moment any `runs-on:` in this file stops being a GitHub-hosted ephemeral runner.
  Invariant that must hold: `integration.yml`'s jobs stay on GitHub-hosted runners, or an
  `if: always()` cleanup step is added in the same change. Recorded in the DESIGN block (Step 1) so
  the trigger is visible where the change would be made.
- **§RISK-NOLINT — nothing mechanically validates workflow YAML in this repo.** A typo in the
  expression (e.g. `github.event-name`) would not fail any check; it would silently produce a
  literal-ish group string and, at worst, degrade to per-run-unique groups (no cancellation at all)
  or to a shared group (Strategy A's failure mode). §VERIFY A is the mitigation, and it is a
  one-shot human-run script, not a gate. Accepted deliberately (§Non-goals); revisit only if a
  second workflow-expression defect ever occurs.
