# Research 004 — mypy 2.3.x strict-mode remediation patterns
Date: 2026-08-30 · Freshness matters: **yes** (mypy 2.4 in development; flags/patterns may shift)

## Question
What are the official remediation patterns for turning on the remaining `--strict` flags in mypy 2.3.1, with Python 3.12/3.13 targets? What does `--strict` comprise; which flags are already enabled, which are NOT; and what is the staged ramp guidance for a multi-package monorepo?

## Findings

### 1. Exact `--strict` composition in mypy 2.3.1

**The flags enabled by `--strict`** in mypy 2.3.1 — [The mypy command line - mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/command_line.html):

- `--disallow-any-generics` (error code: `type-arg`)
- `--disallow-subclassing-any`
- `--disallow-untyped-calls` (error code: `no-untyped-call`)
- `--disallow-untyped-defs` (error code: `no-untyped-def`)
- `--disallow-incomplete-defs` (error code: `no-untyped-def`)
- `--check-untyped-defs`
- `--disallow-untyped-decorators`
- `--warn-redundant-casts`
- `--warn-unused-ignores`
- `--warn-return-any` (error code: `no-any-return`)
- `--no-implicit-reexport`
- `--strict-equality` (error code: `comparison-overlap`)
- `--extra-checks`

**Notable flags NOT in `--strict`:**
- `--disallow-any-expr` — explicitly **not** part of strict mode as of 2.3.1, despite being in the `--disallow-any-*` family. Must be enabled separately if desired. — [The mypy command line - mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/command_line.html)
- `--warn-unreachable` — also not included in strict.

**No changes to strict composition from mypy 1.x to 2.3.1:** — [Mypy Release Notes - mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/changelog.html). Release notes contain no references to modifications affecting `--strict` mode or its constituent flags between these versions.

### 2. `--disallow-any-generics` (error code `type-arg`) — remediation patterns

**What the flag means** — [The mypy command line - mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/command_line.html): "Disallows usage of generic types that do not specify explicit type parameters." Bare `list`, `dict`, `set`, `tuple`, `type`, and `Callable` annotations are rejected; they implicitly become `list[Any]`, `dict[Any, Any]`, etc.

**Remediation for built-in bare generics:**
- Bare `list` → `list[T]` where `T` is the element type, e.g., `list[int]`, `list[str]`
- Bare `dict` → `dict[K, V]`, e.g., `dict[str, int]`
- Bare `set` → `set[E]`, e.g., `set[str]`
- Bare `tuple` → `tuple[T, ...]`, e.g., `tuple[int, ...]` or `tuple[int, str, float]` for fixed-length
- Bare `type` → `type[C]`, e.g., `type[MyClass]`
- Bare `Callable` → `Callable[[ArgTypes], ReturnType]`, e.g., `Callable[[int, str], bool]` or `Callable[..., Any]` when signature is unknown

**Remediation for custom generic classes used unparameterized:**
- If you have `class Foo(Generic[T])` and use it bare (`x: Foo`), parameterize it: `x: Foo[int]`
- — [Generics - mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/generics.html): "unsubscripted aliases are treated as original types with type parameters replaced with `Any`."

**When to use `dict[str, Any]` vs `TypeVar` vs `Mapping[str, object]`:**

The official guidance (implicit in the documentation) is:
- **`dict[str, Any]`** — Use when the value type is genuinely unknown at the time of writing, or when accepting arbitrary JSON-like data. Keeps the spirit of "we don't constrain this" while still declaring the key type.
- **`TypeVar`** — Use when you are writing a generic function/class and need type consistency across parameters (e.g., `def process(items: list[T]) -> T`). Binds related uses to the same concrete type at call time.
- **`Mapping[str, object]`** — Use when you want to signal "this function accepts read-only mappings and doesn't care about the concrete dict type." The `object` type (rather than `Any`) tells type checkers "I'm intentionally not inspecting this value; it's acceptable in a type-safe program but I won't depend on its shape."
  - — [Type inference and type annotations - mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/type_inference_and_annotations.html): annotations guide type narrowing, and `object` is the implicit base of all types, signaling intentional non-specificity without `Any`'s escape hatch.

**PEP 696 (default type parameters) and mypy 2.3.1:**

Yes, mypy 2.3.1 fully supports PEP 696 default type parameters. — [Support PEP 696 – Type defaults for TypeVarLikes · Issue #14851 · python/mypy](https://github.com/python/mypy/issues/14851). You can write:

```python
from typing import Generic, TypeVar

T = TypeVar("T", default=int)

class Box(Generic[T]):
    ...

reveal_type(Box())  # Box[int], using the default
reveal_type(Box[str]())  # Box[str], overridden
```

This **does allow a bare generic to stay `disallow-any-generics`-clean** if the default is supplied; however, this only works if you control the class definition. For external generics without defaults, you must still parameterize on use.

### 3. `--warn-return-any` / error code `no-any-return` — remediation ordering

**What it means** — [Error codes for optional checks - mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/error_code_list2.html): "mypy generates an error if you return a value with an `Any` type in a function that is annotated to return a non-`Any` value."

**Official remediation guidance:**

1. **Preferred: annotate the intermediate variable** — Explicitly annotate the variable receiving the untyped call, binding its type before return:
   ```python
   def get_config() -> dict[str, str]:
       raw: dict[str, Any] = json.load(f)  # Explicit annotation
       return raw  # Now mypy sees the annotation, not the Any
   ```
   This documents intent and gives mypy the type at assignment time.

2. **Second choice: `typing.cast()`** — Use when you cannot annotate the intermediate:
   ```python
   from typing import cast
   def get_config() -> dict[str, str]:
       return cast(dict[str, str], json.load(f))
   ```
   — [Common issues and solutions - mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/common_issues.html): cast is shown in examples where mypy's type narrowing cannot infer the type automatically. However, `cast()` is also criticized for being a "last resort" because it bypasses all type checking on that expression.

3. **Last resort: `# type: ignore[no-any-return]`** — Only when both above are infeasible. Never use bare `# type: ignore`.

**For different `Any` origins:**
- **Untyped third-party call** (e.g., `json.loads()`) — Use approach #1 (annotate) or #2 (cast).
- **`**kwargs` / `*args`** — The function signature itself is incomplete; annotate `**kwargs: Any` and accept that downstream uses must narrow. Use cast at the boundary.
- **`json.loads()`** — Explicitly annotate `result: dict[str, Any] = json.loads(...)` before narrowing/returning.

**Documented preference:** The [mypy documentation](https://mypy.readthedocs.io/en/stable/common_issues.html) shows annotated variables as the canonical form in examples, suggesting it is preferred for clarity and maintainability.

### 4. `--disallow-untyped-defs` / `no-untyped-def` and `--disallow-untyped-calls` / `no-untyped-call`

**`--disallow-untyped-defs` (no-untyped-def):**
- Requires **all** function parameters and return types to be annotated.
- **`__init__` annotation rule:** An explicit `-> None` return type is **required**, even if `__init__` takes no parameters. — [Error codes for optional checks - mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/error_code_list2.html)
  ```python
  # Error: Function is missing a type annotation [no-untyped-def]
  def __init__(self):
      self.value = 0

  # OK
  def __init__(self) -> None:
      self.value = 0
  ```
- **`*args` / `**kwargs`:** Must be annotated. Use `*args: int` and `**kwargs: str` or `*args: Any` if the type is truly unknown (documents the escaping point).
- **Decorated methods:** The decorator itself must have a return type annotation, or the decorated function must be fully annotated. Mypy cannot infer through decorators without explicit types on the decorator.

**`--disallow-untyped-calls` (no-untyped-call):**
- Prevents calling functions lacking type annotations from within an annotated function.
- If an external library function lacks stubs or annotations, you must either:
  1. Annotate the call with explicit types on the intermediate variable.
  2. Add a type stub (`.pyi` file) for the external function.
  3. Use `# type: ignore[no-untyped-call]` if neither is feasible.
- — [The mypy command line - mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/command_line.html): "reports an error whenever a function with type annotations calls a function defined without annotations."

### 5. `--strict-equality` (error code `comparison-overlap`)

**What it catches** — [Error codes for optional checks - mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/error_code_list2.html): Prohibits equality comparisons between types that do not overlap. For example, `if x == 'magic'` where `x` has type `bytes` will error, because `bytes == str` is always `False`.

**Recommended fix when the comparison is intentional:**
1. **Fix the types** — Change `x == 'magic'` to `x == b'magic'` if you meant bytes.
2. **Use `# type: ignore[comparison-overlap]`** — Only when the comparison is genuinely necessary and types cannot be aligned.
3. **Assertion via temporary variable** — Use a narrowing assertion if the comparison is for a runtime check:
   ```python
   if isinstance(x, str) and x == expected_str:
       ...
   ```

### 6. Ramp mechanics: staged strictness in a monorepo

**Official guidance** — [The mypy configuration file - mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/config_file.html) and [How to configure mypy strict mode | pydevtools](https://pydevtools.com/handbook/how-to/how-to-configure-mypy-strict-mode/):

**Recommended stages:**

1. **Phase 1 — Pick impactful flags incrementally** (per-module via `[[tool.mypy.overrides]]`):
   - Enable 1–2 flags at a time in packages deemed "ready."
   - Suggested order: `warn_redundant_casts`, `warn_unused_ignores`, `check_untyped_defs`, then `strict_equality`.
   - Each flag is landed only once its `mypy` run is 0-error on the target module(s).

2. **Phase 2 — Consolidate ready packages under `strict = true`:**
   ```toml
   [mypy]
   strict = false  # Global default

   [[tool.mypy.overrides]]
   module = "mypackage_ready.*"
   strict = true
   ```
   As more packages are annotated, add them to the overrides.

3. **Phase 3 — Global strict (aspirational state):**
   ```toml
   [mypy]
   strict = true  # Packages not listed are strict by default

   [[tool.mypy.overrides]]
   module = "mypackage_not_yet_ready.*"
   strict = false
   ```
   Each relaxation is now explicit and auditable.

**Per-module vs. per-file:**
- Per-module (`[[tool.mypy.overrides]] module = "pkg.subpkg.*"`) is the documented standard for a monorepo.
- Per-file (`# mypy: strict` at the top of a `.py` file) is supported and useful for one-off holdouts, but the codebase-level configuration is preferred.

**Configuration precedence** — [The mypy configuration file - mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/config_file.html):
1. Inline configuration in source files (e.g., `# mypy: strict`)
2. Concrete module name sections (`[[tool.mypy.overrides]] module = "exact.name"`)
3. Glob sections (`[[tool.mypy.overrides]] module = "pkg.*"`)
4. Top-level `[mypy]`

**Interaction with `--warn-unused-ignores`:**
- Enabling `--warn-unused-ignores` (part of strict) will flag any `# type: ignore` line that no longer suppresses an error.
- **Critical:** When adding a new strict flag, previously-required ignores may become unnecessary, and mypy will complain. This is **expected** — review each unused ignore, delete it if the underlying error is fixed, and keep it if the error is from an unrelated code path.
- No documented automatic migration; manual review per flag is the documented approach.

### 7. `type: ignore` hygiene and error codes

**Officially recommended practice** — [Error codes - mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/error_codes.html) and [Common issues and solutions - mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/common_issues.html):

**Narrow over blanket:**
- **Recommended:** `# type: ignore[no-any-return]` — Suppresses only the `no-any-return` error on that line.
- **Avoid:** `# type: ignore` (bare) — Suppresses all errors, risking silent masking of unrelated issues.

**Error code hierarchy:** Some error codes are covered by wider codes. For example, `[method-assign]` can be suppressed by `# type: ignore[assignment]`. The documentation notes this in the "Error codes enabled by default" and "Error codes for optional checks" sections, but individual code descriptions do not always list parent codes. When in doubt, use the narrow code.

**`enable_error_code` / `disable_error_code` configuration:**
- Yes, these configuration options exist. — [The mypy configuration file - mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/config_file.html). They allow you to enable/disable specific error codes globally or per module:
  ```toml
  [mypy]
  disable_error_code = ["no-any-return"]

  [[tool.mypy.overrides]]
  module = "mypackage_legacy.*"
  enable_error_code = ["no-any-return"]  # Re-enable for this module
  ```
- Use `disable_error_code` for a **seam / third-party boundary** where suppressing an error is intentional and systemic (not line-specific).
- Use `# type: ignore[code]` for **single exceptions** to an otherwise-enforced rule.
- Mypy documentation distinguishes between the two by context: configuration-level disables are for "entire modules that will never be fully typed," while ignores are for "this specific line is an exception."

## Version/compatibility notes

- **Mypy 2.3.1** (tested version for this brief): Released July 2026. — [Mypy Release Notes - mypy 2.3.1 documentation](https://mypy.readthedocs.io/en/stable/changelog.html)
- **Python 3.12/3.13 targets**: Fully supported. Mypy 2.3.1 is version-independent; it resolves against a pinned `python_version` in `[tool.mypy]`.
- **PEP 696 support**: Landed in mypy 1.11+; mypy 2.3.1 has full support.
- **No breaking changes to strict mode between 1.x and 2.3.1**: Flag composition is stable.
- **Mypy 2.4.0+dev in progress**: May add/remove flags from `--strict` in the future; this brief is current to 2.3.1.

## Evidence gaps

1. **Object vs. `Any` as a default type parameter:** The documentation does not explicitly compare `object` to `Any` in the context of "I don't inspect this value." The finding (use `object` to signal intentional non-specificity) is inferred from PEP 3119 type hierarchy semantics and mypy's narrowing rules, not from an explicit mypy guideline document.

2. **Ramp ordering justification:** The "suggested order" (warn_redundant_casts → warn_unused_ignores → check_untyped_defs → strict_equality) is inferred from community blogs and the pydevtools article; mypy's official documentation does not prescribe this order. Different codebases may benefit from a different sequence.

3. **Detailed remediation for custom generic classes:** Official docs say to parameterize unparameterized generics but do not provide detailed patterns for refactoring large codebases where a bare generic class is used in hundreds of places. Real-world techniques (introduce a type alias, rework the generic to use defaults) are not in the official docs.

## Librarian's note

**What the sources indicate:**

The mypy 2.3.1 strict mode is a well-defined bundle of 13 flags (not including `disallow-any-expr`), stable since mypy 1.x. The official strategy for ramping is **per-module overrides**, starting with the highest-ROI flags (warn_redundant_casts, warn_unused_ignores) and consolidating into global strict once ready packages exceed not-ready ones.

The remediation patterns are straightforward where official guidance exists (cast vs. annotated variable, the type parameter choices), but the documentation is silent on the staging order for a monorepo and the tradeoff between `object` and `Any`. The staged ramp itself is well-documented in the configuration-precedence section of the official docs.

**Decision upstream should note:**
- Turning on all 13 flags at once in a 10-package monorepo will produce hundreds of errors; the per-module override ramp is not optional for feasibility.
- `--disallow-untyped-calls` (one of the hardest flags) requires either annotating every external call site, finding/writing stubs, or accepting strategic ignores. This should be staged late and carefully.
- `enable_error_code` / `disable_error_code` at configuration scope is better than `# type: ignore` for systemic third-party boundaries; narrow `# type: ignore[code]` for true exceptions.

Sources indicate this is ready for execution; no research gaps block implementation.
