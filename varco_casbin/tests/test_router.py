"""
Unit tests for varco_casbin.router.build_policy_router
======================================================
Covers the REST management surface end-to-end over ASGI:
  - happy path: an admin can CRUD policies + role assignments and run /check
  - unhappy path: a non-admin / anonymous caller is denied (403)
  - persistence visibility: a written rule shows up on subsequent reads
"""

from __future__ import annotations

from varco_core.auth import AuthContext

from tests.conftest import client_for, make_app

ADMIN = AuthContext(user_id="root", roles=frozenset({"admin"}))
NON_ADMIN = AuthContext(user_id="joe", roles=frozenset({"viewer"}))
ANON = AuthContext()


# ── Happy path ────────────────────────────────────────────────────────────────


async def test_admin_can_add_and_list_policies(engine) -> None:
    """An admin adds a policy rule and reads it back."""
    app = make_app(engine, ADMIN)
    async with client_for(app) as cl:
        r = await cl.post("/authz/policies", json={"values": ["admin", "posts", "read"]})
        assert r.status_code == 201
        assert r.json() == {"added": True}

        r = await cl.get("/authz/policies")
        assert r.status_code == 200
        assert ["admin", "posts", "read"] in r.json()


async def test_admin_role_assignment_flow(engine) -> None:
    """An admin grants and revokes a role, observing roles_for_user."""
    app = make_app(engine, ADMIN)
    async with client_for(app) as cl:
        r = await cl.post("/authz/roles", json={"user": "alice", "role": "admin"})
        assert r.status_code == 201 and r.json() == {"added": True}

        r = await cl.get("/authz/roles", params={"user": "alice"})
        assert r.json() == ["admin"]

        r = await cl.request("DELETE", "/authz/roles", json={"user": "alice", "role": "admin"})
        assert r.status_code == 200 and r.json() == {"removed": True}


async def test_check_endpoint_reflects_policy(engine) -> None:
    """POST /check runs a real enforcement decision."""
    app = make_app(engine, ADMIN)
    async with client_for(app) as cl:
        await cl.post("/authz/roles", json={"user": "alice", "role": "admin"})
        await cl.post("/authz/policies", json={"values": ["admin", "*", "*"]})

        r = await cl.post(
            "/authz/check",
            json={"subject": "alice", "object": "posts", "action": "read"},
        )
        assert r.status_code == 200 and r.json() == {"allowed": True}

        r = await cl.post(
            "/authz/check",
            json={"subject": "bob", "object": "posts", "action": "read"},
        )
        assert r.json() == {"allowed": False}


async def test_reload_endpoint(engine) -> None:
    """POST /reload returns a status payload (no-op for in-memory)."""
    app = make_app(engine, ADMIN)
    async with client_for(app) as cl:
        r = await cl.post("/authz/reload")
        assert r.status_code == 200 and r.json() == {"status": "reloaded"}


async def test_remove_policy(engine) -> None:
    """An admin removes a previously-added rule."""
    app = make_app(engine, ADMIN)
    async with client_for(app) as cl:
        await cl.post("/authz/policies", json={"values": ["admin", "posts", "read"]})
        r = await cl.request(
            "DELETE", "/authz/policies", json={"values": ["admin", "posts", "read"]}
        )
        assert r.status_code == 200 and r.json() == {"removed": True}
        r = await cl.get("/authz/policies")
        assert r.json() == []


# ── Unhappy path ──────────────────────────────────────────────────────────────


async def test_non_admin_denied(engine) -> None:
    """A caller without the admin role is forbidden on every route."""
    app = make_app(engine, NON_ADMIN)
    async with client_for(app) as cl:
        assert (await cl.get("/authz/policies")).status_code == 403
        assert (
            await cl.post("/authz/policies", json={"values": ["x", "y", "z"]})
        ).status_code == 403


async def test_anonymous_denied(engine) -> None:
    """An anonymous caller is forbidden (the guard denies anonymous)."""
    app = make_app(engine, ANON)
    async with client_for(app) as cl:
        assert (await cl.get("/authz/policies")).status_code == 403


async def test_custom_admin_role(engine) -> None:
    """The required role is configurable via admin_role."""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from varco_casbin.router import build_policy_router
    from varco_core.exception.service import ServiceAuthorizationError

    from tests.conftest import StaticAuth
    from tests.conftest import client_for as _client

    superuser = AuthContext(user_id="root", roles=frozenset({"superuser"}))
    app = FastAPI()
    app.include_router(
        build_policy_router(engine, server_auth=StaticAuth(superuser), admin_role="superuser")
    )

    @app.exception_handler(ServiceAuthorizationError)
    async def _f(r: Request, e: ServiceAuthorizationError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": "forbidden"})

    async with _client(app) as cl:
        # 'superuser' satisfies admin_role='superuser'.
        assert (await cl.get("/authz/policies")).status_code == 200
