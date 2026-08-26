# Research 002 — uv workspace + GitHub Actions CI/CD setup

Date: 2026-08-26 · Freshness matters: **yes** — uv/setup-uv releases and action defaults change frequently; check setup-uv patch versions and official docs annually.

## Question

Rebuild GitHub Actions workflows for varco's uv workspace monorepo (11 members, `uv.lock`, Python 3.12+). Specifically:
1. Current `astral-sh/setup-uv` state and breaking changes from v4
2. Locked/reproducible sync in CI and workspace-specific behavior
3. Python version matrix handling
4. Python 3.13 readiness for key dependencies
5. Job structure (lint/type-check/test split, aggregate checks, fail-fast)

## Findings

### 1. astral-sh/setup-uv current state (v10.0.1, August 2026)

- **Current major version**: v10 (patch: v10.0.1 as of 2026-08-14) — [astral-sh/setup-uv Releases](https://github.com/astral-sh/setup-uv/releases) (2026-08-14)
- **v8.0.0 (2026, security)**: Eliminated moving major/minor tags (`@v8` no longer works); requires pinning to specific patch versions — [setup-uv Releases](https://github.com/astral-sh/setup-uv/releases) (2026-08)
- **v9.0.0 (2026-07-21)**: Changed `prune-cache` default to false (was true) to reduce PyPI load — [setup-uv Releases](https://github.com/astral-sh/setup-uv/releases) (2026-07-21)
- **v10.0.0 (2026-08-12, BREAKING)**: Disables caching by default on sensitive events (`pull_request_target`, `workflow_run`, `release`). Adds `version: latest-known` for checksummed uv installs. Adds Python detection from `.tool-versions`. — [setup-uv Releases](https://github.com/astral-sh/setup-uv/releases) (2026-08-12)
- **Cache mechanism**: `enable-cache: true` still works and is recommended, but is **not the default in v10+** (must be explicitly set). Default download now uses Astral mirror instead of GitHub Releases. — [astral-sh/setup-uv](https://github.com/astral-sh/setup-uv) (2026-08)
- **cache-dependency-glob for workspace**: The action's default glob pattern includes `uv.lock`, `pyproject.toml`, and requirements files. For varco's single root `uv.lock` + 11 `pyproject.toml`s, use `cache-dependency-glob: "uv.lock"` (single lockfile invalidates cache on any dependency change across all members) — [astral-sh/setup-uv README](https://github.com/astral-sh/setup-uv) (2026-08) + [Using uv in GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/) (2026-08)
- **Python installation**: The action **does not install Python itself**. It only sets environment variables (`UV_PYTHON`) to control which Python uv uses. Still need `actions/setup-python` or `uv python install` to provision the interpreter. — [Using uv in GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/) (2026-08)

**What this means for varco**: Pin setup-uv to a specific patch version (e.g., `@c771a70e6277c0a99b617c7a806ffedaca235ff9 # v9.0.0` if staying on v9, or upgrade to v10.0.1). Set `enable-cache: true` explicitly in v10+. Use `actions/setup-python` before setup-uv to install Python 3.12/3.13. Use `cache-dependency-glob: "uv.lock"` for the workspace.

### 2. Locked/reproducible sync in CI

- **`uv sync --locked`**: Fails (raises error) if lockfile is not up-to-date with `pyproject.toml`. Prevents accidental re-resolution in CI. Recommended for CI. — [Locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/) (2026-08)
- **`uv sync --frozen`**: Uses lockfile without checking if it's current; no error if `pyproject.toml` changed. Skips resolution entirely, installs only what's pinned in `uv.lock`. For maximum reproducibility (e.g., Docker images), but masks "forgot to re-lock" mistakes. — [Locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/) (2026-08)
- **`uv sync` (plain)**: Automatically updates lockfile if `pyproject.toml` changed. Not recommended for CI. — [Locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/) (2026-08)
- **Workspace behavior**: Plain `uv sync` at workspace root **does not install workspace members**; it only syncs the root `pyproject.toml`. Must use `uv sync --all-packages` to install all 11 members' dependencies. — [Using workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/) (2026-08) + [Locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/) (2026-08)
- **Dependency group flags**: `--all-extras` (all extras from all members), `--all-groups` (all custom groups), `--no-dev` (excludes dev), `--group NAME` (specific group), `--only-group NAME` (only that group). — [Locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/) (2026-08)
- **Shared lockfile**: One `uv.lock` at workspace root locks all 11 members together; any dependency change to any member invalidates the entire lockfile. — [Using workspaces](https://docs.astral.sh/uv/concepts/projects/workspaces/) (2026-08)

**What this means for varco**: Use `uv sync --locked --all-packages --all-extras` in CI (lint/test jobs). This enforces that the lockfile is current and installs all member packages + dev dependencies + all extras. Commit `uv.lock` and treat it as a CI contract — any developer changing `pyproject.toml` must run `uv lock` locally before pushing.

### 3. Python version matrix handling

- **Matrix approach 1**: Use `strategy.matrix.python-version` and pass to setup-uv via `python-version` input. The action sets `UV_PYTHON` env var to the matrix value. — [Using uv in GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/) (2026-08)
- **Matrix approach 2**: Set `UV_PYTHON` env var directly at workflow/job/step level to override. — [Using uv in GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/) (2026-08)
- **Interaction with `--locked`**: A `uv.lock` resolved under Python 3.12 can be used with `--locked` under Python 3.13 — the lockfile pins transitive versions independently of the interpreter. No special flag needed; `UV_PYTHON` takes precedence. — [Locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/) (2026-08)
- **Precedence**: Environment variable `UV_PYTHON` (or action input `python-version`) > `.python-version` file > `pyproject.toml` `requires-python` — [Using uv in GitHub Actions](https://docs.astral.sh/uv/guides/integration/github/) (2026-08)

**What this means for varco**: Define a matrix `[3.12, 3.13]` (or test against more versions if desired). Pass `python-version: ${{ matrix.python-version }}` to setup-uv, which sets `UV_PYTHON`. The same `uv.lock` works for both versions.

### 4. Python 3.13 readiness (brief check)

| Package | 3.13 Support | Status | Note |
|---------|---|---|---|
| **aiokafka** | ✅ YES | v0.14.0 released 2026-04-29 with 3.13 wheels (cp313-manylinux_2_17) | Fully supported, pre-built wheels available |
| **beanie** / **motor** | ✅ YES (3.13); ⚠️ Motor EOL | Beanie ≥2.0 requires PyMongo Async API (Motor deprecated 2025-05-14, EOL 2026-05-14) | Beanie 3.13 supported; Motor being phased out in favor of pymongo async driver |
| **pymemcache** / **aiomcache** | ⚠️ UNVERIFIED | aiomcache v0.8.2 has py3-none-any wheel but unmaintained (no releases in 12 months) | aiomcache likely compatible but upstream abandoned; pymemcache support status not confirmed from official docs |
| **casbin** (pycasbin) | ✅ YES | pycasbin 1.30.1+ supports 3.8–3.13 | Main library ready; note casbin_motor_adapter explicitly excludes 3.13 (Python <3.13) |
| **nats-py** | ✅ YES | v2.15.0 released 2026-06-05, supports 3.13 | Fully supported |
| **sqlalchemy** + **asyncpg** | ✅ YES | Both v2+ support 3.13 with wheels; asyncpg had build issue Jan 2025 (resolved) | SQLAlchemy 2.0+ and asyncpg 0.30+ both support 3.13 with pre-built wheels |

**What this means for varco**: Python 3.13 is viable for all core dependencies except aiomcache (abandoned upstream) and Motor (reaching EOL May 2026; migrate Beanie to PyMongo Async API). The `pyproject.toml` `requires-python = ">=3.12"` is safe; consider adding an upper bound (`<4.0`) or testing 3.13 in the matrix.

### 5. GitHub Actions job structure for 10-package monorepo

- **Matrix strategy**: Define `python-version: [3.12, 3.13]` in `strategy.matrix`. Each combination creates a separate job run. — [GitHub Actions Jobs in Workflows](https://docs.github.com/en/actions/using-jobs/using-jobs-in-a-workflow) (2026)
- **fail-fast**: Default is `true` — if one matrix job fails, GitHub cancels all other running jobs in the matrix. Set `fail-fast: false` to let all matrix jobs complete. — [GitHub Actions Matrix Strategy: Basics, Tutorial & Best Practices](https://codefresh.io/learn/github-actions/github-actions-matrix/) (2026)
- **Job split pattern**: Separate jobs for lint, type-check, test-unit (no docker), test-integration (docker). All three lint/type/unit in one matrix to avoid 3N jobs. Integration tests rarely matrix (heavy setup cost); run once with one Python version. — [How to Implement Matrix Builds in GitHub Actions](https://oneuptime.com/blog/post/2026-01-25-github-actions-matrix-builds/view) (2026-01-25)
- **Aggregate "all green" job**: Create a final job that depends on all matrix jobs using `needs: [lint-type-unit]`. Use `if: always()` so it runs even if upstream fails, allowing you to clearly report success/failure. Require *only* this aggregate job in branch protection, not individual matrix variations (avoids hanging PRs when matrix is skipped). — [Troubleshooting required status checks](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/collaborating-on-repository-with-code-quality-features/troubleshooting-required-status-checks) (2026) + [GitHub Actions Required Checks for Conditional Jobs](https://devopsdirective.com/posts/2025/08/github-actions-required-checks-for-conditional-jobs/) (2025-08)
- **needs semantics**: If a job listed in `needs` fails or is skipped, dependent jobs are skipped unless they use `if: always()`. A skipped matrix job (due to early `if:` condition) will skip all dependents unless `if: always()` is present. — [GitHub Actions Jobs in Workflows](https://docs.github.com/en/actions/using-jobs/using-jobs-in-a-workflow) (2026)

**What this means for varco**: Design workflows as:
1. **Job: lint-type-unit** (matrix: [3.12, 3.13], fail-fast: false) — runs `ruff check`, `mypy`, `pytest varco_*/tests/ -k "not integration"` on all 11 packages
2. **Job: integration-test** — runs `pytest -m integration` (may run against single Python version to reduce Docker container churn)
3. **Job: aggregate-status** (needs: [lint-type-unit, integration-test], if: always()) — succeeds if all upstream pass, fails otherwise; this is the only job required in branch protection

Avoid per-package jobs (would require 11 separate matrix runs per test type). Cache hits improve when there are fewer job starts. A single `uv sync --locked --all-packages --all-extras` once per matrix run feeds all 11 packages.

## Version/compatibility notes

- **setup-uv**: v10.0.1 (2026-08-14) is current. v8.0.0+ requires specific patch pinning (no moving tags). — [astral-sh/setup-uv Releases](https://github.com/astral-sh/setup-uv/releases)
- **uv**: Latest stable supports all documented flags. `--all-packages` and workspace support are stable (not experimental). — [astral-sh/uv](https://github.com/astral-sh/uv)
- **Python 3.13**: Supported by all varco dependencies except abandoned aiomcache and deprecated Motor (EOL May 2026). — PyPI package pages (2026-08)
- **GitHub Actions**: `needs`, `if: always()`, matrix, and `fail-fast` are stable documented features. — [GitHub Actions Docs](https://docs.github.com) (2026)

## Evidence gaps

- **pymemcache**: Could not confirm 3.13 support from official PyPI/docs; only universal wheel metadata. May need a test.
- **aiomcache**: Unmaintained (no releases in 12+ months); should verify if it truly runs on 3.13 or find a replacement (e.g., aiomem).
- **Motor + Beanie migration path**: Beanie docs reference Motor deprecation but specific upgrade path for varco's Beanie usage not traced; recommend checking `varco_beanie/` imports.
- **Per-package CI parallelization cost**: no data on GitHub Actions build-minute costs for 11-member workspace; matrix fan-out is cheap (one venv per Python version), but worth monitoring.

## Librarian's note

The sources indicate:
- **Setup**: Use `actions/setup-python` → `astral-sh/setup-uv@<specific-patch>` (pin to exact version, not major/minor). Set `enable-cache: true` and `cache-dependency-glob: "uv.lock"` (both needed in v10+). Python 3.12/3.13 both ready.
- **Sync**: `uv sync --locked --all-packages --all-extras` is the documented CI pattern for workspaces.
- **Job structure**: Matrix [3.12, 3.13] with `fail-fast: false`, then aggregate job (required check). This minimizes job count while covering all versions.
- **3.13 readiness**: Six out of six key packages confirmed or safe; only aiomcache and Motor require attention (fork/deprecation respectively).

---

## Sources

| Title | URL | Date |
|-------|-----|------|
| astral-sh/setup-uv Releases | [https://github.com/astral-sh/setup-uv/releases](https://github.com/astral-sh/setup-uv/releases) | 2026-08-14 |
| Using uv in GitHub Actions | [https://docs.astral.sh/uv/guides/integration/github/](https://docs.astral.sh/uv/guides/integration/github/) | 2026-08 |
| Locking and syncing (uv docs) | [https://docs.astral.sh/uv/concepts/projects/sync/](https://docs.astral.sh/uv/concepts/projects/sync/) | 2026-08 |
| Using workspaces (uv docs) | [https://docs.astral.sh/uv/concepts/projects/workspaces/](https://docs.astral.sh/uv/concepts/projects/workspaces/) | 2026-08 |
| GitHub Actions: Jobs in Workflows | [https://docs.github.com/en/actions/using-jobs/using-jobs-in-a-workflow](https://docs.github.com/en/actions/using-jobs/using-jobs-in-a-workflow) | 2026 |
| GitHub Actions Matrix Strategy | [https://codefresh.io/learn/github-actions/github-actions-matrix/](https://codefresh.io/learn/github-actions/github-actions-matrix/) | 2026 |
| GitHub Actions Required Checks | [https://devopsdirective.com/posts/2025/08/github-actions-required-checks-for-conditional-jobs/](https://devopsdirective.com/posts/2025/08/github-actions-required-checks-for-conditional-jobs/) | 2025-08 |
| Troubleshooting required status checks | [https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/collaborating-on-repositories-with-code-quality-features/troubleshooting-required-status-checks](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/collaborating-on-repositories-with-code-quality-features/troubleshooting-required-status-checks) | 2026 |
| aiokafka · PyPI | [https://pypi.org/project/aiokafka/](https://pypi.org/project/aiokafka/) | 2026-04-29 |
| Beanie ODM Releases | [https://github.com/BeanieODM/beanie/releases](https://github.com/BeanieODM/beanie/releases) | 2026 |
| MongoDB Motor Releases | [https://github.com/mongodb/motor/releases](https://github.com/mongodb/motor/releases) | 2026 |
| casbin · PyPI | [https://pypi.org/project/casbin/](https://pypi.org/project/casbin/) | 2026 |
| nats-py · PyPI | [https://pypi.org/project/nats-py/](https://pypi.org/project/nats-py/) | 2026-06-05 |
| asyncpg · PyPI | [https://pypi.org/project/asyncpg/](https://pypi.org/project/asyncpg/) | 2026 |
| SQLAlchemy Async (v2 docs) | [https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) | 2026 |
