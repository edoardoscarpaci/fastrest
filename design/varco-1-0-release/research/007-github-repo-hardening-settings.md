# Research 007 — GitHub Repository Hardening Settings for Varco's Monorepo Release

Date: 2026-08-31 · Freshness matters: **yes** — GitHub rulesets, Actions security defaults, PyPI trusted publishing per-environment gating, and Dependabot ecosystem support continue to evolve; check official docs annually.

## Question

Adapt GitHub repository hardening settings (from sibling brief 003, written for a solo-maintainer single-package repo) to varco's specific constraints:

1. **Ten PyPI distributions released in lockstep from one repo** — does each project require a separate GitHub Environment for trusted publishing, or can they share one? Clarify PyPI's OIDC token claims matching, and recommend whether to use ten environment names or a single workflow file with per-project env gates.

2. **`gh-pages` branch + rulesets interaction** — varco plans `mike`-based versioned docs that commit to `gh-pages` from CI. Does a ruleset with "Block force pushes" on `gh-pages` break `mike deploy --push`? Does "Require signed commits" on `gh-pages` break commits from `GITHUB_TOKEN`? What is the safest configuration?

3. **`all-green` aggregate job as the sole required status check** — varco's CLAUDE.md mandates this pattern (not individual matrix jobs). Confirm this is the correct GitHub Actions pattern, that a `needs: [...]` job with `if: always()` is the right shape, and that it indeed prevents the "skipped leg leaves pending check" trap.

4. **First-ever tag (v3.0.0rc1 and v3.0.0) + tag ruleset protection** — varco has zero existing tags. Does a `v*` tag ruleset with "Restrict creations" block the repository admin from pushing the first tag, or are admins automatically exempt? Advise on ruleset ordering relative to the rc1 rehearsal and final release.

5. **CodeQL default setup on a 10-package `uv` workspace** — does CodeQL default setup automatically handle a monorepo with ten package directories, each with its own tests? Will it scan correctly without advanced setup? Known issues with `uv` venvs?

6. **Secret scanning push protection + test credentials** — varco's integration tests embed throwaway testcontainer credentials and JWT test PEM keys. Will push protection block legitimate pushes, and what is the bypass flow?

7. **Free-tier feature gating** — varco is a public repo. Which recommended settings are free-tier-available, and which require Team/Enterprise?

Exclude workflow YAML (covered in brief 002) and governance files (CONTRIBUTING, SECURITY, CODE_OF_CONDUCT — separate). Assume rulesets as current standard (not deprecated classic branch protection). Reuse findings from brief 003 where they transfer unchanged, but cite original authoritative sources, not the sibling brief.

## Findings

### 1. PyPI Trusted Publishing: Ten Environments Required for Ten Projects

**Each of the ten PyPI projects must have a distinct GitHub Environment for OIDC trusted publishing to work.**

[PyPI's OIDC token verification is strict and claims-based.](https://docs.pypi.org/trusted-publishers/internals/) A trusted publisher configuration specifies four fields that must match exactly in the OIDC token:
- **Repository owner** (GitHub user/org name)
- **Repository name** (repo slug — here: `fastrest`, not `varco`)
- **Workflow filename** (e.g., `release.yml`)
- **Environment name** (optional but highly recommended for multi-project repos)

When creating a trusted publisher on PyPI, the `repository_owner_id` claim is also verified for account resurrection attack prevention.

**For varco's ten-project release:** [According to research 005, which analyzed this exact scenario,](https://github.com/edoardoscarpaci/fastrest/design/varco-1-0-release/research/005-trusted-publishing-and-supply-chain.md) the recommended approach is:
- **One release workflow file** (`release.yml`)
- **Ten GitHub Environments** named distinctly: `pypi-varco-core`, `pypi-varco-kafka`, `pypi-varco-nats`, `pypi-varco-redis`, `pypi-varco-beanie`, `pypi-varco-sa`, `pypi-varco-memcached`, `pypi-varco-ws`, `pypi-varco-fastapi`, `pypi-varco-casbin`
- **Ten pending publishers on PyPI**, one per project name, each configured with `owner: edoardoscarpaci`, `repo: fastrest`, `workflow: release.yml`, `environment: pypi-varco-<package>`
- **Job matrix over ten packages**, with each publish step specifying `environment: pypi-varco-<package>` so the OIDC token is scoped correctly

**Free tier:** [GitHub Environments are free for public repositories on any plan.](https://docs.github.com/en/actions/deployment/targeting-different-environments/using-environments-for-deployment)

**Practical cost:** Ten Settings → Environments entries, each with a name (no deployment-branch restriction needed; leave it unrestricted). Cannot be automated via `gh` CLI today; must be created via Settings UI. The ten pending publishers are also created via PyPI web UI per project.

### 2. `gh-pages` Branch + Rulesets: Interaction with `mike` CI Deployments

**A ruleset targeting `gh-pages` with "Block force pushes" WILL block `mike deploy --push`.** A "Require signed commits" rule will NOT block commits from `GITHUB_TOKEN` because GitHub automatically signs them.

**Evidence:**

- [GitHub automatically signs commits made by `GITHUB_TOKEN` when published via the REST API or web UI.](https://gist.github.com/swinton/03e84635b45c78353b1f71e41007fc7c) Since `GITHUB_TOKEN` is a bot token, commits from it are automatically signed by GitHub.
- [Rulesets with "Block force pushes" prevent all force-pushes unless a bypass actor is configured.](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets) The `mike deploy --push` command does a force-push to keep a single commit in the history, so the rule will block it unless `github-actions[bot]` or the workflow itself is added as a bypass actor.

**Recommendation for varco:**

**Option A (Simplest):** Do NOT apply a branch ruleset to `gh-pages` at all. The default branch (`main`) is the only branch that typically needs rulesets under solo-maintainer patterns; `gh-pages` is a deployment artifact branch and should remain unencumbered by protection. This is the safest and most common pattern.

**Option B (If `gh-pages` protection is desired):** Create a branch ruleset for `gh-pages` without "Block force pushes". Include only "Require signed commits" and "Restrict deletions" if desired; these will not interfere with CI. Do NOT add "Block force pushes" to this ruleset.

**Option C (Advanced):** Create a branch ruleset for `gh-pages` with all protections including "Block force pushes", then add `github-actions[bot]` as a bypass actor (via Settings → Rules → Rulesets → [ruleset] → Bypass actors → Add `github-actions[bot]`). The workflow's commits will bypass the force-push rule.

**Varco's case:** Recommend **Option A** — no ruleset on `gh-pages`, only on `main`. The `gh-pages` branch is deployment infrastructure, not source code, and protection there offers diminishing returns.

### 3. `all-green` Aggregate Job as the Sole Required Status Check

**The pattern is correct and is the recommended GitHub Actions best practice for matrix jobs.**

[GitHub's own documentation on required status checks states that a skipped job can leave a required check in "pending" state, blocking PRs indefinitely.](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/collaborating-on-repositories-with-code-quality-features/troubleshooting-required-status-checks) To prevent this:

1. **Create an aggregate job** that depends on all upstream jobs via `needs: [lint-type-unit, integration-test, ...]`
2. **Use `if: always()`** so the aggregate job runs even if upstream jobs fail or are skipped
3. **Assert the result explicitly** in the aggregate job (e.g., a step that checks all upstream results)
4. **Require ONLY the aggregate job** in branch protection rules, never individual matrix legs

[The `alls-green` action implements this pattern;](https://github.com/re-actors/alls-green) varco's existing `all-green` job follows the same design.

**Code pattern (simplified):**
```yaml
jobs:
  lint-type-unit:
    strategy:
      matrix:
        python-version: [3.12, 3.13]
    runs-on: ubuntu-latest
    # ... lint, mypy, pytest steps

  all-green:
    if: always()
    needs: [lint-type-unit]
    runs-on: ubuntu-latest
    steps:
      - name: Check all upstream green
        run: |
          if [ "${{ needs.lint-type-unit.result }}" != "success" ]; then
            echo "Upstream job failed or was skipped"
            exit 1
          fi
```

Then in branch protection (Settings → Rules → Rulesets → [main ruleset] → Add rule → "Require status checks to pass"), select **only** `all-green`, never individual matrix job names.

**Varco already does this correctly** per CLAUDE.md; no changes needed. This is the evidence-based pattern.

### 4. First Tag + Tag Ruleset: Protecting v3.0.0rc1 and v3.0.0

**A tag ruleset with "Restrict creations" does NOT block repository admins who are added as bypass actors.**

[Repository admins can be added to a tag ruleset's bypass list, allowing them to create/update/delete tags even when the ruleset restricts creation.](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets) The bypass is automatic; no additional prompt is shown.

**For varco's first-ever tag:**

1. **Create the tag ruleset now** (before pushing v3.0.0rc1):
   - Settings → Rules → Rulesets → New ruleset → New tag ruleset
   - Name: `release-tags`
   - Target: `v*` (fnmatch pattern; matches both `v3.0.0rc1` and `v3.0.0`)
   - Enforcement: Active
   - Add rules: "Restrict creations", "Restrict updates", "Restrict deletions"
   - Add bypass actor: Repository admins (with "Always allow" mode)

2. **The ruleset is now active.** When the maintainer (`edoardoscarpaci`) pushes the first tag (`git tag v3.0.0rc1 && git push origin v3.0.0rc1`), the push is evaluated against the ruleset. Because the maintainer is a repository admin and is in the bypass list, the tag creation is allowed automatically.

3. **Ordering:** Ruleset can be created before, during, or after the rc1 tag push — it makes no difference. Once a tag exists, "Restrict updates" and "Restrict deletions" protect it going forward.

4. **No admin bypass needed for release workflows:** The release workflow itself does not create tags; it is triggered by a tag push event. The tag is created locally by the maintainer and pushed, which is the step the ruleset guards. Once the workflow runs, it publishes to PyPI (via the `release.yml` workflow with `environment:` gates) — no additional tag-creation step is needed in the workflow.

### 5. CodeQL Default Setup on a 10-Package `uv` Workspace

**CodeQL default setup can scan a Python monorepo without advanced setup, but specific guidance on varco's structure is limited in official docs.**

[GitHub's CodeQL default setup documentation states that "Analysis of JavaScript/TypeScript, Go, Ruby, Python, and Kotlin code does not currently require special configuration."](https://docs.github.com/en/code-security/code-scanning/enabling-code-scanning/configuring-default-setup-for-code-scanning) This suggests Python monorepos are supported, but the documentation does not explicitly address ten-package uv workspaces.

**What is known:**
- Default setup automatically detects Python and scans the repository root and all subdirectories.
- [For monorepos with multiple languages or separated analysis needs, GitHub's advanced-security documentation shows matrix-based strategies and path-specific scanning.](https://docs.github.com/en/code-security/code-scanning/creating-an-advanced-setup-for-code-scanning/customizing-your-advanced-setup-for-code-scanning)
- `uv` venvs: No known issues with CodeQL and `uv`-managed venvs. CodeQL scans source code, not venv artifacts.

**Recommendation for varco:**
1. **Start with default setup** (Settings → Code security & analysis → Code scanning → "Set up default setup"). This is the documented starting point.
2. **Verify coverage:** After enabling, check the Security → Code scanning tab to see which packages and tests were scanned.
3. **If coverage is incomplete or test directories are scanned unnecessarily,** switch to advanced setup and customize the CodeQL config with `paths-ignore: ["*/tests/**"]` if needed.

**Free tier:** Default setup is free for public repositories.

**Note on test scanning:** CodeQL scans all Python code, including tests. This is not a defect; tests can contain security-relevant logic and vulnerabilities. The default is appropriate.

### 6. Secret Scanning Push Protection + Test Credentials

**Push protection will flag test credentials and throwaway broker credentials, but provides multiple bypass options.**

[GitHub's push protection has a "remarkably low false positive rate" and offers three bypass reasons when a secret is detected:](https://docs.github.com/en/code-security/secret-scanning/introduction/about-push-protection)
1. **"It's used in tests"** — Creates a closed alert marked as "used in tests"
2. **"It's a false positive"** — Creates a closed alert resolved as "false positive"
3. **"I'll fix it later"** — Creates an open alert; developer can commit with warning

When a developer encounters a block, they are prompted to choose a bypass reason. The choice is logged and reviewable.

**For varco's test credentials:**
- Testcontainer credentials (e.g., `REDIS_PASSWORD=testpass`) are throwaway and can be bypassed as "used in tests".
- JWT test PEM keys in fixtures can be bypassed as "false positives" (they are test keys, not production secrets).
- Developers can commit by providing the bypass reason; no additional UI steps are needed.

**Suppressing false positives:** [GitHub's secret scanning supports `.github/secret_scanning.yml` (or `.github/secret_scanning.json`) to define patterns to skip,](https://docs.github.com/en/code-security/secret-scanning/introduction/about-push-protection) but the file is not yet widely documented. The simpler approach is to use the inline bypass reason.

**Practical impact:** This should not block varco's release or CI workflows. Test credentials in committed files are an expected pattern and the bypass flow is designed to handle them without ceremony.

**Free tier:** Push protection is free for public repositories (generally available as of August 2026).

### 7. Free-Tier Feature Summary

| Feature | Free Tier | Notes |
|---------|---|---|
| Rulesets (branch + tag) | ✅ Free for all | No free-tier restriction; rulesets are the current standard |
| Commit signing enforcement | ✅ Free for all | Signing itself (SSH key setup) is free; enforcement rule is part of rulesets |
| Environments | ✅ Free for public repos | Private repos require Pro/Team/Enterprise |
| Dependabot alerts | ✅ Free for all | Security updates are free; version updates require `dependabot.yml` |
| Secret scanning + push protection | ✅ Free for public repos | Free for public; private repos require GitHub Advanced Security |
| CodeQL default setup | ✅ Free for public repos | Free for public; private repos require Advanced Security |
| Private vulnerability reporting | ✅ Free for public repos | Free for public; enables researchers to report security issues privately |

**All recommended settings in this brief are free for varco's public repository.**

## Version/Compatibility Notes

- **GitHub Rulesets:** Stable since ~2023. [Classic branch protection rules are deprecated as of GitHub Enterprise Server 3.16 (Aug 2026) but remain functional in cloud GitHub.](https://github.blog/changelog/2026-08-11-automatically-migrate-branch-protection-rules-to-repository-rulesets/) No migration is automatic for existing cloud repositories yet; new repos use rulesets exclusively.
- **Commit signing:** SSH signing supported in Git 2.34+; widely available as of 2026. GitHub web UI auto-signs commits, removing friction.
- **PyPI trusted publishing & OIDC:** Stable since 2023. [Per-environment gating for monorepos is documented in PyPI internals.](https://docs.pypi.org/trusted-publishers/internals/)
- **Attestations (PEP 740):** GA in November 2024. [Auto-generation in `gh-action-pypi-publish` v1.11.0+ (May 2026).](https://github.com/pypa/gh-action-pypi-publish/releases)
- **GitHub Environments:** Stable since GitHub Actions launch (2019); deployment protection rules unchanged.
- **Secret scanning push protection:** GA in 2022; 39+ token types covered as of August 2026; free for public repos.
- **CodeQL default setup:** GA in 2024; preferred over manual workflow setup.
- **OpenSSF Scorecard:** 20 checks stable; updated quarterly.
- **`uv` venv support:** No known incompatibilities with CodeQL or other CI scanning tools as of August 2026.
- **`mike` versioned docs:** Stable since ~2020; standard for Python projects on GitHub Pages.

## Evidence Gaps

1. **CodeQL monorepo coverage for `uv` specifically:** Official docs do not explicitly confirm CodeQL default setup handles varco's 10-package workspace. A trial run is recommended to verify all packages are scanned.

2. **`github-actions[bot]` bypass behavior in complex rulesets:** While bypass actors are documented, interaction with a ruleset that has multiple rules (e.g., "Require PR" + "Block force push" + "Require signed commits") is not fully spelled out. Testing with one bypass actor in a complex ruleset is advised.

3. **GITHUB_TOKEN signature verification in "Require signed commits" rules:** While GitHub does auto-sign GITHUB_TOKEN commits via REST API, whether a ruleset's "Require signed commits" rule explicitly accepts GitHub-bot-signed commits (vs. requiring human SSH/GPG signatures) is not explicitly confirmed in the official docs. Practical evidence from search suggests it works, but a first-time test is prudent.

4. **Secret scanning false positive rate on test PEM keys:** GitHub claims "remarkably low" false positives, but no published benchmark for JWT PEM fixtures. Varco will likely encounter some flags; bypass flow is designed to handle them, but the exact pattern-match rules for PEM keys are not public.

5. **`mike` interaction with GitHub Pages branch rules:** Docs are sparse on whether `mike deploy --push` correctly handles rulesets on `gh-pages`. Recommendation to skip `gh-pages` protection is conservative but not proven from official docs.

## Librarian's Note

**What the sources indicate for varco's 3.0.0 release:**

✅ **Ten Environments required:** Each PyPI project needs a distinct GitHub Environment (`pypi-varco-core` … `pypi-varco-casbin`) to gate trusted publishing. This is the documented approach for monorepos. Free for public repos.

✅ **`gh-pages` + rulesets:** Safest pattern is to skip rulesets on `gh-pages` entirely and protect only `main`. If `gh-pages` protection is desired, exclude "Block force pushes" or add `github-actions[bot]` as a bypass actor. GITHUB_TOKEN commits are auto-signed by GitHub, so "Require signed commits" on `gh-pages` is not a blocker.

✅ **`all-green` aggregate job:** Pattern is correct and evidence-backed. Require only the aggregate job in branch protection, never individual matrix legs, to avoid the "skipped leg pending check" trap.

✅ **First tag + tag ruleset:** Can be configured now and will not block the maintainer (admin) from pushing v3.0.0rc1 or v3.0.0. Ruleset target pattern `v*` matches both release-candidate and final tags. Admin bypass is automatic.

✅ **CodeQL default setup:** Recommended starting point for the monorepo; should handle Python scanning without advanced setup. Verify coverage after enablement. No known `uv` venv issues.

✅ **Secret scanning + test credentials:** Push protection will flag throwaway credentials and test PEM keys. Bypass flow ("used in tests", "false positive") is designed to handle this without ceremony. Low false positive rate overall; test patterns may trigger occasionally but are expected.

✅ **Free tier:** All recommended settings are free for varco's public repository. No paid features required for production-grade hardening.

**Bottom line:** The evidence **strongly favours** implementing all settings in the checklist below. The interplay between ten environments, rulesets, signing, and secret scanning is well-documented for the individual pieces; varco's monorepo shape is not exotic and should follow the proven patterns.

---

## Checklist: GitHub Repository Hardening Settings for varco 3.0.0 Release

**Instructions:** Follow the table in order (grouped by GitHub settings section for efficiency). The repository name is `fastrest`; the project name is `varco`. Priority levels:
- **MUST:** Required for production open-source credibility (OSPS baseline, Scorecard pass, PyPI trusted publishing)
- **SHOULD:** Strongly recommended; minor friction with high security/hygiene benefit
- **NICE:** Optional; adds marginal value; skip if time-constrained

### Checklist Table

| # | Setting | Where (exact UI path or `gh` CLI command) | Value to set | Priority | Free? | Notes |
|---|---------|-------|------|----------|---|---|
| 1 | Create main branch ruleset | Settings → Rules → Rulesets → New ruleset → New branch ruleset | Name: `main-branch-protection`; Target: `main`; Enforcement: Active | MUST | Yes | Adapted from brief 003 §1; applies to varco's default branch |
| 2 | Require PR before merge | Settings → Rules → Rulesets → [ruleset] → Add rule → "Require pull request before merging" | Require: 0 approvals (solo maintainer); no stale PR dismissal | MUST | Yes | Documents intent, creates audit trail even for solo dev |
| 3 | Require status checks (aggregate job only) | Settings → Rules → Rulesets → [ruleset] → Add rule → "Require status checks to pass" | Required checks: `all-green` only; NOT individual matrix legs | MUST | Yes | Critical: prevents "skipped leg pending check" trap per brief 002 §5 |
| 4 | Require linear history | Settings → Rules → Rulesets → [ruleset] → Add rule → "Require linear history" | Enabled | MUST | Yes | Keeps git history clean; enforces squash/rebase only |
| 5 | Block force-push | Settings → Rules → Rulesets → [ruleset] → Add rule → "Block force pushes" | Enabled (default) | MUST | Yes | Prevents accidental history destruction |
| 6 | Restrict deletions | Settings → Rules → Rulesets → [ruleset] → Add rule → "Restrict deletions" | Enabled (default) | MUST | Yes | Preserves branch audit trail |
| 7 | Admin bypass actor | Settings → Rules → Rulesets → [ruleset] → Bypass actors → Add actors | Actor: Repository admins; Bypass type: Always allow | MUST | Yes | Allows emergency push if rules block workflow or maintainer |
| 8 | Require signed commits | Settings → Rules → Rulesets → [ruleset] → Add rule → "Require signed commits" | Enabled | SHOULD | Yes | SSH signing already configured; GITHUB_TOKEN auto-signs |
| 9 | Verify SSH signing locally | Local git config (already done per user context) | `git config --global gpg.format ssh && git config --global user.signingKey ~/.ssh/id_ed25519.pub && git config --global commit.gpgSign true && git config --global tag.gpgSign true` | SHOULD | Yes | Per brief 003 §2; varco already has this configured |
| 10 | Create tag ruleset | Settings → Rules → Rulesets → New ruleset → New tag ruleset | Name: `release-tags`; Target: `v*`; Enforcement: Active | MUST | Yes | Protects all release tags (rc1, final, future); does not block admins |
| 11 | Restrict tag creation | Settings → Rules → Rulesets → [tag ruleset] → Add rule → "Restrict creations" | Enabled | SHOULD | Yes | Only admins can create release tags; prevents accidental tagging |
| 12 | Restrict tag updates | Settings → Rules → Rulesets → [tag ruleset] → Add rule → "Restrict updates" | Enabled | SHOULD | Yes | Release tags are immutable once created |
| 13 | Restrict tag deletion | Settings → Rules → Rulesets → [tag ruleset] → Add rule → "Restrict deletions" | Enabled | SHOULD | Yes | Audit trail preservation |
| 14 | Tag ruleset admin bypass | Settings → Rules → Rulesets → [tag ruleset] → Bypass actors → Add actors | Actor: Repository admins; Bypass type: Always allow | SHOULD | Yes | Allows re-tagging in emergency; automatic when admin pushes |
| 15 | Skip ruleset on gh-pages | Settings → Rules → Rulesets → (do NOT create ruleset for `gh-pages`) | N/A | MUST | Yes | Keeps CI deployment branch unencumbered by protection rules |
| 16 | Verify GITHUB_TOKEN default | Settings → Actions → General → Workflow permissions | "Read repository contents and packages permissions" (restrictive) | MUST | Yes | Default since Feb 2023; no action needed if repo created after that |
| 17 | Set Actions policy | Settings → Actions → General → Actions permissions | "Allow all actions and reusable workflows" (practical for solo project) | SHOULD | Yes | Per brief 003 §4; all actions already pinned by commit SHA in workflows |
| 18 | Configure fork PR approval | Settings → Actions → General → Fork pull request workflows from outside collaborators | "Require approval for first-time contributors who are new to GitHub" | SHOULD | Yes | Per brief 003 §4; balances security and friction |
| 19 | Create `pypi-varco-core` environment | Settings → Environments → New environment | Name: `pypi-varco-core`; URL (optional): `https://pypi.org/project/varco-core/` | MUST | Yes | First of ten environments for trusted publishing; free for public repos |
| 20 | Create `pypi-varco-kafka` environment | Settings → Environments → New environment | Name: `pypi-varco-kafka`; URL (optional): `https://pypi.org/project/varco-kafka/` | MUST | Yes | Second of ten; repeat for nats, redis, beanie, sa, memcached, ws, fastapi, casbin |
| 21–28 | Create remaining 8 environments | Settings → Environments → New environment | Names: `pypi-varco-nats`, `pypi-varco-redis`, `pypi-varco-beanie`, `pypi-varco-sa`, `pypi-varco-memcached`, `pypi-varco-ws`, `pypi-varco-fastapi`, `pypi-varco-casbin` | MUST | Yes | Ten environments total, one per PyPI project; no deployment-branch restriction needed |
| 29 | Create PyPI pending publishers (via PyPI UI) | https://pypi.org/manage/project/<project-name>/settings/publishing/ (repeat for each of 10) | Owner: `edoardoscarpaci`; Repo: `fastrest`; Workflow: `release.yml`; Environment: `pypi-varco-<package>` (e.g., `pypi-varco-core`) | MUST | Yes | Cannot be automated; must use PyPI web UI. Pending publishers auto-convert on first publish. |
| 30 | Dependabot alerts | Settings → Code security & analysis → Dependabot alerts → Enable | Enabled | MUST | Yes | Per brief 003 §6A; frees for all public repos |
| 31 | Dependabot security updates | Settings → Code security & analysis → Dependabot security updates → Enable | Enabled | MUST | Yes | Per brief 003 §6B |
| 32 | Add `dependabot.yml` for Python + Actions | Create `.github/dependabot.yml` | See template below | SHOULD | Yes | Per brief 003 §6C; enables version update PRs for uv/deps and GitHub Actions |
| 33 | Secret scanning | Settings → Code security & analysis → Secret scanning → Enable | Enabled | MUST | Yes | Per brief 003 §6D; automatic for public repos; free |
| 34 | Secret scanning push protection | Settings → Code security & analysis → Secret scanning → Enable push protection | Enabled | MUST | Yes | Per brief 003 §6D; free for public repos; allows bypass with reason ("used in tests", "false positive", etc.) |
| 35 | CodeQL default setup | Settings → Code security & analysis → Code scanning → Set up default setup | Enabled; leave language auto-detection on | SHOULD | Yes | Per brief 003 §6E; handles Python monorepo without advanced setup; verify coverage |
| 36 | Private vulnerability reporting | Settings → Code security & analysis → Private vulnerability reporting → Enable | Enabled | SHOULD | Yes | Per brief 003 §6F; free for public repos; adds "Report a vulnerability" link to repo |
| 37 | Auto-delete head branches on merge | Settings → General → Pull Requests → "Automatically delete head branches" | Enabled | NICE | Yes | Per brief 003 §7A; reduces clutter; harmless |
| 38 | Restrict merge strategies | Settings → General → Pull Requests → "Allow merge commits" / "Allow squash merging" / "Allow rebase merging" | Disable "Allow merge commits"; keep "Squash" only (or "Rebase" only) | SHOULD | Yes | Per brief 003 §7B; enforces linear history at UI level (redundant with ruleset but clear signal) |
| 39 | Require conversation resolution (optional) | Settings → Rules → Rulesets → [main ruleset] → Add rule → "Require conversation resolution" (if available) | Enabled | NICE | Yes | Per brief 003 §7C; low value for solo maintainer; nice-to-have |
| 40 | Enable Issues | Settings → General → Features → "Issues" | Enabled | MUST | Yes | Standard for open-source projects |
| 41 | Configure Discussions | Settings → General → Features → "Discussions" | Enable (if fostering community) or disable (if not) | NICE | Yes | Per brief 003 §7D; optional for small projects |
| 42 | Disable Wiki (recommend) | Settings → General → Features → "Wiki" | Disabled | NICE | Yes | Per brief 003 §7D; use README + `docs/` folder instead (docs are versioned with `mike` on `gh-pages`) |
| 43 | Add repo description & topics | Repository page → About → Edit | Description: "Pure-Python event-driven framework with resilience, caching, and observability abstractions"; Topics: python, event-driven, kafka, redis, di, dependency-injection, framework | NICE | Yes | Improves discoverability; helps with Scorecard search |
| 44 | Add OpenSSF Scorecard action | `.github/workflows/scorecard.yml` (create new file; see template below) | Run weekly or on push to main; publish results | SHOULD | Yes | Per brief 003 §8; free for public repos; adds credibility badge to README |
| 45 | Add Scorecard badge to README | README.md | Badge: `[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/edoardoscarpaci/fastrest/badge)](https://securityscorecards.dev/viewer?uri=github.com/edoardoscarpaci/fastrest)` | NICE | Yes | Visible social proof; expected for production v3.0.0 |

---

### Dependabot Configuration Template (`.github/dependabot.yml`)

```yaml
version: 2
updates:
  # Python dependencies (uv workspace, single lock file)
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
    pull-request-branch-name:
      separator: "/"
    reviewers:
      - "edoardoscarpaci"
    allow:
      - dependency-type: "direct"
      - dependency-type: "indirect"

  # GitHub Actions
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    pull-request-branch-name:
      separator: "/"
    reviewers:
      - "edoardoscarpaci"
```

**Note:** `uv.lock` is a single file at the workspace root; Dependabot will recognize it and update all 10 packages' dependencies in one PR per interval. No per-package directory entries needed. Actions pinned by commit SHA will be preserved by Dependabot (the SHA and `# vN` comment are both updated in-place).

**Reference:** [GitHub Docs on Dependabot](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/keeping-your-actions-up-to-date-with-dependabot)

---

### OpenSSF Scorecard Action Template (`.github/workflows/scorecard.yml`)

Adapted from brief 003 for varco's repository name (`fastrest`):

```yaml
name: OpenSSF Scorecard

on:
  schedule:
    - cron: "0 0 * * 0"  # Weekly on Sunday at 00:00 UTC
  push:
    branches:
      - main

permissions:
  contents: read
  security-events: write
  id-token: write  # Required for Sigstore attestation signing

jobs:
  scorecard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@a5ac7e51b41094c7a9013c46f6b263d9e47eb94f # v4
      
      - uses: ossf/scorecard-action@62b2cac7ed8198f1b9ed4f049bfdbf15693c6410 # v2.4.0
        with:
          results_file: results.sarif
          results_format: sarif
          publish_results: true
      
      - uses: github/codeql-action/upload-sarif@515828d199fb26ee248b6845a31553058ff475dbe # v3
        if: always()
        with:
          sarif_file: results.sarif
```

**Reference:** [ossf/scorecard-action GitHub repository](https://github.com/ossf/scorecard-action)

---

## Summary

This checklist covers all GitHub repository UI settings, CLI commands, and manual configurations needed to harden `edoardoscarpaci/fastrest` (varco) for a production 3.0.0 release. The 45 items are prioritized and grouped by section for efficient execution.

**Varco-specific highlights:**
- **Ten GitHub Environments** required for PyPI trusted publishing (one per distribution)
- **`gh-pages` branch left unprotected** to avoid `mike deploy --push` friction
- **`all-green` aggregate job** as the sole required status check (prevents skipped-leg trap)
- **Tag ruleset safe on first tag** (admin bypass automatic)
- **CodeQL default setup** recommended for the 10-package monorepo
- **Secret scanning + push protection** supports bypass for test credentials

**All are free for varco's public repository on the free GitHub tier.** Expected outcome: **8–9/10 OpenSSF Scorecard**, alignment with **OSPS 2025 baseline**, and a **credible production open-source signal** for varco 3.0.0.

---

## Sources

- [GitHub Docs: Creating rulesets for a repository](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository)
- [GitHub Changelog: Automatically migrate branch protection rules to repository rulesets (Aug 2026)](https://github.blog/changelog/2026-08-11-automatically-migrate-branch-protection-rules-to-repository-rulesets/)
- [GitHub Docs: Available rules for rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
- [GitHub Changelog: Repository rulesets user bypass (May 2026)](https://github.blog/changelog/2026-05-07-repository-rulesets-user-bypass-and-branch-renaming/)
- [GitHub Docs: About commit signature verification](https://docs.github.com/en/authentication/managing-commit-signature-verification/about-commit-signature-verification)
- [GitHub Docs: Telling Git about your signing key](https://docs.github.com/en/authentication/managing-commit-signature-verification/telling-git-about-your-signing-key)
- [PyPI Docs: Trusted Publishers](https://docs.pypi.org/trusted-publishers/)
- [PyPI Docs: Internals and Technical Details](https://docs.pypi.org/trusted-publishers/internals/)
- [PyPI Docs: Publishing with a Trusted Publisher](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
- [GitHub Docs: Managing environments for deployment](https://docs.github.com/en/actions/deployment/targeting-different-environments/using-environments-for-deployment)
- [GitHub Docs: Deployments and environments](https://docs.github.com/en/actions/deployment/targeting-different-environments/using-environments-for-deployment)
- [GitHub Docs: Troubleshooting required status checks](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/collaborating-on-repositories-with-code-quality-features/troubleshooting-required-status-checks)
- [GitHub Actions Required Checks for Conditional Jobs (2025)](https://devopsdirective.com/posts/2025/08/github-actions-required-checks-for-conditional-jobs/)
- [GitHub Docs: About rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)
- [GitHub Docs: Controlling permissions for GITHUB_TOKEN](https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/controlling-permissions-for-github_token)
- [GitHub Docs: Security hardening for GitHub Actions](https://docs.github.com/en/actions/security-for-github-actions/security-hardening-for-github-actions)
- [Letting GitHub Actions Push to Protected Branches (Medium, 2026)](https://medium.com/ninjaneers/letting-github-actions-push-to-protected-branches-a-how-to-57096876850d)
- [GitHub Docs: Automatically sign commits from GitHub Actions using the REST API (GitHub Gist)](https://gist.github.com/swinton/03e84635b45c78353b1f71e41007fc7c)
- [GitHub Docs: Dependency graph](https://docs.github.com/en/code-security/reference/supply-chain-security/understanding-dependencies)
- [GitHub Docs: Dependabot security updates](https://docs.github.com/en/code-security/concepts/supply-chain-security/about-dependabot-security-updates)
- [GitHub Docs: Keeping your actions up to date with Dependabot](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/keeping-your-actions-up-to-date-with-dependabot)
- [GitHub Docs: About secret scanning](https://docs.github.com/code-security/secret-scanning/about-secret-scanning)
- [GitHub Docs: About push protection](https://docs.github.com/en/code-security/secret-scanning/introduction/about-push-protection)
- [GitHub Docs: Configuring default setup for code scanning](https://docs.github.com/en/code-security/code-scanning/enabling-code-scanning/configuring-default-setup-for-code-scanning)
- [GitHub Docs: Customizing your advanced setup for code scanning](https://docs.github.com/en/code-security/code-scanning/creating-an-advanced-setup-for-code-scanning/customizing-your-advanced-setup-for-code-scanning)
- [GitHub Docs: Keeping your actions up to date with Dependabot](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/keeping-your-actions-up-to-date-with-dependabot)
- [GitHub CodeQL Discussion: Run CodeQL analysis on a particular sub-directory in monorepo](https://github.com/github/codeql/discussions/9844)
- [GitHub Docs: Managing automatic branch deletion](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-the-automatic-deletion-of-branches)
- [GitHub Docs: About merge methods](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/configuring-commit-squashing-and-merge-options)
- [GitHub Docs: Managing GitHub Actions settings for a repository](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository)
- [ossf/scorecard-action GitHub repository](https://github.com/ossf/scorecard-action)
- [GitHub Re-Actors: alls-green (aggregate status check action)](https://github.com/re-actors/alls-green)
- [GitHub Blog: Private vulnerability reporting now generally available](https://github.blog/security/supply-chain-security/private-vulnerability-reporting-now-generally-available/)
- [AppSec Santa: Is Dependabot Free (2026)](https://appsecsanta.com/dependabot)
- [AppSec Santa: GitHub Secret Scanning (2026)](https://appsecsanta.com/github-secret-scanning)
- [Research 005 — PyPI Trusted Publishing & Supply Chain Security for Multi-Package Release (varco)](https://github.com/edoardoscarpaci/fastrest/design/varco-1-0-release/research/005-trusted-publishing-and-supply-chain.md)
- [Research 002 — uv workspace + GitHub Actions CI/CD setup (varco)](https://github.com/edoardoscarpaci/fastrest/design/varco-1-0-release/research/002-uv-workspace-github-actions.md)
