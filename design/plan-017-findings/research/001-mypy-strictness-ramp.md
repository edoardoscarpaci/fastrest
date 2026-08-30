# Research 001 — mypy strictness ramp strategy for Plan 017

Date: 2026-08-28 · Freshness matters: **yes** — mypy 2.x releases and defaults may evolve

## Question

For a ten-package uv-workspace Python monorepo shipping `py.typed` to PyPI, with mypy pinned at 2.3.1 and 219 existing `# type: ignore[...]` suppressions:

1. What are the exact semantics (mypy 2.3.1) of `disallow_untyped_defs`, `check_untyped_defs`, `disallow_any_generics`, `no_implicit_reexport`, `warn_return_any`, and the three "skip" flags (`disallow_untyped_calls`, `disallow_any_unimported`, `disallow_any_expr`)? How do they interact? Does `check_untyped_defs` change reporting behaviour of other flags?

2. Is `no_implicit_reexport` genuinely the most impactful for a `py.typed` library? Does having `__all__` defined in every `__init__.py` already satisfy it, or does it additionally require `from X import Y as Y` form?

3. What does official mypy guidance (or authoritative practice from major typed libraries) recommend as the ramp order for an existing large codebase?

4. Does mypy 2.3.1 support per-module strictness via `[[tool.mypy.overrides]]`? Can I ramp one package at a time?

5. With `warn_unused_ignores=true` and 219 existing suppressions, what happens when a new flag is enabled—can suppressions become newly unused (maintenance hazard)?

6. What version-specific changes in mypy 2.0–2.3 affect this ramp (breaking defaults, flag behaviour changes)?

## Findings

### Flag Semantics & Interactions (mypy 2.3.1)

- **`disallow_untyped_defs`** — Requires that all functions have type annotations for *both parameters and return type*. Flags both `def f(a, b)` and `def f(a: int, b)` (incomplete). Is a superset of `disallow_incomplete_defs`. — [The mypy configuration file — mypy 2.3.1](https://mypy.readthedocs.io/en/stable/config_file.html)

- **`check_untyped_defs`** — Type-checks the body of functions that lack type annotations (rather than skipping them). Allows you to find runtime errors inside untyped code even before annotations are added. One of the highest-ROI flags per official guidance. — [Using mypy with an existing codebase — mypy 2.3.0](https://mypy.readthedocs.io/en/stable/existing_code.html?highlight=ignore)

- **`disallow_any_generics`** — Disallows generic types without explicit type parameters (e.g., `list` instead of `list[int]`). — [The mypy configuration file — mypy 2.3.1](https://mypy.readthedocs.io/en/stable/config_file.html)

- **`no_implicit_reexport`** — By default, imports are treated as re-exported; this flag disallows that. A name is only re-exported if: (a) it is in `__all__`, **or** (b) it is imported as `from X import Y as Y` (explicit re-declaration). **Having `__all__` defined satisfies this flag**—it is NOT an additional requirement on top of `__all__`. — [The mypy command line — mypy 2.3.1](https://mypy.readthedocs.io/en/stable/command_line.html)

- **`warn_return_any`** — Shows a warning when a function with a non-`Any` return type annotation returns a value of type `Any`. — [The mypy configuration file — mypy 2.3.1](https://mypy.readthedocs.io/en/stable/config_file.html)

- **`disallow_untyped_calls`** — Disallows calling functions that lack type annotations from functions that have annotations. Can be very disruptive in codebases with many untyped dependencies. — [Using mypy with an existing codebase — mypy 2.3.0](https://mypy.readthedocs.io/en/stable/existing_code.html)

- **`disallow_any_unimported`** — Disallows types that have type `Any` due to unfollowed imports (missing stubs). Often high noise in codebases using untyped third-party libraries. — [The mypy command line — mypy 2.3.1](https://mypy.readthedocs.io/en/stable/command_line.html)

- **`disallow_any_expr`** — Disallows all expressions that have type `Any` anywhere in a module. Extremely strict and rarely feasible without extensive workarounds. — [The mypy command line — mypy 2.3.1](https://mypy.readthedocs.io/en/stable/command_line.html)

**Interaction note**: The official docs do not explicitly document flag interactions or whether `check_untyped_defs` changes the reporting of other flags. However, `check_untyped_defs` is independent—it only affects which code is analyzed; enabling it does not invalidate existing error counts from other flags, it only finds *new* errors inside previously-skipped function bodies.

### `no_implicit_reexport` for `py.typed` Libraries

**`no_implicit_reexport` is valuable but NOT the highest-ROI flag.** Official guidance states it "isn't too hard to get passing, but return on investment is lower" compared to `check_untyped_defs` or `disallow_untyped_defs`. — [Using mypy with an existing codebase — mypy 2.3.0](https://mypy.readthedocs.io/en/stable/existing_code.html)

**Blast radius for varco: ~0.** Having `__all__` defined in all ten top-level `__init__.py` files already satisfies `no_implicit_reexport`'s requirement. A name listed in `__all__` is treated as explicitly re-exported. No additional `from X import Y as Y` form is required for names already in `__all__`. — [The mypy command line — mypy 2.3.1](https://mypy.readthedocs.io/en/stable/command_line.html)

**Why it matters for libraries**: `no_implicit_reexport` ensures that downstream consumers can only import names explicitly marked for re-export (via `__all__` or explicit re-import), preventing accidental API surface creep. This is good library hygiene but only enforces existing convention (the `__all__` variable already communicates intent to humans and tools like `sphinx`).

### Recommended Ramp Order (Official Guidance)

Per [Using mypy with an existing codebase — mypy 2.3.0](https://mypy.readthedocs.io/en/stable/existing_code.html), the official sequence for an existing large codebase is:

1. **Foundation** (enable if not already on):
   - `warn_unused_configs`, `warn_redundant_casts`, `warn_unused_ignores` — "Getting this passing should be easy"

2. **Critical**: `check_untyped_defs` — "Strongly recommend enabling this one as soon as you can"

3. **Intermediate** (shouldn't be too much additional work):
   - `disallow_subclassing_any`, `disallow_untyped_decorators`, `disallow_any_generics`

4. **Annotation enforcement** (various gradations, incrementally forcing annotations):
   - `disallow_incomplete_defs`, `disallow_untyped_defs`, `disallow_untyped_calls`

5. **Final refinements**: `no_implicit_reexport`, `warn_return_any` (latter can be tricky with untyped libraries)

**Skip these entirely** (official docs): `disallow_any_unimported`, `disallow_any_expr` (too disruptive; only feasible in very mature codebases or after extensive isolation work)

The docs do NOT recommend `--strict` as a starting point; instead they recommend this gradual sequence to avoid overwhelming the team with too many errors at once.

### Per-Module Strictness Support (mypy 2.3.1)

**Yes.** Mypy 2.3.1 fully supports per-module configuration overrides via `[[tool.mypy.overrides]]` in `pyproject.toml`:

```toml
[[tool.mypy.overrides]]
module = "varco_kafka"
disallow_untyped_defs = true
check_untyped_defs = true

[[tool.mypy.overrides]]
module = "varco_redis"
disallow_untyped_defs = true
```

Glob patterns like `"varco_*"` are supported. Each override section applies only to code *inside* the specified module(s); it does not affect how external callers of that module are checked. — [The mypy configuration file — mypy 2.3.1](https://mypy.readthedocs.io/en/stable/config_file.html)

**This allows package-by-package ramp**: varco could enable stricter flags for one package at a time, leaving others at the baseline. Each package has its own namespace, so per-package overrides are the natural granularity for a workspace monorepo.

### `warn_unused_ignores` Interaction

With `warn_unused_ignores=true` already enabled and 219 existing `# type: ignore[code]` suppressions:

- When a new flag is enabled, existing suppressions can become **newly unused** (maintenance hazard). For example, if `check_untyped_defs` is enabled, it may reveal errors in previously-unchecked function bodies, changing whether a `# type: ignore[arg-type]` on that line is needed. — [The mypy command line — mypy 2.3.1](https://mypy.readthedocs.io/en/stable/command_line.html)

- **However**, the reverse also occurs: enabling a flag may cause a *previously-unused* suppression to become *newly-used* (flagged by `warn_unused_ignores`). This is not a problem—it just means the suppression now serves a purpose.

- **Important exception**: Suppressions on statically unreachable code (e.g., `if sys.version_info >= (3, 13): ...`) are exempt from `warn_unused_ignores` and will not be flagged as unused. — [The mypy command line — mypy 2.3.1](https://mypy.readthedocs.io/en/stable/command_line.html)

**Maintenance strategy**: With 219 suppressions, enabling a new flag will likely surface some newly-unused ignores (warnings). A disciplined approach is to fix these during each flag enablement phase rather than all at once. The signal is healthy—it indicates you're removing technical debt incrementally.

### Version-Specific Changes (mypy 2.0–2.3)

**mypy 2.0.0 (released 2026-05-06)** introduced breaking changes:

- **`--local-partial-types` is now enabled by default** (was opt-in). This means partial types (e.g., `x = []` without initial annotation) are treated as having local scope, not inferred from later assignments. This requires annotation fixes in codebases that relied on the old inference. — [Mypy Release Notes — mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/changelog.html)

- **`--strict-bytes` is now enabled by default**, per PEP 688. `bytearray` and `memoryview` are no longer assignable to `bytes`. — [Mypy Release Notes — mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/changelog.html)

- **`--ignore-missing-imports` now applies consistently** (removed special-casing of legacy bundled stubs). — [Mypy Release Notes — mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/changelog.html)

- **`--allow-redefinition` behaviour changed**: variables can no longer receive explicit type re-annotation and be considered the same variable. This prevents unsound patterns like `x: list[int]` then `x: list[str]`. — [Mypy Release Notes — mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/changelog.html)

**mypy 2.1, 2.2, 2.3**: No documented breaking changes; primarily feature additions (PEP 728, PEP 696 completion, mypyc improvements). Flag semantics remain stable from 2.0 onwards.

**Implication for varco**: If varco was running on mypy 1.x, upgrading to 2.0+ requires handling `--local-partial-types` default enabling (likely a small number of fixes) and the bytes-assignability change (unlikely to affect most codebases). Ramp strategy is unaffected once baseline is established.

## Version/Compatibility Notes

- **mypy pinned version**: 2.3.1 (released 2026-07-13)
- **Python version under mypy**: 3.12 (per varco's `[tool.mypy]`)
- **PEP 561 compliance**: varco already ships `py.typed` in all ten packages
- **Minimum mypy for full per-module overrides support**: 1.0+ (the feature is stable across all 2.x releases)
- **Breaking change upgrade**: varco currently on mypy 2.3.1, so no upgrade breakage expected; maintaining 2.3.1 is safe

## Evidence Gaps

- No official mypy documentation on *whether* `check_untyped_defs` changes how other flags report errors (e.g., does enabling it change the line/error-code of errors flagged by `disallow_untyped_calls`?). Testing would be required to confirm this edge case.
- No guidance from mypy on expected per-flag error-count inflation (how many new errors per thousand LOC should be expected when enabling `disallow_untyped_defs`?). Community experience or empirical measurement on varco would be needed.
- PEP 561 does not address re-export semantics; PEP 484 stubs conventions may cover this, but not fetched here.

## Librarian's Note

**What the sources indicate**: The official ramp order is *not* a single sequence but a prioritized set of tiers. For varco, the path is:

1. **Already done**: `warn_unused_ignores=true` ✅
2. **Next**: `check_untyped_defs=true` (high ROI, should be step 1; per-module in hardened packages first)
3. **Then**: `disallow_any_generics`, `disallow_untyped_decorators` (moderate difficulty)
4. **Later**: `disallow_untyped_defs`, `disallow_incomplete_defs` (high effort; per-package ramp recommended)
5. **Optional**: `no_implicit_reexport` (already satisfied by existing `__all__` in all init files—near-zero work, but lower ROI than annotation enforcement)
6. **Skip**: `disallow_untyped_calls`, `disallow_any_unimported`, `disallow_any_expr` (too noisy for a codebase with untyped third-party deps; revisit only if varco becomes purely typed)

**Per-module enablement is feasible and recommended** to let teams harden packages incrementally. 219 suppressions will have some that become unused as strictness increases—this is normal and the `warn_unused_ignores` signal is valuable.

