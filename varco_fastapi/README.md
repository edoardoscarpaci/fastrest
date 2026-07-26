# varco-fastapi

[![PyPI version](https://img.shields.io/pypi/v/varco-fastapi)](https://pypi.org/project/varco-fastapi/)
[![Python](https://img.shields.io/pypi/pyversions/varco-fastapi)](https://pypi.org/project/varco-fastapi/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://github.com/edoardoscarpaci/varco/blob/main/LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-edoardoscarpaci%2Fvarco-blue?logo=github)](https://github.com/edoardoscarpaci/varco)

FastAPI integration and HTTP client utilities for **varco**.

Provides structured HTTP connection configuration, TLS trust-store management,
JWT authority, and HTTP middleware wiring on top of FastAPI and httpx.
Requires [`varco-core`](https://pypi.org/project/varco-core/).

---

## Install

```bash
pip install varco-fastapi
```

---

## HTTP connection settings

`HttpConnectionSettings` is a structured config object that produces kwargs
for `httpx.AsyncClient` (or `httpx.Client`).

Unlike the Postgres/Redis/Kafka settings, **there is no fixed env-var prefix**.
A service typically calls many different external HTTP APIs — a hardcoded
`HTTP_` prefix would only allow one of them to be configured from env vars at a
time.  Instead, you supply a prefix when loading from env:

```python
payment = HttpConnectionSettings.from_env(prefix="PAYMENT_API_")
notify  = HttpConnectionSettings.from_env(prefix="NOTIF_API_")
```

### Plain connection (no auth, no TLS)

```python
import httpx
from varco_fastapi.connection import HttpConnectionSettings

# Direct construction — no env vars read
conn = HttpConnectionSettings(base_url="https://api.example.com/v1", timeout=10.0)

async with httpx.AsyncClient(**conn.to_httpx_kwargs()) as client:
    response = await client.get("/users")
```

### From environment variables (multi-client)

```bash
# Payment API
PAYMENT_API_BASE_URL=https://pay.example.com/v1
PAYMENT_API_TIMEOUT=5.0

# Notification API
NOTIF_API_BASE_URL=https://notify.example.com
NOTIF_API_TIMEOUT=10.0
```

```python
payment = HttpConnectionSettings.from_env(prefix="PAYMENT_API_")
notify  = HttpConnectionSettings.from_env(prefix="NOTIF_API_")

async with httpx.AsyncClient(**payment.to_httpx_kwargs()) as client:
    await client.post("/charge", json={"amount": 9.99})
```

You can also configure via host and port instead of a full URL:

```bash
MY_SVC_HOST=api.example.com
MY_SVC_PORT=8080
# effective base_url → "http://api.example.com:8080"
```

### With Basic authentication

```python
from varco_core.connection import BasicAuthConfig

conn = HttpConnectionSettings(
    base_url="https://api.example.com",
    auth=BasicAuthConfig(username="svc-user", password="secret"),
)
# to_httpx_kwargs() includes auth=("svc-user", "secret") automatically

async with httpx.AsyncClient(**conn.to_httpx_kwargs()) as client:
    response = await client.get("/protected")
```

From env:

```bash
MY_SVC_BASE_URL=https://api.example.com
MY_SVC_AUTH__TYPE=basic
MY_SVC_AUTH__USERNAME=svc-user
MY_SVC_AUTH__PASSWORD=secret
```

```python
conn = HttpConnectionSettings.from_env(prefix="MY_SVC_")
```

### With OAuth2 static bearer token

```python
from varco_core.connection import OAuth2Config

conn = HttpConnectionSettings(
    base_url="https://api.example.com",
    auth=OAuth2Config(token="eyJhbGciOiJSUzI1NiJ9..."),
)
# OAuth2 is NOT injected into kwargs automatically — httpx has no built-in
# OAuth2 flow.  Add the Authorization header via a middleware or event hook:
async with httpx.AsyncClient(**conn.to_httpx_kwargs()) as client:
    response = await client.get(
        "/protected",
        headers={"Authorization": f"Bearer {conn.auth.token}"},
    )
```

> **Note:** For OAuth2 client-credentials flows (token refresh), use an
> `httpx` event hook or middleware — `HttpConnectionSettings` is a pure config
> object and does not manage token lifecycle.

### With TLS / SSL (custom CA)

```python
from varco_core.connection import SSLConfig
from pathlib import Path

ssl = SSLConfig(ca_cert=Path("/etc/ssl/api-ca.pem"), verify=True)
conn = HttpConnectionSettings.with_ssl(
    ssl,
    base_url="https://secure-api.example.com",
)
# to_httpx_kwargs()["verify"] → ssl.SSLContext built from the CA cert

async with httpx.AsyncClient(**conn.to_httpx_kwargs()) as client:
    response = await client.get("/data")
```

From env:

```bash
MY_SVC_BASE_URL=https://secure-api.example.com
MY_SVC_SSL__CA_CERT=/etc/ssl/api-ca.pem
MY_SVC_SSL__VERIFY=true
```

```python
conn = HttpConnectionSettings.from_env(prefix="MY_SVC_")
```

### Disable TLS verification (dev / testing only)

```python
ssl = SSLConfig(verify=False, check_hostname=False)
conn = HttpConnectionSettings.with_ssl(ssl, base_url="https://localhost:8443")
# to_httpx_kwargs()["verify"] → False
```

### With mTLS (client certificates)

```python
ssl = SSLConfig(
    ca_cert=Path("/etc/ssl/ca.pem"),
    client_cert=Path("/etc/ssl/client.crt"),
    client_key=Path("/etc/ssl/client.key"),
)
conn = HttpConnectionSettings.with_ssl(ssl, base_url="https://mtls-api.example.com")
async with httpx.AsyncClient(**conn.to_httpx_kwargs()) as client:
    response = await client.get("/secure")
```

### Bridge to `TrustStore` (legacy `ClientProfile`)

```python
trust_store = conn.to_trust_store()   # None when ssl is not set
# use with ClientProfile.production(trust_store=trust_store)
```

### Connection settings reference

All field names below assume a prefix of `MY_SVC_` — replace it with your own.

| Env var | Default | Description |
|---|---|---|
| `{PREFIX}HOST` | `localhost` | API hostname (used when `BASE_URL` is empty) |
| `{PREFIX}PORT` | `443` | API port (used when `BASE_URL` is empty) |
| `{PREFIX}BASE_URL` | _(empty)_ | Full base URL — overrides host/port when set |
| `{PREFIX}TIMEOUT` | `30.0` | Default request timeout in seconds |
| `{PREFIX}SSL__CA_CERT` | — | Path to CA certificate |
| `{PREFIX}SSL__CLIENT_CERT` | — | Path to client certificate (mTLS) |
| `{PREFIX}SSL__CLIENT_KEY` | — | Path to client private key (mTLS) |
| `{PREFIX}SSL__VERIFY` | `true` | TLS peer verification (`false` = skip) |
| `{PREFIX}AUTH__TYPE` | — | `basic` or `oauth2` |
| `{PREFIX}AUTH__USERNAME` | — | Basic auth username |
| `{PREFIX}AUTH__PASSWORD` | — | Basic auth password |
| `{PREFIX}AUTH__TOKEN` | — | OAuth2 static bearer token |

---

## CRUD routers

`VarcoCRUDRouter[D, PK, C, R, U]` (and the pre-composed `CRUDRouter` / `ReadOnlyRouter` /
`WriteRouter` / `NoDeleteRouter` presets in `varco_fastapi.router.presets`) is the
service-backed router base — it injects an `AsyncService`, dispatches the standard CRUD
actions, and auto-registers named tasks for `?with_async=true` recovery.

```python
from varco_fastapi.router.presets import CRUDRouter

class OrderRouter(CRUDRouter[Order, UUID, OrderCreate, OrderRead, OrderUpdate]):
    _prefix = "/orders"
    _service = container.get(OrderService)  # AsyncService[Order, UUID, C, R, U]
    _auth = container.get(JwtBearerAuth)
```

### Typed concrete service (6th `S` type arg)

Add the concrete `AsyncService` subclass as an optional 6th type argument to get
`self._service` typed `ConcreteService | None` and the `self.service` accessor typed
non-Optional `ConcreteService` — custom service methods beyond the CRUD surface are then
visible to the type checker with zero per-subclass boilerplate (no cast, no hand-rolled
`@property` override):

```python
from varco_fastapi.router.endpoint import route

class OrderService(AsyncService[Order, UUID, OrderCreate, OrderRead, OrderUpdate]):
    async def cancel_order(self, order_id: UUID) -> None: ...

class OrderRouter(CRUDRouter[Order, UUID, OrderCreate, OrderRead, OrderUpdate, OrderService]):
    _prefix = "/orders"

    @route("POST", "/{order_id}/cancel")
    async def cancel(self, order_id: UUID) -> None:
        # self.service is typed OrderService — .cancel_order is visible with no cast.
        await self.service.cancel_order(order_id)
```

- 5-arg subscription (`CRUDRouter[Order, UUID, OrderCreate, OrderRead, OrderUpdate]`) keeps
  working unchanged — `S` is defaulted via PEP 696 (`typing_extensions.TypeVar`, since
  `requires-python = ">=3.12"` predates the native 3.13 syntax) and resolves to
  `AsyncService[Any, ...]`.
- `self.service` raises `RuntimeError` if `_service` was never injected/set — prefer it over
  `self._service` at call sites that invoke custom methods, so you don't repeat an
  `is None` guard. The 501-Not-Implemented CRUD fallback path is unaffected — it still reads
  `_service` directly, not this property.
- **Fallback idiom for anyone staying on 5 type args** — declare a subclass `@property`
  that casts:

  ```python
  from typing import cast

  class OrderRouter(CRUDRouter[Order, UUID, OrderCreate, OrderRead, OrderUpdate]):
      _prefix = "/orders"
      _service = container.get(OrderService)

      @property
      def order_service(self) -> OrderService:
          return cast(OrderService, self._service)
  ```

---

## Service-free (generic) REST servers

Use `GenericRouter` when the server has no `AsyncService` or repository — for example a
data-transformation pipeline, an API gateway, or computed analytics routes.  All
cross-cutting features (middleware, telemetry, auth, authorization) work identically.

```python
from varco_fastapi.router.presets import GenericRouter
from varco_fastapi.router.endpoint import route
from varco_fastapi.auth import JwtBearerAuth
from varco_fastapi.auth.guard import require_scopes, require_roles, allow_anonymous

class ReportRouter(GenericRouter):
    _prefix = "/reports"
    _auth = JwtBearerAuth(...)

    # Requires scope — denies 403 if caller does not have "reports:read"
    @route("GET", "/summary", requires=require_scopes("reports:read"))
    async def get_summary(self, ctx) -> dict:
        return {"total": 42}

    # Requires role
    @route("DELETE", "/cache", requires=require_roles("admin"))
    async def purge_cache(self, ctx) -> None: ...

    # Public endpoint — allow_anonymous bypasses auth checks entirely
    @route("GET", "/status", requires=allow_anonymous())
    async def status(self, ctx) -> dict:
        return {"ok": True}

app = create_varco_app(routers=[ReportRouter])
```

**Available guard helpers** (`varco_fastapi.auth.guard`):

| Helper | Description |
|---|---|
| `require_scopes(*s, all=True)` | All (or any) OAuth scopes must be present |
| `require_roles(*r, all=True)` | All (or any) named roles must be present |
| `require_grant(action, key)` | `ctx.can(action, resource_key)` must be True |
| `require_token_profile(*names)` | Resolved JWT token profile must be one of `names` |
| `require_predicate(fn)` | Custom sync/async callable returning bool |
| `allow_anonymous()` | Anonymous callers pass through (public endpoints) |

### Custom `@route` handlers — full FastAPI parameters

A custom `@route` method may declare **any parameter a normal FastAPI endpoint can**,
and FastAPI parses, validates, coerces and injects it — `Query(...)`, `Body(...)`
(Pydantic models), `Depends(...)`, `Request`/`Response`/`BackgroundTasks`, and
**type-coerced** path params. The return annotation drives the OpenAPI response model.
`ctx`/`auth`/`context` still receive the router's `AuthContext`, and any `RouteGuard`
still runs before the handler.

```python
from fastapi import Body, Depends, Query, Request
from pydantic import BaseModel

from varco_core.auth.base import AuthContext

class SummaryFilter(BaseModel):
    since: str | None = None

class ReportRouter(GenericRouter):
    _prefix = "/reports"
    _auth = JwtBearerAuth(...)

    @route("POST", "/{report_id}/summary", requires=require_scopes("reports:read"))
    async def summary(
        self,
        report_id: int,                       # typed path param — coerced to int
        ctx: AuthContext,                     # injected from _auth
        window: int = Query(30, ge=1, le=365),  # validated query param (422 on bad input)
        filters: SummaryFilter = Body(...),   # Pydantic request body
        repo: Repo = Depends(get_repo),       # arbitrary FastAPI dependency
        request: Request = None,              # raw request if you want it
    ) -> SummaryResponse:                     # → OpenAPI response model
        ...
```

Under the hood `build_router()` synthesizes a wrapper whose `__signature__` mirrors the
method, so FastAPI drives all parsing natively — no manual request handling needed.

---

## JWT authentication — foreign claim shapes, token profiles, hardening

`JwtBearerAuth` verifies a Bearer JWT via `TrustedIssuerRegistry` and builds an
`AuthContext` from it. As of the claim-transformer + token-profile layer
(`varco_core.jwt`), this happens automatically even when the token was minted by a
third-party IdP with a different claim shape (Keycloak's `realm_access.roles`,
Cognito's `token_use`, a bespoke `sofy-roles` claim, …) — see
`technical_docs/features/jwt-claim-transformer.md` for the full env-var reference and
per-issuer recipes.

```python
from varco_fastapi.auth import JwtBearerAuth

# Hardened: enforce this service's audience + tolerate 30s of clock skew.
# Both also read from VARCO_JWT_AUDIENCE / VARCO_JWT_LEEWAY_SECONDS when omitted.
auth = JwtBearerAuth(registry, audience="orders-api", leeway=30.0)
```

`audience=None` (the default) does **not** enforce `aud` — `JwtBearerAuth` logs one
warning at construction time when this is the case. Set an explicit `audience=` (or
`VARCO_JWT_AUDIENCE`) to reject tokens minted for a different service.

### Named token profiles — replacing `SYSTEM_ISSUER`

A deployment can recognise more than one kind of trusted internal/system token
(`system`, `internal`, `partner`, `service-mesh`, …) via env-declared
`TokenProfile`s, and gate a route on the resolved profile with
`require_token_profile()`:

```python
from varco_fastapi.router.presets import GenericRouter
from varco_fastapi.router.endpoint import route
from varco_fastapi.auth import JwtBearerAuth
from varco_fastapi.auth.guard import require_token_profile

# VARCO_JWT_PROFILE__INTERNAL__ISS=mesh-signer
# VARCO_JWT_PROFILE__INTERNAL__TOKEN_TYPE=system
# VARCO_JWT_PROFILE__INTERNAL__ROLES=internal

class MeshRouter(GenericRouter):
    _prefix = "/mesh"
    _auth = JwtBearerAuth(registry)

    @route("GET", "/internal-only", requires=require_token_profile("internal"))
    async def internal_only(self, ctx) -> dict:
        return {"ok": True}
```

See `technical_docs/features/token-profiles.md` for the full env var reference,
precedence rules, and the `SYSTEM_ISSUER` deprecation note.

---

## Composite / all-in-one deployment

Combine several independently-built services into a **single deployable process**
without changing any of them. Each service keeps its own `DIContainer`, database,
environment, middleware, and `/docs` — they are mounted side by side under path
prefixes.

```python
from varco_fastapi import create_composite_app, ServiceMount

from orders_service.app import app as orders_app      # its own create_varco_app()
from billing_service.app import app as billing_app     # its own container + DB

composite = create_composite_app([
    ServiceMount("/orders", orders_app),
    ServiceMount("/billing", billing_app),
])
# uvicorn composite:composite
#   /orders/...   → orders service (own docs at /orders/docs)
#   /billing/...  → billing service (own docs at /billing/docs)
#   /health       → aggregate health across both services (503 if any is down)
#   /             → landing page listing each service
```

`create_composite_app` installs a `CompositeLifespan` that drives **each sub-app's
own lifespan** — Starlette does not propagate lifespan into mounted apps, so this is
what actually starts each service's DB pool / event bus / outbox relay. Startup is
fail-fast (one broken service aborts the whole process); shutdown is LIFO.

All services share one `os.environ`. Runtime isolation is automatic; the only hazard
is build-time env-name collisions. Either namespace env vars per service
(`ORDERS_DB_URL`, `BILLING_DB_URL`) or use `build_service` for a scoped overlay:

```python
from varco_fastapi import build_service

orders = build_service("/orders", create_orders_app,
                        env={"DATABASE_URL": "postgresql+asyncpg://.../orders"})
billing = build_service("/billing", create_billing_app,
                        env={"DATABASE_URL": "postgresql+asyncpg://.../billing"})
composite = create_composite_app([orders, billing])
```

See `technical_docs/features/composite-deployment.md` and
`examples/23-composite-all-in-one/` for the full reference.

---

## Related packages

| Package | Description |
|---|---|
| [`varco-core`](https://pypi.org/project/varco-core/) | Domain model, service layer, JWT authority — required dependency |
| [`varco-sa`](https://pypi.org/project/varco-sa/) | SQLAlchemy async backend |
| [`varco-kafka`](https://pypi.org/project/varco-kafka/) | Kafka event bus backend |
| [`varco-redis`](https://pypi.org/project/varco-redis/) | Redis event bus + cache backend |

---

## Links

- **Repository**: https://github.com/edoardoscarpaci/varco
- **Issue tracker**: https://github.com/edoardoscarpaci/varco/issues
