# Findings — API Ergonomics Log

Running log of API smells, friction points, and fixes discovered while building each example.
Each entry: the smell → the fix → whether it was breaking → the CHANGELOG entry reference.

---

## 01-minimal-crud-api (Wave 1)

### F01 — `ProfilingSettings` string-interface binding breaks all `@Provider` injection

**Smell**: `container.scan("varco_fastapi", recursive=True)` registers a `ProfilingSettings` binding
whose interface is stored as the string `"ProfilingSettings"` (not the class). This is caused by
`varco_fastapi/di.py` importing `ProfilingSettings` under `TYPE_CHECKING` only — at runtime,
`get_type_hints()` cannot evaluate the return annotation `-> "ProfilingSettings"` and providify
stores the raw string.

`DIContainer._build_localns()` iterates over all bindings and calls `b.interface.__name__`, which
raises `AttributeError: 'str' object has no attribute '__name__'`. This exception is **silently
swallowed** inside `_collect_kwargs_sync()` (`except Exception: hints = {}`), causing all `Inject[T]`
parameters to be resolved as empty. The result: every `@Singleton` class with required `Inject[T]`
args fails with `TypeError: __init__() missing N required positional arguments`.

**Fix**: Move `ProfilingSettings` from the `TYPE_CHECKING` block to a real runtime import in
`varco_fastapi/di.py` so `get_type_hints()` can resolve it.

**Severity**: Breaking — no `@Singleton` with required `Inject[T]` args can be resolved when
`varco_fastapi` is scanned.

**File fixed**: `varco_fastapi/varco_fastapi/di.py`

---

### F02 — `VarcoCRUDRouter` generic `Inject[AsyncService[D,PK,C,R,U]]` not injectable via `container.get(RouterSubclass)`

**Smell**: `VarcoCRUDRouter.__init__` uses `from __future__ import annotations` and
`Inject[AsyncService[D, PK, C, R, U]]` with unresolved `TypeVar`s. When providify calls
`get_type_hints(ProductRouter.__init__)`, the annotations contain bare `TypeVar` names (`D`,
`PK`, etc.) that are absent from any module's globals. `get_type_hints` raises `NameError`,
which is silently swallowed → empty hints → `_service = None`.

**Fix**: Do not rely on `container.get(ProductRouter)` for service injection. Instead:
1. Resolve the concrete service directly: `container.get(AsyncService[Product, UUID, ...])`
2. Construct the router manually: `ProductRouter(service=service)`

This bypasses the broken generic TypeVar resolution entirely.

**Severity**: Breaking — `VarcoCRUDRouter` subclasses cannot be resolved via DI if relying
on `Inject[AsyncService[D,PK,...]]` in the generic base class `__init__`.

**Workaround in**: `examples/01-minimal-crud-api/app.py`

---

### F03 — Path parameter `pk` arrives as `str`, store keys are `UUID`

**Smell**: FastAPI extracts path parameters as strings when the generic type parameter `PK`
is not visible to FastAPI's route builder (TypeVar erasure in `from __future__ import annotations`
context). The in-memory store keys are `UUID` objects (set by `uuid4()`), so
`self._store.get("4987c2db-...")` returns `None`.

`find_all()` / `find_by_query()` work because they never look up by key.
`find_by_id()`, `exists()`, and `delete()` all fail silently with 404.

**Fix**: Accept `UUID | str` in `find_by_id()`, `exists()`, and `delete()`, and coerce strings
to `UUID` at the entry point. Production backends (SQLAlchemy, Beanie) handle coercion
transparently — only in-memory implementations need this guard.

**Severity**: Breaking — all `GET /resource/{id}`, `PUT /resource/{id}`, `DELETE /resource/{id}`
endpoints return 404.

**File fixed**: `examples/01-minimal-crud-api/repo.py`

---

### F04 — `ListMixin` returns a paginated envelope, not a plain list

**Smell**: `GET /v1/products` returns `{"results": [...], "count": N, "total_count": N, "next": null}`
(a `PageResult` / pagination envelope), not a bare JSON array. Code expecting `response.json()`
to be a list fails with `TypeError: string indices must be integers, not 'str'`.

**Fix**: Unwrap `response.json()["results"]` when consuming list endpoints.

**Severity**: Non-breaking — correct by design; callers must be aware of the envelope shape.

**Fixed in**: `examples/01-minimal-crud-api/tests/test_smoke.py`

---

## 02-api-gateway-guards (Wave 2)

### F05 — `ErrorMiddleware` re-raises `HTTPException` from inner middleware, producing 500 instead of 401/403

**Smell**: `ErrorMiddleware.dispatch` catches `HTTPException` and does `raise` — intending to let
FastAPI's built-in exception handler convert it to a proper HTTP response. This works correctly
when the exception originates from a **route handler**: FastAPI's `exception_handler` machinery
intercepts it before it reaches the middleware stack.

However, when `HTTPException` is raised inside another `BaseHTTPMiddleware` (e.g.
`RequestContextMiddleware` when `JwtBearerAuth` rejects an invalid token), Starlette's
`BaseHTTPMiddleware` stream machinery propagates the exception *outward* through all middleware
layers. `ErrorMiddleware.dispatch` catches it with `call_next(request)` and re-raises it with
`raise`. The re-raised exception reaches Starlette's outermost `ServerErrorMiddleware`, which:
- In production (uvicorn): renders an HTML 500 page.
- In tests using `httpx.AsyncClient(transport=ASGITransport(..., raise_app_exceptions=False))`:
  returns a 500 JSON/text response.

The result: an invalid Bearer token produces a 500, not a 401, when the client does not crash on
the unhandled exception (i.e. with `raise_app_exceptions=False`).

**Fix**: Convert `HTTPException` to a `JSONResponse` in `ErrorMiddleware.dispatch` instead of
re-raising it. This preserves the correct status code and all response headers
(`WWW-Authenticate`, etc.) regardless of whether the exception originated from a route handler
or from inner middleware.

**Severity**: Breaking — invalid tokens return 500 instead of 401 in all deployments where
`BaseHTTPMiddleware` is used for both `ErrorMiddleware` and `RequestContextMiddleware`.

**File fixed**: `varco_fastapi/varco_fastapi/middleware/error.py`

---

### F06 — `httpx.ASGITransport` does not trigger the ASGI lifespan

**Smell**: `httpx.AsyncClient(transport=ASGITransport(app))` does not send `lifespan.startup`
or `lifespan.shutdown` events. Any initialization deferred to `VarcoLifespan._setup` (e.g.
`registry.load_all()`, `bus.start()`) never runs, leaving the app in a partially initialized
state.

In this example, `registry.load_all()` was initially deferred to `_bootstrap` (the lifespan
setup). With ASGITransport, the keyset is never populated, so every JWT verification raises
`UnknownKidError` and all authenticated requests get 401.

Routes registered via `app.include_router()` inside `_bootstrap` are also never mounted, making
all endpoints 404.

**Fix**: In this example, register routes synchronously in `create_app()` (only `load_all()`
needs the event loop, so keep that in `_bootstrap`). In tests, call `await registry.load_all()`
explicitly in the `client` fixture before the `AsyncClient` is used.

**Severity**: Non-breaking as a framework issue — this is a known `httpx` limitation, not a
varco bug. Document the pattern for test authors: call async init explicitly in test fixtures
when using `ASGITransport`.

**Fixed in**: `examples/02-api-gateway-guards/app.py`, `tests/test_smoke.py`
