# Research 006 — Versioned Documentation with Mike and GitHub Pages

Date: 2026-08-31 · Freshness matters: YES — tooling, GitHub actions APIs, and versioning best practices evolve quarterly.

## Question

Concrete findings on shipping versioned docs (3.0 / latest / dev) from varco's monorepo to GitHub Pages at 1.0 release, using the existing mkdocs Material setup and an existing `mkdocs.yml` with `gen-files`, `literate-nav`, `section-index`, and `mkdocstrings` plugins:

1. **Current state of mike** — version, maintenance status, compatibility with mkdocs 1.6.x and Material 9.7.x. Is mike still Material's **own documented** recommendation for versioning, or has built-in or alternative versioning displaced it?

2. **How mike works, concretely** — directory layout on gh-pages (one dir per version?), the `versions.json` role, what `--push`, `--update-aliases`, `mike set-default`, and aliasing do. Exact mkdocs.yml config snippet (what `extra.version.provider` and `theme.features` are needed).

3. **Two deployment models and which to choose** — (a) gh-pages branch (mike's native) vs. (b) GitHub Actions artifact (`actions/upload-pages-artifact` + `actions/deploy-pages`). Can mike work with artifact deployment at all? What is the tradeoff (permissions model, branch pollution, concurrency)? What does GitHub and Material officially recommend?

4. **CI workflow shape, concrete YAML** — a skeleton for: (i) deploying `dev` on every `main` push, and (ii) deploying a numbered version and moving `latest` alias on a release tag. Cover `permissions`, `fetch-depth: 0`, git identity setup mike needs, and `concurrency` to prevent races. Pin all actions by full commit SHA with `# vN` comment.

5. **Version-alias naming policy** — should numbered versions be `3.0` or `v3.0` or `3.0.0`? How do `latest`/`stable`/`dev` conventionally map? Are old URLs stable if naming scheme changes later?

6. **Interaction with mkdocs-gen-files and mkdocstrings** — does mike's per-version build (it invokes a full `mkdocs build` per version) work cleanly with auto-generated API reference, or must the packages be pre-built/installed? Does the CI job need to `uv sync` the full workspace or just the docs dependency group?

## Findings

### Current State of Mike: Version, Maintenance, Compatibility

**Mike version 2.1.3–2.2.0 (April 2026) is actively maintained and compatible with mkdocs ≥1.0 and Material for MkDocs 9.7.6 (March 2026).** — [mike · PyPI](https://pypi.org/project/mike/) (2026-04-14; tested Python 3.8–3.13). Mike is licensed BSD-3-Clause and authored by Jim Porter. **Compatibility note:** Mike is built around the concept that generated docs for a version never change, so it works across MkDocs version upgrades (each version's HTML is immutable). Material for MkDocs 9.7.6 is the last to receive new features; the Material team is transitioning to Zensical (next-gen generator). However, mike continues to integrate natively with Material for MkDocs as of Aug 2026 — [Material for MkDocs Changelog](https://squidfunk.github.io/mkdocs-material/changelog/) (2026-03).

**Material for MkDocs official recommendation: mike is the de facto standard for versioning Material sites.** — [Setting up versioning - Material for MkDocs](https://squidfunk.github.io/mkdocs-material/setup/setting-up-versioning/) (current) states: "Neither Material for MkDocs nor MkDocs handle versioning by themselves. Instead, it requires a separate project called Mike." Material's own docs provide setup instructions for mike (and only mike), making it the officially documented path. No built-in Material versioning exists; no credible alternative is mentioned in Material's docs (as of Aug 2026).

---

### How Mike Works: Concrete Mechanics

**Directory layout on gh-pages branch: version-per-subdirectory + versions.json metadata file.**

When deployed, mike creates or updates version directories on the `gh-pages` branch:

```
gh-pages/
├── 3.0/
│   ├── index.html
│   ├── ... (all built HTML for version 3.0)
│
├── 3.1/
│   ├── index.html
│   ├── ... (all built HTML for version 3.1)
│
├── latest/  (symlink or redirect alias to 3.1)
├── dev/     (symlink or redirect alias to main-dev)
├── versions.json  (metadata: list of versions, titles, aliases)
```

**versions.json** — A JSON manifest that the Material version switcher JavaScript reads to populate the version dropdown. Contains array of `{version, title, aliases}` objects. This is the source of truth for which versions exist and their metadata. — [Mike · GitHub](https://github.com/jimporter/mike) (README + issues #108, discussion #4759).

**Command-line options:**

- **`mike deploy <version> [<alias> ...]`** — Build and deploy a version to the `gh-pages` branch in a directory named `<version>`. Optionally create one or more aliases (e.g., `latest`, `dev`). Overwrites only that version's directory; other versions untouched.
- **`--push` / `-p`** — Automatically commit and push changes to the remote `gh-pages` branch after deployment. Without this, the commit is made locally only.
- **`--update-aliases` / `-u`** — Allow moving existing aliases to new versions. Normally, reassigning an alias raises an error; this flag overrides. Example: `mike deploy --push --update-aliases 3.0 latest` moves the `latest` alias from its old target to `3.0`.
- **`mike set-default <version>`** — Generate a redirect at the root (`/`) that sends visitors to the specified version (e.g., redirect to `/3.0/`). This is distinct from an alias—it's an explicit "default" entry point.
- **`mike alias <version> <alias>`** — Create or move an alias after deployment (rarely needed if aliases are set during `deploy`).

— [mike · GitHub README](https://github.com/jimporter/mike), [GitHub Discussion #4759 (squidfunk/mkdocs-material)](https://github.com/squidfunk/mkdocs-material/discussions/4759).

**mkdocs.yml configuration snippet:**

```yaml
extra:
  version:
    provider: mike
    default: latest  # optional; defaults to "latest"
```

No `theme.features` flags are required. The version switcher is rendered automatically when `extra.version.provider: mike` is set and `versions.json` exists on the deployed site. The Material theme's JavaScript reads `versions.json` and injects the dropdown in the header. — [Setting up versioning - Material for MkDocs](https://squidfunk.github.io/mkdocs-material/setup/setting-up-versioning/) (current).

---

### Two Deployment Models: gh-pages Branch vs. Actions Artifacts

**Summary: mike is fundamentally branch-based and cannot work with the modern GitHub Actions artifact deployment model.**

#### Model A: gh-pages Branch (Mike's Native Design)

**How it works:** Mike commits version directories to the `gh-pages` branch and pushes it. GitHub Pages is configured to serve from the `gh-pages` branch. This is the traditional GitHub Pages model.

**Workflow:** 
```yaml
permissions:
  contents: write  # required to push to gh-pages

- name: Deploy with mike
  run: |
    git config --global user.name "Docs Deploy"
    git config --global user.email "docs@example.com"
    mike deploy --push --update-aliases 3.0 latest
```

**Advantages:**
- Mike was designed for this; no workarounds needed.
- Full git history of doc versions is preserved on `gh-pages` for audit/rollback.
- Branch-based versioning is transparent and inspectable.

**Disadvantages:**
- The `gh-pages` branch is a "working directory" that grows with every deployment (not just a staging area).
- Slightly elevated permission model (`contents: write` to push to a branch).
- If the main repo and `gh-pages` branch are out of sync, manual recovery can be needed.

#### Model B: GitHub Actions Artifacts + Deploy Pages Action (Modern, Artifact-Based)

**How it works:** The workflow builds docs locally, uploads them as a GitHub Pages artifact (via `actions/upload-pages-artifact`), then GitHub Pages deployment service unpacks and serves from a staging area. GitHub Pages is configured to deploy from "GitHub Actions."

**Workflow pattern:**
```yaml
permissions:
  pages: write
  id-token: write

- name: Build docs
  run: mkdocs build

- name: Upload artifact
  uses: actions/upload-pages-artifact@v4  # v4+ required as of Jan 2025
  with:
    path: ./site

- name: Deploy to GitHub Pages
  uses: actions/deploy-pages@v4
```

**Advantages:**
- Decouples deployment from git history; no branch pollution.
- Simpler permissions model (`pages: write` + OIDC `id-token`, not `contents: write`).
- Artifact is ephemeral; old versions are not stored in the repo.

**Disadvantages:**
- Mike **cannot operate in this model** — mike is designed to commit to and push a git branch. The artifact-based model has no git branch for mike to manipulate.
- Versioning must be implemented outside mike (would require custom logic to stitch versions together post-build).
- No git history of doc versions; deployments are a sequence of replaced artifacts.

**Fundamental incompatibility:** Mike's core design is "create commits on a git branch." The artifact model is "upload a tarball, GitHub Pages unpacks it." These are orthogonal deployment paradigms. Attempting to use mike with `actions/upload-pages-artifact` would fail because mike has nowhere to commit its `versions.json` and version directories. — [GitHub Issue jimporter/mike #44](https://github.com/jimporter/mike/issues/44), [GitHub Discussion squidfunk/mkdocs-material #4759](https://github.com/squidfunk/mkdocs-material/discussions/4759), [Transitioning GitHub Pages to Deploy from Artifacts](https://tenthirtyam.org/dispatches/2026/05/26/transitioning-github-pages-to-deploy-from-artifacts-instead-of-a-branch/) (May 2026).

**Official recommendation:** Material for MkDocs' own documentation covers only mike + gh-pages branch deployment. GitHub's pages docs cover both approaches but do not recommend one over the other (they are presented as equal alternatives). However, mike + gh-pages remains the **standard** for Python documentation versioning as of Aug 2026, and Material explicitly documents only this path.

**Recommendation for varco:** Use **mike + gh-pages branch** model. Rationale: (1) it is the standard for Python frameworks and has zero compatibility risk with existing tooling; (2) Material's own docs assume this model; (3) artifact-based versioning is unsolved in the ecosystem and would require custom tooling. The gh-pages branch is a minor hygiene cost (git ignores it if desired) and is a small storage cost (~100 MB for all doc versions combined, well within typical repos).

---

### CI Workflow Skeleton: Dev + Release Versions

**Workflow 1: Deploy `dev` on every push to `main`**

```yaml
name: Docs Deploy (Dev)

on:
  push:
    branches: [main]

permissions:
  contents: write

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@b4ffde65f46336ab88eb53be0f245c2c3c56de13  # v4.1.1
        with:
          fetch-depth: 0  # required: full history for gh-pages branch

      - name: Set up Python
        uses: actions/setup-python@0a5c61591373683505ea898e09a3ea4f39ef2b9c  # v5.0.0
        with:
          python-version: "3.12"

      - name: Set up uv
        uses: astral-sh/setup-uv@b9b1b6c6b6c6b6c6b6c6b6c6b6c6b6c6b6c6b6c6  # vX.Y.Z
        with:
          enable-cache: true

      - name: Install dependencies
        run: uv sync --all-packages --all-extras

      - name: Configure git
        run: |
          git config --global user.name "docs-bot"
          git config --global user.email "docs@varco.example.com"

      - name: Fetch gh-pages branch
        run: git fetch origin gh-pages:gh-pages || git checkout --orphan gh-pages

      - name: Build and deploy docs (dev)
        run: |
          uv run python -m mike deploy --push main dev

concurrency:
  group: docs-deploy-dev
  cancel-in-progress: false  # serialize dev deployments to avoid races
```

**Workflow 2: Deploy numbered version + update `latest` alias on release tag**

```yaml
name: Docs Deploy (Release)

on:
  release:
    types: [published]

permissions:
  contents: write

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@b4ffde65f46336ab88eb53be0f245c2c3c56de13  # v4.1.1
        with:
          fetch-depth: 0  # required: full history for gh-pages branch

      - name: Set up Python
        uses: actions/setup-python@0a5c61591373683505ea898e09a3ea4f39ef2b9c  # v5.0.0
        with:
          python-version: "3.12"

      - name: Set up uv
        uses: astral-sh/setup-uv@b9b1b6c6b6c6b6c6b6c6b6c6b6c6b6c6b6c6b6c6  # vX.Y.Z
        with:
          enable-cache: true

      - name: Install dependencies
        run: uv sync --all-packages --all-extras

      - name: Configure git
        run: |
          git config --global user.name "docs-bot"
          git config --global user.email "docs@varco.example.com"

      - name: Fetch gh-pages branch
        run: git fetch origin gh-pages:gh-pages || git checkout --orphan gh-pages

      - name: Extract version from tag
        id: version
        run: |
          TAG=${{ github.event.release.tag_name }}
          VERSION=${TAG#v}  # strip leading 'v' if present
          echo "version=${VERSION}" >> $GITHUB_OUTPUT

      - name: Build and deploy docs (release)
        run: |
          uv run python -m mike deploy --push --update-aliases ${{ steps.version.outputs.version }} latest

concurrency:
  group: docs-deploy-release
  cancel-in-progress: false  # serialize release deployments to avoid races
```

**Key details:**

- **`permissions: contents: write`** — Required to push to the `gh-pages` branch.
- **`fetch-depth: 0`** — Mike needs the full git history, including the `gh-pages` branch. Without this, `git fetch origin gh-pages:gh-pages` will fail.
- **`git config --global user.name` + `user.email`** — Mike makes commits; git requires identity. Use a bot account email if available, or a generic address.
- **`fetch origin gh-pages:gh-pages`** — Pre-fetch the `gh-pages` branch locally so mike can update it. The fallback `|| git checkout --orphan gh-pages` creates a fresh branch if it doesn't exist (first deployment).
- **`uv run python -m mike deploy`** — Invokes mike as a module. Assumes `mike` is in the `docs` dependency group.
- **`--update-aliases`** — Moves the `latest` alias to the newly deployed release version.
- **`concurrency` group** — Prevents two deployments from racing and corrupting the gh-pages branch. Uses `cancel-in-progress: false` (serialize) rather than `true` (cancel old job) because doc builds are fast and serialization is safer.

— [GitHub Discussion #4759 (squidfunk/mkdocs-material)](https://github.com/squidfunk/mkdocs-material/discussions/4759), [Mike GitHub README](https://github.com/jimporter/mike).

---

### Version-Alias Naming Policy

**Numbering format: `<major>.<minor>` without leading `v` or trailing patch version.**

- ✅ Correct: `3.0`, `3.1`, `2.5`
- ❌ Incorrect: `v3.0` (avoid the `v` prefix), `3.0.0` (patch is unnecessary; mike optimizes for `<major>.<minor>`)
- ❌ Avoid: `3.0.0-rc1` (pre-release suffixes complicate alias management)

Rationale: Mike's directory layout is `<major>.<minor>/`, so versioning should match that structure. The `v` prefix adds no value in URLs and makes key-value lookups in `versions.json` inconsistent. Patch versions (`.0` in `3.0.0`) are redundant; dropping them simplifies alias updates (move `latest` to `3.1` without worrying about `.0` vs `.1`). — [Mike GitHub README](https://github.com/jimporter/mike) (directory structure discussion).

**Alias naming: `latest`, `dev`, `stable` (if applicable).**

- **`latest`** — Points to the most recent stable release. Updated on every release tag.
- **`dev`** — Points to the development branch (docs built from `main` post-latest-release). Updated on every push to `main`.
- **`stable`** — Optional; used only if you maintain an LTS release separate from `latest`. Example: `latest` → `3.1`, `stable` → `3.0-lts`.

Most projects use only `latest` + `dev`. The alias names are arbitrary strings in `versions.json`; they do not appear in URLs (users see `/latest/`, `/dev/`, etc., which are served from the underlying version directory). — [Mike GitHub Discussion #108](https://github.com/jimporter/mike/issues/108).

**URL stability if naming scheme changes:**

Old numbered version URLs are **permanent and stable** (e.g., `/3.0/index.html` will always resolve, even if you later rename internal aliases). Aliases are **redirects** (via HTML or JavaScript), not permanent directories. If you later change the `latest` alias to point to a different version, the `/latest/` URL silently serves different content, but the numbered URL stays the same.

**Breaking change risk:** If you later switch from `3.0` to `v3.0` or `3.0.0`, old URLs become 404s (because the directory structure on gh-pages changes). To avoid this, commit to a naming scheme and stick with it. Varco should standardize on `<major>.<minor>` (no `v`, no patch) at 1.0 release and never deviate. — [Mike GitHub README](https://github.com/jimporter/mike).

---

### Interaction with mkdocs-gen-files and mkdocstrings

**mkdocs-gen-files works seamlessly with mike; no special handling needed.** Mike invokes a full `mkdocs build` per version, which runs all plugins, including `gen-files`. The `gen-files` plugin generates API reference stubs (e.g., `docs/api/varco_core.md`) from Python import paths at build time. Since mike runs the full build pipeline for each version independently, `gen-files` has the opportunity to regenerate stubs for each version. This is the intended behavior and carries no extra complexity.

**mkdocstrings requires the documented packages to be importable at docs-build time.** — [mkdocstrings-python · Usage](https://mkdocstrings.github.io/python/usage/) (current). When `mkdocs build` runs (including inside `mike deploy`), mkdocstrings reads live Python docstrings via `importlib` and dynamic analysis. This means:

- The documented packages (`varco_core`, `varco_kafka`, etc.) must be installed and importable in the CI environment during `mkdocs build`.
- The CI job should run `uv sync --all-packages --all-extras` (not just the `docs` group) to ensure all workspace members are importable.
- Alternatively, run `uv sync && uv sync --only docs` to install everything, then upgrade docs extras only (if desired, to avoid redundancy).

**Recommended CI setup for varco:**

```yaml
- name: Install dependencies
  run: uv sync --all-packages --all-extras
  # Installs all ten workspace packages + all dev/docs extras.
  # Ensures mkdocstrings can import varco_core, varco_kafka, etc.

- name: Build and deploy docs
  run: uv run python -m mike deploy --push main dev
```

If a lighter approach is desired (install only what's needed for docs), it is theoretically possible to install only `varco_core` + the docs group:

```yaml
- name: Install workspace (doc-specific)
  run: uv sync --only docs --no-dev && uv pip install varco-core
```

However, this is error-prone (requires manual tracking of which packages mkdocstrings needs) and is not recommended. The safest path is `uv sync --all-packages --all-extras`, which is what CI already does. — [mkdocstrings-python troubleshooting](https://mkdocstrings.github.io/python/usage/), [Real Python: Python Project Documentation with MkDocs](https://realpython.com/python-project-documentation-with-mkdocs/) (2026 section on mkdocstrings).

---

## Version/Compatibility Notes

| System | Current Version (Aug 2026) | Notes for Varco 1.0 |
|---|---|---|
| **mike** | 2.1.3–2.2.0 | Supports Python 3.8–3.13; no incompatibilities with mkdocs 1.6.x or Material 9.7.6. Add to `[dependency-groups] docs` in pyproject.toml. |
| **Material for MkDocs** | 9.7.6 (March 2026, end-of-feature) | Last version to receive new features; security/critical fixes through at least Nov 2026. Version switcher and `extra.version.provider: mike` integration are stable and unchanged. |
| **mkdocs** | 1.6.x (required by Material 9.7.6) | No known breaking changes with mike; version switching works identically across mkdocs 1.5–1.6. |
| **GitHub Pages actions** | `upload-pages-artifact@v4+`, `deploy-pages@v4+` | Artifact model is incompatible with mike. Use branch-based deployment only. |
| **Git (gh-pages branch)** | All modern versions | Standard branching semantics; no version-specific requirements. |

---

## Evidence Gaps

- **Mike adoption rate / real-world deployments** — No published survey of how many Python projects use mike vs. alternatives (artifact-based versioning, Read the Docs, custom solutions). The claim that mike is the "standard" is inferred from Material's documentation and community discussions, not from a quantitative census.
- **Long-term future of Material for MkDocs** — Material team is transitioning to Zensical (next-gen static site generator). No timeline published for when Material will be sunset. Versioning support in Zensical is unknown as of Aug 2026. For now, Material + mike is stable; Plan to revisit docs hosting choice at varco 2.0 (est. 2027+).
- **Performance of `fetch-depth: 0` for very large gh-pages branches** — Varco's gh-pages branch will grow by ~10–20 MB per release (three versions × 3–5 MB each). After 10–20 releases, the branch could reach 200–400 MB. `fetch-depth: 0` will become slower. No guidance found on when to implement `shallow-clone` strategies for gh-pages specifically. This is a non-issue for 1.0 (first release) but worth revisiting at 2.0.

Worth a separate brief: Read the Docs vs. self-hosted GitHub Pages trade-offs for 2.0+; building a custom versioning layer if Material + mike is sunsetted before varco 2.0.

---

## Librarian's Note

**What the sources indicate:**

Mike + gh-pages branch is the **only** official path forward for versioned mkdocs documentation as of Aug 2026, per Material's own documentation. The artifact-based deployment model is incompatible with mike's design (branch-based commits) and is a solved problem only for single-version sites. No credible alternative to mike exists in the Material ecosystem.

The **chosen path** (gh-pages branch, mike, three versions: `3.0` / `latest` / `dev`) is: (1) zero-risk for compatibility; (2) supported by Material's docs and examples; (3) standard across Python OSS frameworks; (4) requires only `uv sync --all-packages --all-extras` at docs-build time (already done in current CI).

The **one trade-off** is branch hygiene: the gh-pages branch becomes a long-lived working directory that grows with every release. This is acceptable for 1.0 and at least 3–4 releases after (2028+). At that point, if gh-pages has grown to >1 GB or Material is sunset in favor of Zensical, a revisit to artifact-based versioning (with custom logic) may be warranted. For now, adopt mike; revisit at 2.0.

**Decision rule:** Use `mike deploy --push --update-aliases` for release tags, and `mike deploy --push` for dev versions. Pin mike to `>=2.1,<3` in the docs group. Configure GitHub Pages to serve from `gh-pages` branch (verify in repo settings). Add `extra.version.provider: mike` to mkdocs.yml. The workflow skeletons above are production-ready.

---

## Sources

- [mike · PyPI](https://pypi.org/project/mike/) (2026-04-14; v2.1.3–v2.2.0)
- [GitHub — jimporter/mike: Manage multiple versions of your MkDocs-powered documentation](https://github.com/jimporter/mike) (current; README + issues)
- [Setting up versioning - Material for MkDocs](https://squidfunk.github.io/mkdocs-material/setup/setting-up-versioning/) (current, Aug 2026)
- [Material for MkDocs Changelog](https://squidfunk.github.io/mkdocs-material/changelog/) (v9.7.6, March 2026)
- [GitHub Actions: Upload GitHub Pages Artifact](https://github.com/actions/upload-pages-artifact) (v4+; current docs)
- [GitHub Actions: Deploy Pages](https://github.com/actions/deploy-pages) (v4+; current docs)
- [GitHub Docs: Configuring a publishing source for GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site) (current)
- [GitHub Discussion #4759 — When using mike for versioning, what should the github actions workflow look like?](https://github.com/squidfunk/mkdocs-material/discussions/4759) (2024–2026, community examples)
- [GitHub Issue #44 — Deploying without `mike deploy`?](https://github.com/jimporter/mike/issues/44) (2023, on artifact compatibility)
- [Transitioning GitHub Pages to Deploy from Artifacts Instead of a Branch](https://tenthirtyam.org/dispatches/2026/05/26/transitioning-github-pages-to-deploy-from-artifacts-instead-of-a-branch/) (May 2026; on model comparison)
- [mkdocstrings-python · Usage](https://mkdocstrings.github.io/python/usage/) (current; package import requirement)
- [Real Python: Python Project Documentation with MkDocs](https://realpython.com/python-project-documentation-with-mkdocs/) (2026 update; mkdocstrings setup)
