# 05 — JWT Authority & Zero-Downtime Key Rotation

Demonstrates `JwtAuthority`, `MultiKeyAuthority` (zero-downtime key rotation),
`TrustedIssuerRegistry`, and JWT authentication in FastAPI routes.

No database, no broker, no Docker needed.  All key material is generated
in-process at startup (ephemeral RSA-2048 keys).

---

## What this example shows

| Concept | Where |
|---------|-------|
| Generate an RSA key pair and build a `JwtAuthority` | `authority.py` → `_build_authority()` |
| Sign a JWT with `authority.sign(builder)` | `authority.py` → `mint_token()` |
| Wrap in `MultiKeyAuthority` for rotation support | `authority.py` → `multi_authority` |
| Register with `TrustedIssuerRegistry` via `register_authority()` | `authority.py` → `registry` |
| Verify Bearer tokens in FastAPI via `JwtBearerAuth` | `app.py` → `server_auth_strict` |
| Zero-downtime rotation: `rotate()` + `retire()` | `tests/test_smoke.py::TestKeyRotation` |

---

## Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/auth/token` | None | Issue a JWT for any subject (demo — no password) |
| `GET` | `/me` | Bearer JWT | Return `{"subject": ..., "kid": ...}` from the verified token |
| `GET` | `/jwks` | None | Serve the current JWKS (all active + in-flight keys) |

---

## Key rotation lifecycle

```
Phase 0: only key A active
    multi_authority = MultiKeyAuthority(authority_a)
    active_kid = "key-A"

Phase 1: rotate to key B — zero downtime
    auth_b = JwtAuthority.from_pem(new_pem, kid="key-B", ...)
    multi_authority.rotate(auth_b)
    active_kid = "key-B"
    ← tokens signed by key-A still verify (A is still registered)
    ← new tokens get kid=key-B

Phase 2: all key-A tokens expired — retire key A
    multi_authority.retire("key-A")
    ← tokens with kid=key-A now raise UnknownKidError → 401
    ← tokens with kid=key-B continue to verify
```

The `TrustedIssuerRegistry` stays in sync: after each rotation phase, call
`await registry.load_all()` to refresh the cached JWKS from the
`MultiKeyAuthority`.

---

## Run locally

```bash
cd examples/05-jwt-authority-rotation
uv run uvicorn app:app --reload
```

Issue a token:

```bash
curl -X POST "http://localhost:8000/auth/token?subject=user:alice"
# {"token": "eyJ...", "active_kid": "rotation-example:key-A"}
```

Call the protected endpoint:

```bash
TOKEN=$(curl -s -X POST "http://localhost:8000/auth/token?subject=user:alice" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/me
# {"subject": "user:alice", "kid": "rotation-example:key-A"}
```

---

## Run the tests

```bash
cd /path/to/varco  # workspace root
uv run pytest .claude/worktrees/feature+examples-catalog/examples/05-jwt-authority-rotation/tests/ -v
```

All 15 tests run in-process — no Docker, no network, no external services.
