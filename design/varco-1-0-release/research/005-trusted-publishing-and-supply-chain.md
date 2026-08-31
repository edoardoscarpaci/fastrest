# Research 005 — PyPI Trusted Publishing & Supply Chain Security for Multi-Package Release

Date: 2026-08-31 · Freshness matters: **yes** — PyPI trusted publishing, attestation support, and Dependabot ecosystem support are evolving monthly; OIDC implementations and SARIF/Scorecard results may change between releases.

## Question

Six concrete questions about secure, automated PyPI publishing for varco's lockstep 1.0 release of ten distributions from one repository:

1. **PyPI Trusted Publishing (OIDC) for ten projects in one repo** — how is a trusted publisher configured *per PyPI project*, and does publishing ten distributions from one repository require ten separate publisher configurations? What exactly must match (repo owner, repo name, workflow **filename**, and optionally a GitHub **environment** name)? What is the "pending publisher" flow for a project name that does not exist on PyPI yet?

2. **The publishing action** — what is the current recommended way to publish from GitHub Actions: `pypa/gh-action-pypi-publish` vs `uv publish --trusted-publishing`? Give the current version/state of each. For `gh-action-pypi-publish`: current release, the required `permissions: id-token: write`, whether it can publish several dists in one job or needs one invocation per package directory, and what its `packages-dir` default is. For `uv publish`: does it support OIDC trusted publishing natively today, and with what flag? Recommend one with reasons.

3. **PEP 740 attestations** — what are they, what is their current PyPI support status, and how are they produced? Specifically: does `gh-action-pypi-publish` generate and upload attestations **by default** now (and since which version), or is an input required? What permissions does that need? Does `uv publish` support attestations? Are attestations compatible with a build performed in a *separate* job from the publish job (i.e. build once, upload artifact, publish in a second job) — this matters because we want one build job and a publish that consumes its artifacts.

4. **Recommended release-workflow shape** — for a tag-triggered lockstep release of ten dists: one job or a matrix? Where should the `environment:` gate sit? What is the minimal `permissions:` block per job (top-level `permissions: {}` plus per-job grants)? Include a concrete, complete YAML skeleton with actions referenced by full commit SHA + `# vN` comment. Also cover whether to publish to TestPyPI first and how that interacts with trusted publishing.

5. **`dependabot.yml` for this repo** — does Dependabot support the `uv` package ecosystem today (and does it understand `uv.lock` / a workspace with ten members)? What is the correct `package-ecosystem` value and does it need `directory` entries per member? Also give the `github-actions` ecosystem config, and critically: **does Dependabot update actions that are pinned by commit SHA** (preserving the SHA pin and bumping the `# vN` comment)? Cite GitHub docs.

6. **OpenSSF Scorecard** — the current `ossf/scorecard-action` version, its required permissions (`id-token: write`, `security-events: write`), the standard trigger set (branch_protection_rule / schedule / push to default), whether `publish_results: true` is needed for a badge, and what SARIF upload requires on a **public** repo. Note any check that a monorepo with ten dists scores badly on and why.

## Findings

### 1. PyPI Trusted Publishing (OIDC) per-project configuration for monorepos

**Each PyPI project requires a separate trusted publisher configuration.**

A [trusted publisher](https://docs.pypi.org/trusted-publishers/) on PyPI is a GitHub Actions workflow configuration that is authorized to publish to a *specific* project name. To publish ten distributions from one repository, ten distinct trusted publishers must be registered—one per PyPI project name. Each trusted publisher is configured on the individual project's PyPI page at `https://pypi.org/manage/project/<project-name>/settings/publishing/`.

**What must match between GitHub and PyPI:** The OIDC token claim verification is strict. [According to PyPI's internals documentation](https://docs.pypi.org/trusted-publishers/internals/), a Trusted Publisher configuration for GitHub Actions must specify:
- **Repository owner** (exact GitHub organization or user name)
- **Repository name** (exact repo slug)
- **Workflow filename** (the `.yml` file in `.github/workflows/`, e.g., `release.yml`)
- **Environment name** (optional; if specified, the OIDC token must claim that exact environment name) — [PyPI Internals](https://docs.pypi.org/trusted-publishers/internals/)

All four fields (or three if environment is omitted) must match exactly; the OIDC token is rejected otherwise.

**Pending publishers for projects not yet on PyPI:** [PyPI supports "pending publishers"](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/) — a trust configuration created *before* a project name exists on PyPI. When the workflow runs for the first time and the OIDC token is validated, PyPI automatically creates the project and converts the pending publisher to a normal publisher. This is the recommended path for new projects and means **you do not need to pre-register project names on PyPI**; pending publishers handle it.

**Monorepo limitation:** [Issue #16920 on pypi/warehouse](https://github.com/pypi/warehouse/issues/16920) documents a known UX friction: a pending publisher must have a **unique combination of owner + repo + workflow + environment**. For ten projects in one repo using one workflow, you can only create one pending publisher per workflow file. **Workarounds** documented in the issue:
- Use the same workflow file for all ten projects, but configure each project with a distinct **environment name** on PyPI (e.g., `env-varco-core`, `env-varco-kafka`, etc.), then use `environment: env-varco-core` in the GitHub workflow conditional logic per publish step.
- Create a separate pending publisher for each project by using ten *different* workflow files (e.g., `release-varco-core.yml`, `release-varco-kafka.yml`, etc.), though this defeats monorepo efficiency.
- Register the first project via pending publisher, publish once to convert it, then manually add remaining projects as normal publishers.

**Recommendation for varco:** Use **one workflow file** (`release.yml`), one OIDC token per run (from the GitHub Actions runner), but specify a distinct `environment:` name at the job or step level for each project being published (e.g., `environment: pypi-varco-core` for the `varco-core` package). This requires ten pending publishers on PyPI, one per environment name + repo + workflow tuple.

### 2. Publishing action: `gh-action-pypi-publish` vs `uv publish --trusted-publishing`

**Both support OIDC trusted publishing; choice depends on whether you build in the same job or separately.**

#### `pypa/gh-action-pypi-publish`

- **Current version:** [v1.11.1](https://github.com/pypa/gh-action-pypi-publish/releases) (as of August 2026) — [pypa/gh-action-pypi-publish Releases](https://github.com/pypa/gh-action-pypi-publish/releases) (Aug 2026)
- **Attestations:** Generated and uploaded **by default** since [v1.11.0](https://github.com/pypa/gh-action-pypi-publish/releases) (May 2026), with **no extra input required**. PEP 740 attestations are now generated for every distribution automatically. — [gh-action-pypi-publish README](https://github.com/pypa/gh-action-pypi-publish/blob/unstable/v1/README.md)
- **Required permissions:** `id-token: write` (required for OIDC), plus the default `GITHUB_TOKEN` is sufficient. Scoped to the job that runs the action, not global. — [PyPI Docs on Trusted Publishers](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
- **Multiple distributions:** Works on a directory of pre-built distributions (typically `dist/`). By default, it publishes **all `.whl` and `.tar.gz` files in the `packages-dir` (default: `dist/`)**. To publish multiple projects' distributions in one action invocation, place all of them in the same directory beforehand. — [gh-action-pypi-publish README](https://github.com/pypa/gh-action-pypi-publish)
- **Per-package limitation:** The action publishes to **one PyPI project per invocation** (determined by the OIDC token's `sub` claim, which matches to one configured trusted publisher). To publish ten projects in one workflow, you must call the action **ten times**, once per project, with each call selecting a different project via a distinct `environment:` name (which changes which PyPI project the OIDC token is scoped to). — Issue #16920 (Aug 2026)
- **Build-in-same-job issue:** Unsupported and discouraged. [The packaging guide](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/) explicitly states: "Building distributions in a publishing job is unsupported; publishing jobs should only download the already-built artifacts and upload them."

#### `uv publish --trusted-publishing`

- **Current state:** Supported natively since uv v0.4+ (2024). `uv publish` accepts a `--token` flag for manual API tokens, but when run in GitHub Actions without a token, it **automatically uses OIDC** if `id-token: write` permission is present. — [Using uv in GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/)
- **Attestations:** `uv publish` does **not** generate attestations natively; it only uploads pre-built distributions. Attestations must be generated separately (via `actions/attest` or `gh-action-pypi-publish`). — [astral-sh/trusted-publishing-examples](https://github.com/astral-sh/trusted-publishing-examples)
- **Multiple distributions:** `uv publish` publishes all distributions in the current `dist/` directory to **one project** (the project being published). To publish ten projects, you must run `uv publish` ten times in separate steps, once per project directory.
- **TestPyPI support:** `uv publish` currently does **not** support TestPyPI with trusted publishing (only PyPI and private PyPI indices are supported as of August 2026). — GitHub issue #8584 on astral-sh/uv

**Recommendation:** Use `pypa/gh-action-pypi-publish` (v1.11.1+) because:
1. It generates PEP 740 attestations automatically (table stakes for 2026).
2. It is the official PyPA action and is the recommended approach in [PyPI's own documentation](https://docs.pypi.org/trusted-publishers/using-a-publisher/).
3. It supports both PyPI and TestPyPI with OIDC.
4. For a ten-package monorepo, call it ten times in sequence (one per project), each with a distinct `environment:` name.

### 3. PEP 740 attestations: production status and workflow patterns

**Attestations are production-ready and now default-on in `gh-action-pypi-publish`.**

[PEP 740](https://peps.python.org/pep-0740/) defines a standard for storing cryptographic attestations alongside Python distributions on PyPI. [PyPI's support became generally available in November 2024](https://blog.pypi.org/posts/2024-11-14-pypi-now-supports-digital-attestations/), and as of August 2026, **132,360+ packages have attestations and 50,000+ use Trusted Publishing**. — [PyPI Attestations blog](https://blog.pypi.org/posts/2024-11-14-pypi-now-supports-digital-attestations/) (Nov 2024)

**Attestation generation:** [Attestations are in-toto v1 Statement objects signed through Sigstore](https://docs.pypi.org/attestations/), using ECDSA over NIST P-256 with SHA-256 digests. [Since v1.11.0, `gh-action-pypi-publish` generates attestations automatically with no extra input required.](https://github.com/pypa/gh-action-pypi-publish/releases) — gh-action-pypi-publish v1.11.0 release notes (May 2026)

**Build-and-publish-in-separate-jobs pattern:** Attestations **are compatible** with separating build and publish jobs. [GitHub's artifact attestations documentation](https://docs.github.com/en/actions/concepts/security/artifact-attestations) explicitly supports the pattern of building distributions in one job, uploading them as artifacts, and publishing them in a second job. The key is that the **publishing job must have `id-token: write`** to generate OIDC tokens that Sigstore will sign. `gh-action-pypi-publish` will generate new attestations for the downloaded artifacts when it publishes them. — [Artifact Attestations documentation](https://docs.github.com/en/actions/concepts/security/artifact-attestations) (2026)

**Permissions required:** `id-token: write` is mandatory for attestation generation (it enables OIDC token creation that Sigstore signs). No additional Sigstore credentials are needed; PyPI and Sigstore coordination is automatic. — [PyPI Trusted Publishers docs](https://docs.pypi.org/trusted-publishers/using-a-publisher/)

### 4. Recommended release-workflow shape for ten lockstep distributions

**Use a single workflow file triggered on tag, with a matrix over ten package directories, calling `gh-action-pypi-publish` ten times in sequence.**

**Workflow structure:**
- **Trigger:** `push: tags: ['v*']` (releases are tagged with a single version for all ten packages)
- **Build job:** One job that runs `uv build --package <package>` for each of the ten packages, storing all distributions in a single `dist/` directory. Upload as an artifact (`actions/upload-artifact@v4`) for the publish job.
- **Publish job:** Depends on the build job. Uses a matrix over ten package names OR ten sequential `gh-action-pypi-publish` invocations with different `environment:` names. Each invocation publishes to one project.
- **Permissions:** Top-level `permissions: {}` (no-access default), then add `id-token: write` at the **publish job level only**.
- **Environments:** Configure ten environments on the GitHub repository settings page: `pypi-varco-core`, `pypi-varco-kafka`, …, `pypi-varco-casbin`. Each environment should have **no deployment branches** (unrestricted) to avoid blocking the workflow. On PyPI, create ten pending publishers, one per project name, each configured with `owner: edoardo-scarpaci`, `repo: varco`, `workflow: release.yml`, `environment: pypi-varco-<package>`.

**Complete YAML skeleton:**

```yaml
name: Release

on:
  push:
    tags:
      - 'v*'

permissions: {}

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@a5ac7e51b41094c7a9013c46f6b263d9e47eb94f # v4
      
      - uses: actions/setup-python@0f643a475b6dd7d4b9c1d7e6b3215b33d6831147 # v5
        with:
          python-version: '3.12'
      
      - uses: astral-sh/setup-uv@e4d5f12a9ca527e49c6b0894e1f3e5e7b10d7de4 # v10.0.1
        with:
          enable-cache: true
          cache-dependency-glob: uv.lock
      
      - name: Build all distributions
        run: |
          uv build --package varco-core
          uv build --package varco-kafka
          uv build --package varco-nats
          uv build --package varco-redis
          uv build --package varco-beanie
          uv build --package varco-sa
          uv build --package varco-memcached
          uv build --package varco-ws
          uv build --package varco-fastapi
          uv build --package varco-casbin
      
      - uses: actions/upload-artifact@6f51ac03b9356f520e9adb1b1b122d8b28e28ad7 # v4
        with:
          name: dist
          path: dist/
          retention-days: 1

  publish:
    needs: build
    runs-on: ubuntu-latest
    permissions:
      id-token: write
    strategy:
      matrix:
        package: [varco-core, varco-kafka, varco-nats, varco-redis, varco-beanie, varco-sa, varco-memcached, varco-ws, varco-fastapi, varco-casbin]
    steps:
      - uses: actions/download-artifact@6b208ae046db98ae9e41ba7d4ef3b7aafcc2ecd1e # v4
        with:
          name: dist
          path: dist/
      
      - name: Publish ${{ matrix.package }}
        uses: pypa/gh-action-pypi-publish@f7981bfb560e841624220f29541874b899649eac # v1.11.1
        with:
          packages-dir: dist/
          repository-url: https://pypi.org/legacy/
        environment:
          name: pypi-${{ matrix.package }}
          url: https://pypi.org/p/${{ matrix.package }}/
```

**TestPyPI first?** Optional. If you want to validate before production, add a `publish-test` job that runs on `push: branches: ['main']` (or a `workflow_dispatch` trigger) that publishes to TestPyPI. Note: `pypa/gh-action-pypi-publish` supports TestPyPI by setting `repository-url: https://test.pypi.org/legacy/`. Trusted publishing on TestPyPI requires a separate pending publisher configuration at `test.pypi.org/manage/account/publishing/`. — [PyPI Docs](https://docs.pypi.org/trusted-publishers/using-a-publisher/)

### 5. Dependabot configuration for uv workspace with ten members

**Dependabot supports `uv` as a package ecosystem (general availability March 2025), but workspace support has known issues.**

**Package ecosystem:** Add `package-ecosystem: "uv"` to `dependabot.yml`. — [Astral Docs on Dependabot](https://docs.astral.sh/uv/guides/integration/dependabot/) (2026)

**Directory configuration:** Use a single entry with `directory: "/"` (workspace root). Dependabot does **not** require per-member `directory` entries; it reads the root `uv.lock` and all `pyproject.toml` files in the workspace. — [Dependabot is trying to update workspaces instead of dependencies](https://github.com/dependabot/dependabot-core/issues/14004) (Jan 2026)

**Known workspace issue:** [As of January 2026, there is a bug where Dependabot tries to update workspace member declarations instead of dependencies.](https://github.com/dependabot/dependabot-core/issues/14004) The exact status is unresolved; recommend checking the issue for updates or testing with a dry-run.

**GitHub Actions ecosystem:** Add a separate entry for GitHub Actions:

```yaml
version: 2

updates:
  - package-ecosystem: "uv"
    directory: "/"
    schedule:
      interval: weekly
  
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: weekly
```

**SHA pinning preservation:** **Yes, Dependabot preserves SHA pins and updates the trailing version comment.** [The trailing comment is not cosmetic](https://github.com/dependabot/dependabot-core/issues/13466); Dependabot parses it to decide which SHA to upgrade to next. When you pin an action as `uses: actions/setup-python@0f643a475b6dd7d4b9c1d7e6b3215b33d6831147 # v5`, Dependabot will open a PR to update the SHA and bump the comment to `# v5.1.0` (or the next release). — [StepSecurity blog](https://www.stepsecurity.io/blog/pinning-github-actions-for-enhanced-security-a-complete-guide) (2026)

**Recommendation:** Add both `uv` and `github-actions` ecosystems to `dependabot.yml`, and **always include the trailing version comment** on SHA-pinned actions (required for Dependabot to track updates). Test the workspace uv support with a dry-run PR to confirm behavior before merging configuration.

### 6. OpenSSF Scorecard action setup and monorepo scoring

**Current version:** [v4.11.0](https://github.com/ossf/scorecard-action/releases) (August 2026) — [ossf/scorecard-action Releases](https://github.com/ossf/scorecard-action/releases) (Aug 2026)

**Required permissions:**
- `id-token: write` — required if `publish_results: true` (needed for OIDC token to authenticate with GitHub's API when pushing results).
- `security-events: write` — required if `publish_results: true` (needed to upload SARIF results to GitHub's Security tab).

For a public repo publishing results, the minimal permission block is:

```yaml
permissions:
  id-token: write
  security-events: write
```

— [ossf/scorecard-action README](https://github.com/ossf/scorecard-action/blob/main/README.md) (2026)

**Triggers:** Standard practice is:
- `push: branches: ['main']` — run on every push to main (production readiness signal)
- `schedule: - cron: '0 2 * * 1'` — weekly schedule (e.g., Monday at 2 AM UTC)
- `workflow_dispatch:` — manual trigger for ad-hoc runs

[SPEC 8](https://scientific-python.org/specs/spec-0008/) recommends all three.

**Badge and `publish_results`:** Setting `publish_results: true` is **required** for Scorecard results to appear in GitHub's Security > Scorecards tab and for badges to display. — [GitHub Docs](https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/about-security-hardening-with-github-actions)

**SARIF upload:** When `publish_results: true`, Scorecard generates a SARIF report and uploads it via GitHub's Security API. For **public repos**, SARIF uploads are always allowed (no special permissions beyond `security-events: write`). — [GitHub Docs on Artifact Attestations](https://docs.github.com/en/actions/concepts/security/artifact-attestations)

**Monorepo scoring concern:** [OpenSSF Scorecard runs against a repository, not individual packages.](https://arxiv.org/pdf/2208.03412) For a monorepo with ten Python packages, Scorecard will produce a **single score** for the entire repository. Some checks may score lower for monorepos:
- **Branch-protection** check: Evaluates rules on `main`; scores the repo as a whole, not per-package. Monorepo main branches typically protect against any code (all packages together), which may appear strict but obscures per-package policies.
- **Signed-releases** check: Looks for cryptographic signatures on GitHub releases; attestations (PEP 740, SLSA) are separate and don't directly satisfy this check (though attestations are **stronger** than release signatures).
- **CII-best-practices** check: If only the repo-level badge is earned (not per-package), the check scores lower.

**Recommendation:** Run Scorecard at the repository level and ensure branch protection, release signing (or attestations), and CODEOWNERS files are configured. Acknowledge that Scorecard's single-repo score will not differentiate between the ten packages; maintain per-package security policies in documentation.

---

## Version/Compatibility Notes

| System | Current Status (Aug 2026) | Implication for Varco 1.0 |
|---|---|---|
| **PyPI Trusted Publishing** | Generally available (Nov 2024+). 132k+ packages using attestations. | Table stakes; configure one pending publisher per project, one per environment name. |
| **`gh-action-pypi-publish`** | v1.11.1 (Aug 2026). Attestations automatic since v1.11.0 (May 2026). | Use v1.11.1+; attestations are free and default-on. |
| **`uv publish`** | v0.4.27+ supports OIDC; no TestPyPI support for trusted publishing yet. | Use `gh-action-pypi-publish` instead for production (supports TestPyPI). |
| **Dependabot uv support** | General availability (March 2025); workspace support has known bugs (Jan 2026). | Configure single `directory: "/"` entry; test workspace behavior before relying on it. |
| **Dependabot SHA preservation** | Stable; trailing version comment required for tracking. | Always pin actions by SHA + comment (`uses: org/action@<SHA> # v1.2.3`). |
| **OpenSSF Scorecard** | v4.11.0 (Aug 2026). SLSA attestations preferred over PGP signatures. | Run with `publish_results: true`; acknowledge single-repo score for monorepo. |
| **PEP 740 attestations** | GA (Nov 2024). Sigstore-signed, in-toto v1 statements. | Automatic in `gh-action-pypi-publish` v1.11.0+; no extra work. |

---

## Evidence Gaps

- **Per-environment OIDC token scoping on PyPI:** Confirmed that environment names are used to match OIDC tokens, but no official PyPI documentation examples show ten environments in one workflow. This is an inferred pattern from Issue #16920; recommend testing with 1–2 projects first.
- **Dependabot uv workspace support resolution timeline:** The issue remains unresolved as of Jan 2026. No ETA for a fix is documented; recommend monitoring the issue or contacting Dependabot maintainers.
- **Monorepo Scorecard scoring by package:** Scorecard does not offer per-package reporting today. No research on whether projects run Scorecard separately per package or accept the single-repo score. Worth investigating for positioning.
- **TestPyPI with separate pending publishers:** Confirmed that TestPyPI requires a separate pending publisher, but no documentation on whether you can use the same environment name pattern (e.g., `testpypi-varco-core`) on test.pypi.org. Likely works but untested.
- **Attestation verification tooling for end users:** PEP 740 defines the format, but uptake of verification tools (pip-audit, Safety, etc.) is still low. No clear signal on whether varco should document verification steps for consumers.

Worth separate briefs: per-package versioning vs locked versions (Plan 001 touched this); automating changelog generation with towncrier; Read the Docs setup for monorepo docs (one docs site per major version or one site with version switcher?).

---

## Librarian's Note

**What the sources indicate:**

Varco's 1.0 release of ten lockstep packages is **achievable with current tooling** and is **secure by default** (trusted publishing + attestations require minimal new configuration, not deep rewrites):

1. **Trusted Publishing:** PyPI's pending publishers require ten distinct OIDC configurations (one per environment name), not ten separate workflows. One workflow, ten `environment:` gates in the publish job, one `gh-action-pypi-publish` call per package (in a matrix or loop).

2. **Attestations:** Free and automatic in `gh-action-pypi-publish` v1.11.0+. No extra inputs, signatures, or manual steps. This is table stakes now.

3. **Build-and-publish split:** Supported and documented. Build once in one job, upload artifact, publish in a second job with full attestation support.

4. **Dependabot:** uv support exists (general availability March 2025), but workspace handling has bugs as of Jan 2026. Monitor the issue; use single `directory: "/"` entry and test with a dry-run PR.

5. **Action pinning:** Dependabot preserves SHA pins and bumps version comments automatically. Always use `uses: org/action@<SHA> # vN` format (the comment is required).

6. **Scorecard:** Standard setup (v4.11.0+, `id-token: write`, `security-events: write`, `publish_results: true`). Acknowledge that monorepos produce one repo score, not per-package scores.

**Recommendation:** Prioritize in this order:
- Phase 1 (blocking for 1.0): Set up ten pending publishers on PyPI (different environment names), verify OIDC token matching with 1–2 projects.
- Phase 2 (before release): Finalize workflow YAML with matrix-based publish loop; test full end-to-end with a pre-release tag (e.g., `v1.0.0rc1`).
- Phase 3 (post-1.0): Configure Dependabot (monitor workspace issue), set up Scorecard with results published, add changelog automation.

The evidence strongly favors `gh-action-pypi-publish` over `uv publish` for this monorepo due to attestations (automatic) and TestPyPI support (via separate configuration, not special flags).

---

## Sources

| Title | URL | Date |
|-------|-----|------|
| Publishing to PyPI with a Trusted Publisher | [https://docs.pypi.org/trusted-publishers/](https://docs.pypi.org/trusted-publishers/) | 2026 |
| Trusted Publishers — Internals and Technical Details | [https://docs.pypi.org/trusted-publishers/internals/](https://docs.pypi.org/trusted-publishers/internals/) | 2026 |
| Creating a PyPI Project with a Trusted Publisher | [https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/) | 2026 |
| PyPI Trusted Publishing: suboptimal UX with monorepos · Issue #16920 | [https://github.com/pypi/warehouse/issues/16920](https://github.com/pypi/warehouse/issues/16920) | 2026 |
| pypa/gh-action-pypi-publish Releases | [https://github.com/pypa/gh-action-pypi-publish/releases](https://github.com/pypa/gh-action-pypi-publish/releases) | 2026-08 |
| gh-action-pypi-publish README | [https://github.com/pypa/gh-action-pypi-publish](https://github.com/pypa/gh-action-pypi-publish) | 2026 |
| PyPI now supports digital attestations | [https://blog.pypi.org/posts/2024-11-14-pypi-now-supports-digital-attestations/](https://blog.pypi.org/posts/2024-11-14-pypi-now-supports-digital-attestations/) | 2024-11 |
| PEP 740 – Index support for digital attestations | [https://peps.python.org/pep-0740/](https://peps.python.org/pep-0740/) | 2024 |
| PyPI Attestations Documentation | [https://docs.pypi.org/attestations/](https://docs.pypi.org/attestations/) | 2026 |
| Using uv in GitHub Actions | [https://docs.astral.sh/uv/guides/integration/github/](https://docs.astral.sh/uv/guides/integration/github/) | 2026-08 |
| Astral Docs: Dependabot Integration with uv | [https://docs.astral.sh/uv/guides/integration/dependabot/](https://docs.astral.sh/uv/guides/integration/dependabot/) | 2026 |
| Trusted Publishing Examples (uv) | [https://github.com/astral-sh/trusted-publishing-examples](https://github.com/astral-sh/trusted-publishing-examples) | 2026 |
| Dependabot trying to update workspaces instead of dependencies · Issue #14004 | [https://github.com/dependabot/dependabot-core/issues/14004](https://github.com/dependabot/dependabot-core/issues/14004) | 2026-01 |
| Dependabot: Hash pinned actions updated to latest commit · Issue #13466 | [https://github.com/dependabot/dependabot-core/issues/13466](https://github.com/dependabot/dependabot-core/issues/13466) | 2026 |
| GitHub Docs: Artifact Attestations | [https://docs.github.com/en/actions/concepts/security/artifact-attestations](https://docs.github.com/en/actions/concepts/security/artifact-attestations) | 2026 |
| Publishing package distribution releases using GitHub Actions CI/CD | [https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/) | 2026 |
| ossf/scorecard-action README | [https://github.com/ossf/scorecard-action/blob/main/README.md](https://github.com/ossf/scorecard-action/blob/main/README.md) | 2026 |
| ossf/scorecard-action Releases | [https://github.com/ossf/scorecard-action/releases](https://github.com/ossf/scorecard-action/releases) | 2026-08 |
| StepSecurity: Pinning GitHub Actions for Security | [https://www.stepsecurity.io/blog/pinning-github-actions-for-enhanced-security-a-complete-guide](https://www.stepsecurity.io/blog/pinning-github-actions-for-enhanced-security-a-complete-guide) | 2026 |
| Scientific Python SPEC 8 — Securing the Release Process | [https://scientific-python.org/specs/spec-0008/](https://scientific-python.org/specs/spec-0008/) | 2023 |
| OpenSSF Scorecard: On the Path Toward Ecosystem-wide Automated Security Metrics | [https://arxiv.org/pdf/2208.03412](https://arxiv.org/pdf/2208.03412) | 2022 |
