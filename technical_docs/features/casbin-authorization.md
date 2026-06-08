# Casbin Authorization (`varco_casbin`)

A pluggable **policy engine** for the service layer: express **ACL, RBAC, and
ABAC** rules, persist them, and edit them at runtime over REST — without
changing any service code.

## Why this exists

Varco's built-in authorization is *static and token-derived*:
`AuthContext.can(action, resource_key)` checks JWT-encoded `ResourceGrant`s.
That is fast and stateless but cannot express dynamic, attribute-based, or
centrally-administered policy. `varco_casbin` adds a *dynamic* layer behind the
same `AbstractAuthorizer` seam, so existing services adopt it with **zero code
changes**.

## Architecture

```
AsyncService → AbstractAuthorizer                       (already injected by services)
                  ↑ implemented by
              PolicyEngineAuthorizer        (varco_core.auth.policy — raising bridge)
                  ↓ delegates to
              PolicyEngine.enforce(EnforcementRequest)  (hot path, backend-agnostic)
              PolicyManagement.add/remove/list/reload   (cold admin surface)
                  ↑ both implemented by
              CasbinPolicyEngine            (varco_casbin — wraps casbin.AsyncEnforcer)
                  ↓ persists via
              build_adapter()               (memory | file | sqlalchemy)
```

The seam (`PolicyEngine`, `PolicyManagement`, `EnforcementRequest`,
`RequestMapper`, `PolicyEngineAuthorizer`) lives in **`varco_core.auth.policy`**
and is backend-agnostic. `varco_casbin` is one implementation; a future
`varco_opa` (see *OPA Backend (Design)*) is another.

## Core concepts

| Concept | Role |
|---|---|
| `EnforcementRequest` | Engine-neutral question: `subject`, `object`, `action`, `subject_attrs`, `object_attrs`, `domain`. |
| `PolicyEngine.enforce(req) -> bool` | Hot path. One boolean decision. Never raises on denial. |
| `PolicyManagement` | Admin: `add_policy` / `remove_policy` / `list_policies` / `add_role_for_user` / `remove_role_for_user` / `roles_for_user` / `reload`. |
| `RequestMapper` | Maps `(AuthContext, Action, Resource)` → `EnforcementRequest`. Override `subject_for` / `object_for` / `domain_for` to customise keying. |
| `PolicyEngineAuthorizer` | `AbstractAuthorizer` that runs the engine and raises `ServiceAuthorizationError` (HTTP 403) on denial. |

`RequestMapper` reuses `_default_resource_key`, so token grants (`"posts:42"`)
and engine policy rules share **one resource-key namespace**.

## Model presets

`CasbinSettings.model_preset` selects a bundled Casbin model (`varco_casbin/models/*.conf`):

| Preset | Model | Request shape |
|---|---|---|
| `acl` | exact `(sub, obj, act)` allow rules | `sub, obj, act` |
| `rbac` (default) | role hierarchy `g(_, _)`, wildcard `*` | `sub, obj, act` |
| `rbac_domains` | per-domain roles `g(_, _, _)` | `sub, dom, obj, act` |
| `abac` | attribute matcher `r.obj.owner_id == r.sub.id \|\| "admin" in r.sub.roles` | `sub, obj, act` |

Supply your own with `model_path=` (a `.conf` file) or `model_text=` (inline).

## Wiring (DI)

```python
from providify import DIContainer
from varco_casbin.di import bootstrap, enable_policy_authorizer
from varco_core.auth import PolicyEngine, PolicyManagement

container = bootstrap(DIContainer())     # CasbinPolicyEngine → PolicyEngine + PolicyManagement
enable_policy_authorizer(container)      # OPT-IN: PolicyEngineAuthorizer → AbstractAuthorizer

engine = await container.aget(PolicyEngine)
await engine.add_role_for_user("alice", "admin")
await engine.add_policy("admin", "*", "*")
```

!!! warning "The authorizer is opt-in by design"
    `scan`/`bootstrap` only bind the *engine*. The authorizer is bound solely by
    `enable_policy_authorizer(container)`. A scanned `@Configuration` would
    auto-activate and silently shadow an application's own authorizer — so the
    binding is an explicit function backed by a module-level `@Provider` (which
    `scan` does not auto-register).

## Configuration (`VARCO_CASBIN_*`)

| Env var | Default | Meaning |
|---|---|---|
| `VARCO_CASBIN_MODEL_PRESET` | `rbac` | `acl` / `rbac` / `rbac_domains` / `abac` |
| `VARCO_CASBIN_MODEL_PATH` | — | explicit `.conf` model file (overrides preset) |
| `VARCO_CASBIN_ADAPTER` | `memory` | `memory` / `file` / `sqlalchemy` |
| `VARCO_CASBIN_DB_URL` | — | SQLAlchemy URL for `sqlalchemy` adapter |
| `VARCO_CASBIN_POLICY_PATH` | — | CSV path for `file` adapter |
| `VARCO_CASBIN_AUTO_SAVE` | `true` | persist each mutation immediately |
| `VARCO_CASBIN_ADMIN_ROLE` | `admin` | role required by the REST router |

For runtime-editable, durable policy (the dynamic-CRUD use case) use
`adapter="sqlalchemy"` (install `varco-casbin[sqlalchemy]`). The `memory`
adapter is non-durable; `file` is single-process.

## REST management API

`build_policy_router` (requires `varco-casbin[fastapi]`) returns a FastAPI
`APIRouter` guarded by `require_roles(admin_role)`:

```python
from varco_casbin.router import build_policy_router
app.include_router(build_policy_router(engine, server_auth=jwt_auth))
```

| Method & path | Action |
|---|---|
| `GET /authz/policies?ptype=p` | list policy rules |
| `POST /authz/policies` | add rule `{"values": ["admin","posts","read"], "ptype":"p"}` |
| `DELETE /authz/policies` | remove rule (same body) |
| `GET /authz/roles?user=&domain=` | list a user's roles |
| `POST /authz/roles` | assign role `{"user","role","domain?"}` |
| `DELETE /authz/roles` | revoke role (same body) |
| `POST /authz/check` | what-if `enforce` decision |
| `POST /authz/reload` | reload from the durable store |

!!! note "Why a plain APIRouter, not a VarcoRouter"
    Policy mutations carry JSON bodies. The `varco_fastapi` `@route` custom
    handler injects only `ctx` and path params — it cannot bind a request body —
    so a standard FastAPI route is the correct tool. Authentication
    (`AbstractServerAuth`) and authorization (`RouteGuard`) are still varco's
    own primitives, so it stays framework-consistent.

## ABAC end-to-end

With the `abac` preset, attributes flow from the loaded entity all the way to
the matcher:

```python
from varco_casbin import CasbinPolicyEngine, CasbinSettings
from varco_core.auth import Action, AuthContext, Resource, PolicyEngineAuthorizer

async with CasbinPolicyEngine(CasbinSettings(model_preset="abac")) as engine:
    authz = PolicyEngineAuthorizer(engine)
    owner = AuthContext(user_id="u1")
    post  = Post(pk="1", owner_id="u1")           # a loaded DomainModel
    await authz.authorize(owner, Action.UPDATE, Resource(Post, post))   # ✅ owner
    # AuthContext(user_id="u2") on the same post → ServiceAuthorizationError (403)
```

`RequestMapper` extracts `object_attrs={"owner_id": "u1", ...}` from the entity
(via `attributes_of`, which strips leading-underscore framework fields) and
`subject_attrs={"id": "u1", "roles": [...], ...}` from the context. The engine
wraps them in `_AttrStr` so the same value is both a string (RBAC/ACL) and an
attribute holder (ABAC).

## Testing notes

- Unit tests use the in-memory adapter — fast, isolated, no Docker.
- The SQLAlchemy persistence round-trip runs against `sqlite+aiosqlite` (no
  Docker) and, marked `@pytest.mark.integration`, against real Postgres via
  testcontainers (`uv run pytest varco_casbin/tests/ -m integration`).
