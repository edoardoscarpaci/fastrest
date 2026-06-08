# varco-casbin

Casbin policy-engine authorization backend for **varco** — ACL, RBAC, and ABAC
with dynamic, persisted policies and a ready-made REST management router.

`varco_casbin` implements the backend-agnostic policy seam defined in
`varco_core.auth.policy`:

```
varco_core.auth.PolicyEngine        ← enforce(request) hot path
varco_core.auth.PolicyManagement    ← add/remove/list rules + role assignments
        ↑ both implemented by
varco_casbin.CasbinPolicyEngine     ← wraps casbin.AsyncEnforcer
        ↑ bridged into the service layer by
varco_core.auth.PolicyEngineAuthorizer  (opt-in via CasbinAuthorizationConfiguration)
        ↑ administered over REST by
varco_casbin.CasbinPolicyRouter     ← varco_fastapi GenericRouter (requires the [fastapi] extra)
```

## Install

```bash
pip install varco-casbin                 # engine only (in-memory / file policies)
pip install "varco-casbin[sqlalchemy]"   # + durable DB-backed policy store
pip install "varco-casbin[fastapi]"      # + REST management router
```

## Quick start (DI)

```python
from providify import DIContainer
from varco_casbin.di import bootstrap, enable_policy_authorizer
from varco_core.auth import PolicyEngine, PolicyManagement, AbstractAuthorizer

container = bootstrap(DIContainer())                 # scans the engine + settings
enable_policy_authorizer(container)                  # opt-in: bind the authorizer

engine: PolicyEngine = await container.aget(PolicyEngine)
mgmt:   PolicyManagement = await container.aget(PolicyManagement)

await mgmt.add_role_for_user("alice", "admin")       # g, alice, admin
await mgmt.add_policy("admin", "*", "*")             # p, admin, *, *
```

## Configuration

All settings read from `VARCO_CASBIN_*` env vars (see `CasbinSettings`):

| Env var | Default | Meaning |
|---|---|---|
| `VARCO_CASBIN_MODEL_PRESET` | `rbac` | `acl` / `rbac` / `rbac_domains` / `abac` |
| `VARCO_CASBIN_MODEL_PATH` | — | explicit `.conf` model file (overrides preset) |
| `VARCO_CASBIN_ADAPTER` | `memory` | `memory` / `file` / `sqlalchemy` |
| `VARCO_CASBIN_DB_URL` | — | SQLAlchemy URL for the `sqlalchemy` adapter |
| `VARCO_CASBIN_POLICY_PATH` | — | CSV path for the `file` adapter |
| `VARCO_CASBIN_ADMIN_ROLE` | `admin` | role the REST router requires by default |

## REST management

Mount `build_policy_router(...)` (requires `[fastapi]`) to administer policies and
role assignments over REST — all endpoints guarded by `require_roles(admin_role)`:

```python
from varco_casbin.router import build_policy_router
app.include_router(build_policy_router(engine, server_auth=auth))   # engine = CasbinPolicyEngine
```


```
GET/POST/DELETE /authz/policies   # p-rules
GET/POST/DELETE /authz/roles      # g-rules (role assignments)
POST            /authz/check      # test an enforcement decision
POST            /authz/reload     # reload from the durable store
```

See the varco docs (`technical_docs/features/casbin-authorization.md`) for the
full guide, including the ABAC example.
