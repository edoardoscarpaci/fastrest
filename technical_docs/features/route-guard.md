# RouteGuard — Technical Reference

`RouteGuard` is a declarative, immutable authorization predicate that attaches to any
`@route`-decorated handler and is evaluated against the request's `AuthContext` **before**
the handler function runs.  Denial raises `ServiceAuthorizationError`, which the existing
exception handler maps to HTTP 403.

---

## Core files

| File | Role |
|---|---|
| `varco_fastapi/auth/guard.py` | `RouteGuard` dataclass + all constructor helpers |
| `varco_fastapi/router/endpoint.py` | `_RouteEntry.requires` field; `route()` kwarg |
| `varco_fastapi/router/introspection.py` | `ResolvedRoute.requires` field; populated by `introspect_routes()` |
| `varco_fastapi/router/base.py` | Build-time safety check in `build_router()`; runtime enforcement in `_make_custom_handler()` |
| `varco_fastapi/auth/__init__.py` | Public re-exports |
| `varco_fastapi/__init__.py` | Package-level re-exports |
| `varco_core/auth/base.py` | `AuthContext`, `Action`, `ResourceGrant` (the objects the guard checks) |
| `varco_core/exception/service.py` | `ServiceAuthorizationError` (raised on denial) |
| `varco_fastapi/exceptions.py` | `add_exception_handlers()` — maps `ServiceAuthorizationError` → HTTP 403 |

---

## Data model

### `RouteGuard` (`auth/guard.py`)

```python
@dataclass(frozen=True)
class RouteGuard:
    scopes: tuple[str, ...]                                          # OAuth scopes
    roles: tuple[str, ...]                                           # named roles
    grant: tuple[Action, str] | None                                 # ctx.can(action, key)
    require_all: bool = True                                         # AND vs OR for scopes/roles
    allow_anonymous: bool = False                                    # bypass for public endpoints
    token_profiles: tuple[str, ...] = ()                             # JWT token profile any-of match
    predicate: Callable[[AuthContext], bool | Awaitable[bool]] | None  # custom callable
```

All fields default to empty / `None` / `True` — a `RouteGuard()` with no arguments still
**denies anonymous callers** (any authenticated caller passes the empty guard).

The `predicate` field is excluded from `hash` and `compare` because callables are not
reliably comparable.  This means two `RouteGuard` instances with identical scopes/roles
but different predicates compare as equal — be aware when using guards as dict keys.

### Constructor helpers

| Helper | Produces |
|---|---|
| `require_scopes(*s, all=True)` | `RouteGuard(scopes=s, require_all=all)` |
| `require_roles(*r, all=True)` | `RouteGuard(roles=r, require_all=all)` |
| `require_grant(action, key)` | `RouteGuard(grant=(action, key))` |
| `require_token_profile(*names)` | `RouteGuard(token_profiles=names)` |
| `require_predicate(fn)` | `RouteGuard(predicate=fn)` |
| `allow_anonymous()` | `RouteGuard(allow_anonymous=True)` |

---

## Evaluation order inside `RouteGuard.check(ctx)`

`check()` is `async def` and is the single entry-point for authorization.  Order:

1. **`allow_anonymous` + anonymous** → return immediately (allow).
2. **Anonymous + no `allow_anonymous`** → raise `ServiceAuthorizationError("Anonymous access is not permitted …")`.
3. **Scope check** (skipped if `self.scopes` is empty):
   - `require_all=True` → `all(ctx.has_scope(s) for s in self.scopes)`
   - `require_all=False` → `any(ctx.has_scope(s) for s in self.scopes)`
   - Fail → raise with message naming the missing scopes.
4. **Role check** (skipped if `self.roles` is empty):
   - Same AND/OR logic via `ctx.has_role(r)`.
   - Fail → raise with message naming the missing roles.
4.5. **Token profile check** (skipped if `self.token_profiles` is empty; Plan 002 §B):
   - Any-of match against `ctx.metadata.get("token_profile")` — the key populated by
     `varco_core.jwt.profile.resolve_token_profile()` when a `TokenProfile` matched
     during JWT parsing (see `technical_docs/features/token-profiles.md`).
   - Fail → raise with message naming the required profile(s) and the actual value
     (including `None` when no profile matched at all, or auth didn't go through the
     JWT layer).
5. **Grant check** (skipped if `self.grant is None`):
   - `ctx.can(action, resource_key)` — checks wildcard `"*"` then exact key.
   - Fail → raise with message naming action + key.
6. **Predicate** (skipped if `self.predicate is None`):
   - Called with `ctx`.  If the return value is a coroutine it is awaited.
   - `False` return → raise generic `ServiceAuthorizationError`.
   - Predicate may raise `ServiceAuthorizationError` directly for custom messages.
   - Any *other* exception propagates as-is (becomes HTTP 500, not 403).

`ctx.is_anonymous()` returns `True` when `ctx.user_id is None`.  
`ctx.has_scope`, `ctx.has_role`, `ctx.can` all live in `varco_core/auth/base.py:AuthContext`.

---

## Compile-time pipeline: `@route` → `_RouteEntry` → `ResolvedRoute`

### 1. Decoration time (`endpoint.py`)

```
@route("GET", "/summary", requires=require_scopes("reports:read"))
async def get_summary(self, ctx) -> dict: ...
```

`route()` constructs a `_RouteEntry` frozen dataclass and stores it on
`func.__route_entry__`.  The `requires` field is passed through verbatim:

```python
# endpoint.py ~ line 252
entry = _RouteEntry(
    ...
    requires=requires,   # RouteGuard | None
)
func.__route_entry__ = entry
```

Nothing is validated or executed here.

### 2. Introspection time (`introspection.py`)

`introspect_routes(router_cls)` walks the class MRO and collects all
`__route_entry__` attributes into `ResolvedRoute` instances:

```python
# introspection.py ~ line 348-351
route = ResolvedRoute(
    ...
    requires=entry.requires,   # copied as-is
)
```

`ResolvedRoute.requires` has `hash=False, compare=False` so that guards with
callables can be stored without breaking frozen-dataclass invariants.

Consumers of `ResolvedRoute`:
- `build_router()` — the only consumer that actually enforces the guard.
- `MCPAdapter`, `SkillAdapter`, `StubGenerator` — currently ignore `requires`.

### 3. `build_router()` — safety check (`base.py:505`)

Before registering any routes, `build_router()` validates the guard/auth combination:

```python
# base.py ~ line 536-548
server_auth_present = getattr(self, "_auth", None) is not None
if not server_auth_present:
    for r in routes:
        if r.requires is not None and not r.requires.allow_anonymous:
            raise RuntimeError(
                f"{router_cls.__name__}.{r.name}: route has `requires` guard "
                f"but the router has no `_auth` strategy set. ..."
            )
```

**Why**: without `_auth`, no `auth_dep = Depends(server_auth)` is injected, so the
`auth_dep is not None` branch in `_make_custom_handler` is never taken, and `auth` is
never populated — the guard's `check(auth)` call would receive garbage or never execute.

`allow_anonymous` guards are exempt because they short-circuit before inspecting `auth`.

---

## Runtime pipeline: request → guard → handler

### `_make_custom_handler()` (`base.py:1050+`)

This function builds the FastAPI endpoint closure for every `@route` method.
The guard is captured at build time (closure over `route.requires`):

```python
guard = route.requires   # captured once at build_router() time

if auth_dep is not None:        # _auth is set

    async def custom_handler(request: Request, auth: AuthContext = auth_dep) -> Any:
        # ── Authorization ─────────────────────────────────────────────────────
        if guard is not None:
            await guard.check(auth)     # <-- RouteGuard.check() called HERE
        # ── Proceed to handler ────────────────────────────────────────────────
        ...
        return await method_fn(router_instance, **call_kwargs)
```

The guard fires **after** `auth_dep` is resolved (i.e. `AbstractServerAuth.__call__`
has already run and authenticated the caller) but **before** the handler method is
invoked and before any async job offload.

If `_auth` is `None`, the entire `auth_dep is not None` branch is skipped and there is
no guard check — which is why the build-time safety check above is essential.

### Exception propagation

`ServiceAuthorizationError` raised by `check()` propagates up through FastAPI's exception
handlers.  `add_exception_handlers(app)` in `exceptions.py` registers:

```python
# exceptions.py
ServiceAuthorizationError → HTTP 403   {"code": "...", "message": "...", "correlation_id": "..."}
```

No special handling is needed in the guard or the handler.

---

## Auth layer this sits in

```
AbstractServerAuth.__call__(request)   → AuthContext
  ↓  (RequestContextMiddleware, middleware/request_context.py)
auth_context_var set for this request
  ↓  (FastAPI Depends — Depends(server_auth) in handler signature)
auth: AuthContext injected into custom_handler
  ↓
RouteGuard.check(auth)                 → None (allow) or raises ServiceAuthorizationError
  ↓
method_fn(router_instance, ctx=auth)   → handler body runs
```

`RouteGuard` sits between authentication (middleware) and the handler body.  It is the
*router-layer* authorization primitive.  The *service-layer* authorization primitive is
`AbstractAuthorizer.authorize(ctx, action, resource)` in `varco_core/auth/base.py` — that
one requires an `entity_type` and is used by `AsyncService`.  `RouteGuard` fills the gap
for handlers that have no domain entity.

---

## How to modify behaviour

### Add a new check type

1. Add a field to `RouteGuard` in `auth/guard.py` (frozen dataclass → keep hash/compare implications in mind; use `field(hash=False, compare=False)` for non-comparable values).
2. Add the check logic to `RouteGuard.check()` in the same file.
3. Add a constructor helper function if the check deserves one.
4. Export from `auth/__init__.py` and `varco_fastapi/__init__.py`.
5. Add tests to `tests/test_route_guard.py`.

### Change when the guard fires (e.g. after path param extraction)

Edit `_make_custom_handler()` in `router/base.py`.  The guard call is the first statement
inside the `auth_dep is not None` closure, before `call_kwargs` are built.  Moving it
after the call_kwargs block would give the predicate access to path params if needed.

### Expose `requires` to adapters (MCP/A2A)

`ResolvedRoute.requires` is already populated.  In `router/mcp.py` or `router/skill.py`,
read `resolved_route.requires` to add authorization metadata to tool/skill definitions.

### Apply guards to CRUD routes

`requires` is currently wired only through `_RouteEntry` (custom `@route` methods).
CRUD routes go through `_make_create_handler`, `_make_read_handler`, etc. in `router/base.py`.
To add guard support to CRUD routes:
1. Add a `_create_requires`, `_read_requires`, etc. ClassVar to `VarcoCRUDRouter` in `router/crud.py`.
2. Plumb through `introspect_routes()` in `introspection.py` (the CRUD `ResolvedRoute` block around line 260).
3. Enforce inside each CRUD handler factory, mirroring the `_make_custom_handler` pattern.
