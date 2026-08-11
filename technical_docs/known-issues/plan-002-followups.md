# Plan 002 — deferred follow-ups

Two issues surfaced while shipping plan 002 — JWT claim transformer + token
profiles (`plans/002-jwt-claim-transformer-and-token-profiles.md`, outside the
docs tree). Neither blocks the feature; both are recorded here to be fixed
separately.

Status, as of 2026-08-12 (verified against the current codebase during a docs
audit):

- **Issue 1 (`detail` leak): still open.** No opt-in flag
  (`expose_detail`/`VARCO_EXPOSE_ERROR_DETAIL`) exists anywhere in
  `varco_core`/`varco_fastapi` — none of the three suggested fixes below were
  implemented. The team instead accepted the behaviour change and documented
  it as a ⚠️ breaking `Changed` entry in `CHANGELOG.md` ("Error response
  bodies now include a `detail` field when present"). Anyone who still wants
  per-exception-type or per-app redaction needs to implement it themselves;
  this doc's suggested fix list is still accurate.
- **Issue 2 (`mkdocs build --strict` warnings): resolved.** `uv run mkdocs
  build --strict` now exits 0 with zero warnings. The docstrings listed below
  were corrected (moved prose out of `Raises:` into `Edge cases:`, fixed a
  `varco_core/varco_core/event/serializer.py` parameter name mismatch, changed
  `ProfilingSettings`'s `Args:` section to `Attributes:` since it documents
  pydantic model fields rather than a function signature, and fixed a
  continuation-line indentation issue in `varco_sa/varco_sa/rls.py`). The
  section below is kept as a historical record of what was wrong and why.

---

## 1. `ErrorMessage.detail` is now leaked into *every* error response body

### What changed

`RouteGuard`'s new `require_token_profile` denial message
(`"Token profile 'internal' required; token profile is 'None'"`) is only useful
to an API client if it actually reaches the response body. To make that happen,
both JSON error builders were changed to stop dropping `msg.detail`:

- `varco_fastapi/varco_fastapi/exceptions.py` → `_make_error_response()`
- `varco_fastapi/varco_fastapi/middleware/error.py` → `ErrorMiddleware`

```python
if msg.detail:
    body["detail"] = msg.detail
```

### Why it's a problem

Those two functions are **not** guard-specific — they are the shared terminal
error path for every `ServiceException` raised anywhere in the app. So the
change did not add `detail` to 403 guard denials; it added `detail` to
**all** error responses: `ServiceNotFoundError`, `ServiceConflictError`,
`ServiceValidationError`, `ServiceAuthorizationError`, and any user-defined
subclass.

`detail` is `str(exc)` verbatim — see
`error_message_for()` in `varco_core/varco_core/exception/http.py:249`. Its own
docstring already warns against exactly this (`http.py:220-223`):

> `str(exc)` may contain internal detail (entity IDs, field names). Filter
> `detail` before including it in external API responses if the data could leak
> internal state.

That guidance is now violated by default. Concretely, an unauthenticated caller
probing a route can receive internal entity IDs, column/field names, tenant
identifiers, or repository-level messages that were previously server-side only.
This is an information-disclosure regression, and it is **on by default with no
opt-out**.

### Scope

- Affects every varco_fastapi app on this working tree.
- User-visible API contract change: response bodies gain a `detail` key.
- Recorded in `CHANGELOG.md` under `[Unreleased]` as a ⚠️ behaviour change.

### Suggested fix

The goal (actionable authorization denials) is legitimate; the blast radius is
not. Options, roughly in order of preference:

1. **Opt-in per exception type.** Add a class-level flag
   (e.g. `ServiceException.expose_detail: ClassVar[bool] = False`, flipped to
   `True` on `ServiceAuthorizationError`) and gate the body on it. Keeps
   guard messages useful, keeps everything else redacted.
2. **Opt-in per app.** A `create_varco_app(expose_error_detail=False)` kwarg /
   `VARCO_EXPOSE_ERROR_DETAIL` env var, defaulting to `False` so production is
   safe by default and debugging is one flag away.
3. **Carry the guard message on a dedicated field** rather than reusing
   `detail`, so `RouteGuard` denials never share a channel with repository
   error strings.

Whichever route is taken, add a test asserting that a `ServiceNotFoundError`
body does **not** contain the entity ID, so the regression cannot silently
return.

### Anchors

- `varco_fastapi/varco_fastapi/exceptions.py:68-79`
- `varco_fastapi/varco_fastapi/middleware/error.py:233-240`
- `varco_core/varco_core/exception/http.py:199-257` (`error_message_for`)
- `varco_fastapi/varco_fastapi/auth/guard.py` (the denial messages that motivated it)

---

## 2. `mkdocs build --strict` aborts on 22 pre-existing griffe warnings

### What's wrong

```
$ uv run mkdocs build --strict
...
Aborted with 22 warnings in strict mode!    # exit 1
```

None of the 22 warnings come from plan 002's changeset — every file that plan
touched (`varco_core/jwt/**`, `varco_core/jwt/transform/**`,
`varco_core/authority/registry.py`, `varco_fastapi/auth/**`, and the two new
feature-doc pages) builds warning-free. The failures predate this work and sit
in four unrelated files.

### The 22 warnings, by cause

**A. `Raises:` section containing prose instead of `Exception: description`
pairs (10 warnings).** griffe parses every line under `Raises:` as a
`type: description` pair, so a sentence like *"Never — all exceptions are
caught"* fails to parse.

| File | Lines | Offending prose |
|---|---|---|
| `varco_core/varco_core/auth/policy.py` | 297-299 | "Implementations should not raise on a *denial*…" |
| `varco_sa/varco_sa/health.py` | 315 | "Never — all exceptions are caught and returned as UNHEALTHY results." |
| `varco_ws/varco_ws/di.py` | 201-202, 329-330 | "Nothing — if providify is not installed the function logs a warning and returns without raising." |

*Fix*: move the prose out of `Raises:` into the body or an `Edge cases:`
section. Where the intent is "raises nothing", drop the `Raises:` section
entirely — an absent section is the correct way to say that.

**B. `Args:` documenting parameters that aren't in the signature (12
warnings).** `varco_fastapi/varco_fastapi/middleware/profiling.py:78-90`
documents `enabled`, `skip_paths`, `slow_threshold_ms`, `mem_threshold_mb`,
`attach_headers`, `top_n`, `track_rss` — each producing two warnings ("No type
or annotation" + "does not appear in the function signature").

*Fix*: reconcile the docstring with the real signature. Most likely those
parameters moved onto a config object, and the `Args:` block was never updated —
worth checking whether the *docstring* or the *signature* is the stale one
before editing.

### Why it matters

`mkdocs build --strict` is the documented docs gate in plan 002's
§ Verification, so it currently cannot be used as a pass/fail
check — a reviewer has to eyeball 22 warnings to notice a 23rd. Fixing these
makes the gate meaningful again and lets it be wired into CI.

### Note

Two griffe warnings *were* introduced by plan 002 and are already fixed:
`varco_core/varco_core/jwt/exceptions.py` (an `Args:` block referencing a
parameter on an inherited `Exception.__init__`) and
`varco_core/varco_core/jwt/transform/shape.py` (a malformed `Returns:`
continuation indent). The 22 above are all older.
