# GenericRouter — Technical Reference

`GenericRouter` is a named alias for `VarcoRouter` with no generic type parameters.
It is the entry point for **service-free** servers: HTTP endpoints that have no
`AsyncService`, no domain model, and no repository behind them — data-processing
pipelines, API gateways, proxy servers, computed analytics routes, etc.

All cross-cutting infrastructure (middleware, telemetry, auth, `RouteGuard`
authorization) applies identically to a `GenericRouter` as to a service-backed
`VarcoCRUDRouter`.

---

## Core files

| File | Role |
|---|---|
| `varco_fastapi/router/presets.py` | `GenericRouter = VarcoRouter` alias + docstring |
| `varco_fastapi/router/base.py` | `VarcoRouter` — the actual implementation; `build_router()`; `_make_custom_handler()` |
| `varco_fastapi/router/endpoint.py` | `@route` decorator, `_RouteEntry` dataclass |
| `varco_fastapi/router/introspection.py` | `introspect_routes()`, `ResolvedRoute` |
| `varco_fastapi/validation.py` | `validate_router_class()`, `_is_service_backed()` |
| `varco_fastapi/app.py` | `create_varco_app()`, `_mount_router()` |
| `varco_fastapi/__init__.py` | Package-level re-exports |

---

## What `GenericRouter` is (and is not)

```python
# router/presets.py
GenericRouter = VarcoRouter
```

That is the entire implementation.  `GenericRouter` is not a subclass, not a metaclass,
not a protocol — it is a Python name alias.  `GenericRouter is VarcoRouter` is `True`.

The alias exists for **discoverability and intent signalling** only:

```python
class ReportRouter(GenericRouter):   # intent: service-free server
    ...

class OrderRouter(CRUDRouter[Order, UUID, C, R, U]):  # intent: CRUD-backed
    ...
```

Importing `GenericRouter` is equivalent to importing `VarcoRouter`.  Both produce the
same runtime behaviour.

---

## How `VarcoRouter` works without type args

`VarcoRouter(Generic[D, PK, C, R, U])` requires type args for CRUD mixin routes because
those routes need to know the Pydantic models for request/response bodies.  But for
`@route`-decorated custom methods, **type args are never needed**.

### Type arg resolution (`base.py:311`)

```python
def _resolve_type_args(router_cls: type) -> tuple[type, ...] | None:
    for base in getattr(router_cls, "__orig_bases__", ()):
        origin = get_origin(base)
        if origin is None:
            continue
        if not issubclass(origin, VarcoRouter):
            continue
        args = get_args(base)
        resolved = tuple(a for a in args if not isinstance(a, TypeVar))
        if len(resolved) == len(args):
            return args
    return None          # <── returns None when no args are bound
```

When `class ReportRouter(GenericRouter): ...` is defined, `__orig_bases__` is
`(VarcoRouter,)` with no `get_args` — the function returns `None`.

### In `build_router()` (`base.py:530`)

```python
type_args = _resolve_type_args(router_cls)   # None for GenericRouter subclasses
...
routes = introspect_routes(router_cls, type_args=type_args)
```

`type_args=None` is passed to `introspect_routes`.  Inside, CRUD routes use `type_args`
to fill `request_model` and `response_model` — but a `GenericRouter` has no CRUD mixins,
so those code paths are never reached.

### In `_make_custom_handler()` (`base.py:1050`)

Custom `@route` handlers never use `type_args`.  The closure captures the router instance
and the resolved `ResolvedRoute`; type args play no role.

---

## The full request lifecycle

### 1. Class definition time

```python
class ReportRouter(GenericRouter):
    _prefix = "/reports"
    _auth = JwtBearerAuth(...)

    @route("GET", "/summary", requires=require_scopes("reports:read"))
    async def get_summary(self, ctx: AuthContext) -> dict:
        return {"total": compute_total()}
```

`@route(...)` constructs a `_RouteEntry` frozen dataclass and stores it on
`get_summary.__route_entry__`.  Nothing is registered with FastAPI yet.

### 2. `build_router()` time (`base.py:505`)

```
ReportRouter().build_router()
  │
  ├─ _resolve_type_args(ReportRouter)      → None
  ├─ _effective_prefix()                   → "/reports"
  ├─ introspect_routes(ReportRouter, type_args=None)
  │    └─ scans MRO for _CRUD_ACTION       → none found
  │    └─ scans methods for __route_entry__ → found on get_summary
  │    └─ builds ResolvedRoute(name="get_summary", method="GET", path="/summary",
  │                             requires=<RouteGuard>, ...)
  │    └─ returns [ResolvedRoute(...)]
  │
  ├─ safety check: requires + no _auth?    → _auth is set, OK
  │
  ├─ APIRouter(prefix="/reports", tags=[])
  ├─ _register_route(api_router, resolved_route, type_args=None)
  │    └─ _make_http_handler(resolved_route, type_args=None)
  │         └─ service = getattr(self, "_service", None)  → None
  │         └─ crud_action = route.crud_action            → None
  │         └─ (no CRUD dispatch — falls through)
  │         └─ _make_custom_handler(router_instance, method_fn, route, server_auth, job_runner)
  │              └─ builds async closure `custom_handler`
  │              └─ closure captures: router_instance, method_fn, route.requires, auth_dep
  │    └─ api_router.add_api_route("/summary", custom_handler, methods=["GET"])
  │
  └─ returns APIRouter
```

### 3. `create_varco_app()` / `_mount_router()` (`app.py:606`)

```python
def _mount_router(app, router_cls, container):
    api_router = router_cls().build_router()   # instantiate + build
    app.include_router(api_router)             # prefix already embedded
```

`build_router()` already sets `prefix="/reports"` on the `APIRouter`, so
`include_router` does NOT pass an extra prefix (doing so would double the path).

### 4. Incoming request: `GET /reports/summary`

```
ASGI stack (Starlette routing)
  └─ FastAPI matched route: custom_handler at /reports/summary
       └─ FastAPI resolves Depends(server_auth) → auth: AuthContext
            └─ custom_handler(request, auth=<AuthContext>)
                 ├─ await route.requires.check(auth)    # RouteGuard.check
                 │    └─ check scopes, roles, grant, predicate
                 │    └─ raises ServiceAuthorizationError → 403 on failure
                 ├─ call_kwargs = {"ctx": auth}         # param_names inspection
                 └─ await method_fn(router_instance, **call_kwargs)
                      └─ ReportRouter.get_summary(self, ctx=auth)
                           └─ return {"total": compute_total()}
```

---

## Validation

### `validate_router_class()` (`validation.py:68`)

Checks (in order):
1. `_prefix` is set and non-empty.
2. At least one `@route` method (or CRUD mixin) exists.
3. Generic type args are fully resolved — **skipped for service-free routers**.

The skip is implemented by `_is_service_backed()`:

```python
def _is_service_backed(router_cls: type) -> bool:
    for cls in router_cls.__mro__:
        if getattr(cls, "_CRUD_ACTION", None) is not None:
            return True          # has a CRUD mixin
    if getattr(router_cls, "_service", None) is not None:
        return True              # has explicit _service ClassVar
    return False
```

A plain `GenericRouter` subclass with only `@route` methods returns `False`, so the
generic-arg check is never run and no warning/error is emitted.

---

## Middleware stack (unchanged from service-backed routers)

`create_varco_app()` in `app.py` assembles the same middleware stack regardless of
whether the routers are service-backed or service-free:

```
CORSMiddleware (outermost)
  → ErrorMiddleware          (maps ServiceException → JSON, logs)
    → TracingMiddleware       (correlation ID, OpenTelemetry span)
      → MetricsMiddleware     (optional, http.server.* OTel metrics)
        → RequestLoggingMiddleware (structured access log)
          → RequestContextMiddleware (sets auth ContextVars; needs container)
            → DISessionMiddleware (per-request DI scope)
              → FastAPI route handler (innermost)
```

`RequestContextMiddleware` is wired only when a DI container is passed to
`create_varco_app()`.  Without it, `auth_context_var` is not set and `X-Request-ID`
is not added to responses — but `ErrorMiddleware`, `TracingMiddleware`, and
`RequestLoggingMiddleware` all still run.

---

## Auth wiring for service-free routers

Authentication works identically to CRUD routers:

```python
class ReportRouter(GenericRouter):
    _auth = JwtBearerAuth(...)   # or ApiKeyAuth, AnonymousAuth, CompositeServerAuth
```

`_auth` is a `ClassVar[AbstractServerAuth | None]` on `VarcoRouter`.  In
`_make_http_handler()` it is read as:

```python
server_auth = getattr(self, "_auth", None)
```

When not `None`, a `Depends(server_auth)` is added to the handler signature, and FastAPI
calls `server_auth(request) → AuthContext` for every request.

**If `_auth` is not set**: no auth dependency is injected, `auth_dep is None`, and the
no-auth branch of `_make_custom_handler` is used.  In this branch, `ctx` is never
populated — handlers must NOT declare a `ctx` parameter.

---

## How to modify behaviour

### Add a new kind of service-free endpoint (new HTTP method variant)

`@route`, `@ws_route`, and `@sse_route` are already the extension points.  No changes
to `GenericRouter` or `VarcoRouter` are needed — just add a new decorated method to the
subclass.

### Allow `ctx` in handlers without `_auth`

Currently the no-auth branch in `_make_custom_handler` (`base.py`) does not populate
`ctx`.  To pass `None` as `ctx` when no auth is configured:

```python
# base.py, no-auth branch of _make_custom_handler
for _name in ("ctx", "auth", "context"):
    if _name in param_names:
        call_kwargs[_name] = None   # add this line
```

Be aware this changes existing behaviour for all no-auth `@route` handlers.

### Support CRUD presets on a service-free router

Currently `GenericRouter` can only use `@route` methods (not `CreateMixin` etc.).
To support CRUD presets:
1. Subclass `VarcoCRUDRouter` alongside `GenericRouter` (already how `CRUDRouter` presets work).
2. Inject a minimal `_service` that delegates to whatever data source you have.
This is the bridge pattern: keep the HTTP layer unchanged, swap the service implementation.

### Change how prefix embedding works

`build_router()` passes `prefix` to the `APIRouter()` constructor:

```python
# base.py:_effective_prefix() and build_router()
prefix = self._effective_prefix()   # _prefix + optional _version prefix
api_router = APIRouter(prefix=prefix, tags=tags)
```

`_mount_router()` in `app.py` then calls `app.include_router(api_router)` with no extra
prefix.  If you want to add a runtime prefix override (e.g. per-tenant routing), override
`_effective_prefix()` on the subclass:

```python
class TenantReportRouter(GenericRouter):
    _prefix = "/reports"

    def _effective_prefix(self) -> str:
        return f"/tenant/{current_tenant()}{super()._effective_prefix()}"
```

### Auto-discover `GenericRouter` subclasses via DI container

`_scan_routers()` in `app.py` calls `container.get_all(VarcoRouter)`.  Since
`GenericRouter is VarcoRouter`, any `GenericRouter` subclass registered as a singleton
in the DI container is automatically discovered and mounted.
