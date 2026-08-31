# Plan 023 — Phase 5 release: lockstep 3.0.0, trusted publishing, governance, versioned docs

Closes BACKLOG.md §"Phase 5 — release" rows **RL-9, RL-10, RL-11, RL-12, RL-13**.

## Goal

After this plan the repository can cut a public 3.0.0 release without a human editing a version
string, holding a PyPI token, or remembering an undocumented step:

1. **One version, one mechanism.** All ten distributions carry `3.0.0` and
   `Development Status :: 5 - Production/Stable`, written by a tested `scripts/bump.py` that also
   rewrites every sibling requirement string; a unit test fails CI if the ten ever diverge again.
2. **A written SemVer + deprecation policy** in `CONTRIBUTING.md` that *describes the code that
   already exists* (`varco_core.deprecation`, the `since="3.0.0" / removed_in="4.0.0"` aliases
   Plan 022 landed), plus `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CODEOWNERS`, issue/PR templates.
3. **A tag-triggered release** that builds once and publishes ten distributions over OIDC trusted
   publishing with PEP 740 attestations, no long-lived secret, all actions SHA-pinned; plus
   `dependabot.yml` and an OpenSSF Scorecard workflow.
4. **Versioned documentation** on GitHub Pages via `mike` — `dev` from `main`, `3.0` + `latest`
   from the release tag.
5. **PEP 639 license metadata** across all ten packages plus a recorded PEP 735 audit.
6. **A hardened GitHub repository** — Actions permissions, secret scanning + push protection,
   CodeQL, private vulnerability reporting, a `main` branch ruleset requiring `all-green`, and a
   `v*` tag ruleset — applied from a written, page-by-page checklist (§RL-SEC-hardening,
   Appendix A). These are *settings*, not code; the plan cannot apply them, only specify them.

## Organizing principle

> A version number published to PyPI is **irreversible** — 3.0.0 can never be re-uploaded. Every
> step that is cheap to redo (a workflow, a policy document, a docs deploy) goes *before* the one
> step that is not (the tag). The plan is ordered so the tag is the last thing that happens.

---

## Non-goals

- **No new features, no defect fixes.** Every open BACKLOG defect encountered in passing
  (RT7b-kafka-restart-recovery, RT9-beanie-index-mode-no-pending-migrations, WD-1,
  NATS-max-deliveries-dlq, RedisJobStore-atomic-lua-claim) stays open. File, do not freelance.
- **No new breaking changes.** The 3.0.0 window closed with Plan 022 (§RL-9-freeze). This plan
  ships what is already in `CHANGELOG.md`'s `[Unreleased]`; it does not add to it.
- **No CloudEvents, no AsyncAPI, no OpenFeature.** Deferred to 3.1 with finished designs
  (Plan 022 §D-CE1–§D-CE4 / §D-AA1–§D-AA4 / §D-OF, `design/api-freeze-and-standards/reserved-seams.md`).
- **No independent per-package versioning, no `varco` umbrella meta-package.** Both are Parked
  decisions in BACKLOG.md and are not relitigated here.
- **No towncrier / changelog automation.** `CHANGELOG.md` stays hand-curated; brief 001 accepts
  "a detailed, hand-curated CHANGELOG.md with semantic sections" as meeting the bar, and the file
  already has that shape.
- **No Read the Docs.** The user chose GitHub Pages (BACKLOG RL-12 rationale); brief 001's
  RTD-is-standard finding is noted and overridden by that choice.
- **No Python 3.14 / free-threaded matrix leg**, no benchmark suite, no MCP/GraphQL positioning
  content. All brief-001 "phase 2 positioning" items, out of scope.
- **No paid GitHub features.** Nothing in Phases 8–9 needs Pro/Team/Enterprise or GitHub Advanced
  Security — brief 007 §7 confirms every recommended setting is free **for a public repository**.
  Deliberately excluded for that reason: environment wait timers, required reviewers on an
  environment, and the Actions "verified creators" allowlist filter.
- **This plan cannot apply GitHub or PyPI settings.** Branch and tag rulesets, GitHub
  environments, the Actions and code-security toggles, the Pages publishing source, and ten PyPI
  trusted-publisher configurations are **manual, out-of-repo operator actions**, recorded in a
  runbook and in Appendix A exactly the way CLAUDE.md already records the pending `all-green`
  branch-protection setting. Do not pretend a Step performs them.

---

## Design

### §ORDER — why the phases run in this order

```
Phase 0  preflight + the irreversible decisions      ─┐
Phase 1  bump mechanism (+ tests)                     │  mechanism BEFORE the bump it performs
Phase 2  PEP 639 / PEP 735 metadata (RL-13)           │  last cheap moment: every file is open anyway
Phase 3  THE FREEZE — 3.0.0 + classifier + CHANGELOG  │  needs 1 and 2
Phase 4  governance + written policy (RL-11 / RL-9)   │  release-blocking per brief 001 §1
Phase 5  release automation (RL-10)                   │  worthless until versions are coherent
Phase 6  versioned docs (RL-12)                      ─┘  independent — may run parallel to 4/5
Phase 7  close-out: BACKLOG/CHANGELOG, rc1 rehearsal, tag
Phase 8  GitHub click-through hardening (§RL-SEC)       no files, no commits — run it in the same
                                                        browser session as Step 39
Phase 9  GitHub rulesets: branch + tag (§RL-SEC)        ONLY after Step 41's v3.0.0 tag has shipped
```

- **1 before 3**: a bump script that has never been tested must not be the thing that writes ten
  irreversible version numbers. Its tests are written first (TDD, CLAUDE.md).
- **2 before 3**: RL-13 is "cheap only if done in the same pass" (BACKLOG rationale). Doing it
  first keeps the freeze commit purely about versions and readable in review.
- **3 before 5**: a publish workflow that ships `varco-core 1.2.0` alongside `varco-sa 2.2.0` is
  worse than no workflow — it would burn ten PyPI version numbers on an incoherent set.
- **6 parallel**: `mike`/Pages touches no `pyproject.toml`, no workflow that Phase 5 writes, and
  no version string except the `3.0` directory name it derives from the tag.
- **7 last among the file-editing phases**: the tag is the irreversible act.
- **8 alongside Step 39, 9 strictly after Step 41**: neither phase edits a file, so neither can
  break a build — but a ruleset *can* refuse the release tag push and a mistargeted one can break
  the docs pipeline. The full reasoning, including the three failure modes that fix this ordering,
  is §RL-SEC-hardening; it is not restated here.
- **Phase 0 confirms the repository's own name** (`varco` — resolved in §RL-SEC-repo-name, no longer a decision), because it is an
  input to Phase 5's ten publisher configs, Phase 6's `site_url`, and Phase 9's badge URL.

### §RL-9-freeze — what 3.0.0 contains, and the one checkpoint

**Resolved from the tree, not assumed.** The scout's open question ("does Phase 5 ship 3.0.0
carrying Plan 022's breaks, or is more expected in the window?") has a decisive answer:

| Evidence | Says |
|---|---|
| `BACKLOG.md:186-189` (Phase 4) | Both rows **✅ DONE**: RL-8 (12 candidates audited, 4 accepted) and RL-8a |
| `plans/022-…md:3-13` (CLOSED banner) | *"The 3.0.0 breaking-change window is closed … **RL-9 (version freeze) is unblocked.**"* |
| `CHANGELOG.md:10-113` | `[Unreleased]` already carries AB-1/AB-2/AB-4/AB-5 and the RL-8a teardown break |
| `BACKLOG.md:209-212` | CloudEvents/AsyncAPI deferred to 3.1 as **purely additive** — they cost no deprecation cycle later |
| Remaining open rows (`BACKLOG.md:170-178`) | Defects and test-coverage gaps; **none changes a public symbol** |

**Verdict: 3.0.0 ships exactly the `[Unreleased]` content. Nothing further is expected in the
window.** No further break may be added by this plan (Non-goals).

DESIGN: one checkpoint anyway, and only one
- ✅ The *decision* is derived above, but the *act* is irreversible: PyPI never lets 3.0.0 be
  re-uploaded, and ten names go stable-classified simultaneously. A confirm-don't-derive gate
  costs one message.
- ✅ It is the natural place to also confirm the two numbers this plan cannot derive from the
  tree: the deprecation floor (§RL-9-policy) and the security-support window (§RL-11-gov) — and,
  the repository slug, which is now **resolved** to `varco` and only restated for confirmation
  (§RL-SEC-repo-name).
- ❌ A checkpoint adds a stop. Accepted, and bounded: it is **Step 3**, before any file is edited,
  and it is the *only* hard stop in the plan (contrast Plan 022 §D-RANK, which needed a
  twelve-row verdict gate).

### §RL-9-bump — `scripts/bump.py` (tomlkit), not `uv version`, not hatch-vcs

Brief 004 §3's verdict: *"a hand-rolled `scripts/bump.py` using tomlkit is the most transparent
and maintainable choice today"*, matching what LlamaIndex's ten-package uv monorepo does.

#### Alternatives considered

- **`uv version --package <m> --bump major` in a loop** — rejected. ✅ First-party, no new
  dependency, `--dry-run` built in. ❌ Brief 004 §1: there is **no `--all-members`/`--workspace`
  flag**, so the loop is hand-written anyway; ❌ decisively, `uv version` sets `[project].version`
  and **nothing else** — it cannot rewrite the sibling requirement strings (§RL-9-pins) or the
  `[project.optional-dependencies]` sibling pins, which is over half the edit; ❌ no `--check`
  mode, so nothing detects drift afterwards.
- **hatch-vcs from the git tag** — rejected. ✅ One tag drives all ten, zero files to edit. ❌
  Brief 004 §2's table rates *"building from sdist without `.git`"* **High** severity: an sdist
  extracted on a machine without git cannot compute its own version, and the mitigation (force-include
  a generated `_version.py`) "doubles maintenance"; ❌ editable installs go stale after a tag bump
  until a reinstall — a daily-driver friction for a ten-member workspace; ❌ brief 004 §2's own
  summary: hatch-vcs suits *CI-determined* versions, and varco's is hand-chosen.
- **`uv-version-bumper` (third-party)** — rejected. ❌ Brief 004 Evidence Gap 1: production-used
  but not Astral-maintained and unbenchmarked in large monorepos. A new unvetted dependency in the
  one script whose failure mode is "publish the wrong version" is a bad trade for ~150 lines.
- **A regex over `^version\s*=`** — rejected. ✅ Zero dependencies, would work for line 3 of each
  file. ❌ Cannot distinguish a sibling entry in `[project].dependencies` (must be pinned) from
  one in `[dependency-groups].dev` (must **not** be — see the contract below); a regex that gets
  that wrong ships a dev-only dependency in a wheel.

#### Contract (write this into the module docstring)

| Aspect | Decision |
|---|---|
| Package list | **Derived by executing `scripts/packages.sh`** — mandatory (Plan 020 / RL-18, CLAUDE.md). Same subprocess pattern `scripts/api_surface.py` uses. Never a hand-written list. |
| Edits (1) | `[project].version` in each distribution's `pyproject.toml` (currently line 3 of all ten) |
| Edits (2) | Every sibling `varco-*` requirement string inside `[project].dependencies` |
| Edits (3) | Every sibling `varco-*` requirement string inside `[project.optional-dependencies].*` — these **do** ship in wheel metadata (`Provides-Extra` + `Requires-Dist`): `varco_fastapi`'s `ws = ["varco-ws"]` (`varco_fastapi/pyproject.toml:41`) and `varco_casbin`'s `fastapi = ["varco-fastapi"]` (`varco_casbin/pyproject.toml:30`) |
| Never edits | `[dependency-groups]` sibling entries — `varco_core`'s dev-group `varco-fastapi` (`varco_core/pyproject.toml:82`), `varco_sa`'s `varco-redis` (`:63`), `varco_fastapi`'s `varco-sa` (`:107`). PEP 735 groups never reach a published artifact; pinning them would be noise, and `varco_fastapi`'s is explicitly documented as test-only at `:102-106`. |
| Never edits | `[tool.uv.sources]` (a resolution directive, not a version), the root `pyproject.toml` (not a distribution), `examples/**` (verified: `examples/pyproject.toml` and `examples/00-full-stack-post-api/pyproject.toml` declare **no** `varco-*` requirement) |
| `uv.lock` | **Yes** — spawns `uv lock` after a successful write, unless `--no-lock`. Brief 004 §1: `uv version` does not re-lock, so this is the script's job. A bump that leaves `uv.lock` stale breaks CI's `uv sync --locked` on the very next push. |
| Modes | `--set X.Y.Z` · `--bump major\|minor\|patch` · `--dry-run` (print a unified diff, write nothing) · `--check` (verify, write nothing, exit 1 on drift) |
| `--check` semantics | Exit 1 if (a) the ten `[project].version` values are not all identical, or (b) any shipped sibling requirement string differs from the canonical pin derived from that version (§RL-9-pins). Prints a package/version/pin table either way. Mirrors `scripts/api_surface.py --check` and `varco gen-client-stubs --check`. |

DESIGN: `--check` **is** a gate, unlike `scripts/api_surface.py --check`
- ✅ It is deterministic and hermetic — it parses TOML, imports no `varco_*` module, renders no
  signature. The three reasons `api_surface.py --check` is *not* wired into CI (interpreter-dependent
  class-signature rendering, heap addresses, import cost — CLAUDE.md §"Public API surface snapshot")
  do not apply to it.
- ✅ Wired as a **unit test** (`varco_core/tests/test_bump_script.py::test_workspace_versions_are_coherent`),
  not a Makefile edit: it then runs in `make test` *and* in CI's existing `unit` job, with no new
  CI surface and no change to the `all-green` required-check contract.
- ❌ A CI failure now has one more possible cause. Accepted: the failure message is a table naming
  the divergent package, and the fix is a single `scripts/bump.py --set` run.

`tomlkit>=0.13` is added to the root `[dependency-groups] dev` (not `lint` — that group is the
CI-pinned ruff/mypy pair and must stay minimal). The script is invoked as
`uv run python scripts/bump.py`, exactly like `scripts/api_surface.py`; it does **not** need the
bare-`python3`, no-venv property that forced `scripts/packages.sh`'s design, because nothing in
`make lint` calls it.

### §RL-9-pins — compatible (`~=3.0`), not exact (`==3.0.0`)

Today the sibling requirement is a bare `"varco-core"` (e.g. `varco_kafka/pyproject.toml:23`) —
**unbounded**, which is the actual defect: a published `varco-kafka 3.0.0` would accept
`varco-core 9.x`. The canonical pin becomes `varco-core~=3.0` (PEP 440 compatible release:
`>=3.0, ==3.*`).

DESIGN: `~=<major>.0` over `==<full version>`
- ✅ Brief 004 §4's explicit recommendation for published distributions, and its diamond-conflict
  table: with exact pins, one consumer needing `varco-redis 3.0.1` and another needing
  `varco-sa 3.0.0` forces the resolver to reconcile two different exact `varco-core` demands and
  fail. With `~=3.0` both resolve.
- ✅ A patch fix to one package does not cascade a forced ten-package upgrade on every consumer
  (brief 004 §4, "PyPI conflict frequency: High" for exact pins).
- ✅ **It keeps `uv lock` working mid-development.** `[tool.uv.sources] workspace = true` makes uv
  resolve the sibling locally, but the declared specifier still has to be *satisfiable* by the
  local member. With `==3.0.0` pins, the first commit that bumps `varco-core` to `3.1.0` without
  bumping the other nine breaks the whole workspace lock. `~=3.0` tolerates that window.
- ✅ The pin string is a function of the **major only**, so a 3.0.1 or 3.1.0 bump changes ten
  `version =` lines and *zero* requirement strings — a smaller, more reviewable diff, and the
  `--check` invariant stays trivially true.
- ❌ The lockstep guarantee is now carried by the *release process* (all ten published from one
  tag at one number), not by the metadata. Accepted: brief 004 §4 says the honest alternative to
  that is an umbrella meta-package, which BACKLOG has already Parked as "the most machinery for
  the least benefit".
- ❌ A consumer can install `varco-kafka 3.0.0` with `varco-core 3.4.0`. Accepted — that
  combination is exactly what SemVer promises to keep working, and §RL-9-policy now says so in
  writing.

**Interaction with `[tool.uv.sources] workspace = true`, stated for the implementer**: brief 004
§4 — the `workspace` source is *local resolution only* and is **stripped at build time**. The
wheel/sdist `METADATA` carries only `Requires-Dist: varco-core~=3.0`. So the pin the bump script
writes is precisely and only what a PyPI consumer sees; nothing else in the file affects them.
Phase 2's build check verifies this on a real artifact rather than trusting it.

### §RL-9-policy — where the policy lives, and what it must say

`CONTRIBUTING.md` carries versioning + deprecation (BACKLOG RL-11 says so explicitly: *"carrying
RL-9's versioning/deprecation policy"*); `SECURITY.md` carries the supported-version matrix and
disclosure process (brief 001 §1: release-blocking for a framework handling JWTs, encryption keys
and multitenant isolation).

**The policy must describe the code, not aspire past it.** The repo already deprecates for real:
`varco_core/varco_core/deprecation.py` provides `deprecated(*, since, removed_in, replacement,
name)` and `deprecated_alias(...)`, with `removed_in` a **required** keyword (`deprecation.py:100-134`),
and Plan 022 shipped four aliases at `since="3.0.0", removed_in="4.0.0"`. Therefore:

| Clause | Text the policy must contain | Grounded in |
|---|---|---|
| Versioning | SemVer 2.0.0. **Lockstep**: all ten distributions share one version; a breaking change in any one bumps all ten. Siblings pin `~=<major>.0`. | brief 001 §1; §RL-9-pins |
| Removal window | A symbol may only be **removed in a major**. `removed_in` names the earliest major in which removal is permitted. | matches `deprecated(removed_in=…)` as used |
| Wall-clock floor | At least **12 months** between the release that deprecates and the release that removes. | see the ❌ below |
| Mechanism | Every hard deprecation emits a `DeprecationWarning` via `varco_core.deprecation`; aliases resolve to the **identical object** so `isinstance`/`except` keep working (`deprecation.py:266-340`). | Plan 022's AB-1/AB-2/AB-4 |
| Soft deprecation | Documentation-only discouragement with no removal date is permitted and is *not* accompanied by a warning. | brief 001 §1 (PEP 387 recognises it) |
| Enforcement | `uv run python scripts/api_surface.py --check` detects a removal or signature narrowing against the committed snapshot. Note honestly that it is not yet a CI gate (CLAUDE.md says so) and that it cannot see a narrowed class `__init__`. | CLAUDE.md §"Public API surface snapshot" |
| Python support | `requires-python = ">=3.12"`; tested on 3.12 and 3.13 (`test.yml` matrix). Dropping a Python minor is a **major** bump. | root `pyproject.toml`, `.github/workflows/test.yml:70` |

DESIGN: a 12-month floor, not PEP 387's two-year one
- ✅ PEP 387's actual mechanism is *"at least two consecutive minor releases"* of a runtime with a
  fixed annual train (brief 001 §1); the faithful translation for a library is "at least one full
  major cycle", which the policy states. The wall-clock number is the added guarantee, not the
  mechanism.
- ✅ 12 months is consistent with what already shipped: 3.0.0's aliases are removable in 4.0.0, and
  a 24-month floor would silently forbid a 4.0.0 before mid-2028 — a promise this plan would be
  making on the project's behalf without evidence it can keep.
- ❌ Brief 001 §1 reports the *expected* bar as "at least 2-year guarantee". Divergence recorded
  in Risks; if the user prefers 24 months, **only `CONTRIBUTING.md` changes** — no code, no
  `removed_in` string. Confirmed at the Step 3 checkpoint.

### §RL-10-publish — one workflow, build once, publish over a matrix

`.github/workflows/publish.yml` is 155 fully commented-out lines from the 0.1.0 era. **Rewrite as
`.github/workflows/release.yml`; `git rm` the old file.**

DESIGN: rename the file rather than uncomment it
- ✅ The workflow **filename is load-bearing**: brief 005 §1 — a PyPI trusted publisher matches on
  owner + repo + *workflow filename* + environment, and the token is rejected otherwise. Choosing
  the final name once, before ten publisher configs exist, avoids invalidating all ten later.
- ✅ The old file predates the ten-package workspace, `scripts/packages.sh`, SHA pinning, and
  attestations. Nothing in it survives review.
- ❌ Any operator who already bookmarked `publish.yml` must relearn one name. Trivially accepted —
  it has never run.

**Shape** (brief 005 §4), with two deliberate corrections to that brief's skeleton:

```
packages job ──> outputs.matrix (JSON, from scripts/packages.sh)  [RL-18 compliance]
     │
build job ────> make build  (uv build --package <p> --out-dir <p>/dist, Makefile:226-228)
     │          upload-artifact: one artifact per package, path <p>/dist
     │
publish job ──> matrix over the ten names
                environment: pypi-<dist-name>          <- job level
                permissions: id-token: write           <- this job only
                download-artifact <p> -> dist/
                pypa/gh-action-pypi-publish  packages-dir: dist/
```

- ✅ `gh-action-pypi-publish` over `uv publish --trusted-publishing`: brief 005 §2 — it emits PEP
  740 attestations **by default since v1.11.0** with no input, is the PyPA-official action PyPI's
  own docs recommend, and `uv publish` generates no attestations at all.
- ✅ Build-once/publish-later is explicitly supported and is what the packaging guide *requires*:
  brief 005 §2, *"Building distributions in a publishing job is unsupported"*; §3 confirms
  attestations still work across the job split as long as the publish job holds `id-token: write`.
- ✅ Top-level `permissions: {}`; `id-token: write` granted on the publish job only (brief 005 §4,
  SPEC 8 minimal-permissions posture cited in brief 001 §1).
- ✅ `make build` is reused rather than re-listing ten `uv build` lines: it already loops over
  `scripts/packages.sh`'s derivation and already writes **per-package** `dist/` directories, which
  is exactly what correction (1) below needs.

**Correction (1) to brief 005 §4's skeleton — `packages-dir` must be per-package.** The brief's
own §2 states the action *"publishes all `.whl` and `.tar.gz` files in the `packages-dir`"*, yet
its skeleton passes `packages-dir: dist/` (all ten dists) on every matrix leg. That would attempt
to upload all ten distributions to each of the ten projects and fail (or, worse, half-succeed).
Each leg must download **only its own** artifact.

**Correction (2) — `environment:` is a job-level key.** The brief's skeleton nests `environment:`
under a *step*, which is not valid GitHub Actions syntax. With a matrix, `environment: { name:
pypi-${{ matrix.package }}, url: … }` at job level yields one environment per leg, which is
precisely the per-project OIDC scoping brief 005 §1 requires.

**TestPyPI: no.** Rejected — ✅ a genuine rehearsal target; ❌ brief 005 §4 says it needs its own
separate pending-publisher configuration, i.e. **ten more** manual PyPI-side configs for a
one-time rehearsal, and brief 005's Evidence Gap 5 notes the environment-name pattern on
test.pypi.org is *untested*. Instead rehearse on **real PyPI with a pre-release tag `v3.0.0rc1`**
(Phase 7): ✅ exercises the exact production path, attestations included, and `pip install varco-core`
still ignores pre-releases by default; ❌ burns the `3.0.0rc1` version number irreversibly.
Accepted — that is what a release-candidate number is for.

**Manual, out-of-repo operator steps** (this plan cannot perform them; they go in the runbook and
get a CLAUDE.md note in the same style as the pending `all-green` branch-protection note):
1. Ten GitHub **environments**, `pypi-varco-core` … `pypi-varco-casbin`, with **no** deployment-branch
   restriction (brief 005 §4 / brief 007 §1 — a restriction would block the tag-triggered run).
   Free for a public repo (brief 007 §7); **not scriptable with `gh` today** (brief 007 §1) — and
   `gh` is not installed on the maintainer's machine anyway (§RL-SEC-hardening).
2. Ten **PyPI publisher configs** — owner `edoardoscarpaci`, **repo `varco`** (resolved, not a
   decision — §RL-SEC-repo-name; the pre-rename `fastrest` slug is a redirect and must NOT be
   used here, because OIDC matches the repository's current name, not a redirect), workflow
   `release.yml`, environment `pypi-<name>`. The repo slug is a *matched OIDC claim* (brief 007
   §1), so a guess here costs ten rejected token exchanges. For names not yet on PyPI this is a
   **pending publisher** (brief 005 §1: PyPI creates the project on the first successful run);
   for names already registered it is a normal publisher added under project settings. Step 4
   determines which of the ten is which.
3. The `main` branch ruleset (still pending from Plan 017 — CLAUDE.md §CI). Required check stays
   **`Tests / All tests passed` only** (the `all-green` job — brief 007 §3 confirms the aggregate-job
   pattern and the skipped-leg trap); `release`, `docs`, `scorecard` and `chaos` must never become
   required checks. Specified in full at §RL-SEC-hardening and Phase 9 — do **not** apply it
   before Step 41.

### §RL-10-matrix — the matrix package list is derived, never typed

A `packages` job runs `scripts/packages.sh`, converts to JSON, and exposes it as an output that
`publish` consumes via `fromJSON`. Distribution *names* are the directory names with `_`→`-`
(`varco_core` → `varco-core`), matching `[project] name`.

- ✅ Satisfies CLAUDE.md's RL-18 rule ("any new script or workflow must derive its package list
  from `scripts/packages.sh`") — the one rule that would otherwise be violated by a ten-entry YAML
  matrix literal.
- ✅ A future eleventh package is published with no workflow edit.
- ❌ One extra ~10-second job. Accepted; it is the same trade `scripts/unit_tests.sh` and
  `Makefile:48` already made.
- ⚠️ Verify at Step 4 that `uv build --package varco_kafka` (the underscore form the Makefile
  passes today, `Makefile:228`) is accepted; if uv requires the normalized `varco-kafka`, the
  normalization belongs in the `packages` job's output and `make build` is left alone.

### §RL-10-supply — dependabot + Scorecard

`.github/dependabot.yml`, two ecosystems (brief 005 §5):

- `package-ecosystem: "uv"`, `directory: "/"` — one entry for the whole workspace; the brief is
  explicit that per-member `directory` entries are **not** required. (Brief 007's own template
  says `"pip"`; brief 005 §5 is the later, uv-specific finding and wins. Brief 007's accompanying
  note — one lock file at the root, no per-package directories — agrees either way.)
- `package-ecosystem: "github-actions"`, `directory: "/"`.
- `groups:` for both, so ten members produce one PR per ecosystem per week rather than a flood;
  `open-pull-requests-limit` bounded.
- **No `reviewers:` key**, despite brief 007's template offering one: a solo maintainer is notified
  of their own repo's PRs regardless, and a mistyped login silently breaks the key.
- The `# vN` trailing comment on every SHA pin is **mandatory, not cosmetic** — brief 005 §5:
  Dependabot parses it to decide the next SHA. The repo's existing pins already carry it
  (`test.yml:43-47`); every new workflow must match that style exactly.

`.github/workflows/scorecard.yml` (brief 005 §6): `ossf/scorecard-action` SHA-pinned,
`permissions: { id-token: write, security-events: write }`, `publish_results: true`, triggers
`branch_protection_rule` + weekly `schedule` + `push: [main]`, SARIF uploaded via
`github/codeql-action/upload-sarif`.

- ✅ Brief 001 §1 lists SPEC 8 compliance (trusted publishing, SHA-pinned actions, minimal
  permissions) as table stakes; Scorecard is how that posture becomes observable.
- ❌ Brief 005 §6: a monorepo gets **one** score for ten distributions, and the *Branch-Protection*
  check will score poorly until the Phase 9 ruleset is applied. Accepted and documented rather than
  gamed — the score is a signal, never a gate, and Scorecard is not in any `needs:`.

**These two files are the only hardening items that are files.** Everything else in brief 007 is a
UI toggle and lives in Phases 8–9; Appendix A marks these two rows "done in Phase 5" so nobody
writes them twice.

### §RL-11-gov — governance files

| File | Content anchor |
|---|---|
| `CONTRIBUTING.md` | Dev setup (`uv sync --all-packages --all-extras`, the `git config blame.ignoreRevsFile` one-liner), `make lint`/`type-check`/`test`, the **never `uvx ruff`** rule, the docs-in-the-same-commit rule, the "tests with every code addition" rule, and §RL-9-policy's versioning/deprecation section |
| `SECURITY.md` | Supported versions (**latest minor of the current major only** — 3.0.x today), private reporting via GitHub Security Advisories, 90-day coordinated-disclosure embargo, explicit scope note naming the security-bearing subsystems (JWT/authority, field-level encryption, multitenant isolation/RLS, CORS defaults) |
| `CODE_OF_CONDUCT.md` | Contributor Covenant 2.1, verbatim, with the maintainer contact filled in |
| `.github/CODEOWNERS` | `* @edoardoscarpaci` — also feeds Scorecard's Code-Review check |
| `.github/ISSUE_TEMPLATE/bug_report.yml`, `feature_request.yml`, `config.yml` | Bug form asks for package + version + Python + broker; `config.yml` sets `blank_issues_enabled: false` and links security reports to `SECURITY.md` so vulnerabilities never land in public issues |
| `.github/PULL_REQUEST_TEMPLATE.md` | Checklist mirroring CLAUDE.md: docs updated *in this commit*, tests added, `make lint`/`type-check`/`test` green, CHANGELOG entry, BACKLOG row referenced |

`SECURITY.md`'s promised private channel only exists once **private vulnerability reporting** is
switched on — Step 8.5, Appendix A row 36. Writing the file without the toggle publishes a
reporting route that 404s.

**Gitignore hygiene: verify before inventing work.** BACKLOG RL-11 claims stray `dist/`, `site/`,
`scratchpad/`, `integration_test.log` and a `varco_beanie/.venv/` are in the tree. Present state:
`.gitignore:13` covers `dist/`, `:140` covers `.venv`, `:155` covers `/site`, and the scout found
no stray untracked artifacts. Step 2 re-verifies with `git status --porcelain` and `git ls-files`
and **records the finding**; only genuinely-missing entries (`scratchpad/`, `integration_test.log`,
and `site/` unanchored if a nested one can occur) are added. Do not manufacture a cleanup.

### §RL-12-docs — mike on a `gh-pages` branch

- ✅ Brief 006: mike 2.1.3–2.2.0 is maintained, compatible with mkdocs 1.6.x / Material 9.7.x, and
  is the **only** versioning path Material's own docs document.
- ❌ **The Actions-artifact model is rejected on a hard incompatibility, not a preference** — brief
  006 §3: mike's entire design is "create commits on a git branch"; `actions/upload-pages-artifact`
  gives it nowhere to write `versions.json`. There is no third option in the ecosystem.
- ❌ `contents: write` (to push `gh-pages`) is a broader grant than the artifact model's
  `pages: write` + `id-token: write`, and the branch grows every release (brief 006 Evidence Gap 3
  estimates ~10–20 MB per release; a non-issue for 3.0.0, revisit at 4.0). Accepted as the price
  of the only working mechanism.

Concretely:
- `mike>=2.1,<3` into the root `[dependency-groups] docs` (brief 006's decision rule).
- `mkdocs.yml`: add `site_url` (the switcher resolves `versions.json` relative to it) and
  `extra: { version: { provider: mike } }`. The URL's last segment is the repository slug, which
  is `varco` (§RL-SEC-repo-name) — so `https://edoardoscarpaci.github.io/varco/`. **No
  `theme.features` change is needed** (brief 006 §2).
- `.github/workflows/docs.yml`, two jobs in one file:
  - `dev` — `on: push: branches: [main]` → `mike deploy --push dev`.
  - `release` — `on: push: tags: ["v*"]` → derive `<major>.<minor>` from the tag and
    `mike deploy --push --update-aliases 3.0 latest`, then `mike set-default --push latest` (idempotent).
  - Both: `fetch-depth: 0`, `git config user.name/user.email`, `git fetch origin gh-pages:gh-pages
    || true`, `uv sync --locked --all-packages --all-extras` (brief 006 §6 — mkdocstrings imports
    the packages live; a docs-group-only install cannot render the API reference),
    `concurrency: { group: docs-deploy, cancel-in-progress: false }` to serialize branch writes.
- **Version naming: `3.0`** — `<major>.<minor>`, no `v`, no patch (brief 006 §5). ❌ Changing the
  scheme later 404s every old URL, so it is fixed now and never revisited. Aliases: `latest`
  (moves on each final release) and `dev` (main). `stable` is not used.
- **Pre-release tags must not move `latest`.** `v3.0.0rc1` is a real tag this plan pushes
  (Phase 7); the `release` job skips when the tag matches `rc|a|b|dev`.
- **No `make docs-deploy` target.** ✅ A local `mike deploy --push` writes straight to the public
  `gh-pages` branch from a developer machine with no review; CI is the only sanctioned deployer.
  ❌ No local preview of the *switcher*; `make docs-serve` still previews content, which is the
  99% case.
- Manual operator step: Settings → Pages → source = **Deploy from a branch**, `gh-pages` / root.
- ⛔ **`gh-pages` must never be targeted by a branch ruleset** — §RL-SEC-ghpages. This is the one
  hardening decision that can silently break the docs pipeline, and it is a *non-action*, which is
  exactly why it needs writing down.

### §RL-13-metadata — PEP 639

Across all ten distributions (and the root, which declares no `[project]` and so needs nothing):

```toml
license = "Apache-2.0"                  # SPDX expression (was: { text = "Apache-2.0" })
license-files = ["LICENSE"]             # PEP 639 final form: an array of glob strings
```

- ✅ Brief 004 §5: hatchling ≥ 1.27.0 implements PEP 639 (SPDX parsing, `License-Expression` core
  metadata, metadata 2.4) and warns on the legacy table form. `[build-system] requires` is raised
  to `hatchling>=1.27` in all ten so a fresh build environment cannot silently pick an older
  backend that ignores the field.
- ⚠️ Brief 004 renders `license-files` **two ways** — `{ globs = ["LICENSE"] }` in §5's first block
  and `["LICENSE"]` in its action block. The array is PEP 639's final form; the table form is
  draft-era. The plan writes the array and **verifies empirically** on a built artifact (Step 11)
  rather than trusting either rendering. Carried into Risks.
- **The `License :: OSI Approved :: Apache Software License` classifier is removed** from all ten.
  ✅ Brief 004 §5: redundant once an SPDX `license` field is present. ❌ Brief 004 Evidence Gap 4
  says PEP 639 does not *formally* deprecate it and calls keeping it "conservative" — so removal is
  a decision, not a citation, and Step 9 tests it empirically first: build one package with both
  present and record whether hatchling errors, warns, or is silent. Removal proceeds either way
  (redundancy is sufficient reason), but the recorded observation is what the rc1 upload then
  confirms against PyPI itself.
- **`Development Status` flip is deliberately NOT part of this section.** Modernizing license
  metadata is mechanical; declaring the project production-stable is a release statement. It lands
  in Phase 3 with the version freeze, so one commit carries the whole "this is 3.0.0" claim.

**PEP 735 audit — expected outcome is "already compliant; record it and stop".** The root and all
ten packages already use `[dependency-groups]` for dev/test/docs. The remaining
`[project.optional-dependencies]` entries (`varco_fastapi`'s `ws`/`mcp`/`a2a`/`otel`/`prometheus`/
`openapi`, `varco_casbin`'s `fastapi`/`sqlalchemy`/`beanie`, `varco_sa`'s `migrations`) are genuine
**runtime** extras a consumer installs — which is exactly what optional-dependencies is *for*; PEP
735 replaces them only for unpublished dev/test/docs sets. The audit's deliverable is a short
recorded finding, not a migration. Brief 001 §3's blanket "`[dependency-groups]`, not
`[project.optional-dependencies]`" is a statement about *dev* dependencies and must not be
over-applied.

### §RL-SEC-hardening — GitHub repository hardening (brief 007)

Everything in this subsection is grounded in
`design/varco-1-0-release/research/007-github-repo-hardening-settings.md`, cited as **007 §N**.
It is production-readiness *configuration*, not a BACKLOG row — see Acceptance criteria.

#### Files I commit vs. buttons I click vs. commands I run once

The hardening work has exactly three shapes and mixing them is how it goes wrong — a "step" that
is really a click gets marked done without anything happening, and a click that is really a file
gets clicked into a state no future contributor can see:

| Shape | What | Where in this plan |
|---|---|---|
| **Files I commit** | `.github/dependabot.yml`, `.github/workflows/scorecard.yml`, `.github/workflows/release.yml`, `.github/workflows/docs.yml`, `.github/CODEOWNERS`, `SECURITY.md`, the Scorecard README badge | Phases 4–6 (Steps 21, 23, 27–29, 35) + Step 9.7 |
| **Buttons I click** | Ten `pypi-*` environments, ten PyPI publisher configs, Pages source, Actions permissions, code-security toggles + CodeQL, PR/merge settings, repo About, branch ruleset, tag ruleset | Step 39, **Phase 8**, **Phase 9** |
| **Commands I run locally, once** | *Nothing to configure* — commit/tag signing is **already set up on this machine**; there is only a verification (Step 4a) | **Step 4a** |

Two asymmetries are deliberate, matching the providify precedent: **CodeQL is a button** (default
setup, no YAML — 007 §5), while **Scorecard is a file** (it has no default-setup equivalent).

#### §RL-SEC-signing — signing is already done; this is a VERIFY, not a setup

The maintainer's machine already has `gpg.format=ssh`, `user.signingKey=~/.ssh/id_ed25519.pub`,
`commit.gpgSign=true`, `tag.gpgSign=true`, and the last 15 commits on `main` all verify locally
(`git log --format=%G? -15` → `G`). 007 §Checklist row 9 says the same ("varco already has this
configured"). **So varco has no equivalent of providify's Phase 0 "configure signing" phase**, and
none of that plan's "unsigned release history" failure mode applies here.

What is *not* established from the tree is the GitHub side: a key registered only as an
**Authentication key** signs locally but renders **Unverified** on github.com, because GitHub
matches signatures against keys of type `signing`. That single check is Step 4a. It matters twice
over — for the green Verified badge on the release history, and because Phase 9's "Require signed
commits" rule would otherwise lock the maintainer out of their own default branch.

`tag.gpgSign=true` also means Steps 40/41's tags are signed at no extra cost, which is what
Scorecard's `Signed-Releases` check looks for (brief 005 §6 notes PEP 740 attestations do *not*
satisfy that check on their own).

#### §RL-SEC-repo-name — ✅ RESOLVED: the repo is `varco`; only the local remote is stale

**Previously filed here as a ⛔ release blocker. It is not one.** The question was whether the
GitHub slug is `fastrest` (what `git remote -v` shows) or `varco` (what all ten `[project.urls]`
say). Resolved by direct measurement, and by the user:

| Probe | Result |
|---|---|
| `git ls-remote git@github.com:edoardoscarpaci/varco.git HEAD` | `d51cf882…` |
| `git ls-remote git@github.com:edoardoscarpaci/fastrest.git HEAD` | `d51cf882…` — **identical** |
| User confirmation | `github.com/edoardoscarpaci/varco` exists and **is the correct repo name** |

Two names resolving to the same HEAD is the signature of **one repository that was renamed**, with
GitHub serving the old slug as a redirect. There is no second repo and no divergence.

**Consequences, all in the cheap direction:**

1. **The ten packages' `[project.urls]` are already correct.** No metadata edit — the ~30 URLs
   point at the live repo. (Former Step 10a, which would have rewritten them, is deleted.)
2. **The ten PyPI trusted-publisher configs use `varco`** — the *current* name. The redirect does
   not apply to OIDC: the claim is matched against the repository's name at token-exchange time,
   so a config written for `fastrest` would be wrong even though the URL redirects (007 §1).
3. **`site_url` is `https://edoardoscarpaci.github.io/varco/`** and the Scorecard badge/identity
   use `varco` (§RL-12-docs, 007 checklist row 45).
4. **Only the local clone is stale.** `origin` still points at the pre-rename slug and works purely
   by redirect. Fix it once (Step 4b) so nothing in this plan reads a misleading `git remote -v`.

DESIGN: fix the local remote rather than leaving it on the redirect
- ✅ Every later step that derives the slug from `git remote -v` — and any contributor who does
  the same — reads the true name instead of a redirect artifact.
- ✅ Costs one command, reversible, and touches no committed file.
- ❌ Not strictly required; the redirect works today. Accepted: a redirect is a courtesy GitHub
  may stop honouring if the `fastrest` name is ever reused by anyone, and this plan is about
  removing exactly that class of latent surprise.

**⚠️ Residual, unverifiable here:** whether GitHub still lists `fastrest` as a *retired* slug that
could be reclaimed. Not checkable without the API (`gh` is not installed — §RL-SEC-envs). Low
impact given the remote is being repointed anyway.

#### Ordering with GitHub hardening folded in

```
P0 ─ Step 3 checkpoint: freeze + policy + security window (repo name: confirm `varco`)
     Step 4a: confirm the SSH key is registered as a GitHub *Signing Key*
     │
     ▼
P1 ─► P2 ─► P3 ─► P4 ─► P5 (files: release.yml, dependabot.yml, scorecard.yml) ─► P6 (docs.yml)
     │
     ▼
P7 Step 39  ten environments · ten PyPI publishers · Pages source   ─┐
P8          Actions / code security / CodeQL / PR settings / About  ─┘ one browser session
     │
     ▼
P7 Step 40  v3.0.0rc1 rehearsal ─► Step 41  v3.0.0 ─► ten PyPI projects + docs site
                                                              │
                                                              ▼
P9  branch ruleset (all-green, signed commits) · tag ruleset (v*) · Scorecard badge
```

**Three hard ordering facts, each a real failure mode, not a style preference:**

1. **The repo slug must be asserted as `varco` before Step 39.** It is the only
   hardening-adjacent input that ten irreversible PyPI publisher configs are keyed on (007 §1).
   Now resolved (§RL-SEC-repo-name), the live risk inverts: the danger is no longer an *unknown*
   slug but a *stale* one — the redirecting `fastrest` name still resolves in a browser while
   failing OIDC, so a config copied from an old note fails ten token exchanges during the rc1
   rehearsal. Step 4b repoints `origin` precisely so nothing reads the old name.
2. **The `v*` tag ruleset must not exist before Step 41 — even though 007 §4 says it safely
   could.** The brief is explicit and is accepted as accurate: admins added as bypass actors create
   tags without a prompt, so ordering "makes no difference". This plan still orders it *after*,
   because varco has **zero git tags today** — `v3.0.0rc1` and `v3.0.0` are the first two tag
   pushes in the repository's history, they are the plan's two irreversible acts, and the plan's
   own recovery path is already thin ("never re-tag; re-run the failed leg", Edge cases). Adding
   an untested bypass-actor interaction to that moment buys nothing: the ruleset's entire purpose
   is protecting `v3.0.1` onward. ⚠️ 007 Evidence Gap 2 also notes bypass behaviour in *multi-rule*
   rulesets is not fully documented — which is precisely the shape being built here.
3. **A branch ruleset that catches `gh-pages` breaks the docs pipeline silently.** See below. This
   is the one ordering fact with no providify equivalent, because providify has no `gh-pages`.

A fourth, *non*-fact worth stating because providify's plan makes it a blocker and varco's does
not: **required status checks are already selectable.** Providify could not pick `test (3.12)`
before its first CI run; varco's `test.yml` has been reporting on every push for many plans, so
`all-green` is in the picker today. The constraint still bites if a job is ever *renamed* — the
picker only lists check-run names GitHub has already observed.

#### §RL-SEC-ghpages — the `gh-pages` ruleset trap

`mike deploy --push` (§RL-12-docs) is CI writing commits to `gh-pages` with `GITHUB_TOKEN`. Two
independent ruleset rules interact with that, in opposite directions:

| Rule, if it catches `gh-pages` | Effect | Source |
|---|---|---|
| **Block force pushes** | ⛔ **Breaks `mike deploy --push`** — mike force-pushes to keep the branch a single-commit history | 007 §2 |
| **Require signed commits** | Survives — GitHub auto-signs `GITHUB_TOKEN` commits made through its API | 007 §2, with ⚠️ Evidence Gap 3: whether the rule *accepts* bot signatures is not explicitly confirmed in GitHub's docs |
| **Restrict deletions** | Harmless | 007 §2 |

**Decision: 007's Option A — no ruleset on `gh-pages` at all.** ✅ It is the brief's own
recommendation for varco (007 §2, "deployment infrastructure, not source code"); ✅ it removes
both the confirmed breakage (force push) and the undocumented one (Evidence Gap 3) in one
non-action; ✅ nothing of value is protected — `gh-pages` is 100% regenerable by re-running
`docs.yml`. ❌ A force-push or deletion of `gh-pages` by a compromised token is unblocked.
Accepted: the same token can already rewrite the branch's entire contents, so the rule would be
theatre. Options B (protect without force-push blocking) and C (`github-actions[bot]` as bypass
actor) are recorded in Appendix A row 15's notes but not taken.

**The actual trap is a *targeting* mistake, not a rule choice.** A branch ruleset created with
target `All branches`, `~ALL`, or a wildcard like `*` catches `gh-pages` without anyone deciding
to. The failure is silent in the worst way: `main` CI stays green, the release ships, and the
docs deploy fails days later in a workflow nobody is watching. **Invariant: the branch ruleset's
target is the literal string `main` and nothing else** — asserted in Step 9.4 and in Phase 9's
Verify block, not left implicit.

#### §RL-SEC-envs — ten environments, ten publishers, and no `gh` CLI

007 §1 confirms brief 005's finding: ten PyPI projects need **ten distinct GitHub Environments**
(`pypi-varco-core` … `pypi-varco-casbin`), because the environment name is one of the four matched
OIDC claims. Free for a public repo (007 §7). No deployment-branch restriction — one would block
the tag-triggered run.

**Cost, honestly:** twenty manual UI operations (ten Settings → Environments entries, ten PyPI
publisher forms), each a name and an optional URL. 007 §1 states it "cannot be automated via `gh`
CLI today". ⚠️ **`gh` is not installed on the maintainer's machine** (`gh: command not found`), so
any step in this plan that offers a `gh` command must say so and must give the UI path as the
primary route — never assume it is available. Nothing in Phases 8–9 requires `gh`.

#### Say it plainly: admin bypass means the rulesets enforce nothing against the maintainer

Phase 9 adds `Repository admin → Always allow` as a bypass actor on both rulesets, and that is the
correct call for a one-maintainer project — without it, a wedged status check means the fix cannot
land, and the `v3.0.1` tag cannot be pushed at all. But the consequence must not get sanded off
into reassurance:

> With admin bypass on and exactly one admin, these rulesets enforce **nothing** against that
> admin. What they actually buy is (a) an audit trail, (b) the configuration OpenSSF Scorecard's
> `Branch-Protection` and `Code-Review` checks read, and (c) muscle memory for the day a second
> contributor arrives.

**Invariant: never describe this repository as "protected" on the basis of these rulesets** — not
in `SECURITY.md`, not in release notes, not in a README badge caption. Required approvals stay at
**0** for the same reason: GitHub permits self-approval, so a `1` buys a click, not a review.

#### Alternatives considered

- **Classic branch protection instead of rulesets** — rejected. ❌ 007 §Version notes: classic
  rules are deprecated (GHES 3.16, Aug 2026) with auto-migration shipping; only one classic rule
  applies to a branch and tags need a separate deprecated UI. ✅ Rulesets cover branches and tags
  in one model with per-actor bypass — the exact escape hatch a solo maintainer needs.
- **Requiring the matrix legs `unit (3.12)` / `unit (3.13)` as status checks** — rejected, and this
  is the sharpest deviation from providify's plan (which does select its matrix legs). ❌ CLAUDE.md
  §CI is explicit: "never select an individual matrix leg in branch protection, or a skipped leg
  leaves the check permanently pending"; 007 §3 independently confirms the aggregate-job pattern
  and the trap. ✅ `all-green` (`needs: [lint, unit]`, `if: always()`, asserts both results are
  literally `'success'`) already exists and is the only correct selection.
- **CodeQL advanced setup (a committed workflow)** — rejected for now. ❌ One more workflow to pin
  and babysit; 007 §5 says Python needs no special configuration and names default setup the
  documented starting point. ✅ Two clicks, GitHub maintains the config. ⚠️ 007 Evidence Gap 1:
  ten-package `uv` workspace coverage is *not* confirmed by official docs — hence Step 8.6's
  explicit coverage verification and the `paths-ignore: ["*/tests/**"]` escape hatch, taken only
  on evidence.
- **Scripting Phases 8–9 with `gh`/REST** — rejected. ❌ 007 §1 states environments cannot be
  created via `gh` today, and `gh` is not even installed here; a half-scripted checklist is worse
  than a clicked one because it hides which half ran. ✅ UI paths, once, by hand, recorded in
  Appendix A.
- **Adding a `.github/secret_scanning.yml` to pre-suppress test-credential patterns** — rejected.
  ❌ 007 §6 says the file "is not yet widely documented", and the inline bypass flow ("used in
  tests") is the supported path with a logged, reviewable reason. ✅ Fewer undocumented files;
  each bypass is an explicit decision rather than a blanket exclusion.

---

## Steps

### Phase 0 — Preflight and the irreversible decisions

1. [x] `design/varco-1-0-release/measurements/version-baseline.md` (new) — record, from the live
   tree: each of the ten `[project].version` values, its `Development Status` classifier line, its
   `license` form, its `[build-system] requires`, and every sibling requirement string with
   `file:line`. This is the before-picture every later `--check` is measured against.
2. [x] `.gitignore` — verify §RL-11-gov's hygiene claim: run `git status --porcelain`,
   `git ls-files | grep -E '(^|/)(dist|site)/'`, and `git check-ignore -v scratchpad
   integration_test.log varco_beanie/.venv`. Append **only** the entries proven missing; record the
   verification result (including "already clean") in the Step 1 measurement file. Do not
   manufacture cleanup work the tree does not need.
3. [x] ⛔ **CHECKPOINT — do not edit a single file past this point until answered.** Present:
   (a) §RL-9-freeze's evidence table and the claim that 3.0.0 ships exactly `CHANGELOG.md`'s
   current `[Unreleased]`; (b) the target `3.0.0` + `Development Status :: 5 - Production/Stable`;
   (c) §RL-9-policy's **12-month** deprecation floor vs brief 001's 2-year expectation; (d)
   §RL-11-gov's "latest minor of the current major only" security-support window.
   **(e) is no longer a decision** — §RL-SEC-repo-name was resolved by measurement and by the
   user: the slug is **`varco`**. Restate it here as a one-line confirmation only, and record
   `slug = varco` in the Step 1 measurement file — Steps 27, 30, 34, 39 and Appendix A read it.
4. [x] Record PyPI registration status for all ten names (`pip index versions varco-core` or the
   project page) in the Step 1 measurement file — this splits the ten into "pending publisher" vs
   "normal publisher" for §RL-10-publish's operator runbook. Also verify in the same step that
   `uv build --package varco_kafka` (underscore form) is accepted, per §RL-10-matrix's ⚠️.
4b. [ ] **Repoint the stale local remote** (§RL-SEC-repo-name). `origin` still carries the
   pre-rename slug and works only via GitHub's redirect:
   ```bash
   git remote -v                        # shows .../fastrest.git (pre-rename, redirected)
   git remote set-url origin git@github.com:edoardoscarpaci/varco.git
   git remote -v                        # expect: .../varco.git
   git ls-remote origin HEAD            # expect: resolves, same HEAD as before
   ```
   Local-only, committed to nothing, reversible. Do it before Step 39 so every slug-deriving
   step and any `git remote -v` a contributor runs reads the true name.

4a. [X] **Signing check — verify, do not configure** (§RL-SEC-signing; Appendix A rows 9/9b).
   Signing is already working locally, so this step has exactly one open question and one
   command-plus-one-browser-check:
   ```bash
   git config --get gpg.format          # expect: ssh
   git config --get commit.gpgsign      # expect: true
   git config --get tag.gpgsign         # expect: true   <- makes Steps 40/41's tags signed
   git log --format='%G? %h %s' -15     # expect: every line starts with G
   ```
   Then open the most recent `main` commit on github.com and confirm it renders a green
   **Verified** badge. If it says **Unverified**, the public key is registered as an
   *Authentication* key only: add it a second time at https://github.com/settings/keys → New SSH
   key → Key type: **Signing Key**, same key material. ⚠️ This registration requirement is not in
   007 (its row 9 covers only the `git config` lines); it comes from GitHub's signing docs — see
   Risks. Record the outcome in the Step 1 measurement file. **Nothing here is a blocker for
   Phases 1–7**; it is a blocker for Step 9.5.

### Phase 1 — The bump mechanism (RL-9, §RL-9-bump)

5. [x] `varco_core/tests/test_bump_script.py` (new) — **failing tests first**, importing the script
   by path exactly as `varco_core/tests/test_api_surface_snapshot.py:33-48` does. Cases:
   - `--set 3.0.0` on a temp copy of the workspace rewrites all ten `[project].version`;
   - the sibling requirement `"varco-core"` becomes `"varco-core~=3.0"` in `[project].dependencies`;
   - `varco_fastapi`'s `ws = ["varco-ws"]` extra and `varco_casbin`'s `fastapi = ["varco-fastapi"]`
     extra are pinned too;
   - `[dependency-groups]` sibling entries (`varco_core` dev→`varco-fastapi`, `varco_sa`
     dev→`varco-redis`, `varco_fastapi` dev→`varco-sa`) are **left untouched**;
   - `[tool.uv.sources]` and `examples/**` are untouched;
   - **round-trip fidelity**: `--set <the current version>` on the real tree produces a byte-identical
     file (guards tomlkit reformatting the aligned `version       = "…"` style);
   - `--dry-run` writes nothing and exits 0; `--check` on a doctored divergent copy exits 1 and
     names the divergent package; `--bump major/minor/patch` arithmetic;
   - the package list comes from `scripts/packages.sh` (assert `examples` is absent, all ten present);
   - `test_workspace_versions_are_coherent` — runs `--check` against the **real** tree. It is
     *expected to fail* until Phase 3 lands; mark it `xfail(strict=True)` with `reason="RL-9: ten
     versions still divergent until Plan 023 Phase 3"` and **flip it to a plain assertion in Step 17**
     (strict xfail means Phase 3 cannot land silently).
6. [x] `pyproject.toml` (root) — add `"tomlkit>=0.13"` to `[dependency-groups] dev`, with a comment
   citing §RL-9-bump (style-preserving TOML edits; a regex cannot distinguish shipped from
   dev-group sibling entries). Run `uv lock`.
7. [x] `scripts/bump.py` (new) — implement §RL-9-bump's contract. Module docstring carries the
   contract table and a `DESIGN:` block with the ✅/❌ from §RL-9-bump and §RL-9-pins. Derives its
   package list by executing `scripts/packages.sh` (subprocess, same as `scripts/api_surface.py`).
   Full docstrings with `Args:`/`Returns:`/`Raises:`/`Edge cases:` per CLAUDE.md.
8. [x] Verify Phase 1: `uv run pytest varco_core/tests/test_bump_script.py`, `make lint`,
   `make type-check`.

### Phase 2 — Metadata modernization (RL-13, §RL-13-metadata)

9. [x] Empirical check, recorded in the Step 1 measurement file: with `varco_core/pyproject.toml`
   temporarily carrying **both** `license = "Apache-2.0"` and the `License :: OSI Approved`
   classifier, run `uv build --package varco_core` and record whether hatchling errors, warns, or
   is silent. Revert the scratch edit. (Settles brief 004's Evidence Gap 4 for this toolchain.)
10. [x] All ten `varco_*/pyproject.toml` — replace `license = { text = "Apache-2.0" }` with
    `license = "Apache-2.0"` + `license-files = ["LICENSE"]`; remove the
    `"License :: OSI Approved :: Apache Software License"` classifier; raise `[build-system]
    requires` to `["hatchling>=1.27"]`. One commit, ten files.
11. [x] Verify on a real artifact: `uv build --package varco_core`, unzip the wheel, and assert
    `METADATA` contains `Metadata-Version: 2.4`, `License-Expression: Apache-2.0`,
    `License-File: LICENSE`, and **no** `Classifier: License ::`. Record the observed
    `license-files` spelling that worked (settles the §RL-13-metadata ⚠️). Append the result to the
    Step 1 measurement file.
12. [x] `design/varco-1-0-release/packaging-audit.md` (new) — the PEP 735 finding: per package,
    which sets are `[dependency-groups]` and which are `[project.optional-dependencies]`, plus the
    one-line verdict that the latter are all genuine runtime extras and correctly placed. Expected
    outcome: no migration.

### Phase 3 — The freeze (RL-9)

13. [x] `uv run python scripts/bump.py --set 3.0.0 --dry-run` — read the diff. It must touch
    exactly: ten `version =` lines, nine `[project].dependencies` sibling entries, two
    `[project.optional-dependencies]` sibling entries. Anything else is a Phase 1 bug, not an
    acceptable surprise.
14. [x] `uv run python scripts/bump.py --set 3.0.0` (writes files and runs `uv lock`).
15. [x] All ten `varco_*/pyproject.toml` — `Development Status :: 3 - Alpha` →
    `Development Status :: 5 - Production/Stable` (brief 004 §6: exact canonical string). Done by
    hand, not by `bump.py` — it is a one-time release statement, not a repeatable bump concern.
16. [x] `CHANGELOG.md` — retitle `## [Unreleased]` to `## [3.0.0] — <release date>`, open a fresh
    empty `## [Unreleased]` above it, and add a short "Packaging & release" subsection recording:
    lockstep versioning at 3.0.0 (and what each package's previous version was, from Step 1),
    sibling pins now `~=3.0`, PEP 639 license metadata, Production/Stable classifier.
17. [x] `varco_core/tests/test_bump_script.py` — remove the `xfail(strict=True)` from
    `test_workspace_versions_are_coherent`; it is now a live gate (§RL-9-bump).
18. [x] `CLAUDE.md` + `README.md` — **same commit** (CLAUDE.md doc rule): a `scripts/bump.py`
    paragraph in CLAUDE.md's Commands section next to the `scripts/api_surface.py` one, stating the
    `--check`-is-a-gate contrast; a one-line lockstep-versioning + `~=3.0` note in README's
    installation section.
19. [x] Verify Phase 3: `make lint`, `make type-check`, `make test`, `uv sync --locked
    --all-packages --all-extras` (proves `uv.lock` is not stale), `uv run python
    scripts/api_surface.py --check` (proves the freeze changed no public symbol).

### Phase 4 — Governance and the written policy (RL-11, §RL-9-policy / §RL-11-gov)

20. [x] `CONTRIBUTING.md` (new) — dev setup, the command table, house rules, and the **Versioning
    and deprecation policy** section built from §RL-9-policy's clause table. Every clause must be
    checkable against the tree; cite `varco_core/varco_core/deprecation.py` and name AB-1/AB-2/AB-4
    as the worked examples.
21. [x] `SECURITY.md` (new) — supported-version matrix (3.0.x), private-advisory reporting, 90-day
    embargo, security-bearing-subsystem scope note. ⚠️ The private channel it promises does not
    exist until Step 8.5 enables private vulnerability reporting — the two are a pair.
22. [x] `CODE_OF_CONDUCT.md` (new) — Contributor Covenant 2.1 with the contact filled in.
23. [x] `.github/CODEOWNERS` (new).
24. [x] `.github/ISSUE_TEMPLATE/{bug_report.yml,feature_request.yml,config.yml}` and
    `.github/PULL_REQUEST_TEMPLATE.md` (new) — per §RL-11-gov's table; `config.yml` must set
    `blank_issues_enabled: false` and route security reports to `SECURITY.md`.
25. [x] `README.md` — link the four new root documents from the header/contributing section.

### Phase 5 — Release automation (RL-10, §RL-10-publish / §RL-10-matrix / §RL-10-supply)

26. [x] `git rm .github/workflows/publish.yml`.
27. [x] `.github/workflows/release.yml` (new) — §RL-10-publish's three-job shape. Header comment in
    `test.yml`'s house style: a `DESIGN:` block with ✅/❌, the load-bearing-filename warning, the
    "never a required check" note, and brief citations. Every action SHA-pinned with a trailing
    `# vN` comment (`test.yml:43-47` style — mandatory for Dependabot, brief 005 §5). Trigger:
    `push: tags: ["v*"]` plus `workflow_dispatch`. Top-level `permissions: {}`. Any URL in the file
    (e.g. an `environment.url`) uses the slug `varco` (§RL-SEC-repo-name).
28. [x] `.github/dependabot.yml` (new) — `uv` + `github-actions`, `directory: "/"`, weekly,
    grouped, **no `reviewers:` key** (§RL-10-supply), with a comment recording brief 005's
    unresolved uv-workspace bug (dependabot-core#14004) and the "delete the uv block if it produces
    nonsense PRs" escape hatch. This is Appendix A row 32 — do not write it again in Phase 8.
29. [x] `.github/workflows/scorecard.yml` (new) — §RL-10-supply. Comment records the monorepo
    single-score caveat and that Branch-Protection scores low until Phase 9's ruleset is applied.
    This is Appendix A row 44 — do not write it again in Phase 8. (007's own template omits
    `branch_protection_rule` from the triggers and pins `upload-sarif` differently; follow brief
    005 §6 and the repo's existing `# vN` SHA-pin style, and check every pin against its releases
    page before committing.)
30. [x] `design/varco-1-0-release/release-runbook.md` (new) — the operator runbook: the three manual
    step groups from §RL-10-publish (ten environments, ten publisher configs split by the Step 4
    registration finding, the `main` ruleset), the Pages setting from §RL-12-docs, the rc1 rehearsal
    procedure, and the tag-to-publish sequence. Open it by stating the slug is `varco`, since
    every URL and the publisher `repo:` field depend on it, and cross-reference **Appendix A** for
    the hardening checklist rather than duplicating those rows.
31. [x] `Makefile` — leave `build`/`publish` behaviour unchanged; add a comment above `publish`
    marking it the **break-glass manual path** and naming `release.yml` + trusted publishing as the
    sanctioned one. (No token is stored anywhere in the repo or CI either way.)
32. [x] `CLAUDE.md` — extend the §CI section with the two new workflows and a manual-operator-steps
    note in the same style as the existing pending branch-protection note; extend the Commands
    section's release paragraph. State that the pending branch-protection note is now specified in
    full by Plan 023 Phase 9 + Appendix A, and that the required check is still **only**
    `Tests / All tests passed`.

### Phase 6 — Versioned docs (RL-12, §RL-12-docs) — may run in parallel with Phases 4–5

33. [x] `pyproject.toml` (root) — `"mike>=2.1,<3"` into `[dependency-groups] docs`, with a comment
    citing brief 006's decision rule. Run `uv lock`.
34. [x] `mkdocs.yml` — add `site_url: https://edoardoscarpaci.github.io/varco/` (slug resolved in
    §RL-SEC-repo-name; a wrong value 404s the version switcher, not the pages), and
    `extra: { version: { provider: mike } }`,
    with a DESIGN comment recording why the Actions-artifact model was rejected (brief 006 §3 —
    mike has nowhere to commit `versions.json`).
35. [x] `.github/workflows/docs.yml` (new) — the `dev` and `release` jobs from §RL-12-docs, with
    `fetch-depth: 0`, git identity, the `gh-pages` pre-fetch, `uv sync --locked --all-packages
    --all-extras`, serialized `concurrency`, `permissions: contents: write` scoped to the jobs, the
    pre-release-tag guard, and SHA-pinned actions with `# vN` comments.
36. [x] `design/varco-1-0-release/release-runbook.md` — add the Pages publishing-source step, a
    "first deploy creates `gh-pages`" note, and a ⛔ line reproducing §RL-SEC-ghpages: **never
    create a branch ruleset that targets `gh-pages`** (Appendix A row 15).
37. [x] Verify Phase 6 locally as far as is possible without pushing: `make docs-strict` still
    builds, and `uv run mike --version` resolves. **Do not run `mike deploy` locally** (§RL-12-docs).

### Phase 7 — Close-out, rehearsal, tag

38. [x] `BACKLOG.md` — mark RL-9/RL-10/RL-11/RL-12/RL-13 **✅ done (Plan 023)** with the evidence
    column pointing at the artifacts (`scripts/bump.py` + its tests, `release.yml`, `dependabot.yml`,
    `scorecard.yml`, `docs.yml`, the four governance files, `packaging-audit.md`, the runbook).
    Move the two answered "Open questions for `/plan`" bullets (bump mechanism; exact-vs-compatible
    pins) into an "Answered by Plan 023" section with the one-line verdicts, matching the existing
    "Answered by Plan 016" block's format. **Recommended, not mandated**: file one *new* row for
    the hardening work (suggested ID **RL-SEC**, "GitHub repository hardening — rulesets, code
    scanning, secret scanning, ten publishing environments; spec in Plan 023 §RL-SEC-hardening +
    Appendix A"), since Phases 8–9 close no existing row and would otherwise be invisible to
    BACKLOG. See Acceptance criteria.
39. [X] **Operator: apply the runbook's manual steps** — ten environments, ten publisher configs
    (repo `varco`, §RL-SEC-repo-name), Pages source. **Do Phase 8 in the same browser session**; do
    **not** do Phase 9 yet. Nothing after this can succeed without the environments and publishers.
40. [ ] **Rehearsal**: tag `v3.0.0rc1` and push. Expect: `release.yml` green across all ten legs,
    ten pre-releases on PyPI with attestations, `docs.yml`'s `release` job **skipped** by the
    pre-release guard, `dev` docs still current. Record the outcome in the runbook. Any failure is
    fixed and re-rehearsed as `rc2` — never by patching production during the real tag.
41. [ ] **Release**: tag `v3.0.0` and push. Verify ten PyPI pages show 3.0.0 + attestations +
    `License-Expression: Apache-2.0` + Production/Stable, the docs site serves `3.0`/`latest`/`dev`
    with a working switcher, and `pip install varco-fastapi==3.0.0` resolves `varco-core~=3.0` in a
    clean venv. `git tag -v v3.0.0` shows a good signature (Step 4a).

### Phase 8 — GitHub hardening: click-through settings (do them with Step 39)

🧑 **Browser only. No files, no commits, no `gh`** (§RL-SEC-envs: `gh` is not installed and could
not create environments anyway). Nothing in this phase blocks or is blocked by Phases 1–7, so run
it in the same Settings session as Step 39 rather than making a second trip. Row numbers refer to
**Appendix A**.

8.1 [X] **Settings → General → Features** (rows 40–42): **Issues** ON. **Wiki** OFF — the
    documentation surface is `docs/`, published versioned via mike (§RL-12-docs); a stale wiki
    competing with it is worse than none. **Discussions**: OFF until there is demand — an empty
    Discussions tab reads as abandonment.

8.2 [X] **Settings → General → Pull Requests** (rows 37–38): "Automatically delete head branches"
    **ON**. Merge strategies: **disable "Allow merge commits"**, keep **"Allow squash merging"**;
    rebase optional. Doing this here is what turns Phase 9's "Require linear history" rule into a
    no-op instead of a trap — the UI stops offering the forbidden button.

8.3 [X] **Repo home → About → ⚙** (row 43): one-line description; Website → the docs site
    (`https://edoardoscarpaci.github.io/varco/`) rather than a PyPI page, since ten
    PyPI pages cannot be linked from one field; Topics → `python`, `event-driven`, `kafka`,
    `redis`, `dependency-injection`, `framework`, `async`. Discoverability, not security — but free.

8.4 [X] **Settings → Actions → General** (rows 16–18):
    - *Workflow permissions* → "Read repository contents and packages permissions". **Verify only**
      — read-only has been the default since Feb 2023 (007 checklist row 16). `release.yml`
      escalates to `id-token: write` in its own job block and `docs.yml` to `contents: write` in
      its own, which is exactly the pattern this default exists for (§RL-10-publish, §RL-12-docs).
    - *Actions permissions* → "Allow all actions and reusable workflows" (007 row 17). The
      verified-creator allowlist filter is not on the free tier; the practical substitute is that
      every action in this repo is SHA-pinned with a `# vN` comment (§RL-10-supply).
    - *Fork pull request workflows from outside collaborators* → "Require approval for first-time
      contributors who are new to GitHub" (007 row 18).

8.5 [X] **Settings → Code security** (rows 30–36), top to bottom:
    - Dependabot alerts → **Enable**
    - Dependabot security updates → **Enable**
    - Secret scanning → **Enable**
    - Secret scanning **push protection** → **Enable** — the one that prevents an incident rather
      than reporting it. See 8.7 before pushing test fixtures.
    - Code scanning → **Set up → Default setup** (CodeQL, language auto-detection on — 007 §5).
      Not advanced setup: see §RL-SEC-hardening's Alternatives.
    - Private vulnerability reporting → **Enable** — this is what makes `SECURITY.md`'s promised
      channel (Step 21) real.
    Nothing to do for the dependency graph: automatic on public repos.

8.6 [X] ⚠️ **Verify CodeQL actually covers the ten packages** (007 Evidence Gap 1 — official docs
    do not confirm default setup on a ten-member `uv` workspace). After the first CodeQL run
    completes, open Security → Code scanning and confirm findings/scanned paths span more than one
    `varco_*` directory. Record the observation in the release runbook. **Only on evidence of
    incomplete coverage** switch to advanced setup, and only then consider
    `paths-ignore: ["*/tests/**"]`. Do not pre-emptively narrow the scan — 007 §5 notes tests are
    legitimately scanned.

8.7 [X] **Expect push protection to flag test credentials; do not "fix" the fixtures.** varco
    commits throwaway testcontainer credentials and JWT test PEM keys. 007 §6: the block offers
    three bypass reasons — choose **"It's used in tests"** (creates a closed, labelled alert). Do
    **not** delete or obfuscate a test fixture to appease a scanner, and do **not** add
    `.github/secret_scanning.yml` (§RL-SEC-hardening Alternatives). ⚠️ 007 Evidence Gap 4: the
    false-positive rate on PEM fixtures specifically is unbenchmarked, so the first push after 8.5
    may surprise you. Note in the runbook which fixtures were bypassed and why.

**Solo-maintainer note.** Every setting in Phase 8 is a real control — none of them depends on a
second human existing, and none is weakened by there being one admin. That is why they are
unconditional and come first. The theatre starts in Phase 9, and §RL-SEC-hardening says so out loud.

**Verify:** Settings → Code security shows five green toggles plus CodeQL "Default setup enabled";
Settings → Actions → General shows read-only workflow permissions and the fork-PR approval setting;
Settings → General → Pull Requests shows merge commits disabled and auto-delete on; the About panel
shows the description, docs URL and topics; the Security tab offers "Report a vulnerability" (which
is the exact link `SECURITY.md` promises). One behavioural smoke test worth running: on a scratch
branch, commit a string shaped like an AWS key and push — it must be refused, and the bypass dialog
must offer "It's used in tests".

### Phase 9 — GitHub hardening: rulesets (ONLY after Step 41 has shipped)

🧑 **Browser only.** **Do not start until `v3.0.0` is pushed and all ten PyPI projects show 3.0.0.**
The ordering reasoning is §RL-SEC-hardening's three ordering facts and is not repeated here; the
short version is that fact 2 (this repo's first-ever tags are Steps 40/41) and fact 3 (`gh-pages`)
are the binding ones, and that unlike providify, required-status-check selectability is *not* a
constraint here because `test.yml` has been reporting for many plans.

9.1 [ ] **Branch ruleset** (rows 1–6). Settings → Rules → Rulesets → New ruleset → **New branch
    ruleset**. Name `main-branch-protection`; Target: **`main` only** — see 9.4. Enforcement:
    **Active**. Rules:
    - Require a pull request before merging — **0 required approvals** (see 9.2)
    - Require status checks to pass — select **`Tests / All tests passed`** (the `all-green` job)
      and **nothing else**. ⛔ Do **not** select `unit (3.12)`, `unit (3.13)` or `lint`: CLAUDE.md
      §CI and 007 §3 both say a skipped matrix leg leaves the check permanently pending.
    - Require linear history (pairs with 8.2)
    - Block force pushes
    - Restrict deletions

9.2 [ ] **Bypass actors** (row 7): add **Repository admin → Always allow**. Read
    §RL-SEC-hardening's honesty block before ticking it and do not paraphrase it away: with one
    admin and bypass on, this ruleset enforces **nothing** against that admin — it buys an audit
    trail, the config Scorecard reads, and muscle memory. It is still the correct call for a solo
    maintainer, because the alternative is being unable to land a fix when a status check wedges.
    **Required approvals stay 0** — GitHub permits self-approval, so 1 buys a click, not a review.

9.3 [ ] (row 39) "Require conversation resolution" — **skip**. Zero value with zero reviewers; it
    creates a merge blocker you must clear yourself.

9.4 [ ] ⛔ **Confirm the ruleset does NOT catch `gh-pages`** (row 15, §RL-SEC-ghpages). Re-open the
    ruleset created in 9.1 and confirm the target is the literal branch `main` — not
    `All branches`, not `~ALL`, not a `*` pattern. A wildcard that catches `gh-pages` combined
    with "Block force pushes" **breaks `mike deploy --push`** (007 §2), and it fails days later in
    a workflow nobody is watching, not in the run you are looking at. Create **no** ruleset for
    `gh-pages`. Then push a trivial docs change to `main` and confirm `docs.yml`'s `dev` job still
    deploys — that is the actual proof, not the settings page.

9.5 [ ] (row 8) **Now** add "Require signed commits" to the 9.1 ruleset — last, and only after
    Step 4a confirmed a **Verified** badge on github.com. If commits render "Unverified", the key
    is registered as an Authentication key only; fix that first or this rule locks you out of your
    own default branch. (Signing itself is already configured — §RL-SEC-signing.)

9.6 [ ] **Tag ruleset** (rows 10–14). Settings → Rules → Rulesets → New ruleset → **New tag
    ruleset**. Name `release-tags`; Target `v*` (matches both `v3.0.0rc1` and `v3.0.0`);
    Enforcement Active. Rules: Restrict creations, Restrict updates, Restrict deletions. **Bypass
    actors: Repository admin → Always allow** — set it in the *same sitting* as the three rules,
    never the rules alone, or `v3.0.1` cannot be cut. From here on, the plan's "delete and re-cut
    the tag" recovery path runs through the bypass or a temporarily disabled ruleset.

9.7 [ ] (row 45, **a file edit, not a click**) Once `scorecard.yml` has produced a result, add the
    badge to `README.md`'s badge block and commit it normally:
    ```markdown
    [![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/edoardoscarpaci/<slug>/badge)](https://securityscorecards.dev/viewer?uri=github.com/edoardoscarpaci/<slug>)
    ```
    `<slug>` is `varco`. Expect **8–9/10**, not 10 — the `Contributors` check
    penalises single-maintainer projects by design, and `Signed-Releases` may lag (brief 005 §6).
    Do not chase the digit.

**Verify:** Settings → Rules → Rulesets lists exactly **two** Active rulesets and **no** ruleset
targeting `gh-pages`. Open a throwaway PR against `main`: the merge button stays blocked until
`Tests / All tests passed` reports. `git push --force origin main` is rejected (unless deliberately
bypassed). A docs change pushed to `main` still deploys via `docs.yml` (9.4 — the `gh-pages`
proof). `git push origin refs/tags/scratch-tag` (non-`v*`) succeeds; a `v*` tag push lands only via
the admin bypass. Scorecard's `Branch-Protection` check moves off zero on the next weekly run —
⚠️ or reports "unknown", which is a `GITHUB_TOKEN` scope artefact, not a missing setting; do not
mint a PAT to chase it.

---

## Edge cases

- **tomlkit reformats the aligned `version       = "…"` style** → Step 5's round-trip test fails
  before anything is written. Fallback if it does: fall back to a surgical line rewrite for
  `[project].version` only, keeping tomlkit for the dependency arrays (where alignment does not
  exist); do **not** accept a whole-file reformat of ten `pyproject.toml`s.
- **`bump.py --check` run on a workspace mid-bump** (some files written, some not) → exits 1 and
  prints the table; that is the designed behaviour, not a crash.
- **A sibling requirement already carries a specifier** (none does today; a future one might) →
  the script replaces the whole specifier with the canonical `~=<major>.0` and reports it in the
  dry-run diff, rather than appending a second constraint.
- **A new eleventh workspace member is added** → `scripts/packages.sh` picks it up; `bump.py`,
  `make build`, and `release.yml`'s matrix all follow with no edit. It will, however, need its own
  PyPI publisher config and GitHub environment (runbook), which are still manual.
- **A tag is pushed while a `main` push is still deploying docs** → `concurrency: docs-deploy` with
  `cancel-in-progress: false` serializes them; the release deploy runs after the dev deploy
  finishes, never concurrently on `gh-pages`.
- **A pre-release tag (`v3.0.0rc1`)** → publishes to PyPI as a pre-release, does **not** move the
  `latest` docs alias, and is invisible to a default `pip install`.
- **`gh-pages` does not exist on the first deploy** → `git fetch origin gh-pages:gh-pages || true`
  followed by mike's own branch creation; the runbook records that the first run creates it and
  that the Pages source can only be set *after* that first run.
- **One publish matrix leg fails, nine succeed** → the release is partially on PyPI and cannot be
  rolled back. Re-run the failed leg only (`workflow_dispatch` + the same tag); never re-tag. The
  runbook states this explicitly. ⚠️ After Step 9.6 the "delete and re-cut" alternative is
  additionally blocked by the tag ruleset.
- **`docs.yml` starts failing with a push rejection after Phase 9** → a branch ruleset is catching
  `gh-pages` (§RL-SEC-ghpages). Fix by narrowing the ruleset target to `main`; do **not** "fix" it
  by adding `github-actions[bot]` as a bypass actor unless `gh-pages` protection was a deliberate
  choice (007 §2 Option C).
- **A push is refused by secret-scanning push protection** → this is expected for testcontainer
  credentials and JWT test PEM fixtures (007 §6). Choose the bypass reason **"It's used in tests"**;
  never delete or obfuscate the fixture, and never disable push protection to get one push through.
- **`git push origin v3.0.1` is rejected: "tag creation is restricted"** → the 9.6 tag ruleset is
  Active without the admin bypass actor (row 14). Add the bypass, or set the ruleset to Disabled
  for the duration of the release and re-enable it after.
- **Push to `main` rejected with "commits must have verified signatures"** → 9.5 was enabled while
  the key is registered as an Authentication key only. Re-register it as a **Signing Key** (Step
  4a); nothing needs re-committing, GitHub re-evaluates existing signatures.
- **`Tests / All tests passed` is missing from the required-status-checks picker** → the picker
  only lists check-run names GitHub has already observed, and the entry is the *job* name, not the
  workflow name. Push once so `test.yml` reports, then reopen the ruleset editor.
- **CodeQL default setup reports "no languages detected"** → auto-detection found nothing; set the
  language to Python explicitly in the default-setup dialog (007 §5).
- **A Dependabot PR fails CI immediately** → it bumped `pyproject.toml` without regenerating
  `uv.lock`, so `uv sync --locked` fails. Check out the branch, run `uv lock`, push.

---

## Verification

```bash
# Phase 0 — signing is a VERIFY, not a setup (§RL-SEC-signing)
git config --get gpg.format                      # ssh
git config --get commit.gpgsign                  # true
git config --get tag.gpgsign                     # true
git log --format='%G? %h' -15                    # every line starts with G
git remote -v                                    # expect .../varco.git after Step 4b

# Phase 1
uv run pytest varco_core/tests/test_bump_script.py -v
uv run python scripts/bump.py --check            # expected: exit 1 until Phase 3, then 0

# Phase 2 — on a real artifact, not on trust
uv build --package varco_core --out-dir /tmp/varco-meta-check
python -m zipfile -e /tmp/varco-meta-check/*.whl /tmp/varco-meta-check/x && \
  grep -E '^(Metadata-Version|License-Expression|License-File|Classifier: License)' \
  /tmp/varco-meta-check/x/*.dist-info/METADATA

# Phase 3 — the full gate set
make lint                                        # ruff check + ruff format --check
make type-check                                  # mypy, ten source dirs
make test                                        # all eleven suites, accumulated
uv sync --locked --all-packages --all-extras     # uv.lock is not stale
uv run python scripts/api_surface.py --check     # the freeze changed no public symbol
uv run python scripts/bump.py --check            # ten identical versions, canonical pins

# Phase 6
make docs-strict
uv run mike --version

# Phase 7 (after tagging) — resolution from a clean environment
uv venv /tmp/varco-smoke && VIRTUAL_ENV=/tmp/varco-smoke uv pip install "varco-fastapi==3.0.0"
git tag -v v3.0.0                                # signed tag (tag.gpgSign is already on)
```

CI must be green on `Tests / All tests passed` before Step 40's rehearsal tag and again before
Step 41's release tag.

**Phases 8 and 9 are UI-verified, not command-verified** — `gh` is not installed here and, per
007 §1, could not create the environments even if it were. Walk **Appendix A** top to bottom and
confirm each row's state on its settings page. The three behavioural smoke tests worth actually
running, because a settings page can look right and behave wrong:

1. Push an AWS-key-shaped string on a scratch branch — **push protection must refuse it**, and the
   dialog must offer "It's used in tests" (row 34).
2. Open a throwaway PR against `main` — **merge stays blocked until `Tests / All tests passed`
   reports** (row 3), and `git push --force origin main` is rejected (row 5).
3. Push a docs-only change to `main` after Phase 9 — **`docs.yml` must still deploy to
   `gh-pages`** (row 15, §RL-SEC-ghpages). This is the only proof that the branch ruleset's target
   is narrow enough.

---

## Risks

- **⚠️ Brief 004 renders `license-files` two different ways** (`{ globs = [...] }` in §5's first
  block, `["LICENSE"]` in its action block) and its Evidence Gap 4 leaves the `License ::`
  classifier question formally unresolved. Mitigated by Steps 9 and 11, which decide both against
  a real built artifact rather than against prose. **Invariant that must hold**: the built wheel's
  `METADATA` shows `License-Expression: Apache-2.0` and no `Classifier: License ::`.
- **⚠️ Per-environment OIDC scoping for ten projects in one repo is an *inferred* pattern** — brief
  005 Evidence Gap 1: *"no official PyPI documentation examples show ten environments in one
  workflow … recommend testing with 1–2 projects first."* 007 §1 reaches the same conclusion from
  the PyPI internals docs but adds no new primary evidence. Mitigated by Step 40's rc1 rehearsal,
  which is exactly that test at full width. **If it fails**, the documented fallbacks (brief 005
  §1) are ten separate workflow files or convert-then-add-normal-publishers — both cost workflow
  churn, neither costs a version number beyond rc1.
- **⚠️ Dependabot's uv-workspace handling has a known open bug** (dependabot-core#14004, brief 005
  §5 and Evidence Gap 2, unresolved as of Jan 2026): it may propose edits to workspace member
  declarations rather than dependencies. Mitigated by shipping the `uv` block with a comment saying
  it may be deleted if it misbehaves; the `github-actions` block (the security-relevant one, since
  it is what keeps SHA pins fresh) is unaffected.
- **⚠️ ASSUMPTION — the ten PyPI names are available to us.** Not verifiable from the tree; the
  `CHANGELOG.md` `[0.1.0] — 2026-04-07` entry suggests some may already be published. Step 4
  resolves it before any publisher config is created. If a name is owned by a third party, that
  package cannot ship under it and the release becomes nine-of-ten plus a rename decision — a
  finding, not a fix, for this plan.
- **⚠️ ASSUMPTION — `uv build --package varco_kafka` (underscore) is accepted.** `Makefile:228`
  passes the directory name today and `make build` is presumed working, but this plan did not
  execute it. Step 4 verifies; if uv wants `varco-kafka`, the normalization goes in
  `release.yml`'s `packages` job output and the Makefile is left alone.
- **✅ RESOLVED (was a 🚨 blocker) — the repository slug is `varco`.** `git ls-remote` against
  both `…/varco.git` and `…/fastrest.git` returned the **same HEAD** (`d51cf882…`), and the user
  confirmed `varco` is the correct name: one repo, renamed, old slug redirecting. So the ten
  packages' ~30 `[project.urls]` were right all along (no metadata edit), and the residual risk
  is narrow and one-directional: **a publisher config, `site_url`, or badge written against the
  redirecting `fastrest` slug still fails**, because OIDC matches the repository's current name
  rather than following a redirect (007 §1). Steps 27/30/34/39 and Appendix A all assert `varco`.
  Step 4b repoints the stale local `origin` so no later step reads the old name from
  `git remote -v`. **⚠️ Residual, unverified**: whether GitHub still holds `fastrest` as a retired
  slug that could later be reclaimed by someone else — not checkable without the API (`gh` is not
  installed). Low impact once the remote is repointed and no artifact references it.
- **⚠️ ASSUMPTION — the SSH key is registered on GitHub as a *Signing Key*.** Local signing is
  verified (`gpg.format=ssh`, `commit.gpgSign=true`, `tag.gpgSign=true`, last 15 commits `%G?` =
  `G`), so §RL-SEC-signing is a check, not a setup. But a key registered only as an
  *Authentication* key signs locally and still renders **Unverified** on github.com, and 007's
  checklist row 9 covers only the `git config` lines — the second registration requirement comes
  from GitHub's own signing docs, not from the brief. **Invariant:** confirm one commit shows
  **Verified** (Step 4a) before enabling Step 9.5, or that rule locks the maintainer out of `main`.
- **⚠️ A branch ruleset that catches `gh-pages` silently breaks the docs pipeline.** "Block force
  pushes" blocks `mike deploy --push`, which force-pushes by design (007 §2). The failure surfaces
  days later in a workflow nobody watches, not in the run being observed. **Invariant:** the branch
  ruleset targets the literal string `main`; no ruleset targets `gh-pages`; Step 9.4's docs-deploy
  smoke test is the proof. ⚠️ 007 Evidence Gap 5 notes the `mike`-vs-rulesets interaction is not
  documented officially — the chosen mitigation (protect nothing on `gh-pages`) is deliberately the
  one that needs no undocumented behaviour to hold.
- **Admin bypass means the rulesets enforce nothing against the maintainer.** Stated in
  §RL-SEC-hardening and repeated here because it is the single most misreadable line in this plan:
  a solo repo with `Repository admin → Always allow` is *documented*, not *enforced*. It is the
  right trade — the alternative is being unable to land a fix or cut `v3.0.1` — but it must be said
  rather than implied. **Invariant:** never cite the branch or tag ruleset as a security control in
  `SECURITY.md`, in release notes, or in a badge caption.
- **⚠️ Evidence gaps carried from 007, each with its mitigation:** (1) CodeQL default-setup
  coverage of a ten-package `uv` workspace is unconfirmed → Step 8.6 verifies empirically before
  anything is changed. (2) `github-actions[bot]` bypass behaviour in *multi-rule* rulesets is not
  fully documented → this plan never relies on it (§RL-SEC-ghpages Option A). (3) Whether a
  "Require signed commits" rule accepts GitHub-bot signatures is not explicitly documented → never
  tested here, because no ruleset targets `gh-pages`. (4) Secret-scanning false-positive rate on
  PEM fixtures is unbenchmarked → Step 8.7 pre-warns and prescribes the bypass reason.
- **The deprecation floor is 12 months, where brief 001 §1 reports the expected bar as 2 years.**
  Reasoned in §RL-9-policy and confirmed at the Step 3 checkpoint. If reversed, only
  `CONTRIBUTING.md` changes — no code, no `removed_in` string.
- **`gh-pages` branch growth** — brief 006 Evidence Gap 3: ~10–20 MB per release and no published
  guidance on when `fetch-depth: 0` becomes slow. A non-issue for 3.0.0; the invariant to watch is
  that docs CI wall-clock stays under a few minutes. Revisit at 4.0.
- **Material for MkDocs is at end-of-feature and the team is moving to Zensical** (brief 006
  Evidence Gap 2; Material 9.7.6 gets security fixes through at least Nov 2026). Mike's versioning
  contract is unaffected for 3.0.0; the docs-hosting choice is re-openable at 4.0, and the `3.0`
  URLs are permanent regardless.
- **Scorecard will score low on Branch-Protection and Signed-Releases** until Phase 9's ruleset
  lands and until PEP 740 attestations are recognised by that check (brief 005 §6 notes
  attestations do not directly satisfy Signed-Releases). Invariant: Scorecard is never in any
  `needs:` and never a required check. ⚠️ `Branch-Protection` may also report "unknown" after
  Phase 9 — a `GITHUB_TOKEN` scope artefact, not a missing setting. Do not mint a PAT for a digit.
- **New workflows must not become required checks.** `all-green` stays the only one (CLAUDE.md
  §CI). A `release`/`docs`/`scorecard`/`chaos` job promoted to required would leave PRs permanently
  pending, since none of them runs on `pull_request`. Step 9.1 is where this is most likely to be
  got wrong — the picker shows every observed check-run name, including ones that must not be
  selected.
- **⚠️ Every "free" claim assumes a PUBLIC repository.** 007 §7: environments, secret scanning
  **and** push protection, CodeQL default setup and private vulnerability reporting are free only
  for public repos; a private repo needs Pro/Team or GitHub Advanced Security. **Invariant:** if
  this repo is ever made private, roughly half of Phase 8 silently stops working — re-audit before
  flipping visibility.
- **⚠️ `gh` CLI is not installed on this machine** (`gh: command not found`) and 007 §1 says
  environments cannot be created with it regardless. Any future step that offers a `gh` one-liner
  must state the install prerequisite and give the UI path as the primary route. Do not lose an
  afternoon to the REST API for a one-time checklist.
- **Partial publish is unrecoverable.** Ten independent uploads under one tag; a mid-matrix failure
  leaves a mixed set on PyPI. Invariant: never re-tag; re-run the failed leg. rc1 exists to make
  this improbable.

---

## Acceptance criteria (traceable to BACKLOG IDs)

| Row | Done when | Verified by |
|---|---|---|
| **RL-9** | All ten `pyproject.toml` carry `version = "3.0.0"` and `Development Status :: 5 - Production/Stable`; sibling pins are `~=3.0` in `[project].dependencies` and in the two shipped extras; `scripts/bump.py` exists with `--set`/`--bump`/`--dry-run`/`--check`, derives its list from `scripts/packages.sh`, and has unit tests; a written SemVer + deprecation policy is in `CONTRIBUTING.md` and matches `varco_core.deprecation`'s actual behaviour | Steps 5–8, 13–19, 20; `uv run python scripts/bump.py --check` exits 0; `varco_core/tests/test_bump_script.py` green with no xfail |
| **RL-10** | `release.yml` publishes ten distributions on a `v*` tag via OIDC trusted publishing with default-on PEP 740 attestations, top-level `permissions: {}`, per-job `id-token: write`, per-package `environment:` and `packages-dir`, a `scripts/packages.sh`-derived matrix, and every action SHA-pinned + `# vN`; `dependabot.yml` and `scorecard.yml` exist; `publish.yml` is deleted; the manual PyPI/GitHub steps are written down | Steps 26–32; Step 40's rc1 rehearsal is the functional proof |
| **RL-11** | `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `.github/CODEOWNERS`, three issue templates and a PR template exist and are linked from `README.md`; the gitignore-hygiene claim is **verified and recorded** (not assumed) | Steps 2, 20–25 |
| **RL-12** | `mike` is in the docs group; `mkdocs.yml` has `site_url` (slug `varco`) + `extra.version.provider: mike`; `docs.yml` deploys `dev` from `main` and `3.0` + `latest` from a non-pre-release tag, with `fetch-depth: 0`, git identity, serialized concurrency and a full `uv sync --all-packages --all-extras`; the Pages source step is in the runbook | Steps 33–37; the live site's switcher after Step 41 |
| **RL-13** | All ten declare `license = "Apache-2.0"` + `license-files`, `hatchling>=1.27`, and no `License ::` classifier; a built wheel's `METADATA` proves `Metadata-Version: 2.4` + `License-Expression`; the PEP 735 audit is recorded with its "already compliant" verdict | Steps 9–12; the Phase 2 verification command block |

### GitHub hardening — traceable to Appendix A, not to a BACKLOG row

**These are repository *settings*, not code, and they close no existing BACKLOG row.** No ID is
invented for them here. Their acceptance is the checklist itself:

| Item | Done when | Verified by |
|---|---|---|
| **Repo identity** (§RL-SEC-repo-name) | Resolved to `varco`; the runbook, `mkdocs.yml`, the badge and the ten publisher configs all use that one value, and no artifact references the redirecting `fastrest` slug | `slug = varco` recorded in the Step 1 measurement file; Step 4b repointed `origin`; `grep -rn fastrest` over committed files returns nothing; Step 39's ten publisher configs accepted by PyPI at Step 40 |
| **Signing** (§RL-SEC-signing) | A `main` commit renders **Verified** on github.com and `git tag -v v3.0.0` reports a good signature | Step 4a; Step 41 |
| **Click-through hardening** (Phase 8) | Appendix A rows 16–18, 30–36, 37–38, 40–43 are in their stated state; CodeQL coverage across the ten packages is *observed*, not assumed | Steps 8.1–8.7 + Phase 8's Verify block; the push-protection smoke test |
| **Rulesets** (Phase 9) | Exactly two Active rulesets exist (`main-branch-protection`, `release-tags`), the required check is **only** `Tests / All tests passed`, both carry an admin bypass actor, and **no** ruleset targets `gh-pages` | Steps 9.1–9.7 + Phase 9's Verify block; the docs-deploy smoke test is the `gh-pages` proof |
| **Supply-chain files** | `dependabot.yml` + `scorecard.yml` exist (Appendix A rows 32/44, **already delivered by Phase 5**) and the Scorecard badge is in `README.md` | Steps 28, 29, 9.7 |

**Recommendation (not performed by this plan): file one BACKLOG row for this work** — suggested ID
**RL-SEC** under "Phase 5 — release", pointing at §RL-SEC-hardening and Appendix A, so the settings
are auditable and re-checkable after the release the way every other release obligation is. Step 38
is where it would land. This plan deliberately does not edit `BACKLOG.md` to add it.

---

## Appendix A — GitHub repository hardening checklist (all 45 settings)

Adapted from `design/varco-1-0-release/research/007-github-repo-hardening-settings.md`
§Checklist, **regrouped by GitHub settings page** so the maintainer works page-by-page without
bouncing around the UI, and annotated with the step that owns each row. The `#` column keeps 007's
own numbering so any row can be traced back to the brief.

Priority tiers (007's): **MUST** = required for production OSS credibility (OSPS baseline /
Scorecard pass / trusted publishing) · **SHOULD** = strongly recommended, low friction ·
**NICE** = optional. Where this plan *disagrees* with the brief, the disagreement is in the Value
column. Every "Yes" in the Free? column assumes a **public** repository (007 §7 — see Risks).

| # | Setting | Where (UI path / command) | Value to set | Prio | Free? | Phase |
|---|---|---|---|---|---|---|
| **— Local machine —** |
| 9 | SSH commit + tag signing | `git config --get gpg.format` / `commit.gpgsign` / `tag.gpgsign` | **Already configured — verify only.** `ssh` / `true` / `true`; last 15 commits `%G?` = `G` | SHOULD | Yes | **4a** |
| 9b | Register the key as a **Signing Key** on GitHub | https://github.com/settings/keys → New SSH key → Key type: **Signing Key** | Same public key as the auth key; without it commits sign locally but render "Unverified" | SHOULD | Yes | **4a** ⚠️ not in 007 |
| **— Settings → General → Features —** |
| 40 | Issues | Settings → General → Features | Enabled | MUST | Yes | 8.1 |
| 42 | Wiki | Settings → General → Features | **Disabled** — `docs/` + mike is the documentation surface | NICE | Yes | 8.1 |
| 41 | Discussions | Settings → General → Features | **Off** for now; enable on demand | NICE | Yes | 8.1 |
| **— Settings → General → Pull Requests —** |
| 38 | Merge strategies | Settings → General → Pull Requests | **Disable "Allow merge commits"**; keep squash | SHOULD | Yes | 8.2 |
| 37 | Auto-delete head branches | Settings → General → Pull Requests | Enabled | NICE | Yes | 8.2 |
| **— Repo home → About —** |
| 43 | Description, website, topics | Repo page → About → ⚙ | Description; Website = the **docs site** `https://edoardoscarpaci.github.io/<slug>/` (not a PyPI page — there are ten); topics: python, event-driven, kafka, redis, dependency-injection, framework, async | NICE | Yes | 8.3 |
| **— Settings → Actions → General —** |
| 16 | Default `GITHUB_TOKEN` permissions | Settings → Actions → General → Workflow permissions | "Read repository contents and packages permissions" (**verify only** — default since Feb 2023) | MUST | Yes | 8.4 |
| 17 | Actions permissions policy | Settings → Actions → General → Actions permissions | "Allow all actions and reusable workflows"; the verified-creator filter is **not on the free tier**. Substitute: every action SHA-pinned + `# vN` (§RL-10-supply) | SHOULD | Yes | 8.4 |
| 18 | Fork PR workflow approval | Settings → Actions → General → Fork pull request workflows | "Require approval for first-time contributors who are new to GitHub" | SHOULD | Yes | 8.4 |
| **— Settings → Environments —** |
| 19–28 | Ten environments, one per distribution | Settings → Environments → New environment (×10) | `pypi-varco-core`, `-kafka`, `-nats`, `-redis`, `-beanie`, `-sa`, `-memcached`, `-ws`, `-fastapi`, `-casbin`. **No** deployment-branch restriction (it would block the tag-triggered run). Optional URL = that project's PyPI page. **Leave "Required reviewers" OFF** — a solo maintainer approving their own deploy is friction with no benefit | MUST | Yes (public) | **done in Step 39** |
| **— PyPI (not GitHub) —** |
| 29 | Ten trusted-publisher configs | https://pypi.org/manage/project/&lt;name&gt;/settings/publishing/ (×10) | Owner `edoardoscarpaci`; Repo **`varco`** (not the redirecting `fastrest`); Workflow `release.yml`; Environment `pypi-varco-<pkg>`. Pending publishers for names not yet on PyPI (Step 4 splits them) | MUST | Yes | **done in Step 39** |
| **— Settings → Code security —** |
| 30 | Dependabot alerts | Settings → Code security → Dependabot alerts | Enabled | MUST | Yes | 8.5 |
| 31 | Dependabot security updates | Settings → Code security → Dependabot security updates | Enabled | MUST | Yes | 8.5 |
| 33 | Secret scanning | Settings → Code security → Secret scanning | Enabled | MUST | Yes (public) | 8.5 |
| 34 | Secret scanning **push protection** | Settings → Code security → Secret scanning → Push protection | Enabled. Expect test PEM/testcontainer creds to be flagged; bypass with **"It's used in tests"** — never edit the fixture | MUST | Yes (public) | 8.5 / 8.7 |
| 35 | CodeQL code scanning | Settings → Code security → Code scanning → Set up → **Default setup** | Enabled, language auto-detect. ⚠️ Coverage of a ten-package `uv` workspace is unconfirmed (007 Evidence Gap 1) — verify before trusting | SHOULD | Yes (public) | 8.5 / **8.6** |
| 36 | Private vulnerability reporting | Settings → Code security → Private vulnerability reporting | Enabled — this is what makes `SECURITY.md`'s promised channel real | SHOULD | Yes (public) | 8.5 |
| — | Dependency graph | (none) | Automatic on public repos — no action | — | Yes | — |
| **— Files you commit —** |
| 32 | `.github/dependabot.yml` | File in repo | `uv` + `github-actions`, `directory: "/"`, weekly, grouped, **no `reviewers:` key**. ⚠️ 007's template says `pip`; brief 005 §5's `uv` ecosystem supersedes it | SHOULD | Yes | **done in Phase 5 (Step 28)** |
| 44 | `.github/workflows/scorecard.yml` | File in repo | Weekly cron + `push: [main]` + `branch_protection_rule`; `publish_results: true`; `id-token: write` + `security-events: write`; SHA-pinned | SHOULD | Yes | **done in Phase 5 (Step 29)** |
| **— Settings → Rules → Rulesets (branch) — AFTER Step 41 —** |
| 1 | Create branch ruleset | Settings → Rules → Rulesets → New **branch** ruleset | Name `main-branch-protection`; Target **`main` only** (literal, never `~ALL`/`*` — row 15); Enforcement Active | MUST | Yes | 9.1 |
| 2 | Require PR before merging | [ruleset] → Add rule | Enabled, **0 required approvals** (self-approval is not review) | MUST | Yes | 9.1 |
| 3 | Require status checks | [ruleset] → Add rule | **`Tests / All tests passed` (the `all-green` job) ONLY.** ⛔ Never `unit (3.12)`/`unit (3.13)`/`lint` — a skipped leg leaves the check permanently pending (CLAUDE.md §CI, 007 §3) | MUST | Yes | 9.1 |
| 4 | Require linear history | [ruleset] → Add rule | Enabled (pairs with row 38) | MUST | Yes | 9.1 |
| 5 | Block force pushes | [ruleset] → Add rule | Enabled (ruleset default) | MUST | Yes | 9.1 |
| 6 | Restrict deletions | [ruleset] → Add rule | Enabled (ruleset default) | MUST | Yes | 9.1 |
| 7 | Bypass actors | [ruleset] → Bypass actors | Repository admin → **Always allow** — read §RL-SEC-hardening's honesty block; this makes the ruleset documentation, not enforcement | MUST | Yes | 9.2 |
| 39 | Require conversation resolution | [ruleset] → Add rule | **Skip** — no value with zero reviewers | NICE | Yes | 9.3 (skip) |
| 15 | **No ruleset on `gh-pages`** | Settings → Rules → Rulesets — *do not create one* | ⛔ A non-action. "Block force pushes" breaks `mike deploy --push` (007 §2). Options B (no force-push rule) / C (`github-actions[bot]` bypass) exist and are **not taken** | MUST | Yes | 9.4 |
| 8 | Require signed commits | [ruleset] → Add rule | Enabled **last**, only after row 9b confirms a Verified badge | SHOULD | Yes | 9.5 |
| **— Settings → Rules → Rulesets (tag) — AFTER the `v3.0.0` tag is pushed —** |
| 10 | Create tag ruleset | Settings → Rules → Rulesets → New **tag** ruleset | Name `release-tags`; Target `v*` (matches rc and final); Enforcement Active | MUST | Yes | 9.6 |
| 11 | Restrict tag creations | [tag ruleset] → Add rule | Enabled | SHOULD | Yes | 9.6 |
| 12 | Restrict tag updates | [tag ruleset] → Add rule | Enabled (immutable releases) | SHOULD | Yes | 9.6 |
| 13 | Restrict tag deletions | [tag ruleset] → Add rule | Enabled | SHOULD | Yes | 9.6 |
| 14 | Tag ruleset bypass actors | [tag ruleset] → Bypass actors | Repository admin → **Always allow** — set in the *same sitting* as rows 11–13, or `v3.0.1` cannot be cut | SHOULD | Yes | 9.6 |
| **— README —** |
| 45 | OpenSSF Scorecard badge | `README.md` badge block (a **file edit**) | Add only after `scorecard.yml` has produced a result; URL uses slug `varco`; expect 8–9/10 | NICE | Yes | 9.7 |

**Not free / not used** (007 §7): environment **wait timers**, environment **required reviewers**,
and the Actions **verified-creator allowlist filter**. Deliberately absent from the action rows —
do not go hunting for them in the UI and conclude the checklist is wrong.

**Deviations from 007's checklist, each deliberate:** row 3 requires the aggregate `all-green` job
rather than any matrix leg (CLAUDE.md §CI, 007 §3 concurs); row 32 uses the `uv` ecosystem rather
than 007's `pip` (brief 005 §5 is the later finding) and drops its `reviewers:` key; row 43's
Website points at the docs site rather than a PyPI page, because ten projects cannot share one
field; rows 10–14 are ordered *after* the release tag even though 007 §4 says ordering is
immaterial (§RL-SEC-hardening ordering fact 2); row 9b is not in 007 at all.
