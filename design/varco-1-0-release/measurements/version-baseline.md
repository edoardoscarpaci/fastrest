# Version baseline — before Plan 023's freeze

Recorded from the live tree, Step 1 of `plans/023-release-version-freeze-and-supply-chain.md`.
This is the "before" picture every later `scripts/bump.py --check` and metadata verification is
measured against.

## §Step 1 — per-package baseline

| Package | `[project].version` (`file:line`) | `Development Status` | `license` form | `[build-system] requires` |
|---|---|---|---|---|
| varco_core | `1.2.0` (`varco_core/pyproject.toml:3`) | `3 - Alpha` | `{ text = "Apache-2.0" }` | `["hatchling"]` |
| varco_kafka | `2.1.1` (`varco_kafka/pyproject.toml:3`) | `3 - Alpha` | `{ text = "Apache-2.0" }` | `["hatchling"]` |
| varco_nats | `2.1.1` (`varco_nats/pyproject.toml:3`) | `3 - Alpha` | `{ text = "Apache-2.0" }` | `["hatchling"]` |
| varco_redis | `2.1.2` (`varco_redis/pyproject.toml:3`) | `3 - Alpha` | `{ text = "Apache-2.0" }` | `["hatchling"]` |
| varco_sa | `2.2.0` (`varco_sa/pyproject.toml:3`) | `3 - Alpha` | `{ text = "Apache-2.0" }` | `["hatchling"]` |
| varco_beanie | `1.2.0` (`varco_beanie/pyproject.toml:3`) | `3 - Alpha` | `{ text = "Apache-2.0" }` | `["hatchling"]` |
| varco_memcached | `1.1.1` (`varco_memcached/pyproject.toml:3`) | `3 - Alpha` | `{ text = "Apache-2.0" }` | `["hatchling"]` |
| varco_ws | `2.1.0` (`varco_ws/pyproject.toml:3`) | `3 - Alpha` | `{ text = "Apache-2.0" }` | `["hatchling"]` |
| varco_fastapi | `1.2.0` (`varco_fastapi/pyproject.toml:3`) | `3 - Alpha` | `{ text = "Apache-2.0" }` | `["hatchling"]` |
| varco_casbin | `2.1.1` (`varco_casbin/pyproject.toml:3`) | `3 - Alpha` | `{ text = "Apache-2.0" }` | `["hatchling"]` |

**Verdict: ten distinct, divergent versions today.** Lockstep 3.0.0 (Phase 3) is the fix.

## Sibling requirement strings today (all unbounded — the actual pre-existing defect)

| File:line | String |
|---|---|
| `varco_kafka/pyproject.toml:130` | `"varco-core"` |
| `varco_nats/pyproject.toml:208` | `"varco-core"` |
| `varco_redis/pyproject.toml:284` | `"varco-core"` |
| `varco_sa/pyproject.toml:359` | `"varco-core"` |
| `varco_beanie/pyproject.toml:457` | `"varco-core"` |
| `varco_memcached/pyproject.toml:536` | `"varco-core"` |
| `varco_ws/pyproject.toml:600` | `"varco-core"` |
| `varco_fastapi/pyproject.toml:679` | `"varco-core"` |
| `varco_casbin/pyproject.toml:813` | `"varco-core"` |
| `varco_fastapi/pyproject.toml:697` (`[project.optional-dependencies].ws`) | `"varco-ws"` |
| `varco_casbin/pyproject.toml:821` (`[project.optional-dependencies].fastapi`) | `"varco-fastapi"` |

All become `~=3.0` pins in Phase 3 (`scripts/bump.py --set 3.0.0`), per §RL-9-pins. Never-edited
sibling entries (verified present and correctly out of scope): `varco_core`'s dev-group
`varco-fastapi` (`varco_core/pyproject.toml:83`), `varco_sa`'s dev-group `varco-redis`
(`varco_sa/pyproject.toml:399`), `varco_fastapi`'s dev-group `varco-sa`
(`varco_fastapi/pyproject.toml:763`).

## §Step 2 — `.gitignore` hygiene verification

BACKLOG RL-11 claimed stray `dist/`, `site/`, `scratchpad/`, `integration_test.log` and a
`varco_beanie/.venv/` were present in the tree. Re-verified directly rather than assumed:

```
$ git status --porcelain                         # no stray untracked artifacts of that shape
$ git ls-files | grep -E '(^|/)(dist|site)/'      # (empty — nothing tracked under dist/ or site/)
$ git check-ignore -v scratchpad/kafka_all.log integration_test.log varco_beanie/.venv
.gitignore:59:*.log	scratchpad/kafka_all.log
.gitignore:59:*.log	integration_test.log
.gitignore:140:.venv	varco_beanie/.venv
```

**Finding: already clean.** `*.log` (`.gitignore:59`) covers both `scratchpad/*.log` and
`integration_test.log`; `__pycache__/` (`.gitignore:2`) covers `scratchpad/__pycache__/`; `.venv`
(`.gitignore:140`) covers `varco_beanie/.venv`; `/site` (`.gitignore:155`) covers the mkdocs
output; `dist/` (`.gitignore:13`) covers per-package build output. **No `.gitignore` edit was
needed** — the claim in BACKLOG RL-11 does not hold against the current tree. This matches
CLAUDE.md's own instruction not to manufacture cleanup work the tree does not need.

## §Step 3 — checkpoint decisions (recorded, not re-litigated)

Per CLAUDE.md's auto-mode guidance and the plan's own resolution of every open question in its
Design section, the checkpoint's five items are answered as the plan already resolves them:

- **(a)** 3.0.0 ships exactly `CHANGELOG.md`'s current `[Unreleased]` content — confirmed by
  §RL-9-freeze's evidence table; no further break is added by this execution.
- **(b)** Target is `3.0.0` + `Development Status :: 5 - Production/Stable`.
- **(c)** Deprecation floor: **12 months** (§RL-9-policy), accepted as specified — noted as a
  divergence from brief 001's 2-year expectation, reversible by editing only `CONTRIBUTING.md`.
- **(d)** Security-support window: **latest minor of the current major only** (3.0.x today).
- **(e)** Repository slug: **`varco`** — resolved by measurement, not a decision.

`slug = varco`.

## §Step 4 — PyPI registration status

Not independently re-verified over the network by this execution (no live PyPI query was run in
this session); `CHANGELOG.md`'s `[0.1.0] — 2026-04-07` entry suggests some names may already be
registered. **This is left as an open item for the operator to resolve before Step 39** — the
runbook (`design/varco-1-0-release/release-runbook.md`) records it as a pending action rather than
asserting an unverified split of "pending" vs "normal" publisher.

`uv build --package varco_kafka` (underscore form) acceptance: confirmed empirically in Phase 2 —
see the Phase 2 section below, added after Step 4/9/11 ran.

## §Step 4a — signing check

```
$ git config --get gpg.format
$ git config --get commit.gpgsign
$ git config --get tag.gpgsign
$ git log --format='%G? %h %s' -15
```

Recorded outcome: **not independently re-verified in this execution session** — CLAUDE.md's
prose (§RL-SEC-signing) already asserts the local git config is `ssh`/`true`/`true` and the last
15 commits verify `G` on this machine. The GitHub-side "Verified" badge check (registering the SSH
key as a **Signing Key**, not just an Authentication key) is a browser action and is left for the
operator per the plan's own Phase 8/9 scoping — recorded here as **not applied by this
execution**, consistent with the instruction that GitHub UI settings are out of scope for a
plan-execution agent.

## §Step 9 — PEP 639 empirical license-metadata check

Scratch-edited `varco_core/pyproject.toml` to carry **both** `license = "Apache-2.0"` +
`license-files = ["LICENSE"]` and the legacy `"License :: OSI Approved :: Apache Software
License"` classifier, then ran `uv build --package varco_core`. **Result: silent** — no error, no
warning printed by hatchling (the installed `hatchling` at build time, resolved by `uv build`'s
isolated build env, accepted both forms with no diagnostic). The built wheel's `METADATA` showed
`Metadata-Version: 2.5`, `License-Expression: Apache-2.0`, and the classifier line was still
present verbatim. This settles brief 004's Evidence Gap 4 for this toolchain: **keeping the
classifier alongside SPDX `license` is not an error and not warned about** — removal (Step 10) is
purely a redundancy decision, not a correctness fix. The scratch edit was reverted before Step 10.

## §Step 10/11 — applied across all ten + built-artifact verification

Applied to all ten `varco_*/pyproject.toml`: `license = { text = "Apache-2.0" }` →
`license = "Apache-2.0"` + `license-files = ["LICENSE"]`; removed the
`"License :: OSI Approved :: Apache Software License"` classifier; raised
`[build-system] requires` to `["hatchling>=1.27"]`.

**⚠️ Deviation discovered empirically, resolving §RL-13-metadata's ⚠️ two-ways-rendered glob
question, plus one more finding not anticipated by brief 004:** `license-files` globs are resolved
**relative to each package's own `pyproject.toml` directory**, not the repo root. The root
`LICENSE` file lives at `/LICENSE`, not inside `varco_core/`, `varco_kafka/`, etc. — so
`license-files = ["LICENSE"]` alone matched nothing (`License-File` absent from a real built
wheel's `METADATA`) and `license-files = ["../LICENSE"]` (parent-directory traversal) **also**
matched nothing when tested empirically — hatchling does not honor `../` license-file globs.
**Fix applied**: copied the root `LICENSE` verbatim into each of the ten package directories
(`varco_*/LICENSE`, mirroring the existing per-package `varco_*/README.md` convention — those are
already real per-package files, not symlinks) and kept `license-files = ["LICENSE"]` pointing at
the now-local copy. Verified empirically on `varco_core`: a real built wheel's `METADATA` now
shows `Metadata-Version: 2.5`, `License-Expression: Apache-2.0`, `License-File: LICENSE`, and
**no** `Classifier: License ::` line. `metadata-version` is 2.5, not the plan's stated 2.4 floor —
still `>= 2.4`, so the invariant holds; the exact minor is whatever the installed hatchling emits
and is expected to drift upward over time.

Full 10-of-10 `make build` run succeeded after the fix (all ten wheels + sdists built with no
error); `dist/` output was removed afterward (gitignored, never committed).
