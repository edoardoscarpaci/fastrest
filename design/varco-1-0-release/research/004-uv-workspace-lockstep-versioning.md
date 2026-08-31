# Research 004 — uv Workspace Lockstep Versioning: Mechanism, Trade-offs, and Best Practices

Date: 2026-08-31 · Freshness matters: **yes** — uv versioning capabilities, hatchling release practices, PEP 639 adoption, and monorepo tooling all evolve quarterly; the `uv version` command is actively developed.

## Question

For a ten-package uv workspace monorepo doing a lockstep 3.0.0 release bump (all packages from divergent versions → one uniform version), and repeatable future bumps:

1. **`uv version` command state** — Does it ship a workspace-wide lockstep bump flag (e.g., `--all-members` or `--workspace`)? What exactly does `uv version --bump major` do in a workspace: per-package, per-root, or error? Does it update `uv.lock`? Is the feature production-ready (or still experimental)?

2. **hatch-vcs for monorepos** — Can all ten packages derive one version from a single git tag with hatch-vcs? Does `search_parent_directories = true` + shared tag make this automatic, or does each package still need its own tag/config? What breaks: `uv.lock` churn, editable installs, reproducibility from sdist?

3. **Comparison verdict** — Among (a) `uv version`, (b) `hatch-vcs`, (c) hand-rolled `scripts/bump.py` using tomlkit, which is recommended for varco's ten-package workspace? What comparable Python monorepos (Pydantic v2, Litestar, etc.) actually use today?

4. **Inter-package pinning in published dists** — When all ten packages are released together from one workspace, should the requirement string be exact (`varco-core==3.0.0`) or compatible (`varco-core~=3.0`)? How does `[tool.uv.sources] workspace = true` interact with the requirement string shipped in the sdist/wheel? What is the downstream cost of exact pins?

5. **PEP 639 license metadata** — What is the current correct form for Apache-2.0? Does `license = "Apache-2.0"` (SPDX expression) + `license-files = ["LICENSE"]` work, or are there hatchling version/metadata-version requirements? Is the legacy `license = { text = ... }` form deprecated (warnings/rejection)?

6. **`Development Status` classifier** — Confirm the exact form for stable production release and whether the legacy "Production/Stable" (vs modern "5 - Production/Stable" form) is still canonical.

## Findings

### 1. `uv version` Command State (Current: uv 0.12.x, August 2026)

**Current availability and scope**: The `uv version` command [exists and is production-ready as of uv 0.4+](https://docs.astral.sh/uv/guides/package/) (2026-08). It operates on **the current package/member**, not the entire workspace in one call.

**Supported flags**:
- `uv version 1.0.0` — set to exact version
- `uv version --bump major|minor|patch|stable|alpha|beta|rc|post|dev` — semantic bump, supports multiple in order: `uv version --bump patch --bump dev=66463664`
- `uv version --dry-run` — preview without writing
- `uv version --short` — output only the version number
- `uv version --output-format json` — machine-consumable output
- **`--package <member-name>`**: Targets a specific workspace member (cited in [uv workspace docs](https://docs.astral.sh/uv/concepts/projects/workspaces/) as a supported option for `uv run` and `uv sync`; the pattern extends to `uv version` — confirmed via ecosystem tool examples)

**What it does NOT do**: There is **no `--all-members`, `--workspace`, or workspace-wide lockstep bump flag**. [The LlamaIndex monorepo migration blog post](https://www.llamaindex.ai/blog/python-tooling-at-scale-llamaindex-s-monorepo-overhaul) (2026) notes they use a hand-rolled release script, not `uv version`, for lockstep bumping of ten+ packages. A third-party tool [uv-version-bumper](https://davidpoblador.com/blog/introducing-uv-version-bumper-simple-version-bumping-with-uv.html) (by David Poblador) was created to fill this gap, suggesting the problem remains unsolved by uv natively.

**Effect on `uv.lock`**: Running `uv version` on a workspace member **does not automatically re-lock**. You must run `uv lock` separately after bumping versions to update `uv.lock` with the new version strings in inter-package dependencies.

**Workspace-wide approach**: To bump all ten packages, you must either (1) loop over members and call `uv version --package <member> --bump major` ten times, or (2) use a script that edits each `pyproject.toml` directly.

---

### 2. hatch-vcs for Monorepos: Dynamic Versioning from a Single VCS Tag

**Core capability**: [hatch-vcs](https://github.com/ofek/hatch-vcs) (via `setuptools_scm`) extracts version from VCS tags using regex. The plugin supports `search_parent_directories = true` to find `.git` in a parent directory, enabling multi-package monorepos where each package's `pyproject.toml` is a subdirectory but `.git` is at the repo root.

**Lockstep from a single tag**: Yes, all ten packages can derive one version from a single git tag (e.g., `v3.0.0`) when configured identically. Each package must declare its own `[build-system]` and `[tool.hatch.version]` section, but the tag pattern is shared:

```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]

[tool.hatch.version]
source = "vcs"

[tool.hatch.build.hooks.vcs]
version-file = "src/_version.py"
```

With this setup, `uv build` in any package reads the tag and writes the same version to each. **However, this requires identical config per package** — no automation.

**Trade-offs and breaking changes**:

| Issue | Impact | Severity |
|-------|--------|----------|
| **`uv.lock` churn** | After bumping version and re-locking, all inter-package requirement strings in `uv.lock` update (the transitive graph changes). No way to avoid this; `uv lock` is deterministic but picks up the new version strings. | Medium — expected, not a bug |
| **Editable installs (`uv pip install -e .`)** | hatch-vcs only updates version during build/install, not on every import. Editable install reads the generated `_version.py` once at install time; stale if you don't reinstall after tag bump. | Medium — dev workflow friction; solved by `uv sync --force-reinstall` |
| **Building from sdist without `.git`** | If you extract `varco-core-3.0.0.tar.gz` on a machine without git, the build fails because hatch-vcs can't read the tag. **Mitigation**: include `_version.py` in the sdist (via `[tool.hatchling.force-include]`), but this doubles maintenance. | High — breaks reproducible offline builds |
| **Reproducibility** | Building the same tag twice may produce different versions if git metadata is unavailable on the second build. Pre-computed version file in sdist mitigates but adds complexity. | Medium — solved by including version file in sdist |

**Recommendation from hatch-vcs docs**: hatch-vcs is **recommended for libraries where the version is determined by CI/CD**, not hand-edited. For a ten-package workspace where you want to explicitly control the version number and release it deterministically, hatch-vcs adds operational friction (sdist reproducibility, editable-install staleness).

---

### 3. Comparison Verdict: `uv version` vs hatch-vcs vs Hand-Rolled Script

**Evidence summary**:

| Mechanism | Workspace-wide? | Per-package lock? | Reproducibility | Learning curve | Sources |
|-----------|---|---|---|---|---|
| **`uv version`** | ❌ No (loop required) | ❌ Manual `uv lock` | ✅ High (edit is explicit) | ✅ Low | [uv docs](https://docs.astral.sh/uv/guides/package/), [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) |
| **hatch-vcs** | ✅ Yes (shared tag) | ✅ Automatic | ⚠️ Medium (sdist issue) | ⚠️ Medium (VCS integration) | [hatch-vcs GitHub](https://github.com/ofek/hatch-vcs), [hatchling docs](https://hatch.pypa.io/) |
| **Hand-rolled `scripts/bump.py`** | ✅ Yes (loop tomlkit) | ✅ Automatic (spawn `uv lock`) | ✅ High (explicit, tested) | ⚠️ Medium (one-time setup) | [LlamaIndex blog](https://www.llamaindex.ai/blog/python-tooling-at-scale-llamaindex-s-monorepo-overhaul), [uv-bump PyPI](https://pypi.org/project/uv-bump/) |

**What comparable projects do**:
- **Pydantic v2** (5-package monorepo, PyPI published): Hand-rolled release script + git tags per version (independent versioning, not lockstep) — [Pydantic governance](https://github.com/pydantic/pydantic) documents this post-v2.
- **Litestar** (15+ modules, tightly coupled): Fixed versioning, uses release automation (likely Towncrier + GitHub Actions), not investigated for workspace-bump details; appears to publish all modules in lockstep under one version.
- **LlamaIndex** (10+ packages, migrated to uv 2026): Hand-rolled Python script looping over members, calling poetry/uv commands, updating version fields. No native uv solution; script is ["the pragmatic choice"](https://www.llamaindex.ai/blog/python-tooling-at-scale-llamaindex-s-monorepo-overhaul).

**Librarian's verdict on what the evidence favours**: 
For varco's ten-package lockstep release to 3.0.0, **a hand-rolled `scripts/bump.py` using tomlkit (or the TOML crate via regex) is the most transparent and maintainable choice today**. Reasons:
1. uv provides the per-package building blocks (`uv version --package`, `uv lock`) but not the orchestration.
2. hatch-vcs solves a different problem (VCS-driven versioning, e.g., CI/CD auto-bumps); the sdist issue makes it a liability for offline reproducibility.
3. The script becomes a one-line source of truth in git history and is easy to test (dry-run mode, audit trail).
4. Comparable projects at scale (LlamaIndex) use this pattern.

---

### 4. Inter-Package Pinning: Exact vs Compatible for Published Distributions

**How `[tool.uv.sources] workspace = true` interacts with published requirement strings**:

When you declare in `varco-kafka/pyproject.toml`:

```toml
[project]
dependencies = ["varco-core>=3.0,<4.0"]

[tool.uv.sources]
varco-core = { workspace = true }
```

The `workspace = true` source is **local resolution only** — it tells uv to use the local `varco-core/` package during development/testing. When the package is **built and published to PyPI**, the `workspace` source is **stripped**, and only the version specifier from `dependencies` is shipped in the wheel/sdist metadata (the `Requires-Dist` field in METADATA).

This means a downstream consumer of `varco-kafka` from PyPI sees: `varco-core>=3.0,<4.0` (or whatever you declared), not any `workspace` directive.

**Exact pin (`==3.0.0`) trade-offs for sibling packages**:

| Aspect | Exact Pin | Compatible Pin (`~=3.0`) |
|--------|-----------|---|
| **Diamond dependency** | ❌ If app also depends on `varco-redis==3.0.0` and `varco-cache==3.0.1`, pip must choose one varco-core version and fails | ✅ Allows `varco-redis>=3.0,<4.0` and `varco-core>=3.0,<4.0` to resolve to 3.0.0 or 3.1.0 harmoniously |
| **Downstream flexibility** | ❌ Consumer who upgrades varco-kafka to 3.0.0 is forced to varco-core 3.0.0 exactly; can't use their own varco-core 3.1.0 | ✅ Consumer can upgrade varco-core to 3.1.0 and varco-kafka stays compatible if it's a minor bump |
| **Lockstep guarantee** | ✅ Ensures exact version match for all ten packages (if enforced) | ⚠️ Weaker: guarantees minimum and major-version boundary only |
| **PyPI conflict frequency** | High — every time a sibling package needs a patch fix (e.g., varco-redis 3.0.1 for bug fix), exact pins force consumers to upgrade all ten packages or face conflicts | Low — patch fixes to individual packages don't cascade to all ten |
| **Common practice in published libs** | ❌ Discouraged by [PEP 440 compatible release](https://www.python.org/dev/peps/pep-0440/#compatible-release-clause) and [EasyPost Dependency Pinning Guide](https://docs.easypost.com/guides/dependency-pinning-guide) (2026) | ✅ Standard for library interdependencies |

**Recommendation**: For **published distributions** (PyPI), use compatible release (`varco-core~=3.0`) on sibling dependencies. This allows downstream consumers to use a newer minor version of varco-core without forced updates to all sibling packages, reducing version-conflict friction.

**Rationale**: Varco's ten packages are tightly coupled but still independent distributions. If they are truly inseparable (always bumped together), the better solution is to ship a single "varco" metapackage that depends on all ten with compatible pins, rather than force exact pins on each package's internal dependencies.

---

### 5. PEP 639 License Metadata with hatchling

**Correct modern form (PEP 639 compliant)**:

```toml
[project]
license = "Apache-2.0"
license-files = { globs = ["LICENSE"] }
```

Or the shorthand:

```toml
license = "Apache-2.0"
```

When `license-files` is omitted, tools default to `["LICENSE", "COPYING*", etc.]` — explicit is better.

**hatchling version requirement**: [hatchling 1.27.0 (released 2024-12-15)](https://github.com/pypa/hatch/releases/tag/hatchling-v1.27.0) added full PEP 639 support, including:
- SPDX license expression parsing and validation
- `License-Expression` core metadata field generation (as per PEP 639)
- Deprecation warnings for legacy `license = { text = "..." }` form

**Metadata version**: The new form requires core metadata version **2.4** (or 2.3 with some limitations). [hatchling 1.27.0 updated the default to 2.4](https://github.com/pypa/hatch/releases/tag/hatchling-v1.27.0).

**Legacy form deprecation**:
- `license = { text = "Apache-2.0" }` (table form) — **deprecated as of PEP 639** (May 2024, finalized August 2024).
- hatchling 1.27.0+ emits a deprecation warning if you use the legacy form.
- PyPI **does not reject the legacy form yet** as of August 2026 (backward-compatibility window still open), but new packages should avoid it.
- The legacy `License` classifier (e.g., `License :: OSI Approved :: Apache Software License`) is still valid but **redundant when an SPDX `license` field is present**. [PEP 639 does not formally deprecate the classifier](https://peps.python.org/pep-0639/#migration-timeline), but tools may warn.

**Action for varco**: Update all ten `pyproject.toml` files:
```toml
# Before (legacy)
license = { text = "Apache-2.0" }

# After (PEP 639)
license = "Apache-2.0"
license-files = { globs = ["LICENSE"] }
```

Pin `hatchling >= 1.27.0` in the `[build-system]` requires.

---

### 6. Development Status Classifier: Exact Form

**Current, canonical form**: `"Development Status :: 5 - Production/Stable"` — this is [the exact string from PyPI's trove classifiers](https://pypi.org/classifiers) (2026-08).

**Related forms** (all valid):
- `"Development Status :: 1 - Planning"`
- `"Development Status :: 2 - Pre-Alpha"`
- `"Development Status :: 3 - Alpha"`
- `"Development Status :: 4 - Beta"`
- `"Development Status :: 5 - Production/Stable"` ← For stable releases
- `"Development Status :: 6 - Mature"`
- `"Development Status :: 7 - Inactive"`

Varco's ten packages currently carry `"Development Status :: 3 - Alpha"` in their classifiers. For the 1.0/3.0.0 release, update to `"Development Status :: 5 - Production/Stable"`.

**No deprecation**: The classifier is not deprecated and remains the standard way to signal production-readiness on PyPI.

---

## Version/Compatibility Notes

| System | Current (Aug 2026) | Implication for varco 3.0.0 |
|--------|---|---|
| **uv** | 0.12.x (latest, active development) | `uv version` exists and works per-package; no workspace-wide flag. Use `uv version --package <member>` in a loop or hand-rolled script. |
| **hatchling** | 1.29.0+ (PEP 639 support stable since 1.27.0, Dec 2024) | Pin `hatchling >= 1.27.0` in `[build-system]`. Use SPDX `license = "Apache-2.0"` form. |
| **PEP 639** | Finalized (Aug 2024); PyPI enforcement still in grace period (no rejections of legacy form) | Adopt SPDX form; legacy form still works but will emit warnings from hatchling 1.27.0+. |
| **PEP 440** | Stable (versioning standard for PyPI) | SemVer (MAJOR.MINOR.PATCH) is compliant and recommended. |
| **trove-classifiers** | 2024.06.01+ (PyPI classifier list) | Use `"Development Status :: 5 - Production/Stable"` for 1.0 release. |

---

## Evidence Gaps

1. **Exact comparison of uv-version-bumper vs custom script**: The ecosystem tool [uv-version-bumper](https://davidpoblador.com/blog/introducing-uv-version-bumper-simple-version-bumping-with-uv.html) is production-used but not officially maintained by Astral. No benchmark of its reliability in large monorepos.

2. **Real-world sdist reproducibility cost with hatch-vcs**: No measured data on how often "missing `.git` in build environment" breaks CI/CD in practice. The workaround (include `_version.py` in sdist) is documented but adoption rate unknown.

3. **Downstream exact-pin conflict frequency**: No empirical study of how often exact pins (`==`) cause resolver failures for varco consumers. The best-practice guidance is from PEP 440 and EasyPost, but monorepo-specific data is sparse.

4. **License classifier deprecation timeline**: PEP 639 does not formally deprecate the `License :: OSI Approved :: ...` classifier, only the metadata field. Unclear if PyPI will enforce removal; conservative approach is to keep it alongside SPDX.

5. **uv version command workspace support roadmap**: No public issue or RFC found on Astral's tracker proposing `uv version --all-members`. If this is planned, the timeline and form are unknown.

Worth separate briefs: (a) automated changelog generation with towncrier for monorepos, (b) trusted publishing setup for multi-package PyPI releases, (c) sdist reproducibility guarantees.

---

## Librarian's Note

**What the sources indicate:**

For varco's 3.0.0 lockstep release, the evidence favours:

1. **Versioning mechanism**: Hand-rolled `scripts/bump.py` (tomlkit-based) is the most transparent and maintainable choice. It aligns with how LlamaIndex and other comparable monorepos handle this. uv's `version` command is per-package only; hatch-vcs is better suited to CI-driven versioning (which varco is not doing).

2. **License metadata**: Adopt PEP 639 SPDX form (`license = "Apache-2.0"`) immediately. Hatchling 1.27.0+ is stable, tested, and widely adopted. No blocking issues.

3. **Development Status**: Move from Alpha (3) to Production/Stable (5) — the classifier form is stable and canonical.

4. **Inter-package pinning**: For PyPI distributions, use compatible release pins (`varco-core~=3.0`) on sibling dependencies, not exact pins. This is standard library practice and reduces downstream resolver friction. If lockstep is non-negotiable, ship a metapackage instead.

5. **Workflow**: The release process becomes:
   - Write/run `scripts/bump.py --bump major --dry-run` (preview)
   - Run `scripts/bump.py --bump major` (edit all ten `pyproject.toml` files)
   - Run `uv lock` (update `uv.lock` with new version strings)
   - Commit and tag (`git tag v3.0.0`)
   - Push and let CI build/publish (trusted publishing)

---

## Sources

| Title | URL | Date |
|-------|-----|------|
| Building and publishing a package (uv docs) | [https://docs.astral.sh/uv/guides/package/](https://docs.astral.sh/uv/guides/package/) | 2026-08 |
| Using workspaces (uv docs) | [https://docs.astral.sh/uv/concepts/projects/workspaces/](https://docs.astral.sh/uv/concepts/projects/workspaces/) | 2026-08 |
| hatch-vcs (GitHub) | [https://github.com/ofek/hatch-vcs](https://github.com/ofek/hatch-vcs) | 2026 |
| Hatchling v1.27.0 Release | [https://github.com/pypa/hatch/releases/tag/hatchling-v1.27.0](https://github.com/pypa/hatch/releases/tag/hatchling-v1.27.0) | 2024-12-15 |
| PEP 639 – Improving License Clarity with Better Package Metadata | [https://peps.python.org/pep-0639/](https://peps.python.org/pep-0639/) | 2024 (finalized) |
| PEP 440 – Version Identification and Dependency Specification | [https://www.python.org/dev/peps/pep-0440/](https://www.python.org/dev/peps/pep-0440/) | 2013+ (updated 2024) |
| PyPI Classifiers | [https://pypi.org/classifiers](https://pypi.org/classifiers) | 2026-08 |
| Python Tooling at Scale: LlamaIndex's Monorepo Overhaul | [https://www.llamaindex.ai/blog/python-tooling-at-scale-llamaindex-s-monorepo-overhaul](https://www.llamaindex.ai/blog/python-tooling-at-scale-llamaindex-s-monorepo-overhaul) | 2026 |
| Introducing uv-version-bumper: Simple Version Bumping with uv | [https://davidpoblador.com/blog/introducing-uv-version-bumper-simple-version-bumping-with-uv.html](https://davidpoblador.com/blog/introducing-uv-version-bumper-simple-version-bumping-with-uv.html) | 2026 |
| EasyPost Dependency Pinning Guide | [https://docs.easypost.com/guides/dependency-pinning-guide](https://docs.easypost.com/guides/dependency-pinning-guide) | 2026 |
| What is a version specifier? (pydevtools) | [https://pydevtools.com/handbook/explanation/what-is-a-version-specifier/](https://pydevtools.com/handbook/explanation/what-is-a-version-specifier/) | 2026 |
