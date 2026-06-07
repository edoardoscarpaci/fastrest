---
name: griffe-warning-patterns
description: Root causes and fixes for the 82 griffe/mkdocstrings strict-build warnings resolved on 2026-06-07
type: project
---

Six recurring griffe warning patterns were found and fixed across the codebase.
All 82 warnings eliminated in one pass; `make docs-strict` now exits 0.

## Pattern 1 — "Failed to get 'exception: description' pair" (50 warnings)

**Cause:** `Raises:` entries written as free prose instead of `ExceptionType: description`.
Examples that broke:
- `Nothing — validators collect errors rather than raising immediately.`
- `Any exception raised inside the async with body is propagated unchanged.`
- `Any store-specific error from store.save().`

**Fix:** Every `Raises:` item must start with an exception name, even if the
function never raises. Use a plausible type as a signal:
- `Nothing — ...` → `ValidationError: Never raised directly — ...`
- `Any exception from X` → `Exception: Any exception from X propagates unchanged.`
- `Never raises SchemaDrift` → `SchemaDrift: Never raised by this method — ...`

## Pattern 2 — "No type or annotation for parameter" + "Parameter does not appear in the function signature" (18 warnings, often paired)

**Cause:** Class docstrings documenting constructor params that don't match the actual
`__init__` signature. Three sub-cases:
1. Class-level `Args:` documenting injected config fields (e.g. `base`, `session_factory`,
   `mongo_client`) when the real `__init__` takes a single `settings: Inject[...]` param.
2. Exception classes with no explicit `__init__` documenting `message:` (inherited from
   `Exception`; griffe can't see it).
3. Protocol classes documenting TypeVar parameters (e.g. `R_co`) as if they were Args.

**Fix:** Move the description into prose in the class body. Never use `Args:` for
params that don't appear in the actual signature griffe sees.

## Pattern 3 — Middleware `app` and `call_next` untyped (8 warnings)

**Cause:** Starlette `BaseHTTPMiddleware` subclasses had `app` in `__init__` and
`call_next` in `dispatch` without type annotations.

**Fix:** Import `ASGIApp` from `starlette.types` and `RequestResponseEndpoint` from
`starlette.middleware.base`, then annotate both params.

## Pattern 4 — "Type parameter does not appear in the class signature" (2 warnings)

**Cause:** `Type parameters:` docstring section heading with `K:` / `V:` entries on
a `Protocol[K, V]` class. Griffe treats this section like `Args:` and can't find
`K`/`V` in the `__init__` signature.

**Fix:** Remove the `Type parameters:` section entirely; describe `K` and `V` in
inline prose instead.

## Pattern 5 — "Confusing indentation for continuation line" (4 warnings)

**Cause:** Continuation lines in `Args:` or `Returns:` sections with wrong indentation.
Griffe expects continuation lines to be at `base_indent + 4 * (nesting_level)` spaces.
Common mistakes:
- 15 spaces instead of 16 (off by 1 in manual alignment)
- 14 spaces instead of 16
- Bulleted list inside `Returns:` using `- item\n  continuation` (10 spaces)
  instead of 8 spaces.

**Fix:** Either fix the space count precisely, or flatten the list into a single
prose sentence in the `Returns:` description.

## Pattern 6 — "(none) —" or "(none)" name:description pair (2 warnings)

**Cause:** `Args:` sections literally saying `(none) — ...` when the constructor
takes no arguments.

**Fix:** Remove the `Args:` section and describe the no-arg nature in prose.

## Files modified (source only — never touch technical_docs/reference/)

- varco_core/varco_core/validation.py (4 Raises fixes)
- varco_core/varco_core/event/base.py (1 Raises fix)
- varco_core/varco_core/cache/base.py (Type parameters → prose)
- varco_core/varco_core/cache/decorator.py (Returns: indentation fix)
- varco_core/varco_core/job/base.py (3 Raises fixes)
- varco_core/varco_core/authority/sources/authority.py (1 Raises fix)
- varco_core/varco_core/authority/jwt_authority.py (1 Raises fix)
- varco_core/varco_core/authority/exceptions.py (phantom Args removed)
- varco_core/varco_core/authority/registry.py (1 Raises fix)
- varco_core/varco_core/tracing.py (1 Raises fix)
- varco_core/varco_core/observability/metrics.py (2 Raises fixes)
- varco_core/varco_core/observability/helpers.py (1 Raises fix)
- varco_core/varco_core/observability/span.py (1 Raises fix)
- varco_core/varco_core/exception/http.py (1 Raises fix)
- varco_core/varco_core/service/outbox.py (1 Raises fix)
- varco_core/varco_core/service/audit.py (1 Raises fix)
- varco_core/varco_core/service/inbox.py (1 Raises fix)
- varco_core/varco_core/service/saga.py (2 Raises fixes)
- varco_core/varco_core/service/types.py (Type parameters → prose)
- varco_beanie/varco_beanie/job_store.py ("(none)" Args removed)
- varco_beanie/varco_beanie/provider.py (phantom Args → prose)
- varco_memcached/varco_memcached/cache.py ("(none)" Args removed)
- varco_sa/varco_sa/job_store.py (indentation fix)
- varco_sa/varco_sa/provider.py (phantom Args → prose)
- varco_sa/varco_sa/schema_guard.py (1 Raises fix)
- varco_fastapi/varco_fastapi/auth/client_auth.py (1 Raises fix)
- varco_fastapi/varco_fastapi/client/protocol.py (phantom TypeVar Args removed)
- varco_fastapi/varco_fastapi/validation.py (phantom Args removed)
- varco_fastapi/varco_fastapi/job/runner.py (2 Raises fixes)
- varco_fastapi/varco_fastapi/middleware/error.py (typed app/call_next)
- varco_fastapi/varco_fastapi/middleware/request_context.py (typed app/call_next)
- varco_fastapi/varco_fastapi/middleware/logging.py (typed app/call_next)
- varco_fastapi/varco_fastapi/middleware/tracing.py (typed app/call_next)
- varco_fastapi/varco_fastapi/middleware/cors.py (typed app + indentation fix)
- varco_fastapi/varco_fastapi/middleware/session.py (typed app/call_next)

**Why:** `make docs-strict` exits non-zero on any griffe warning. All 82 warnings were docstring formatting issues — no logic changes, no refactoring.
**How to apply:** Before writing any `Raises:` entry, ensure it starts with an exception class name. Before writing `Args:` for a class, confirm every param appears in the actual `__init__` signature griffe will see.
