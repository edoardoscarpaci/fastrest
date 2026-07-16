# 23 — Composite (All-in-One) Deployment

Combine two independently-built varco services — **orders** and **billing** — into a
single deployable ASGI process, while each keeps its own configuration, middleware, and
docs. This is the *all-in-one deployment* pattern: ship a service-oriented codebase as
one container/process without merging code or config.

## What this example shows

| Feature | Details |
|---|---|
| `create_composite_app` | Mounts each service as an ASGI sub-app under a prefix (`/orders/*`, `/billing/*`) |
| `ServiceMount` | Wraps an already-built `FastAPI` app with its mount prefix |
| Lifespan aggregation | `CompositeLifespan` drives every sub-app's own lifespan (Starlette does **not** do this for mounted apps) |
| Aggregate health | Root `GET /health` probes both services in-process and returns a per-service breakdown (503 if any is unhealthy) |
| Landing page | Root `GET /` lists each service and links its `{prefix}/docs` |
| `build_service` | Builds two services that read the **same bare env var** under isolated scoped environments |
| Per-service isolation | Each service keeps its own middleware, exception handlers, `/docs`, and config |

## Files

| File | Role |
|---|---|
| `orders_service.py` | A minimal standalone service (`create_orders_app`) — reads `ORDERS_DB_URL` |
| `billing_service.py` | A second standalone service (`create_billing_app`) — reads `SERVICE_DB_URL` |
| `composite.py` | Combines them two ways: plain `ServiceMount`s and scoped `build_service` |
| `tests/` | Smoke tests for routing, docs, aggregate health, and env isolation |

## Run it

```bash
cd examples/23-composite-all-in-one
uv run uvicorn composite:composite --reload
```

Then open:

| URL | What you get |
|---|---|
| `http://localhost:8000/` | Landing page listing both services |
| `http://localhost:8000/orders/docs` | The **orders** service's own Swagger docs |
| `http://localhost:8000/billing/docs` | The **billing** service's own Swagger docs |
| `http://localhost:8000/orders/orders-api/status` | Orders route (echoes its DB URL) |
| `http://localhost:8000/billing/billing-api/status` | Billing route (echoes its DB URL) |
| `http://localhost:8000/health` | Aggregate health across both services |

## Key ideas

**Mounting preserves everything.** Each service is mounted verbatim, so its middleware,
exception handlers, and OpenAPI docs work exactly as they do standalone. There is no
single merged schema — browse each service's own `{prefix}/docs`.

**Lifespan is the one hard part.** Starlette only runs the root app's lifespan, so a
naive `app.mount(...)` would leave each service's DB pool / event bus / outbox relay
un-started. `create_composite_app` installs a `CompositeLifespan` that enters each
sub-app's own lifespan — fail-fast on startup (one broken service aborts the whole
process) and LIFO on shutdown.

**Environment isolation.** All services share one process and one `os.environ`. Runtime
isolation is automatic (each app holds its own container/engine). The only hazard is
build-time env-name collisions — solved either by namespacing env vars per service
(`ORDERS_DB_URL`, `BILLING_DB_URL`) or by `build_service(..., env={...})`, which overlays
an environment only while that one service is built. See `build_scoped_composite()` in
`composite.py`.

## Run the tests

```bash
uv run pytest examples/23-composite-all-in-one/tests/ -q
```

## See also

- `technical_docs/features/composite-deployment.md` — full technical reference.
- Example 01 — the minimal single service that this pattern combines.
