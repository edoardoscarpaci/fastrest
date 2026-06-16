# 07 · Casbin Policy Engine

**Goal**: Demonstrate Casbin RBAC authorization at the service layer via `PolicyEngineAuthorizer` and `CasbinPolicyEngine`.

Unlike the grant-based example (06), authorization rules live in a **durable policy store** (PostgreSQL) and can be changed at runtime — no token reissue required.

---

## What this example shows

| Concept | Where |
|---|---|
| Casbin RBAC model preset | `CasbinSettings(model_preset="rbac")` in `app.py` |
| SQLAlchemy-backed policy store | `adapter="sqlalchemy"`, `db_url=...` in `app.py` |
| Service-layer authorization bridge | `PolicyEngineAuthorizer` + `RequestMapper` in `app.py` |
| Policy CRUD API | `engine.add_policy()`, `engine.add_role_for_user()` in tests |
| Policy persistence round-trip | `TestPolicyPersistence.test_policies_survive_engine_restart` |
| HTTP endpoint + Casbin enforcement | `TestHttpEndpointWithCasbin` in `test_smoke.py` |

---

## Architecture

```
HTTP request
  ↓
HeaderAuth              reads X-User-Id / X-User-Role → AuthContext
  ↓
RequestContextMiddleware sets AuthContext ContextVar
  ↓
DocumentRouter          routes to DocumentService
  ↓
DocumentService         calls AbstractAuthorizer.authorize(ctx, action, resource)
  ↓
PolicyEngineAuthorizer  maps ctx.user_id → Casbin sub, resource_key → obj
  ↓
CasbinPolicyEngine      evaluates (sub, obj, act) against stored rules
  ↓
PostgreSQL              durable casbin_rule table (via casbin-async-sqlalchemy-adapter)
```

---

## Files

| File | Purpose |
|---|---|
| `models.py` | `Document` domain model (title, content, UUID pk) |
| `dtos.py` | `DocumentCreate` / `DocumentRead` / `DocumentUpdate` DTOs |
| `assembler.py` | DTO ↔ domain model mapping (`DocumentAssembler`) |
| `repo.py` | In-memory repository + UoW (documents only; policy store uses Postgres) |
| `di.py` | `DocumentModule` — registers `IUoWProvider` |
| `auth.py` | `HeaderAuth` — lightweight header-based `AuthContext` builder |
| `service.py` | `DocumentService` — delegates authorization to `PolicyEngineAuthorizer` |
| `router.py` | `DocumentRouter` — CRUD HTTP endpoints |
| `app.py` | `create_app(db_url, model_preset)` — wires everything together |
| `tests/conftest.py` | Session-scoped Postgres container + engine + HTTP client fixtures |
| `tests/test_smoke.py` | Integration smoke tests |

---

## Running locally

```bash
cd examples/07-casbin-policy-engine
# Requires a local Postgres instance
VARCO_CASBIN_DB_URL=postgresql+asyncpg://postgres:postgres@localhost/casbin_example \
    uv run uvicorn app:app --reload
```

Add a policy (using curl against the running server — authenticated as alice with a Casbin policy):

```bash
# Seed a policy directly via psql or via PolicyManagement:
# p, alice, documents, create
# g, alice, writer

# Create a document as alice
curl -X POST http://localhost:8000/v1/documents \
  -H "X-User-Id: alice" \
  -H "Content-Type: application/json" \
  -d '{"title": "My First Doc", "content": "Hello Casbin"}'
```

---

## Running integration tests

Requires Docker (testcontainers spins up Postgres 16-alpine automatically):

```bash
cd /path/to/varco  # workspace root
uv run pytest .claude/worktrees/feature+examples-catalog/examples/07-casbin-policy-engine/tests/ -v -m integration
```

---

## Key design decisions

### In-memory document store + SQLAlchemy for policies only

The document store is kept in-memory (`InMemoryUoWProvider`) so the example stays focused on the Casbin policy engine. A real application would use `varco_sa` or `varco_beanie` for documents. The SQLAlchemy adapter is used **only** for the Casbin `casbin_rule` table — this is intentional: it demonstrates that you can add Casbin persistence to any app without changing the domain layer.

### `HeaderAuth` over `JwtBearerAuth`

Production applications use `JwtBearerAuth` + `TrustedIssuerRegistry` (see example 06). Here we use a lightweight `X-User-Id` header so the example stays focused on Casbin rather than JWT key management.

### Manual engine construction over `bootstrap(container)`

`varco_casbin.di.bootstrap()` scans the package and auto-discovers `CasbinPolicyEngine`. This example constructs the engine directly so `db_url` can be supplied programmatically (essential for test isolation with per-run SQLite / Postgres URLs).

### `RequestMapper` default convention

`RequestMapper.object_for()` calls `_default_resource_key(Document, entity)`:
- Collection ops (create, list): `"documents"`
- Instance ops (read, update, delete): `"documents:<pk>"`

Casbin policies must use `"documents"` as the object for collection-level enforcement. For instance-level enforcement you would add rules like `("alice", "documents:abc123", "delete")`.

---

## Casbin RBAC policy model

The `"rbac"` preset model defines:
```
[request_definition]
r = sub, obj, act

[policy_definition]
p = sub, obj, act

[role_definition]
g = _, _

[policy_effect]
e = some(where (p.eft == allow))

[matchers]
m = g(r.sub, p.sub) && r.obj == p.obj && r.act == p.act
```

Example rules:
```
p, writer, documents, create
p, reader, documents, read
g, alice, writer
g, alice, reader
```

With these rules, alice (assigned both `writer` and `reader` roles) can create and read documents; she cannot update or delete without additional policies.
