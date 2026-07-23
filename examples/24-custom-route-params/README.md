# 24 — Custom Route Parameters

A **service-free** FastAPI app showing that custom `@route` handlers on a
`GenericRouter` (or any `VarcoRouter` / `VarcoCRUDRouter`) accept the **full
FastAPI parameter surface** — exactly like a hand-written FastAPI endpoint —
while `ctx` injection and `RouteGuard` authorization keep working.

No database, no broker, no Docker needed.

## What you'll learn

| Concept | Where |
|---------|-------|
| Type-coerced path params (`int` in `/{item_id}`, 422 on bad segment) | `GET /catalog/items/{item_id}` |
| `Query(...)` params — validated & coerced (`str` pattern, `int` range, `bool`) | `get_item`, `search` |
| `Body(...)` — Pydantic request body, validated | `POST /catalog/items` |
| `Depends(...)` — arbitrary FastAPI dependency injection | `search` (`PricingService`) |
| `Request` — the raw request injected by annotation | `search` |
| `ctx: AuthContext` — still injected from the router's `_auth` | every authed handler |
| Return annotation → OpenAPI response model | `get_item`, `create_item` |
| `RouteGuard` (`require_scopes`) on a rich handler | `GET /catalog/reports/summary` |
| `allow_anonymous()` — public, no params | `GET /catalog/health` |

## Endpoints

| Method | Path | Guard | Demonstrates |
|--------|------|-------|--------------|
| GET | `/catalog/health` | `allow_anonymous()` | No params, no token |
| GET | `/catalog/items/{item_id}` | authed | Typed path param + Query + ctx → `Item` |
| POST | `/catalog/items` | authed | Pydantic `Body` + ctx → `Item` (201) |
| GET | `/catalog/search` | authed | Query + `Depends` + `Request` |
| GET | `/catalog/reports/summary` | `require_scopes("catalog:read")` | Guard + ctx + Query |

## Run

```bash
cd examples/24-custom-route-params
uv run uvicorn app:app --reload
```

Two demo API keys (sent as `X-API-Key`): `reader-key` (has the `catalog:read`
scope) and `guest-key` (no scopes).

```bash
# Anonymous — no key needed
curl localhost:8000/catalog/health

# Typed path param + validated query param + ctx
curl -H 'X-API-Key: reader-key' 'localhost:8000/catalog/items/42?currency=eur'

# Pydantic body → 201 + response model
curl -X POST -H 'X-API-Key: reader-key' -H 'content-type: application/json' \
     -d '{"name":"widget","price_cents":500}' localhost:8000/catalog/items

# Query + Depends + Request
curl -H 'X-API-Key: reader-key' 'localhost:8000/catalog/search?q=widget&limit=5&in_stock=false'

# Guarded — 200 for reader-key, 403 for guest-key
curl -H 'X-API-Key: reader-key' 'localhost:8000/catalog/reports/summary?window=7'
curl -H 'X-API-Key: guest-key'  'localhost:8000/catalog/reports/summary'   # 403
```

Open `localhost:8000/docs` — every Query/Body param and the `Item` response
model appear in the generated OpenAPI schema, because FastAPI drives the parsing
from the handler's synthesized signature.

## Test

```bash
cd examples
uv run pytest 24-custom-route-params/tests/
```

## How it works

When `build_router()` materializes a `@route` method, it synthesizes a wrapper
whose `__signature__` **mirrors your handler** (minus `self`), so FastAPI's own
dependency resolver parses, validates, coerces and injects every parameter.
`ctx`/`auth`/`context` is fed from the router's `_auth` via a `Depends`, the
`RouteGuard` runs before the body, and the `?with_async` offload path is
preserved. See `technical_docs/features/custom-routes.md` for the full mechanics.
