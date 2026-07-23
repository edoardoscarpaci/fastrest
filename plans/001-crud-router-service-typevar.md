# Plan 001 — Typed concrete service on `VarcoCRUDRouter` (6th `S` TypeVar)

## Goal
Let a router subclass declare its concrete `AsyncService` type so a static checker
sees custom service methods (e.g. `compile`/`validate`/`evaluate`) on `self._service`
without any LSP-invariance error and with zero per-subclass boilerplate. After this
plan, `class RuleRouter(CRUDRouter[Rule, UUID, RuleCreate, RuleRead, RuleUpdate, RuleService])`
types `self._service` as `RuleService | None` and `self.service` as `RuleService`.

## Non-goals
- No runtime behavior change to CRUD dispatch, DI injection, task recovery, or auth.
- Do NOT touch `VarcoRouter`'s 5-parameter public generic arity (it stays `Generic[D, PK, C, R, U]`).
- Do NOT modify the downstream `opa_dsl` repo (out of this workspace); only make the
  varco-side change that lets that comment/workaround be deleted.
- No new backend, no service-layer change in `varco_core`.

## Problem statement (verified)
- `varco_fastapi/router/crud.py:189` declares
  `_service: ClassVar[AsyncService[D, PK, C, R, U] | None]`.
- Two independent defects:
  1. **TypeVars inside `ClassVar` are illegal** for type checkers ("ClassVar cannot
     contain type variables"). The current annotation is already ill-formed; pyright/mypy
     in strict mode flag it. At best it degrades to the erased `AsyncService[Any,...]`.
  2. **No narrowing seam.** A subclass assigning `_service = concrete_rule_service`
     is seen as the declared base `AsyncService`, so `self._service.compile` is unknown.
     Re-declaring `_service` with a narrower type in the subclass is an LSP-invariance
     error because `ClassVar` is invariant (this is exactly the workaround comment at
     `opa_fastapi/opa_fastapi/routers.py:111-118`).
- Verified facts:
  - Only `VarcoCRUDRouter` declares `_service` (base `VarcoRouter` merely does
    `getattr(self, "_service", None)` at `base.py:792`; no `_service` annotation there).
  - Runtime value is always a plain attribute: `self._service = service` (`crud.py:239`)
    or a class-level assignment in tests (`_service = _MockService()`); lookup is via
    `getattr`/MRO, so **removing `ClassVar` changes nothing at runtime**.
  - `requires-python = ">=3.12"` across the workspace. PEP 696 (`TypeVar(default=...)`)
    is native only on 3.13 → must use `typing_extensions.TypeVar` for the default.
    `typing_extensions` is NOT currently a declared dependency of `varco_fastapi`.
  - `_resolve_type_args` (`base.py:363`) walks `__orig_bases__` for any origin that is a
    subclass of `VarcoRouter` and returns ALL its `get_args(...)`. Callers at
    `base.py:615` and `crud.py:280` index positions 0..4 (D,PK,C,R,U). A 6-arg base would
    make it return a 6-tuple with the service type at index 5 → must be sliced off.

## Design (recommended: Option A — 6th TypeVar `S`, instance annotation, typed `service` property)

Add an optional, defaulted sixth TypeVar `S` (the concrete service type) to
`VarcoCRUDRouter` and the CRUD presets, change `_service` from an (illegal) `ClassVar`
to an **instance-level annotation** typed `S | None`, and add a convenience
`service` property typed `S`.

```
VarcoRouter[D, PK, C, R, U]                         # unchanged, 5 params
        ▲
VarcoCRUDRouter[D, PK, C, R, U, S = AsyncService[Any,Any,Any,Any,Any]]
        _service: S | None          # instance annotation (NOT ClassVar)
        @property service -> S      # non-optional accessor, raises if unset
        ▲
CRUDRouter / ReadOnlyRouter / WriteRouter / NoDeleteRouter   # thread S through
```

Key mechanics:
- `S = TypeVar("S", bound="AsyncService[Any, Any, Any, Any, Any]", default="AsyncService[Any, Any, Any, Any, Any]")`
  using `typing_extensions.TypeVar` (PEP 696 default; must be the LAST type param).
- `class VarcoCRUDRouter(VarcoRouter[D, PK, C, R, U], Generic[D, PK, C, R, U, S])`.
- Replace the `ClassVar` line with an instance annotation:
  `_service: S | None` (no `ClassVar`, no default value — annotation only, exactly as
  now, but TypeVar-in-ClassVar illegality removed and narrowing enabled).
- `service` property returns `S` and raises `RuntimeError` if `_service is None`
  (ergonomic: `self.service.compile(...)` avoids the `| None` at every call site).
- `__init__` `service:` param stays `Inject[AsyncService[D, PK, C, R, U]] | None` — DI
  still resolves the base interface; the class TypeVar `S` provides the *static narrowing*
  seam for subclasses. (Do not tie the ctor param to `S`; DI resolves by the injected
  interface, and forcing `S` there would over-constrain the DI signature.)

Backward compat:
- `S` has a PEP 696 default, so 5-arg subscription (`CRUDRouter[D, PK, C, R, U]`) and all
  existing preset internals keep working; `S` resolves to `AsyncService[Any,...]`.
- Removing `ClassVar` is runtime-invisible; the test idiom `_service = _MockService()` at
  class level still resolves via MRO (`test_router_split.py:100-116` unaffected).

### Alternatives considered
- **(B) Typed accessor only, no TypeVar** (`service_as(RuleService)` / subclass `@property`
  returning `cast(RuleService, self._service)`): ✅ zero library-arity change, works on
  3.12 with no new deps. ❌ Per-subclass boilerplate on every router; does not make
  `self._service` itself correctly typed; fails the "easier to use correctly" goal.
- **(C) `get_service(type_)` generic getter**: ✅ no arity change. ❌ Verbose at every
  call site (`self.service_as(RuleService).compile(...)`); leaks the concrete type at use
  sites instead of declaring it once. Rejected.
- **(D) Docs-only property idiom**: ✅ zero code change. ❌ Still boilerplate per router;
  doesn't fix the pre-existing illegal TypeVar-in-ClassVar. Rejected as the primary fix,
  but the property idiom is documented as the fallback for users who cannot adopt the 6th
  TypeVar.
- **Drop `ClassVar`, keep fixed `AsyncService[D,PK,C,R,U]` instance annotation (no `S`)**:
  ✅ fixes the illegal-ClassVar defect, no new dep. ❌ Still no narrowing — subclass
  `_service` is seen as base `AsyncService`, `.compile` remains unknown. Insufficient alone.

Chosen: **(A)** because it is the only option that makes `self._service` correctly typed
with zero per-subclass boilerplate while fixing the pre-existing ClassVar defect. The
`typing_extensions` dependency is cheap and already transitively present via FastAPI/pydantic.

## Steps
TDD-ordered. Type-level assertions are the primary tests here (this is a typing feature),
plus runtime regression tests.

1. [ ] `varco_fastapi/pyproject.toml` — add `"typing-extensions>=4.12"` to
   `[project].dependencies` (needed for `TypeVar(default=...)` on Python 3.12). Run
   `uv sync` afterward.

2. [ ] `varco_fastapi/tests/router/test_service_typevar.py` (new) — **failing-first**
   runtime tests:
   - `test_six_arg_subscription_runtime`: define
     `class RuleRouterT(CRUDRouter[_Rule, UUID, _RC, _RR, _RU, _RuleService])` with a
     custom `@route` method calling `self.service.custom_method()`; instantiate with a
     `_RuleService` fake, `build_router()`, and assert the custom route dispatches and
     returns the expected value. Asserts 6-arg subscription works at runtime.
   - `test_five_arg_subscription_still_works`: `CRUDRouter[_Rule, UUID, _RC, _RR, _RU]`
     (default `S`) builds and dispatches standard CRUD — backward-compat guard.
   - `test_classvar_service_fallback`: set `_service = _FakeService()` at class level
     (no DI), assert `getattr(self, "_service")` and `self.service` resolve it — guards
     the removed-`ClassVar` runtime equivalence.
   - `test_service_property_raises_when_unset`: a router with `_service = None` →
     `router.service` raises `RuntimeError` with a clear message.
   - `test_resolve_type_args_ignores_service_arg`: a 6-arg subclass →
     `_resolve_type_args(cls)` returns exactly the 5 model args (D,PK,C,R,U), NOT 6, so
     CRUD model resolution is unaffected.

3. [ ] `varco_fastapi/varco_fastapi/router/crud.py` — implementation:
   - Import `TypeVar` from `typing_extensions`; import `AsyncService` at runtime (it is
     currently only under `TYPE_CHECKING` at `crud.py:88` — the `bound=`/`default=` string
     forward-refs mean a runtime import is NOT required, keep it under `TYPE_CHECKING` and
     use string forms in the `TypeVar` args to avoid a circular/runtime import).
   - Define `S = TypeVar("S", bound="AsyncService[Any, Any, Any, Any, Any]",
     default="AsyncService[Any, Any, Any, Any, Any]")` (module level, after the D..U import).
   - Change class header `class VarcoCRUDRouter(VarcoRouter[D, PK, C, R, U])` →
     `class VarcoCRUDRouter(VarcoRouter[D, PK, C, R, U], Generic[D, PK, C, R, U, S])`
     (add `Generic` import from `typing`).
   - Replace `crud.py:189` `_service: ClassVar[AsyncService[D, PK, C, R, U] | None]`
     with instance annotation `_service: S | None` (keep the surrounding comment block,
     update it to explain the S-narrowing seam and why it is no longer a `ClassVar`).
   - Add a `service` property:
     ```python
     @property
     def service(self) -> S:
         """Concrete, non-optional service accessor (see class docstring)."""
         svc = getattr(self, "_service", None)
         if svc is None:
             raise RuntimeError(...)  # clear message: service not injected/set
         return svc  # type: ignore[return-value]  (narrowed by S at subclass sites)
     ```
   - Update the class docstring: document the new `S` type parameter, the
     `CRUDRouter[..., ConcreteService]` usage, and the `self.service` accessor, with a
     `DESIGN:` block (✅ zero-boilerplate typed service / fixes illegal TypeVar-in-ClassVar;
     ❌ adds a 6th type param + `typing_extensions` dep) per coding-practice skill.

4. [ ] `varco_fastapi/varco_fastapi/router/base.py` — make `_resolve_type_args`
   (`base.py:363`) robust to the optional 6th arg: after collecting `args`, if the matched
   origin is (a subclass of) `VarcoCRUDRouter` and `len(args) == 6`, slice to `args[:5]`
   before returning so model resolution (callers at `base.py:615`, `crud.py:280`) still
   receives exactly `(D, PK, C, R, U)`. Update the function docstring Edge cases to note
   the trailing service-type arg is dropped. Add nothing to `VarcoRouter`'s arity.

5. [ ] `varco_fastapi/varco_fastapi/router/presets.py` — thread `S` through the presets so
   6-arg subscription is exposed to end users:
   - Add `S = TypeVar("S")` alongside the existing D..U TypeVars (plain `typing.TypeVar`
     is fine here — presets don't need the default; they inherit the default from
     `VarcoCRUDRouter`). Actually declare `S` with the same default via `typing_extensions`
     to keep 5-arg preset subscription valid: `S = TypeVar("S", default=...)`.
   - For each of `CRUDRouter`, `ReadOnlyRouter`, `WriteRouter`, `NoDeleteRouter`: change
     `VarcoCRUDRouter[D, PK, C, R, U]` → `VarcoCRUDRouter[D, PK, C, R, U, S]` and add
     `Generic[D, PK, C, R, U, S]` to the bases (import `Generic`). Verify MRO still lands
     `VarcoCRUDRouter` correctly (mixins first, then the parametrized base).
   - `AllRouteMixin` and `GenericRouter` are unchanged (no service arity).
   - Update the module + `CRUDRouter` docstrings to show
     `CRUDRouter[Order, UUID, C, R, U, OrderService]` and `self.service` usage.

6. [ ] `varco_fastapi/tests/router/test_service_typevar.py` — add a static-typing guard
   that runs under the project's checker if one is configured; since there is no configured
   type-check command, encode the intent as `reveal_type`-style comments plus a
   `typing.get_type_hints`-based runtime assertion is NOT reliable for TypeVars — instead
   add an `# pyright: strict` header to a tiny fixture module
   `varco_fastapi/tests/router/_typing_fixtures/rule_router_typed.py` that:
   - subclasses `CRUDRouter[..., _RuleService]`, calls `self._service.compile(...)` and
     `self.service.validate(...)`, and is expected to type-check clean. Document in the test
     module that this file is the human/pyright-verifiable proof (CI has no type gate today;
     see Risks).

7. [ ] Docs — same change, per project memory (docs updated in the SAME change):
   - `varco_fastapi/README.md` — add a subsection under the CRUD router docs showing the
     6th `S` param + `self.service` accessor, and the fallback property idiom for anyone
     staying on 5 args.
   - `CLAUDE.md` — under "Scenario: Build a service-free / data-processing REST server" or a
     new short CRUD scenario, note: "To expose custom service methods on a CRUD router,
     add the concrete service as the 6th type arg: `CRUDRouter[D, PK, C, R, U, MyService]`,
     then use `self.service.<method>()`." Add a Pitfalls-table row: *"Custom service method
     unknown on `self._service`" → declared without the 6th `S` type arg → subscript
     `CRUDRouter[..., ConcreteService]`.*
   - `ARCHITECTURE.md` — update the `VarcoCRUDRouter` entry to reflect 6 type params
     `[D, PK, C, R, U, S]` and the `service` property.

## Edge cases
- 5-arg subscription (`CRUDRouter[D, PK, C, R, U]`) → `S` resolves to
  `AsyncService[Any,...]`; identical to today. Must remain valid.
- `_service = _MockService()` set at class level (no DI) → resolves via MRO at runtime;
  `self.service` returns it; type checker sees `S` (or `Any` default).
- `_service is None` (never injected/set) → `self.service` raises `RuntimeError`;
  `self._service` stays `None` for the existing 501-Not-Implemented CRUD fallback path
  (unchanged — that path reads `getattr(self, "_service", None)` directly, not `.service`).
- 6-arg subclass passed to `_resolve_type_args` → returns the first 5 (model) args only.
- DI injection: `__init__(service=...)` still assigns `self._service`; static type at the
  subclass is `S` because the class is parametrized, independent of the ctor param type.
- Python 3.13 runtime → `typing_extensions.TypeVar` re-exports/accepts `default=`; no
  behavioral difference.

## Verification
```bash
# From workspace root
uv sync
uv run pytest varco_fastapi/tests/router/test_service_typevar.py -v
# Full router regression (ensure no arity/MRO breakage)
uv run pytest varco_fastapi/tests/ -k router
# Backward-compat: existing split-router test using ClassVar _service fallback
uv run pytest varco_fastapi/tests/milestone_e/test_router_split.py -v
# Optional (no gate configured): manual static check of the typed fixture
#   pyright varco_fastapi/tests/router/_typing_fixtures/rule_router_typed.py
```

## Risks
- **No configured type checker in CI** — the core value (static narrowing) can only be
  verified by pyright/mypy, which the repo does not run. Mitigation: ship the
  `_typing_fixtures/rule_router_typed.py` proof file with an `# pyright: strict` header and
  document the manual `pyright` command; runtime tests still guard arity/MRO/property.
- **PEP 696 default ordering** — `S` MUST be the last type parameter everywhere it appears
  (`VarcoCRUDRouter`, each preset). Invariant: no non-defaulted TypeVar may follow `S`.
- **`Generic[...]` + existing base MRO** — adding `Generic[D,PK,C,R,U,S]` alongside
  `VarcoRouter[D,PK,C,R,U]` must not create an inconsistent MRO or duplicate-TypeVar error.
  Verify `VarcoCRUDRouter.__mro__` and each preset instantiates. Invariant: `VarcoRouter`
  keeps exactly 5 params.
- **`_resolve_type_args` slice** — if the 6th arg is not stripped, CRUD model resolution
  could pick up the service type as a model. Invariant: callers receive exactly 5 model
  args. Covered by step-2 test `test_resolve_type_args_ignores_service_arg`.
- **`typing_extensions` version** — `default=` needs `typing_extensions>=4.4` (PEP 696
  support landed there); pin `>=4.12` to be safe.
