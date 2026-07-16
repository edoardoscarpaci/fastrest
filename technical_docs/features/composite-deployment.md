# Composite Deployment — Technical Reference

`create_composite_app` combines several **independently-built** varco services into a
single deployable ASGI process — an *all-in-one deployment*. Each microservice keeps
its own `DIContainer`, its own database, its own environment, its own middleware stack,
and its own `/docs`. Nothing about how a service runs changes; the services are merely
mounted side by side under path prefixes (`/orders/*`, `/billing/*`) in one process.

Use it when you have multiple services already built with `create_varco_app()` and want
to ship them as one container/process (fewer pods, simpler local dev, a monolith-style
deploy of a service-oriented codebase) **without merging their code or their config**.

---

## Core files

| File | Role |
|---|---|
| `varco_fastapi/composite.py` | `ServiceMount`, `CompositeLifespan`, `create_composite_app`, `build_service` |
| `varco_fastapi/app.py` | `create_varco_app()` — builds each individual service (unchanged) |
| `varco_fastapi/lifespan.py` | `VarcoLifespan` — the per-service lifespan the composite drives |
| `varco_fastapi/router/health.py` | `HealthRouter` — the per-service health contract the aggregate reuses |
| `varco_fastapi/__init__.py` | Package-level re-exports |

---

## The model: mount pre-built apps as ASGI sub-applications

You build each service exactly as today, then hand the finished `FastAPI` apps to
`create_composite_app`:

```python
from orders_service.app import app as orders_app      # its own create_varco_app()
from billing_service.app import app as billing_app     # its own container + DB + env

from varco_fastapi import create_composite_app, ServiceMount

composite = create_composite_app([
    ServiceMount("/orders", orders_app),
    ServiceMount("/billing", billing_app),
])
# uvicorn composite:composite
```

Each service is mounted verbatim with `app.mount(prefix, sub_app)`. Mounting a full
FastAPI app routes the entire subtree to it, so the sub-app's **middleware, exception
handlers, and `/docs` + `/openapi.json` are all preserved**. `orders_app` behaves at
`/orders/...` identically to how it behaves standalone at `/...`.

### Why not merge everything into one app + one container?

Because that would break isolation. Two services' same-typed DI singletons (an
`AsyncEngine`, a settings object) would collide in one container, defeating the
"different database, different environment" guarantee. Mounting sub-apps keeps each
service's container and connections fully separate — which is the entire point.

---

## The one hard part: lifespan aggregation

> **Gotcha:** Starlette's `Router.lifespan` only runs the **root** app's lifespan. It
> does **not** propagate lifespan startup/shutdown into mounted sub-applications.

Every varco service wires its DB pools, `AbstractEventBus`, `OutboxRelay`, and job
runners through the app's lifespan (`VarcoLifespan`). If you mount services naively,
none of that startup ever runs — the app answers requests with dead connections.

`CompositeLifespan` is the piece that fixes this. It enters each sub-app's own
`lifespan_context` explicitly, using an `AsyncExitStack`:

```python
async with AsyncExitStack() as stack:
    for service in services:
        await stack.enter_async_context(
            service.app.router.lifespan_context(service.app)
        )
    yield
# Stack exit stops every started service in LIFO order.
```

`create_composite_app` installs this as the composite's lifespan automatically — you
never construct it by hand.

### Startup failure is fail-fast

If any service's startup raises (e.g. its database is unreachable), the `AsyncExitStack`
unwinds the services that already started and the exception propagates — the **whole
process refuses to serve traffic**. This matches single-service behaviour: a broken
service never comes up, and you never get a half-broken deployment silently serving
some routes and 500-ing others. Shutdown is LIFO (last started, first stopped).

---

## Aggregate health

With `aggregate_health=True` (the default), the composite exposes a root
`GET /health` that probes **each service's own** `/health` endpoint in-process (via an
`httpx.ASGITransport` call — no network hop) and returns a per-service breakdown:

```json
{
  "status": "unhealthy",
  "services": {
    "orders":  { "status": "healthy",   "detail": { ... } },
    "billing": { "status": "unhealthy", "detail": { ... } }
  }
}
```

Overall status is worst-case (`unhealthy` > `degraded` > `healthy`); the endpoint
returns **HTTP 503** if any service is unhealthy, making it a single Kubernetes
readiness probe for the whole deployment. Each service's own `/orders/health`,
`/billing/health` still work underneath unchanged. A service whose probe fails or
times out is reported `unhealthy` — a bad probe never sinks the aggregate.

A root `GET /` landing page (`landing_page=True`) lists each service and links its
`{prefix}/docs`, since the composite root's own `/docs` only shows the health/landing
routes.

---

## Environment isolation — the collision hazard and `build_service`

All services in a composite share **one OS process** and therefore **one `os.environ`**.

- **Runtime isolation is automatic.** Each service captured its own container, engine,
  and settings as plain Python objects at build time. At request time nothing is shared.
- **Build-time env reads can collide.** If two services both read
  `os.environ["DATABASE_URL"]` when they build their engine, and you build both in one
  process, they read the *same* value.

Two ways to keep build-time config isolated:

### Option A — namespace env vars per service (convention)

Give each service its own env-var prefix and read those names in its `create_app()`:

```bash
ORDERS_DATABASE_URL=postgresql+asyncpg://.../orders
BILLING_DATABASE_URL=postgresql+asyncpg://.../billing
```

This is the plain, dependency-free approach — the services simply never share a bare
name.

### Option B — `build_service()` with a scoped env overlay

When services already read bare names (e.g. both read `DATABASE_URL`) and you don't want
to change them, `build_service` overlays an environment **only while the factory builds
that one service**, then restores it:

```python
from varco_fastapi import build_service, create_composite_app

orders = build_service(
    "/orders",
    lambda: create_orders_app(),                       # reads DATABASE_URL
    env={"DATABASE_URL": "postgresql+asyncpg://.../orders"},
)
billing = build_service(
    "/billing",
    lambda: create_billing_app(),                      # also reads DATABASE_URL
    env={"DATABASE_URL": "postgresql+asyncpg://.../billing"},
)

composite = create_composite_app([orders, billing])
```

Each factory sees its own `DATABASE_URL`; the previous environment (including keys that
were unset) is restored after each build, even if the factory raises.

> **Limitation:** the overlay only covers the duration of the `factory()` call. A factory
> must read its configuration **synchronously inside that call** for isolation to hold —
> config read lazily *after* the app is returned will see the restored environment.
> `build_service` mutates `os.environ`, so it is startup-only and not thread-safe.

---

## Public API

```python
@dataclass(frozen=True)
class ServiceMount:
    prefix: str                    # "/orders" — non-empty, starts with "/", unique
    app: FastAPI                   # already built via create_varco_app()
    name: str | None = None        # health-key name; defaults to prefix.strip("/")
    health_path: str = "/health"   # sub-app path the aggregate probe calls

def create_composite_app(
    services: list[ServiceMount],
    *,
    title: str = "Varco Composite Deployment",
    version: str = "0.1.0",
    description: str = "",
    aggregate_health: bool = True,
    health_path: str = "/health",
    landing_page: bool = True,
) -> FastAPI: ...

def build_service(
    prefix: str,
    factory: Callable[[], FastAPI],
    *,
    env: Mapping[str, str] | None = None,
    name: str | None = None,
    health_path: str = "/health",
) -> ServiceMount: ...
```

`create_composite_app` raises `ValueError` at build time if `services` is empty, or if a
prefix is empty, missing a leading `/`, equal to `/`, equal to `health_path`, or
duplicated.

---

## What this is **not**

- **Not** a merged app: there is no single combined OpenAPI schema — browse each service's
  own `{prefix}/docs`.
- **Not** a shared middleware layer: each sub-app owns its full middleware stack and runs
  it independently (no composite-level CORS/tracing by default — that would risk
  double-processing).
- **Not** cross-service DI: services stay fully decoupled. If two services need to talk,
  they use the HTTP client (`varco_fastapi.client`) exactly as they would across a network.

---

## See also

- **Example 23 — `examples/23-composite-all-in-one/`** — a runnable two-service composite.
- `varco_fastapi/lifespan.py` — the per-service `VarcoLifespan` the composite drives.
- `varco_fastapi/router/health.py` — the per-service health contract the aggregate reuses.
