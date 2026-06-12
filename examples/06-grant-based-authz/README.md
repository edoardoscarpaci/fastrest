# 06-grant-based-authz

Demonstrates **service-layer authorization** using JWT-embedded `ResourceGrant`s
and ownership checks.  No database, no broker, no Docker required.

## What this example teaches

| Concept | Where it lives |
|---|---|
| `ResourceGrant` — type-level and instance-level JWT claims | `auth.py` |
| `GrantBasedAuthorizer` — checks `ctx.can(action, key)` | `service.py` |
| Ownership check via `_check_entity` | `service.py` |
| `_prepare_for_create` — stamp `owner_id` from the JWT | `service.py` |
| Admin role bypass for ownership | `service.py` |
| Existence oracle prevention (404 not 403) | `service.py` |

## Authorization rules

| Endpoint | Requirement |
|---|---|
| `POST /v1/documents` | `docs:write` grant in JWT |
| `GET /v1/documents/{id}` | any authenticated token + owner or admin |
| `DELETE /v1/documents/{id}` | `docs:write` grant + (owner OR admin role) |

## Key design decisions

### Two-layer authorization
Authorization runs at **two layers** in the service:

1. **`authorize()` (GrantBasedAuthorizer)** — checks JWT grants for the
   action type.  Called by `AsyncService` before fetching any entity for
   CREATE/LIST; after fetch for GET/UPDATE/DELETE.
2. **`_check_entity()` hook** — checks `entity.owner_id == ctx.user_id`
   for GET and DELETE.  Raises `ServiceNotFoundError` (not
   `ServiceAuthorizationError`) to prevent existence oracles.

### Existence oracle prevention
A non-owner trying to `DELETE` a document gets `404`, not `403`.  This
prevents attackers from probing whether a document exists by alternating
their identity.  This is the correct varco pattern: `_check_entity` must
raise `ServiceNotFoundError` for cross-cutting entity-level blocks.

### Admin bypass
The `"admin"` role bypasses the ownership check inside `_check_entity`.
Admins still need a grant (`ADMIN_GRANT` with `resource="*"` covers all
actions) to pass `GrantBasedAuthorizer.authorize()`.

## Running locally

```bash
cd examples/06-grant-based-authz
uv run uvicorn app:app --reload
```

Then open <http://localhost:8000/docs>.

## Running tests

```bash
# From workspace root:
uv run pytest .claude/worktrees/feature+examples-catalog/examples/06-grant-based-authz/tests/ -v
```

## Token structure

```python
from auth import mint_token, DOCS_READ_GRANT, DOCS_WRITE_GRANT, ADMIN_GRANT

# Read-only user (can GET but not POST)
token = mint_token("user:alice", grants=(DOCS_READ_GRANT,))

# Read-write user (can POST and DELETE their own documents)
token = mint_token("user:alice", grants=(DOCS_READ_GRANT, DOCS_WRITE_GRANT))

# Admin (can do everything, bypass ownership)
token = mint_token("user:admin", roles=frozenset({"admin"}), grants=(ADMIN_GRANT,))
```
