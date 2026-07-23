# Custom `@route` Handlers — Technical Reference

Custom `@route` handlers on any router (`VarcoRouter`, `GenericRouter`,
`VarcoCRUDRouter` and its presets) accept the **full FastAPI parameter surface** —
`Query(...)`, `Body(...)` (Pydantic models), `Depends(...)`, `Request`, `Response`,
`BackgroundTasks`, and **type-coerced** path params — exactly like a hand-written
FastAPI endpoint. On top of that, varco's own contract is preserved:
`ctx`/`auth`/`context` receive the router's `AuthContext`, any `RouteGuard` runs
before the handler, and the `?with_async` async-offload path still works.

```python
class ReportRouter(GenericRouter):
    _prefix = "/reports"
    _auth = JwtBearerAuth(...)

    @route("POST", "/{report_id}/summary", requires=require_scopes("reports:read"))
    async def summary(
        self,
        report_id: int,                       # typed path param → coerced to int
        ctx: AuthContext,                     # injected from _auth
        window: int = Query(30, ge=1, le=365),  # validated query param (422 on bad input)
        filters: FilterBody = Body(...),      # Pydantic request body
        repo: Repo = Depends(get_repo),       # arbitrary FastAPI dependency
        request: Request = None,              # raw request if wanted
    ) -> SummaryResponse:                     # return annotation → OpenAPI response model
        ...
```

---

## Core files

| File | Role |
|---|---|
| `varco_fastapi/router/endpoint.py` | `@route` decorator, `_RouteEntry` frozen dataclass |
| `varco_fastapi/router/base.py` | `_make_custom_handler`, `_synthesize_custom_signature`, `_resolve_handler_hints`, `_CustomHandlerSig`, `_register_route` |
| `varco_fastapi/router/introspection.py` | `introspect_routes()`, `ResolvedRoute` (`is_crud`, `crud_action`, `async_capable`) |
| `varco_fastapi/auth/server_auth.py` | `AbstractServerAuth` — a FastAPI-callable dependency yielding `AuthContext` |
| `varco_fastapi/auth/guard.py` | `RouteGuard`, `require_scopes/roles/grant/predicate`, `allow_anonymous` |

---

## The problem this solves

Historically, a custom `@route` handler was registered with a **fixed** closure
signature — `(request: Request, auth: AuthContext = Depends(server_auth))`. FastAPI
introspected only that, so its dependency resolver never saw the user's real
parameters. The wrapper then hand-injected only `ctx`/`auth`/`context` and
**raw-string** path params by name. Any `Query`/`Body`/`Depends`/`Request`
parameter a handler declared was simply never passed → the call failed with a 500
(missing argument), and path params were never type-coerced.

## The technique

FastAPI builds its dependency model from the endpoint callable's signature
(`inspect.signature`, honoring `__signature__`). The fix is to **synthesize a
wrapper whose `__signature__` mirrors the user's method** (minus `self`), so
FastAPI does all parsing, validation, coercion and injection natively. This is the
same signature/annotation-override pattern the CRUD factories already use
(`_make_read_handler`, `_make_update_handler`).

### `_synthesize_custom_signature` (`base.py`)

Builds a `_CustomHandlerSig` (frozen dataclass):

```python
@dataclass(frozen=True)
class _CustomHandlerSig:
    signature: inspect.Signature   # the FastAPI-visible signature
    auth_kwarg: str | None         # kwarg key carrying the AuthContext
    request_kwarg: str | None      # kwarg key carrying the Request (offload flag)
    hidden_kwargs: frozenset[str]  # keys stripped before the user's method is called
```

Classification of each parameter (excluding `self`):

- **Auth param** — name in `("ctx", "auth", "context")`: replaced with a
  keyword-only param defaulting to `Depends(server_auth)`, annotated `AuthContext`.
  FastAPI resolves the router's `_auth` and passes it under the user's own param
  name, so it flows straight to the handler.
- **Everything else** — copied through with its resolved annotation and original
  default, so `Query()`/`Body()`/`Depends()` markers and `Request`/`Response`/path
  annotations reach FastAPI intact. Path params are auto-recognized by FastAPI via
  name-match against the route path and are **type-coerced**.

All synthesized params are `KEYWORD_ONLY` — this sidesteps the "non-default before
default" ordering rule and lets the wrapper receive everything as `**kwargs`.

Two framework-only params are added **only when needed**:

- a hidden `AuthContext = Depends(server_auth)` when the router has `_auth` but the
  handler did *not* declare `ctx`/`auth`/`context` — the `RouteGuard` and the
  offload auth snapshot still need the resolved `AuthContext`;
- a hidden `Request` when the route can offload and the handler did not declare a
  `Request` itself — needed to read `?with_async` / `?callback_url`.

The handler's **return annotation** is placed on the synthesized signature so
FastAPI infers the response model and OpenAPI schema.

### `_resolve_handler_hints` (`base.py`)

Every router file uses `from __future__ import annotations`, so a handler's raw
annotations are **strings**. FastAPI evaluates string annotations against the
*endpoint callable's* `__globals__` — which for the synthesized wrapper is
`router/base.py`, **not** the user's module. So annotations referencing user types
(Pydantic models, etc.) would not resolve. `_resolve_handler_hints` calls
`typing.get_type_hints(method_fn, include_extras=True)` against the user method's
own module and hands FastAPI real objects. `include_extras=True` preserves
`Annotated[int, Query()]` metadata. A single unresolvable annotation (e.g. a
`TYPE_CHECKING`-only import) is caught and that parameter degrades to `Any` rather
than aborting the whole build.

### `_make_custom_handler` (`base.py`)

The registered closure is `async def custom_handler(**kwargs)` carrying the
synthesized `__signature__`. FastAPI passes every resolved value by keyword; the
wrapper:

1. reads the `AuthContext` from `auth_kwarg` and runs `route.requires.check(auth)`
   if a guard is present (unchanged authorization semantics);
2. reads the `Request` from `request_kwarg` and checks `?with_async` when the route
   can offload;
3. strips the hidden framework kwargs, then calls
   `method_fn(router_instance, **call_kwargs)` — or offloads via `_submit_job`.

### `_register_route` — response model (`base.py`)

FastAPI only infers `response_model` from the return annotation when the argument
is its `Default(None)` sentinel — passing a literal `None` means "no response
model". The registration therefore:

- **CRUD routes** — pass the explicit `R` model (`None` for `"list"`, which is set
  via `handler.__annotations__["return"]`);
- **offloadable custom routes** (`async_capable` *and* a job runner wired) — pass
  `response_model=None` explicitly, because the response is polymorphic: the
  handler's own return type inline, or a `JobAcceptedResponse` when offloaded;
- **plain custom routes** — omit `response_model` so FastAPI infers it from the
  synthesized return annotation.

> Gotcha: `async_capable=True` is the `@route` default, so it alone does not mean a
> route ever offloads — the suppression is gated on a job runner actually being
> present (`self._job_runner`), matching `can_offload` in the handler.

---

## Request lifecycle (custom route)

```
GET /reports/7/summary?window=12   (X-API-Key / Bearer ...)
  └─ FastAPI matched: custom_handler  (its __signature__ mirrors summary())
       └─ FastAPI resolves each param from the request:
            report_id ← path "7"  → coerced to int 7
            ctx       ← Depends(server_auth)(request) → AuthContext
            window    ← query "12" → coerced to int 12, validated (ge/le)
            filters   ← request body → FilterBody(...)
            repo      ← Depends(get_repo)() → Repo
       └─ custom_handler(**resolved)
            ├─ auth = resolved["ctx"]
            ├─ await route.requires.check(auth)   # 403 on failure
            ├─ (not with_async) → call_kwargs = resolved (no hidden keys)
            └─ await summary(self, report_id=7, ctx=…, window=12, filters=…, repo=…)
```

---

## Backward compatibility

The change is **additive** — every previously-valid handler keeps working:

- `ctx: AuthContext` (or `auth` / `context`) still receives the `AuthContext`.
- Handlers with no extra params (`async def ping(self)`) are unchanged.
- Path-param handlers keep working, now with type coercion instead of raw strings.
- `RouteGuard` runs before the body exactly as before, using an `AuthContext`
  resolved even when the handler does not declare `ctx`.
- The `?with_async` offload path returns the same `JobAcceptedResponse`.

`VarcoCRUDRouter` inherits the behavior for free: its `_make_http_handler` delegates
non-CRUD (custom) routes to `super()._make_http_handler` → base `_make_custom_handler`.

### Edge cases

| Case | Behavior |
|---|---|
| Handler declares `ctx` but router has **no `_auth`** | The auth param is dropped (preserves the historical no-inject contract); the route still *builds*. Calling it raises a missing-argument error — set `_auth` to fix. |
| Handler declares its own `Request` | Reused for the offload flag; no hidden `Request` is added, and it flows to the handler. |
| Non-standard ctx name (e.g. `ctx_`) | Not treated as auth — becomes an ordinary FastAPI param (query by default). |
| Annotation only importable under `TYPE_CHECKING` | `get_type_hints` fails → that param degrades to `Any` (logged at debug), build continues. |
| `async_capable` route + job runner | `response_model` inference suppressed (polymorphic offload response). |

---

## How to extend / modify

- **Expose `with_async` in OpenAPI** — currently read from the request (kept out of
  the schema by design). To surface it, add a real `Query` param in
  `_synthesize_custom_signature` instead of the hidden `Request`.
- **Change the auth param names** — edit `_AUTH_PARAM_NAMES` in `base.py`.
- **WebSocket / SSE custom routes** — `ws_route` / `sse_route` are unchanged by this
  feature; extend their handlers (`_make_ws_handler`) with the same synthesis
  approach if arbitrary param injection is needed there.

---

## Tests & example

- `varco_fastapi/tests/test_custom_route_params.py` — Query/Body/Depends/typed-path/
  Request/guard/offload/response-model coverage, plus unit tests for
  `_synthesize_custom_signature`.
- `examples/24-custom-route-params/` — a runnable `GenericRouter` demonstrating
  every parameter kind with smoke tests.

## Related

- [GenericRouter](generic-router.md) — service-free routers built on `@route`.
- [RouteGuard](route-guard.md) — declarative per-route authorization.
