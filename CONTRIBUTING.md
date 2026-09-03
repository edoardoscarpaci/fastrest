# Contributing to varco

Thank you for considering a contribution. This document covers dev setup, the house rules CI
enforces, and the versioning/deprecation policy every breaking change must follow.

## Dev setup

```bash
# Install everything (all workspace members + dev deps, including ruff/mypy)
uv sync --all-packages --all-extras

# One-time, per clone: makes `git blame` skip the mechanical ruff sweep commits (Plan 017 / RL-6)
git config blame.ignoreRevsFile .git-blame-ignore-revs
```

Run tests from the workspace root:

```bash
uv run pytest varco_core/tests/         # one package
uv run pytest varco_core/tests/test_event.py::TestInMemoryEventBus::test_subscribe  # one test
make test                                # all eleven suites, accumulated summary
make test PKG=varco_redis                # narrow to one package
```

## Before you open a PR

| Command | What it checks |
|---|---|
| `make lint` | `ruff check .` **+** `ruff format --check .` (the formatter is a CI gate, Plan 020 / RL-17) **+** `scripts/api_surface.py --check` (a CI gate as of Plan 024 / C5 — regenerate and commit the snapshot alongside any `__all__`/exported-function-signature change: `uv run python scripts/api_surface.py`) |
| `make type-check` | `mypy` over the ten source dirs, `strict = true` |
| `make test` | all eleven unit-test suites |

⚠️ **Never invoke linting via `uvx ruff`.** `uvx` resolves whatever the newest ruff release is at
the moment you run it, which can silently diverge from the pin CI enforces
(`[dependency-groups].lint` in the root `pyproject.toml`). Always `uv run ruff` /
`uv run mypy`.

## House rules

- **Docs update in the same commit as the code change, never a follow-up.** README.md and
  CLAUDE.md (and, for a versioned public-API change, `CHANGELOG.md`) are updated alongside the
  code that makes them true — see CLAUDE.md's own instructions for the full per-subsystem list of
  what lives where.
- **Every new code addition includes a test.** Integration tests (requiring a real broker/DB —
  Docker via testcontainers) are required when touching anything that talks to an external
  system; unit tests use `InMemoryEventBus` / `InMemoryDeadLetterQueue` and are the default.
- **A CHANGELOG entry accompanies any user-visible change.** Follow the existing
  `### Added` / `### Changed` / `### BREAKING` section shape under `## [Unreleased]`.
- **Reference the relevant BACKLOG.md row** (or open a new one) when a PR closes or advances a
  tracked item.

## Versioning and deprecation policy

varco follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html), with one
workspace-wide rule layered on top:

### Lockstep versioning

All ten distribution packages (`varco-core`, `varco-kafka`, `varco-nats`, `varco-redis`,
`varco-sa`, `varco-beanie`, `varco-memcached`, `varco-ws`, `varco-fastapi`, `varco-casbin`) share
**one version number**. A breaking change in *any one* of them bumps all ten — there is no
independent per-package versioning and no umbrella meta-package (both considered and rejected;
see BACKLOG.md's Parked decisions). The only tool that may write a version number is
`scripts/bump.py` (`uv run python scripts/bump.py --set X.Y.Z` /
`uv run python scripts/bump.py --bump major|minor|patch`); its `--check` mode is a live CI gate
(`varco_core/tests/test_bump_script.py::test_workspace_versions_are_coherent`) that fails the
build if the ten ever diverge. See CLAUDE.md's "Lockstep version bump" section for the full
command reference.

Sibling requirement strings (e.g. `varco-kafka`'s dependency on `varco-core`) are pinned
**compatible-release**, `~=<major>.0`, never an exact `==<version>` — a `varco-kafka==3.0.1`
install resolves happily against `varco-core==3.4.0`, avoiding the diamond-dependency conflicts an
exact pin would force on the very first post-release patch.

### Removal window

A public symbol may only be **removed in a major version**. `varco_core.deprecation.deprecated`
and `deprecated_alias` (`varco_core/varco_core/deprecation.py`) require a `removed_in=` keyword at
authoring time — this is not optional, and it names the *earliest* major release in which the
removal is permitted to actually happen.

**Wall-clock floor: at least 12 months** between the release that deprecates a symbol and the
release that removes it. This is the faithful library-scale translation of PEP 387's "at least two
consecutive minor releases of a runtime with a fixed annual train" — the mechanism is "at least one
full major cycle"; twelve months is the added wall-clock guarantee on top. (This is a deliberate
divergence from a stricter two-year floor some ecosystems use — see the reasoning trail in
`plans/023-release-version-freeze-and-supply-chain.md` §RL-9-policy if you are weighing whether to
propose changing it. Reversing it only requires editing this document; no code or `removed_in=`
string encodes the number.)

### Mechanism

Every hard deprecation emits a `DeprecationWarning` via `varco_core.deprecation`. A
`deprecated_alias(...)`-created alias resolves to the **identical object** as its replacement, so
`isinstance()` and `except SomeException:` keep working across the deprecation window — it is not
a wrapper or a shim with subtly different behaviour. Plan 022 shipped four worked examples, all
`since="3.0.0", removed_in="4.0.0"`:

- `varco_sa.rls.enable_rls_ddl` → `render_rls_ddl` (AB-1)
- `varco_core.migration.MigrationError`/`.MigrationPlan` → `varco_core.SchemaMigrationError`/
  `.SchemaMigrationPlan` (AB-2)
- `varco_beanie.BeanieConfig` → `BeanieSettings` (AB-4)

### Soft deprecation

Documentation-only discouragement with **no** removal date and **no** warning is permitted (PEP
387 recognises this category too) — use it when a newer pattern is preferred but the old one is
not scheduled for removal.

### Enforcement

`uv run python scripts/api_surface.py --check` diffs the live public API surface against the
committed snapshot (`design/api-freeze-and-standards/measurements/api-surface.json`) and detects a
**removal** or a **function signature narrowing**. Run it by hand after touching any `__all__` or
any exported function's signature, and commit the regenerated snapshot alongside the change.
Honestly: it is **not yet a CI gate** (see CLAUDE.md's own caveat), and it cannot see a narrowed
class `__init__` (class signatures are not recorded — see the same section for why).

### Python support

`requires-python = ">=3.12"`; every push is tested on 3.12 and 3.13
(`.github/workflows/test.yml`'s matrix). **Dropping a Python minor version is a major bump.**

## Reporting a security issue

Do **not** open a public issue for a security vulnerability — see [SECURITY.md](SECURITY.md) for
the private reporting channel.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
