"""
tests/test_smoke.py
===================
Smoke tests for the ``02-api-gateway-guards`` example.

Exercises every endpoint with the correct token (or no token) and verifies
the expected HTTP status.  Integration is via ``httpx.AsyncClient`` with
``ASGITransport`` — no real TCP socket, no Docker needed.

All tests are ``async def`` and run under ``asyncio_mode = "auto"``
(configured in ``examples/pyproject.toml``), so no ``@pytest.mark.asyncio``
decorator is needed.

Test organisation
-----------------
``TestPublicEndpoints``   — no token required (allow_anonymous)
``TestAuthenticatedMe``   — any valid JWT; no scope/role constraint
``TestScopeGuard``        — require_scopes("reports:read")
``TestRoleGuard``         — require_roles("admin")
``TestPredicateGuard``    — require_predicate (svc: subject prefix)

Each class has:
- One or more happy-path tests (correct token → 200/204)
- One or more unhappy-path tests (wrong/missing token → 401 or 403)

Thread safety:  ✅ Each test creates a fresh ``httpx.AsyncClient``.
Async safety:   ✅ All test methods are ``async def``.
"""

from __future__ import annotations

import os
import sys

import httpx
import pytest
from httpx import ASGITransport

# Add the example root to sys.path so ``from app import ...`` works
# regardless of the working directory pytest is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402 (path manipulation must come first)
from auth import mint_token  # noqa: E402

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def client():
    """
    Yield an ``httpx.AsyncClient`` backed by a fresh ASGI app instance.

    Uses ``ASGITransport`` — note that httpx does NOT trigger the ASGI lifespan
    when using this transport.  We call ``registry.load_all()`` explicitly
    here so JwtBearerAuth can verify tokens even without the lifespan running.

    Each test function gets its own fresh app instance.

    Yields:
        An active ``httpx.AsyncClient`` connected to the example app.

    Edge cases:
        - ``registry.load_all()`` is called after ``create_app()`` returns
          because the registry is already populated (``register_authority`` is
          sync); ``load_all()`` only needs to set ``entry._keyset`` — cheap,
          no network I/O for an ``AuthoritySource``.
        - ``registry`` is a module-level singleton in ``auth.py``; calling
          ``load_all()`` here is idempotent across tests.
    """
    from auth import registry  # noqa: E402

    app = create_app()
    # Manually trigger the key-loading step that normally runs in the lifespan.
    # Without this, JwtBearerAuth.verify() raises UnknownKidError on every call
    # because entry._keyset is None.
    await registry.load_all()
    # raise_app_exceptions=False: convert ASGI exceptions (including HTTPException
    # raised inside BaseHTTPMiddleware) to HTTP responses instead of re-raising.
    # Required because Starlette's BaseHTTPMiddleware re-raises HTTPException
    # through the stream machinery in a way that bypasses FastAPI's own exception
    # handlers when using ASGITransport.
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as c:
        yield c


# ── Token factories ────────────────────────────────────────────────────────────


def _user_token(
    subject: str = "user:alice",
    roles: frozenset[str] = frozenset(),
    scopes: frozenset[str] = frozenset(),
) -> str:
    """
    Mint a JWT for a regular user (non-service account).

    Args:
        subject: Subject claim.  Defaults to ``"user:alice"``.
        roles:   Roles to embed in the JWT.
        scopes:  Scopes to embed in the JWT.

    Returns:
        Signed JWT string.
    """
    return mint_token(subject, roles=roles, scopes=scopes)


def _svc_token(service: str = "my-service") -> str:
    """
    Mint a JWT for a service account (subject starts with ``"svc:"``).

    Args:
        service: Service name suffix.  Subject becomes ``"svc:<service>"``.

    Returns:
        Signed JWT string.
    """
    return mint_token(f"svc:{service}")


def _bearer(token: str) -> dict[str, str]:
    """Return an ``Authorization`` header dict for ``httpx`` requests."""
    return {"Authorization": f"Bearer {token}"}


# ── TestPublicEndpoints ────────────────────────────────────────────────────────


class TestPublicEndpoints:
    """
    Tests for endpoints decorated with ``allow_anonymous()``.

    No token is required.  Any caller — anonymous or authenticated — succeeds.
    """

    async def test_health_no_token(self, client: httpx.AsyncClient) -> None:
        """GET /health → 200 without any token."""
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_health_with_token(self, client: httpx.AsyncClient) -> None:
        """GET /health → 200 even when a token is provided (allow_anonymous passes through)."""
        token = _user_token()
        resp = await client.get("/health", headers=_bearer(token))
        assert resp.status_code == 200

    async def test_echo_no_token(self, client: httpx.AsyncClient) -> None:
        """GET /v1/echo → 200 without any token."""
        resp = await client.get("/v1/echo")
        assert resp.status_code == 200
        body = resp.json()
        assert "echo" in body

    async def test_echo_with_token(self, client: httpx.AsyncClient) -> None:
        """GET /v1/echo → 200 with a valid token (anonymous guard doesn't block)."""
        token = _user_token()
        resp = await client.get("/v1/echo", headers=_bearer(token))
        assert resp.status_code == 200


# ── TestAuthenticatedMe ────────────────────────────────────────────────────────


class TestAuthenticatedMe:
    """
    Tests for ``GET /v1/me`` — requires a valid JWT but no specific scope or role.
    """

    async def test_me_with_valid_token(self, client: httpx.AsyncClient) -> None:
        """GET /v1/me with a valid JWT → 200 with subject in body."""
        token = _user_token("user:alice")
        resp = await client.get("/v1/me", headers=_bearer(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["subject"] == "user:alice"

    async def test_me_roles_and_scopes_reflected(self, client: httpx.AsyncClient) -> None:
        """GET /v1/me → roles and scopes from the JWT are returned in the body."""
        token = _user_token(
            "user:bob",
            roles=frozenset({"editor"}),
            scopes=frozenset({"posts:read"}),
        )
        resp = await client.get("/v1/me", headers=_bearer(token))
        assert resp.status_code == 200
        body = resp.json()
        assert "editor" in body["roles"]
        assert "posts:read" in body["scopes"]

    async def test_me_no_token_denied(self, client: httpx.AsyncClient) -> None:
        """GET /v1/me without a token → 401 or 403 (anonymous access not allowed)."""
        resp = await client.get("/v1/me")
        # RouteGuard denies anonymous when allow_anonymous=False.
        # ServiceAuthorizationError → 403 via add_exception_handlers.
        # JwtBearerAuth with required=False returns AnonymousAuth → guard denies → 403.
        assert resp.status_code in (401, 403)

    async def test_me_invalid_token_denied(self, client: httpx.AsyncClient) -> None:
        """GET /v1/me with a garbage token → 401 from JwtBearerAuth."""
        resp = await client.get("/v1/me", headers={"Authorization": "Bearer not-a-real-token"})
        assert resp.status_code == 401


# ── TestScopeGuard ────────────────────────────────────────────────────────────


class TestScopeGuard:
    """
    Tests for ``GET /v1/reports/summary`` — requires ``reports:read`` scope.
    """

    async def test_reports_with_scope(self, client: httpx.AsyncClient) -> None:
        """GET /v1/reports/summary with ``reports:read`` scope → 200."""
        token = _user_token(scopes=frozenset({"reports:read"}))
        resp = await client.get("/v1/reports/summary", headers=_bearer(token))
        assert resp.status_code == 200
        body = resp.json()
        assert "total_requests" in body
        assert "error_rate_pct" in body

    async def test_reports_without_scope_denied(self, client: httpx.AsyncClient) -> None:
        """GET /v1/reports/summary with a JWT but no ``reports:read`` scope → 403."""
        token = _user_token(scopes=frozenset({"posts:read"}))
        resp = await client.get("/v1/reports/summary", headers=_bearer(token))
        assert resp.status_code == 403

    async def test_reports_no_token_denied(self, client: httpx.AsyncClient) -> None:
        """GET /v1/reports/summary without any token → 401 or 403."""
        resp = await client.get("/v1/reports/summary")
        assert resp.status_code in (401, 403)

    async def test_reports_wrong_scope_denied(self, client: httpx.AsyncClient) -> None:
        """GET /v1/reports/summary with an unrelated scope → 403."""
        token = _user_token(scopes=frozenset({"admin:all"}))
        resp = await client.get("/v1/reports/summary", headers=_bearer(token))
        assert resp.status_code == 403


# ── TestRoleGuard ─────────────────────────────────────────────────────────────


class TestRoleGuard:
    """
    Tests for ``POST /v1/admin/flush-cache`` — requires ``admin`` role.
    """

    async def test_flush_cache_with_admin_role(self, client: httpx.AsyncClient) -> None:
        """POST /v1/admin/flush-cache with ``admin`` role → 204."""
        token = _user_token(roles=frozenset({"admin"}))
        resp = await client.post("/v1/admin/flush-cache", headers=_bearer(token))
        assert resp.status_code == 204

    async def test_flush_cache_without_role_denied(self, client: httpx.AsyncClient) -> None:
        """POST /v1/admin/flush-cache with a JWT but no ``admin`` role → 403."""
        token = _user_token(roles=frozenset({"editor"}))
        resp = await client.post("/v1/admin/flush-cache", headers=_bearer(token))
        assert resp.status_code == 403

    async def test_flush_cache_no_token_denied(self, client: httpx.AsyncClient) -> None:
        """POST /v1/admin/flush-cache without a token → 401 or 403."""
        resp = await client.post("/v1/admin/flush-cache")
        assert resp.status_code in (401, 403)

    async def test_flush_cache_scope_not_role_denied(self, client: httpx.AsyncClient) -> None:
        """POST /v1/admin/flush-cache with a scope (not a role) → 403."""
        token = _user_token(scopes=frozenset({"admin:all"}))
        resp = await client.post("/v1/admin/flush-cache", headers=_bearer(token))
        assert resp.status_code == 403


# ── TestPredicateGuard ────────────────────────────────────────────────────────


class TestPredicateGuard:
    """
    Tests for ``GET /v1/internal/status`` — requires subject starting with ``"svc:"``.
    """

    async def test_internal_status_service_account(self, client: httpx.AsyncClient) -> None:
        """GET /v1/internal/status with ``svc:`` subject → 200."""
        token = _svc_token("my-service")
        resp = await client.get("/v1/internal/status", headers=_bearer(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["caller"].startswith("svc:")

    async def test_internal_status_user_account_denied(self, client: httpx.AsyncClient) -> None:
        """GET /v1/internal/status with a ``user:`` subject → 403."""
        token = _user_token("user:alice")
        resp = await client.get("/v1/internal/status", headers=_bearer(token))
        assert resp.status_code == 403

    async def test_internal_status_no_token_denied(self, client: httpx.AsyncClient) -> None:
        """GET /v1/internal/status without a token → 401 or 403."""
        resp = await client.get("/v1/internal/status")
        assert resp.status_code in (401, 403)

    async def test_internal_status_wrong_prefix_denied(self, client: httpx.AsyncClient) -> None:
        """GET /v1/internal/status with ``service:x`` (no svc: prefix) → 403."""
        token = _user_token("service:my-service")
        resp = await client.get("/v1/internal/status", headers=_bearer(token))
        assert resp.status_code == 403
