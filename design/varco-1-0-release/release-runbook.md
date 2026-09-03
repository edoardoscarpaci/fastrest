# Release runbook — v3.0.0

✅ **Applied (2026-09-02, Plan 024 / C1).** Every operator step below — the ten Environments, the
ten trusted-publisher configs, the Pages source, and the Phase 9 `main`/tag rulesets — is reported
complete by the operator. This document is kept as the **reference for re-running these steps**
(e.g. onto an eleventh package or a rotated key), not as a pending checklist.

Plan 023 / Phase 5 Step 30, §RL-10-publish. The operator steps this repository's files cannot
perform (no `gh` CLI installed, no PyPI/GitHub API credentials in this execution context) — walk
this document top to bottom before pushing the release tag.

**Repository slug: `varco`** (§RL-SEC-repo-name — resolved by measurement and by the user, not a
decision). Every URL, every publisher `repo:` field, and the docs `site_url` below use this value.
The pre-rename `fastrest` slug still resolves as a GitHub redirect but must **never** be used in a
publisher config, because OIDC matches the repository's *current* name, not a redirect.

## 1. Ten GitHub Environments — ✅ Applied

Settings → Environments → New environment, ten times:

`pypi-varco-core`, `pypi-varco-kafka`, `pypi-varco-nats`, `pypi-varco-redis`, `pypi-varco-sa`,
`pypi-varco-beanie`, `pypi-varco-memcached`, `pypi-varco-ws`, `pypi-varco-fastapi`,
`pypi-varco-casbin`.

- **No deployment-branch restriction** — one would block the tag-triggered `release.yml` run.
- **No required reviewers** — a solo maintainer approving their own deploy is friction with no
  benefit.
- Optional: set the environment URL to that project's PyPI page.

Not scriptable with `gh` today (brief 007 §1), and `gh` is not installed on this machine.

## 2. Ten PyPI trusted-publisher configs — ✅ Applied

For each of the ten `https://pypi.org/manage/project/<name>/settings/publishing/` pages (or the
"add a pending publisher" flow at `https://pypi.org/manage/account/publishing/` for a name not yet
registered):

| Field | Value |
|---|---|
| Owner | `edoardoscarpaci` |
| Repository name | `varco` (**not** `fastrest`) |
| Workflow filename | `release.yml` |
| Environment name | `pypi-<name>` (e.g. `pypi-varco-core`) |

**PyPI registration status for the ten names was not independently re-verified over the network by
this plan execution** (see `design/varco-1-0-release/measurements/version-baseline.md`'s §Step 4).
Check each name at `https://pypi.org/project/<name>/` before configuring: a name with no existing
project needs the **pending publisher** flow (PyPI creates the project on the first successful
run); a name that already exists gets a normal publisher added under its own project settings.

## 3. GitHub Pages source — ✅ Applied

Settings → Pages → Build and deployment → Source: **Deploy from a branch** → branch `gh-pages` /
`root`. This can only be set **after** `docs.yml`'s `dev` job has run at least once (it creates the
`gh-pages` branch on its first run via `mike deploy --push dev`).

⛔ **`gh-pages` must never be targeted by a branch ruleset** (§RL-SEC-ghpages). "Block force
pushes" breaks `mike deploy --push`, which force-pushes by design. See Phase 9's ruleset target
invariant — it is the literal string `main`, never a wildcard that could also catch `gh-pages`.

## 4. `main` branch ruleset (Phase 9, after the release tag) — ✅ Applied

Specified in full in `plans/023-release-version-freeze-and-supply-chain.md`'s Phase 9 and
Appendix A. Applied after the `v3.0.0` tag shipped (§RL-SEC-hardening's ordering fact 2 — this
repository's first-ever tags were the rc1/final release tags themselves), per the operator's
report (Plan 024 / C1).

## 5. Rehearsal: `v3.0.0rc1`

```bash
git tag -s v3.0.0rc1 -m "Release candidate 1 for v3.0.0"
git push origin v3.0.0rc1
```

Expect:
- `release.yml` green across all ten `publish` matrix legs.
- Ten pre-releases visible on PyPI, each with a PEP 740 attestation.
- `docs.yml`'s `release` job **skipped** (pre-release tag guard — `rc|a|b|dev` in the tag).
- `docs.yml`'s `dev` job (triggered separately by `main` pushes) is unaffected and stays current.

**Record the outcome here once run**: _(not run in this execution — no PyPI trusted-publisher
configs exist yet; this is an operator action, see §1–2 above)_.

If a leg fails: fix the root cause and re-rehearse as `v3.0.0rc2`. **Never patch during the real
release tag** — that is what the rc exists to de-risk.

## 6. Release: `v3.0.0` — ✅ Released

```bash
git tag -s v3.0.0 -m "Release v3.0.0"
git push origin v3.0.0
```

Verify:
- All ten PyPI project pages show `3.0.0` with an attestation badge.
- A downloaded wheel's `METADATA` shows `License-Expression: Apache-2.0` and
  `Development Status :: 5 - Production/Stable`.
- The docs site serves `3.0`, `latest`, and `dev` with a working version switcher.
- `pip install "varco-fastapi==3.0.0"` in a clean venv resolves `varco-core~=3.0`.
- `git tag -v v3.0.0` shows a good signature.

**Record the outcome here once run**: _(not run in this execution — the tag has not been pushed;
see the Deviations section of the executing agent's final report)_.

## Edge cases (mirrors the plan's own Edge cases section)

- **One publish matrix leg fails, nine succeed.** The release is partially on PyPI and cannot be
  rolled back. Re-run the failed leg only via `workflow_dispatch` on the same tag. **Never re-tag.**
  After the Phase 9 tag ruleset is applied, "delete and re-cut" is additionally blocked by the
  ruleset — use the admin bypass or temporarily disable the ruleset.
- **`gh-pages` does not exist yet.** The first `docs.yml` `dev` run creates it via
  `git fetch origin gh-pages:gh-pages || true` followed by mike's own branch creation. The Pages
  source (§3 above) can only be set after that first run.
- **A push to `main` is refused for "commits must have verified signatures"** (only possible after
  Phase 9 Step 9.5) — the SSH key is registered as an Authentication key only; re-register it as a
  **Signing Key** at https://github.com/settings/keys.

## §RL-SEC — CodeQL coverage observation (Step 8.6, once applied)

_(Not applicable in this execution — Phase 8 is a browser-only settings phase, out of scope for a
plan-execution agent per the task's own scope notes. Record the observation here once an operator
completes Phase 8.)_
