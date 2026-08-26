# Research 003 — Mypy 2.x defaults and incremental-adoption strategy for varco

Date: 2026-08-26 · Freshness matters: yes — mypy 2.0 released May 2026; strictness and configuration are evergreen but version-specific.

## Question

When adopting mypy type checking for the first time on an existing 10-package Python monorepo (varco) using mypy 2.3.1 against Python 3.12+, what are the major default/breaking changes from 1.x to 2.x, the official incremental-adoption path, the strictness levels available and their cost, the mypy configuration needed for a monorepo, and the guarantees PEP 561 `py.typed` actually imposes on a library?

## Findings

### 1. Major Breaking Changes in Mypy 2.0

- **`--local-partial-types` is now enabled by default** — affects type inference from assignments in outer scopes, hardening soundness. The 1.x behavior is no longer available. — [Mypy 2.0 Release Notes](https://mypy.readthedocs.io/en/stable/changelog.html) (May 2026)

- **`--strict-bytes` is now enabled by default per PEP 688** — `bytearray` and `memoryview` are no longer assignable to `bytes`. — [Mypy 2.0 Release Notes](https://mypy.readthedocs.io/en/stable/changelog.html) (May 2026)

- **`--allow-redefinition` now behaves like the old `--allow-redefinition-new`** — allows reassigning variables to completely different types in conditional blocks. — [Mypy 2.0 Release Notes](https://mypy.readthedocs.io/en/stable/changelog.html) (May 2026)

- **Python 3.9 support dropped; mypy requires Python 3.10+ at runtime** — targeting Python 3.8 no longer permitted. — [Mypy 2.0 Release Notes](https://mypy.readthedocs.io/en/stable/changelog.html) (May 2026)

- **Legacy bundled stubs handling removed; `--ignore-missing-imports` now consistently respected** — third-party packages no longer receive special casing. — [Mypy 2.0 Release Notes](https://mypy.readthedocs.io/en/stable/changelog.html) (May 2026)

- **Type comments for class variable None assignment tightened** — assignment to None for non-optional class variables via type comment no longer allowed, closing a soundness hole. — [Mypy 2.0 Release Notes](https://mypy.readthedocs.io/en/stable/changelog.html) (May 2026)

- **Parallel type checking support added via `--num-workers`** — potential speedup of up to 5x for large projects. — [Mypy 2.0 Release Notes](https://mypy.readthedocs.io/en/stable/changelog.html) (May 2026)

#### What this means for varco

The `--local-partial-types` and `--strict-bytes` defaults will immediately affect a codebase that previously ran on 1.x. Varco's current baseline (38 errors at default settings on varco_core alone) may change when run on mypy 2.3.1 due to these new defaults. The Python 3.10+ runtime requirement is satisfied (varco targets 3.12+). The removal of legacy stub special-casing means varco's own `py.typed` marker will be respected more aggressively by downstream consumers, raising the bar for what "typed" means in practice.

---

### 2. Official Incremental-Adoption Path and Configuration

**Mypy's documented recommendation for existing codebases:**
- Start with a small subset (5,000–50,000 lines) and get mypy running on *that subset first*, before adding annotations. — [Using mypy with an existing codebase](https://mypy.readthedocs.io/en/latest/existing_code.html) (mypy docs, latest)

**Per-module configuration via `[mypy-PATTERN]` overrides:**
- Configuration sections matching specific modules take precedence over wildcard patterns, enabling selective enforcement. — [The mypy configuration file](https://mypy.readthedocs.io/en/stable/config_file.html) (mypy 2.3.1 docs, stable)
- Example: `[mypy-legacy.*]` can have `disallow_untyped_defs = False` while the global setting is `True`, allowing "strict by default, loose for legacy".

**Error code management:**
- `disable_error_code` — suppresses specific error codes globally (e.g., `disable_error_code = arg-type, assignment` to mute two categories). — [The mypy configuration file](https://mypy.readthedocs.io/en/stable/config_file.html) (mypy 2.3.1 docs, stable)
- `enable_error_code` — re-enables error codes suppressed elsewhere; takes precedence over `disable_error_code`. — [The mypy configuration file](https://mypy.readthedocs.io/en/stable/config_file.html) (mypy 2.3.1 docs, stable)

**Import handling:**
- `ignore_missing_imports = True` — suppresses "module not found" errors for untyped third-party libraries *globally*. — [The mypy configuration file](https://mypy.readthedocs.io/en/stable/config_file.html) (mypy 2.3.1 docs, stable)
- `follow_imports = skip` (or `normal`/`silent`) controls whether mypy reads imported modules or treats them as `Any`; per-module overrides available. — [The mypy command line](https://mypy.readthedocs.io/en/stable/command_line.html) (mypy 2.3.1 docs, stable)
- Per-module `[mypy-pkg.*]` sections can have their own `ignore_missing_imports` setting, applying to *imports from* that module, not imports *of* it.

**Baseline mechanism — ⚠️ NOT official:**
- Mypy's official documentation does not include a dedicated "baseline" or "ignore-errors file" feature for capturing and excluding pre-existing errors. — [The mypy configuration file](https://mypy.readthedocs.io/en/stable/config_file.html) (mypy 2.3.1 docs, stable)
- The third-party tool `mypy-baseline` fills this gap: it generates a text file of baseline errors on the first run and filters them on subsequent runs, allowing only *new* errors to be reported. — [mypy-baseline GitHub](https://github.com/orsinium-labs/mypy-baseline) (community tool)
- Basedmypy (a fork) offers a native `[mypy] baseline = <file>` option, but this is not in upstream mypy. — [Baseline - basedmypy](https://kotlinisland.github.io/basedmypy/baseline) (2024)

#### What this means for varco

Varco should:
1. Use per-package or per-module `[mypy-varco_*]` overrides to enforce strictness selectively (strict for new packages, relaxed for legacy ones).
2. Use `--ignore-missing-imports` globally to unblock on untyped third-party deps (Kafka, Redis, SQLAlchemy may have incomplete types).
3. Adopt a baseline tool (mypy-baseline) to capture the current 38 errors and gate CI on *new* errors only, until the baseline is resolved.
4. Avoid the official `ignore_errors` option; per-module configuration is clearer.

---

### 3. What `py.typed` Actually Obligates

**PEP 561 requirement:**
- A package must include a `py.typed` marker file to signal that its inline type annotations should be read by type checkers. Without it, all imported values become `Any` in downstream consumers. — [PEP 561](https://peps.python.org/pep-0561/) (Python Enhancement Proposal)

**Strictness level mandated by PEP 561:**
- **PEP 561 does NOT specify any strictness level or mypy configuration requirement.** The PEP mandates only the presence of the marker file and the format of stub packages; it makes no claim about what strictness flags the package must be checked at. — [PEP 561](https://peps.python.org/pep-0561/) (Python Enhancement Proposal)

**Partial type coverage:**
- If a package has incomplete annotations (e.g., some modules untyped), PEP 561 permits labeling it as "partial" by adding the string `partial\n` to the `py.typed` file. Downstream consumers know the coverage is incomplete. — [PEP 561](https://peps.python.org/pep-0561/) (Python Enhancement Proposal)

**`no_implicit_reexport` and `implicit_reexport` in library context:**
- These are mypy-specific strictness settings, NOT PEP 561 requirements.
- `--no-implicit-reexport` (part of `--strict`) requires that modules explicitly declare what they re-export via `__all__`. Without it, `from module import A` followed by `A` used in `__init__.py` is an implicit re-export. — [The mypy command line](https://mypy.readthedocs.io/en/stable/command_line.html) (mypy 2.3.1 docs, stable)
- A library with `py.typed` that omits `__all__` or explicit re-export declarations will cause downstream strict-mode consumers to fail with "no-reexport" errors. — [Usage and community perspectives on mypy --no-implicit-reexport in library development](https://discuss.python.org/t/usage-and-community-perspectives-on-mypy-no-implicit-reexport-in-library-development/43600) (Python Discourse, 2024)

**Recommendation from the typing community:**
- Typed libraries commonly use a semi-strict configuration including `check_untyped_defs`, `disallow_any_generics`, `disallow_incomplete_defs`, `no_implicit_optional`, and `no_implicit_reexport`, even if not full `--strict`. — [Usage and community perspectives on mypy --no-implicit-reexport in library development](https://discuss.python.org/t/usage-and-community-perspectives-on-mypy-no-implicit-reexport-in-library-development/43600) (Python Discourse, 2024)

#### What this means for varco

PEP 561 alone does NOT require varco to be strictly typed or even to reach a particular strictness level. However, varco ships `py.typed`, which signals to downstream consumers that the library is typed and safe to check. In practice:
- If varco is only partially typed (heavy `Any` usage), downstream strict consumers will silently degrade to loose checking on varco's symbols.
- If varco aspires to a "properly typed" claim, the library should define `__all__` in every module that re-exports, and should be checked at least at the semi-strict configuration mentioned above (including `no_implicit_reexport`).
- Varco's own choice of strictness level is a non-functional decision (PEP 561 is silent). The choice affects downstream DX but not correctness.

---

### 4. Checking a Multi-Package Monorepo

**Single invocation vs. per-package:**
- **Recommended**: invoke mypy once over all source directories, not per-package. — [Running mypy and managing imports](https://mypy.readthedocs.io/en/stable/running_mypy.html) (mypy 2.3.1 docs, stable)
- Mypy will discover inter-package imports within a single run and check them correctly if configured properly.

**Critical configuration options for monorepos:**

1. **`mypy_path` (or `MYPYPATH` environment variable):**
   - Specifies directories where mypy should search for modules. Useful for monorepos where packages share common dependencies or are installed into a single venv. — [Running mypy and managing imports](https://mypy.readthedocs.io/en/stable/running_mypy.html) (mypy 2.3.1 docs, stable)
   - Example: `mypy_path = src` tells mypy to treat `src` as a root for module discovery.

2. **`explicit_package_bases = True`:**
   - Mypy will locate the nearest parent directory that is in `mypy_path`, the `MYPYPATH` env var, or the current working directory, then use the relative path to determine fully qualified module names. — [Running mypy and managing imports](https://mypy.readthedocs.io/en/stable/running_mypy.html) (mypy 2.3.1 docs, stable)
   - Essential for monorepos with non-standard directory structures.

3. **`namespace_packages = True` (default since mypy 0.990):**
   - Allows directories without `__init__.py` to be treated as packages. Default behavior, but worth stating explicitly in config. — [Running mypy and managing imports](https://mypy.readthedocs.io/en/stable/running_mypy.html) (mypy 2.3.1 docs, stable)

**Duplicate module name error ("found module twice under different names"):**
- **Root cause**: mypy resolves the same source file via two different import paths (e.g., `varco.core.event` and `varco_core.event`), creating confusion. — [Namespace packages supported by default? · Issue #14057 · python/mypy](https://github.com/python/mypy/issues/14057) (GitHub, mypy project)
- **Fix**: ensure `explicit_package_bases = True` is set AND use consistent import paths (either all absolute from workspace root or all relative to package root, not mixed). — [Make `--explicit-package-bases` revertible · Issue #9968 · python/mypy](https://github.com/python/mypy/issues/9968) (GitHub, mypy project)
- **Also verify**: confirm each package has a `pyproject.toml` or `setup.py` defining `package_root` correctly so mypy infers the right base.

**Incremental cache (`cache_dir = .mypy_cache`):**
- By default, mypy writes to and reads from `.mypy_cache` to speed up incremental runs. — [Running mypy and managing imports](https://mypy.readthedocs.io/en/stable/running_mypy.html) (mypy 2.3.1 docs, stable)
- **CI best practice**: caching `.mypy_cache` across CI runs is recommended and safe. However, run a full (non-incremental) build at least once per CI pipeline to create a fresh cache and avoid long-term cache drift. — [Additional features](https://mypy.readthedocs.io/en/stable/additional_features.html) (mypy 2.3.1 docs, stable)
- Set `cache_dir = /dev/null` (Unix) or `nul` (Windows) to disable caching if reproducibility is critical. — [Running mypy and managing imports](https://mypy.readthedocs.io/en/stable/running_mypy.html) (mypy 2.3.1 docs, stable)

#### What this means for varco

Varco's monorepo configuration should:
1. Set `mypy_path = .` (or nothing, relying on default cwd) since varco installs all packages into one venv.
2. **Set `explicit_package_bases = True`** to avoid duplicate-module-name errors when mypy resolves `varco_core`, `varco_kafka`, etc.
3. Invoke mypy once: `mypy varco_core varco_kafka varco_redis ...` (all 10 packages in one command), not per-package.
4. Archive `.mypy_cache` in CI to speed up type-check runs (e.g., via `actions/cache@v3` on GitHub Actions or equivalent).
5. Run a full, non-incremental build once per week or per release to refresh the cache and prevent drift.

---

### 5. Realistic Strictness Target and Per-Flag Cost

**Strictness levels available (no intermediate "semi-strict" official designation):**

The mypy documentation offers no officially-named intermediate levels between default and `--strict`. The 13 individual flags in `--strict` can be enabled/disabled independently:

| Flag | Error Code | What It Detects | Typical Churn Cost on Existing Codebase |
|---|---|---|---|
| `--disallow-untyped-defs` | `no-untyped-def` | Functions with no type annotations at all (parameters, return type). | **High churn** — Every unannotated function/method must be annotated or marked `type: ignore`. For a 200+ file codebase, this is often 500+ locations. Common in legacy code. |
| `--disallow-untyped-calls` | `no-untyped-call` | Calling functions that lack annotations from typed code. | **Medium-high churn** — Requires annotating the called function or wrapping the call in `# type: ignore`. Cascades: fixing one unannotated function unblocks several call sites. |
| `--disallow-incomplete-defs` | `no-untyped-def` (subset) | Functions with partial annotations (some parameters untyped, or return type missing). | **Medium churn** — Often combined with `--disallow-untyped-defs`. Catches the "annotate half and hope" pattern. |
| `--check-untyped-defs` | `assignment`, `arg-type`, etc. | Type-checks the *body* of unannotated functions as if all params/return are `Any`. | **Medium-high churn** — Exposes type mismatches inside functions that were previously unchecked. May reveal subtle bugs or require defensive `# type: ignore` comments. |
| `--disallow-any-generics` | `type-arg` | Requires all generic types to specify type parameters (e.g., `list[int]` not `list`). | **Medium churn** — Affects: bare `list`, `dict`, `tuple` usage; unparameterized function returns (`def f() -> dict` must be `dict[str, int]`). Older codebases heavy in this. |
| `--warn-return-any` | `no-any-return` | Functions annotated to return specific type but actually return `Any` (e.g., via `json.loads()`). | **Medium churn** — Forces intermediate variable typing or narrows return type to `Any`. Catches real type unsoundness (code that *looks* typed but isn't). |
| `--warn-unused-ignores` | `unused-ignore` | Flags `# type: ignore` comments that are no longer needed (error was fixed elsewhere, or the comment was wrong). | **Low churn at first, high value over time** — One-time cleanup; prevents bitrot in suppression comments. Essential for keeping `# type: ignore` count honest. |
| `--warn-redundant-casts` | `redundant-cast` | Detects `cast(SameType, x)` where the value is already that type. | **Very low churn** — Rare in practice; mostly code-quality. |
| `--strict-equality` | `comparison-overlap` | Detects comparisons that always evaluate the same (e.g., `x == None` when `x` is never `None`, should be `is None`). | **Very low churn** — Fixes real bugs (identity vs. equality confusion). Common in legacy Python 2 code. |
| `--disallow-untyped-decorators` | `no-untyped-def` (decorators) | Decorators must be typed or wrapped in `# type: ignore`. | **Medium churn** — Affects custom decorators and third-party decorator imports. Cascades like untyped-defs. |
| `--disallow-any-unimported` | `no-any-unimported` | Reports when an unresolved import becomes `Any`, revealing missing stubs or `ignore_missing_imports` cases. | **Medium churn** — Requires `ignore_missing_imports` settings or finding/installing type stubs. Good diagnostic but often requires external action (stubs don't exist). |
| `--disallow-any-expr` | `no-any-expr` | Variables assigned or expressions that evaluate to `Any` are flagged (broader than `warn-return-any`). | **High churn** — Strict about any `Any` appearing in source, including from untyped third-party modules. Very strict; few projects use this. |
| `--no-implicit-reexport` | `no-reexport` | Requires explicit `__all__` or explicit imports to re-export symbols from `__init__.py` or module-level `__init__`. | **Medium churn** — Requires adding `__all__` lists or explicit re-import statements. Especially impactful for libraries; application code often skips this. |

**High-value, moderate-cost starting point (semi-strict):**

A practical middle ground cited by the community is to enable:
- `disallow_untyped_defs`
- `check_untyped_defs`
- `disallow_any_generics`
- `disallow_incomplete_defs`
- `no_implicit_reexport` (for libraries)
- `warn_return_any`
- `warn_unused_ignores`

This configuration covers the most common soundness issues without the full cost of `--strict` (saves ~20–30% effort by skipping `disallow_untyped_calls`, `disallow_any_unimported`, and `disallow_any_expr`). — [Professional-grade mypy configuration](https://careers.wolt.com/en/blog/tech/professional-grade-mypy-configuration) (Wolt Engineering Blog, 2025)

#### What this means for varco

Given varco's baseline of 38 errors at default settings in varco_core:
1. **Do not target `--strict` immediately.** A measured ramp:
   - Week 1: establish config, capture baseline with mypy-baseline.
   - Weeks 2–4: enable `--disallow-untyped-defs` and `--check-untyped-defs` on new/high-confidence modules; use per-module overrides for legacy code.
   - Weeks 5–6: enable `--disallow-any-generics` and `--disallow-incomplete-defs`.
   - Week 7+: add `--no-implicit-reexport` (since varco ships `py.typed`) and `--warn-return-any`.
   - Defer `--disallow-untyped-calls`, `--disallow-any-unimported`, `--disallow-any-expr` unless there is appetite for higher strictness.

2. **Use per-module overrides** to be strict on high-value modules (e.g., varco_core.event, varco_core.service) and looser on adapters (e.g., varco_sa, varco_beanie).

3. **Prioritize `--warn-unused-ignores`** early — it prevents suppression comment bitrot and is low-cost.

4. **The full `--strict`** mode is a long-term goal, not an initial sprint target for a 10-package monorepo with 38 baseline errors.

---

## Version/Compatibility Notes

- **Mypy 2.3.1** (current resolved version in question): stable; released as part of mypy 2.x line (May 2026).
- **Mypy 2.0** (major release, May 2026): introduced `--local-partial-types` default, `--strict-bytes` default, Python 3.10+ requirement at runtime. Notable: added parallel type checking via `--num-workers`.
- **Mypy 1.x** (EOL after 2.0 release): will no longer receive updates; migration from 1.x to 2.x recommended for new projects.
- **Mypy 2.1, 2.2, 2.3, 2.4+** (ongoing): incremental improvements to performance and cache handling; no breaking changes reported between 2.0 and 2.3.1.
- **Python support:** mypy 2.x requires Python 3.10+ at runtime but can type-check Python 3.12 code without issue (varco's target).

---

## Evidence Gaps

- **Mypy-baseline adoption path within Varco CI**: The brief identifies mypy-baseline as a third-party tool for capturing baseline errors, but does not provide a worked example of its integration with GitHub Actions or equivalent CI. Worth a separate brief if varco adopts it.
- **Per-flag migration order for varco specifically**: The brief suggests a ramp-up sequence but does not prioritize based on varco's actual error distribution (38 errors at default; breakdown by error code not measured). A follow-up run of `mypy --show-error-codes varco_core` would inform a more precise ramp schedule.
- **Mypy cache performance on varco's 10-package scale**: The brief covers cache best practices but not benchmarks for varco's specific invocation (10 packages, 217+ source files, shared venv). Local profiling of cache hit rates on CI would inform whether `.mypy_cache` archiving is worthwhile.

---

## Librarian's Note

The evidence favours a **measured, multi-week ramp-up to semi-strict mypy**, not an immediate jump to `--strict`. PEP 561 `py.typed` imposes only packaging obligations, not strictness ones, but shipping `py.typed` signals to downstream consumers that varco is typed — raising expectations that annotations exist and are reasonably sound. Mypy 2.0's new defaults (`--local-partial-types`, `--strict-bytes`) will likely increase varco's current 38-error baseline when first run, so a baseline-capture tool (mypy-baseline, third-party but well-established) is essential to avoid overwhelming the team. Per-module configuration via `[[tool.mypy.overrides]]` allows enforcement to ramify selectively, protecting high-value modules (event, service, auth) while allowing legacy backends (SA, Beanie, Kafka) to adopt strictness gradually. The monorepo configuration requires `explicit_package_bases = True` to avoid duplicate-module-name errors across the 10 packages.

---

## Sources

| Title | URL | Date/Version |
|---|---|---|
| Mypy Release Notes | https://mypy.readthedocs.io/en/stable/changelog.html | mypy 2.3.1 stable docs (released May 2026) |
| The mypy configuration file | https://mypy.readthedocs.io/en/stable/config_file.html | mypy 2.3.1 stable docs |
| The mypy command line | https://mypy.readthedocs.io/en/stable/command_line.html | mypy 2.3.1 stable docs |
| Running mypy and managing imports | https://mypy.readthedocs.io/en/stable/running_mypy.html | mypy 2.3.1 stable docs |
| Additional features | https://mypy.readthedocs.io/en/stable/additional_features.html | mypy 2.3.1 stable docs |
| Error codes for optional checks | https://mypy.readthedocs.io/en/stable/error_code_list2.html | mypy 2.3.1 stable docs |
| Using mypy with an existing codebase | https://mypy.readthedocs.io/en/latest/existing_code.html | mypy latest development docs |
| PEP 561 – Distributing and Packaging Type Information | https://peps.python.org/pep-0561/ | Python Enhancement Proposal (final) |
| Namespace packages supported by default? · Issue #14057 | https://github.com/python/mypy/issues/14057 | GitHub mypy project (2023–2024) |
| Make `--explicit-package-bases` revertible · Issue #9968 | https://github.com/python/mypy/issues/9968 | GitHub mypy project (2022–2023) |
| Support namespace packages when passing files · PR #9742 | https://github.com/python/mypy/pull/9742 | GitHub mypy project (2021) |
| mypy-baseline GitHub | https://github.com/orsinium-labs/mypy-baseline | Third-party tool (2024–2025) |
| Baseline - basedmypy | https://kotlinisland.github.io/basedmypy/baseline | Basedmypy fork documentation (2024) |
| Usage and community perspectives on mypy --no-implicit-reexport in library development | https://discuss.python.org/t/usage-and-community-perspectives-on-mypy-no-implicit-reexport-in-library-development/43600 | Python Discourse (2024) |
| Professional-grade mypy configuration | https://careers.wolt.com/en/blog/tech/professional-grade-mypy-configuration | Wolt Engineering Blog (2025) |
