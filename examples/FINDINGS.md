# Findings — API Ergonomics Log

### 18-realtime-ws-sse — no new findings

`WebSocketEventBus` and `SSEEventBus` are adapters that wrap an existing
`AbstractEventBus`; they are not `AbstractEventBus` implementations.
The in-process wiring pattern (bus → ws_bus + sse_bus, both started in the
FastAPI lifespan) is clean and requires no DI container for simple examples.

`TestClient` WebSocket works directly with `websocket_connect("/ws")` and
`ws.receive_text()` — no event loop management needed.
SSE endpoint testing at the adapter layer (checking `subscriber_count` and
reading directly from the `SSEConnection._queue`) is more reliable than
trying to drive a streaming HTTP response to completion in tests.

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

---

## 04-profiling-hotspot — no new findings

---

## 06-grant-based-authz

### F07 — Grant-based auth requires DELETE in the write grant; ownership is a second layer

**Smell**: `GrantBasedAuthorizer.authorize()` is called by `AsyncService` before `_check_entity`
for READ/UPDATE/DELETE. A write grant without `Action.DELETE` means the authorizer raises 403
before the ownership check in `_check_entity` ever runs. The two layers are sequential, not
composed: the grant check is a type-level gate; `_check_entity` is an instance-level gate.

**Fix**: Include `Action.DELETE` in the write grant constant if you want users to be able to
delete their own documents. The ownership check in `_check_entity` then provides the
instance-level guard (only the owner or an admin can actually delete a specific document).

**Pattern confirmed**:
- `GrantBasedAuthorizer` → type-level: "can this token class perform this action on documents?"
- `_check_entity` → instance-level: "does this token own this specific document?"

Both checks must pass for DELETE. If the type-level grant is absent, the instance-level check
never runs. Document your write grant's action set explicitly in your `auth.py` constants.

**Severity**: Non-breaking (correct design) but requires explicit awareness when assigning
grants. Callers should not expect `_check_entity` to be the only gate — the authorizer always
runs first.

**Fixed in**: `examples/06-grant-based-authz/auth.py` — `DOCS_WRITE_GRANT` includes DELETE.

---

### F08 — `_check_entity` must raise `ServiceNotFoundError`, never `ServiceAuthorizationError`, for ownership checks

**Smell**: It is tempting to raise `ServiceAuthorizationError` (→ 403) when a non-owner tries
to access a document — the caller *is* unauthorized. But returning 403 reveals the document's
existence, enabling existence oracle attacks (caller can determine "document exists but isn't
mine" by alternating tokens).

**Fix**: Always raise `ServiceNotFoundError` (→ 404) in `_check_entity` when the entity should
be invisible from the caller's perspective (wrong tenant, wrong owner). `ServiceAuthorizationError`
is reserved for cases where the entity's *existence* is not sensitive (e.g., a type-level deny
before the entity is fetched, like "you cannot read any documents of this type at all").

**Rule confirmed**: `_check_entity` contract in `AsyncService`:
- Raise `ServiceNotFoundError` for cross-cutting entity-level blocks (ownership, tenancy).
- Raise `ServiceAuthorizationError` only for type-level or collection-level blocks where
  existence disclosure is not a concern.

**Fixed in**: `examples/06-grant-based-authz/service.py` — `_check_entity` raises
`ServiceNotFoundError(entity.pk, Document)`.

---

## 11-query-filtering

### F09 — Lark grammar `ESCAPED_STRING` requires double-quotes; single-quoted strings cause `UnexpectedCharacters`

**Smell**: The varco `grammar.lark` uses Lark's built-in `ESCAPED_STRING` terminal for string
values. `ESCAPED_STRING` only accepts **double-quoted** strings (e.g. `"electronics"`). Passing
single-quoted strings (e.g. `category = 'electronics'`) causes a Lark
`UnexpectedCharacters: No terminal matches ''' in the current parser context` error which, if
uncaught, propagates as a 500 Internal Server Error.

This affects:
- String equality: `category = "books"` ✅ / `category = 'books'` ❌
- IN lists: `category IN ("books", "home")` ✅ / `category IN ('books', 'home')` ❌
- LIKE: `name LIKE "widget"` ✅ / `name LIKE 'widget'` ❌
- Boolean strings (since the grammar has no bool literal): `in_stock = "True"` ✅

**Fix**: Document the double-quote requirement prominently and validate / normalise client input
in production (swap `'...'` to `"..."` at the HTTP adapter layer, or return a 422 with a clear
message). Numeric and identifier values (`price >= 50.0`, `id = 1`) do not use quotes and are
unaffected.

**Severity**: Non-breaking (correct Lark behaviour) but a common source of confusion when
building REST clients against the filter endpoint. Always validate `q=` input and return 422
with a human-readable message on parse errors rather than propagating `lark.UnexpectedCharacters`
as a 500.

**Noted in**: `examples/11-query-filtering/README.md` and test docstrings.

---

### F10 — `@route` / `GenericRouter` does not inject `Request` into handlers; use plain `APIRouter` + `Query()` for query-param-heavy endpoints


**Smell**: varco's `@route` decorator in `GenericRouter` only injects `ctx` (AuthContext) and
path parameters into the handler method.  It does **not** forward the raw `starlette.Request`
object.  A handler that declares `request: Request` as a parameter receives a
`TypeError: list_products() missing 1 required positional argument: 'request'` at call time.

This means `GenericRouter` cannot be used for endpoints that need access to raw query string
params (e.g. `request.query_params.getlist("filter")`).

**Fix**: For query-param-heavy read endpoints, use a plain FastAPI `APIRouter` with `Query()`
dependency parameters instead of `GenericRouter`.  `Query()` gives OpenAPI schema and
validation for free while keeping the handler signature explicit and testable.

**When to use each**:
- `GenericRouter` + `@route` — service-free handlers that need only `ctx` (AuthContext) and
  path params; no need for request-level access.
- Plain `APIRouter` + `Query()` — handlers that consume query string params directly
  (filter, sort, pagination, etc.).

**Severity**: Non-breaking (correct design — `@route` intentionally limits handler signatures
to the varco-managed injection surface). Document the `APIRouter` escape hatch for query-param
endpoints.

**Noted in**: `examples/11-query-filtering/router.py` DESIGN block.

---

## 19-resilience-payment-gateway

### F11 — `@timeout` covers the entire retry loop, not per-attempt

**Observed**: When stacking `@timeout(0.5)` (outer) and `@retry(max_attempts=3)` (inner), the
0.5 s budget is shared across ALL retry attempts, not reset per attempt. With `base_delay=0.05`
and 3 attempts, the full retry sequence (3 calls × ~0 ms stub latency + 2 × 50 ms delays = ~100 ms)
fits within 0.5 s easily. However, in production with real network calls (say 200 ms each), a
0.5 s outer timeout allows at most two attempts (200 ms + 200 ms = 400 ms) before the timeout fires.

**Pattern confirmed**: stack `@timeout` outermost for an overall SLA budget; `@retry` covers
per-attempt transient failures within that budget. If per-attempt timeouts are needed, apply a
separate `@timeout` to the inner function before `@retry`.

**Severity**: Non-breaking (correct design) but a common source of confusion. Document
explicitly in any gateway code that stacks these two decorators.

---

### F12 — `CircuitOpenError` must be excluded from `RetryPolicy.retryable_on`

**Observed**: `@retry` with default `retryable_on=(Exception,)` will retry on `CircuitOpenError`
because it is an `Exception` subclass. This means the retry loop burns all its attempts trying
to call a circuit that is already OPEN — each attempt gets `CircuitOpenError` immediately
(fast-fail), counts as a "retry", and exhausts the budget. The final error returned is
`RetryExhaustedError` wrapping `CircuitOpenError`, which is confusing.

**Fix**: Always narrow `retryable_on` to the specific transient error types (e.g.
`retryable_on=(RuntimeError, ConnectionError)`) so that `CircuitOpenError` propagates
immediately without consuming retry attempts.

**Rule confirmed**: When stacking `@retry` outside a circuit breaker, always set
`retryable_on` to exclude `CircuitOpenError`.

---

### F13 — `@hedge` exists and is fully functional

`@hedge` / `HedgeConfig` are present in `varco_core.resilience` and export correctly from
`varco_core.resilience.__init__`. The decorator works as documented: issues a speculative
duplicate call after `delay` seconds, accepts the first result, and cancels the other.

In this example `@hedge(HedgeConfig(delay=0.05))` is applied to `get_balance()`. In the happy
path the stub returns in < 1 ms so the hedge never fires — no extra stub calls, no overhead.

**Usage rule confirmed**: only apply `@hedge` to idempotent operations. The example
intentionally omits it from `charge()` to avoid double-charging.

---

### F14 — Circuit breaker `.reset()` is the correct test isolation mechanism for class-level singletons

**Observed**: `PaymentGateway` uses class-level `CircuitBreaker` singletons. Tests that trip the
circuit (e.g. by sending 3 `ALWAYS_FAIL` requests) leave the breaker OPEN for subsequent tests.
The correct fix is `CircuitBreaker.reset()` (or the wrapper `PaymentGateway.reset_breakers()`),
called at the start of each test or in a `pytest.fixture`.

An alternative — creating a fresh `PaymentGateway` per test — does NOT reset the breaker
because the breaker is class-level. Only `reset()` or the class-level singletons being replaced
works.

**Rule confirmed**: For tests that need circuit-breaker isolation, always call `reset()` on
the shared instance — do not rely on creating a new gateway instance.

---

### 21-async-job-runner — F15, F16

#### F15 — `lifespan=True` on `ASGITransport` is required to start `JobRunner` in tests

`JobRunner.start()` must be called before jobs can be enqueued — it sets `_started = True` and
resolves the optional `event_bus` injection.  When using `httpx.ASGITransport`, passing no extra
options skips the ASGI lifespan, so `runner.start()` is never called and jobs may behave
unexpectedly.

The fix: use `ASGITransport(app=test_app, raise_app_exceptions=True)` combined with a
session-scoped `async with httpx.AsyncClient(...)` — the `async with` block triggers the lifespan
context manager on entry and calls `runner.stop()` on exit.  No manual `start()` / `stop()` in
test fixtures needed.

**Rule confirmed**: always use `async with httpx.AsyncClient(transport=ASGITransport(...))` (not
a plain `AsyncClient` instantiation) so the FastAPI lifespan fires in tests.

#### F16 — `await asyncio.sleep(0)` drives asyncio.Task-per-job jobs in tests; repeated ticks needed for jobs with internal `sleep`

`JobRunner` schedules one `asyncio.Task` per job.  A single `await asyncio.sleep(0)` yields to
the event loop and lets the task step forward once.  For jobs that call `asyncio.sleep(...)` 
internally (even a very short duration like `0.001s`), a single `sleep(0)` is not enough — the
task suspends at its own sleep and does not reach the terminal state.

The reliable pattern is a short polling loop:

```python
terminal = {"completed", "failed", "cancelled"}
for _ in range(50):
    await asyncio.sleep(0)
    resp = await client.get(f"/v1/jobs/{job_id}")
    if resp.json()["status"] in terminal:
        break
```

Each tick either advances the task one step or finds it already finished.  50 ticks × ~0 wall
clock = effectively instant in tests while robust to tasks with sub-millisecond internal delays.

**Avoid `asyncio.sleep(0.1)` in tests** — it couples test speed to wall-clock time.  Ticks are
cheaper and more deterministic.
