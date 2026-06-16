# 02 — API Gateway Guards

A **service-free** FastAPI gateway that demonstrates every `RouteGuard` variant
available in `varco_fastapi`.  No database, no broker, no Docker needed.

## What you'll learn

| Concept | Where |
|---------|-------|
| `GenericRouter` — a `VarcoRouter` with no `AsyncService` or domain type args | `router.py` |
| `@route` — custom HTTP handler decorator | `router.py` |
| `allow_anonymous()` — explicitly public endpoint | `GET /health`, `GET /v1/echo` |
| `RouteGuard()` (empty) — any authenticated caller, no scope/role required | `GET /v1/me` |
| `require_scopes("reports:read")` — OAuth scope gate | `GET /v1/reports/summary` |
| `require_roles("admin")` — role gate | `POST /v1/admin/flush-cache` |
| `require_predicate(fn)` — arbitrary callable check | `GET /v1/internal/status` |
| `JwtBearerAuth` — JWT extraction + verification | `auth.py`, `app.py` |
| `AuthContext` — the parsed JWT payload injected into handlers | `router.py` |

## Endpoints

| Method | Path | Guard | Notes |
|--------|------|-------|-------|
| GET | `/health` | `allow_anonymous()` | Always 200; no token needed |
| GET | `/v1/echo` | `allow_anonymous()` | Echo demo; no token needed |
| GET | `/v1/me` | `RouteGuard()` | Any valid JWT; returns subject/roles/scopes |
| GET | `/v1/reports/summary` | `require_scopes("reports:read")` | Fake aggregate data |
| POST | `/v1/admin/flush-cache` | `require_roles("admin")` | 204 No Content |
| GET | `/v1/internal/status` | `require_predicate(...)` | Subject must start with `svc:` |

## Run locally

```bash
# From the workspace root
uv run uvicorn examples.02-api-gateway-guards.app:app --reload
```

Or from inside the example directory:

```bash
cd examples/02-api-gateway-guards
uv run uvicorn app:app --reload
```

## Run the tests

```bash
# From the workspace root
uv run pytest .claude/worktrees/feature+examples-catalog/examples/02-api-gateway-guards/tests/ -v
```

## Token format

Tokens are RS256 JWTs with `roles` and `scopes` claims.  The `auth.py` module exposes
`mint_token(subject, roles=..., scopes=...)` for test use.  In a real app, tokens would
come from your identity provider (Auth0, Keycloak, etc.) after configuring
`TrustedIssuerRegistry.from_env()`.

Example claims:
```json
{
  "sub":    "user:alice",
  "roles":  ["admin"],
  "scopes": ["reports:read"],
  "iss":    "example-gateway",
  "exp":    1234567890
}
```
