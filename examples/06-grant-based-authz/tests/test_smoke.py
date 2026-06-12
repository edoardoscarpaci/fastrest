"""
tests/test_smoke.py
===================
Smoke tests for the ``06-grant-based-authz`` example.

Exercises authorization at the service layer:
    - JWT-embedded ``ResourceGrant``s (create requires ``docs:write``)
    - Ownership checks (delete requires being the owner)
    - Admin role bypass (admin can delete any document)
    - Anonymous / missing token → 401

All tests are ``async def`` and run under ``asyncio_mode = "auto"``
(configured in ``examples/pyproject.toml``), so no ``@pytest.mark.asyncio``
is needed.

Test organisation
-----------------
``TestCreate``   — POST /v1/documents; grant-based access control
``TestRead``     — GET  /v1/documents/{id}; authenticated-only access
``TestDelete``   — DELETE /v1/documents/{id}; ownership + admin bypass
``TestNoToken``  — requests without Authorization header → 401 or 403

Thread safety:  ✅ Each test function receives its own ``client`` fixture.
Async safety:   ✅ All test methods are ``async def``.
"""

from __future__ import annotations

import os
import sys

import httpx
import pytest
from httpx import ASGITransport

# Add the example root to sys.path so relative imports resolve correctly
# regardless of the working directory pytest is invoked from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402 (path manipulation must come first)
from auth import (
    ADMIN_GRANT,
    DOCS_READ_GRANT,
    DOCS_WRITE_GRANT,
    mint_token,
)  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def client():
    """
    Yield an ``httpx.AsyncClient`` backed by a fresh ASGI app instance.

    Calls ``registry.load_all()`` explicitly because ``ASGITransport`` does
    NOT trigger the ASGI lifespan — without this call, JwtBearerAuth would
    raise ``UnknownKidError`` on every token verification attempt.

    Each test gets its own fresh app with an isolated in-memory store.

    Yields:
        An active ``httpx.AsyncClient`` connected to the example app.

    Edge cases:
        - ``registry`` is a module-level singleton; ``load_all()`` is idempotent.
        - ``raise_app_exceptions=False``: convert ASGI exceptions to HTTP
          responses — required because Starlette's ``BaseHTTPMiddleware``
          re-raises ``HTTPException`` through the stream machinery in a way
          that bypasses FastAPI's exception handlers in test mode.
    """
    from auth import registry  # noqa: E402

    app = create_app()
    # Manually trigger the key-loading step normally handled by the lifespan.
    await registry.load_all()

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as c:
        yield c


# ── Token helpers ─────────────────────────────────────────────────────────────


def _token_with_write(subject: str = "user:alice") -> str:
    """Mint a token with docs:read + docs:write grants."""
    return mint_token(
        subject,
        grants=(DOCS_READ_GRANT, DOCS_WRITE_GRANT),
    )


def _token_read_only(subject: str = "user:bob") -> str:
    """Mint a token with docs:read grant only — cannot create."""
    return mint_token(
        subject,
        grants=(DOCS_READ_GRANT,),
    )


def _token_admin(subject: str = "user:admin") -> str:
    """Mint a token with admin role and wildcard grant — can do everything."""
    return mint_token(
        subject,
        roles=frozenset({"admin"}),
        grants=(ADMIN_GRANT,),
    )


def _auth_header(token: str) -> dict[str, str]:
    """Return the ``Authorization: Bearer <token>`` header dict."""
    return {"Authorization": f"Bearer {token}"}


# ── Helper: create a document as alice ───────────────────────────────────────


async def _create_document(
    client: httpx.AsyncClient,
    subject: str = "user:alice",
    title: str = "Test Doc",
    content: str = "Hello, world",
) -> dict:
    """
    Create a document via POST and return the response body.

    Uses a write-capable token for ``subject``.

    Args:
        client:  The test ``httpx.AsyncClient``.
        subject: JWT subject for the creator.
        title:   Document title.
        content: Document body.

    Returns:
        Parsed JSON response body (``DocumentRead`` fields).

    Raises:
        AssertionError: Response status is not 201.
    """
    resp = await client.post(
        "/v1/documents",
        json={"title": title, "content": content},
        headers=_auth_header(_token_with_write(subject)),
    )
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    return resp.json()


# ── TestCreate ────────────────────────────────────────────────────────────────


class TestCreate:
    """POST /v1/documents — grant-based create authorization."""

    async def test_create_with_write_grant_returns_201(
        self, client: httpx.AsyncClient
    ) -> None:
        """A token with docs:write grant → 201 with document fields."""
        resp = await client.post(
            "/v1/documents",
            json={"title": "My First Doc", "content": "Hello!"},
            headers=_auth_header(_token_with_write()),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["title"] == "My First Doc"
        assert body["content"] == "Hello!"
        # owner_id must be stamped from the JWT subject
        assert body["owner_id"] == "user:alice"
        assert "pk" in body
        assert "created_at" in body

    async def test_create_sets_owner_id_from_jwt(
        self, client: httpx.AsyncClient
    ) -> None:
        """owner_id is always taken from ctx.user_id, never from the request body."""
        resp = await client.post(
            "/v1/documents",
            json={"title": "Ownership Test"},
            headers=_auth_header(_token_with_write("user:carol")),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # owner_id must equal the JWT sub, regardless of any body field
        assert body["owner_id"] == "user:carol"

    async def test_create_without_write_grant_returns_403(
        self, client: httpx.AsyncClient
    ) -> None:
        """A token with only docs:read grant cannot create — 403."""
        resp = await client.post(
            "/v1/documents",
            json={"title": "Forbidden Doc"},
            headers=_auth_header(_token_read_only()),
        )
        # ServiceAuthorizationError → 403 via add_exception_handlers
        assert resp.status_code == 403, resp.text

    async def test_create_with_admin_grant_returns_201(
        self, client: httpx.AsyncClient
    ) -> None:
        """Admin wildcard grant (``"*"``) satisfies any resource check → 201."""
        resp = await client.post(
            "/v1/documents",
            json={"title": "Admin Doc"},
            headers=_auth_header(_token_admin()),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["owner_id"] == "user:admin"


# ── TestRead ──────────────────────────────────────────────────────────────────


class TestRead:
    """GET /v1/documents/{id} — any authenticated caller."""

    async def test_owner_can_read_document(self, client: httpx.AsyncClient) -> None:
        """Owner can read their own document."""
        body = await _create_document(client, "user:alice")
        pk = body["pk"]

        resp = await client.get(
            f"/v1/documents/{pk}",
            headers=_auth_header(_token_with_write("user:alice")),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["pk"] == pk
        assert resp.json()["title"] == "Test Doc"

    async def test_non_owner_cannot_read_document_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        """Non-owner gets a 404 (existence oracle prevention), not 403."""
        body = await _create_document(client, "user:alice")
        pk = body["pk"]

        # bob has a read grant but is not the owner — existence oracle prevention
        # means the service raises ServiceNotFoundError → 404, not 403.
        resp = await client.get(
            f"/v1/documents/{pk}",
            headers=_auth_header(_token_read_only("user:bob")),
        )
        assert resp.status_code == 404, resp.text

    async def test_admin_can_read_any_document(self, client: httpx.AsyncClient) -> None:
        """Admin role bypasses the ownership check — can read any document."""
        body = await _create_document(client, "user:alice")
        pk = body["pk"]

        resp = await client.get(
            f"/v1/documents/{pk}",
            headers=_auth_header(_token_admin()),
        )
        assert resp.status_code == 200, resp.text

    async def test_read_missing_document_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        """GET on a non-existent pk → 404."""
        import uuid

        non_existent = str(uuid.uuid4())
        resp = await client.get(
            f"/v1/documents/{non_existent}",
            headers=_auth_header(_token_with_write()),
        )
        assert resp.status_code == 404, resp.text


# ── TestDelete ────────────────────────────────────────────────────────────────


class TestDelete:
    """DELETE /v1/documents/{id} — ownership + admin role bypass."""

    async def test_owner_can_delete_own_document(
        self, client: httpx.AsyncClient
    ) -> None:
        """Owner can delete their document → 204."""
        body = await _create_document(client, "user:alice")
        pk = body["pk"]

        resp = await client.delete(
            f"/v1/documents/{pk}",
            headers=_auth_header(_token_with_write("user:alice")),
        )
        assert resp.status_code == 204, resp.text

        # Verify it's gone — subsequent read by the same user → 404
        read_resp = await client.get(
            f"/v1/documents/{pk}",
            headers=_auth_header(_token_with_write("user:alice")),
        )
        assert read_resp.status_code == 404, read_resp.text

    async def test_admin_can_delete_any_document(
        self, client: httpx.AsyncClient
    ) -> None:
        """Admin role bypasses ownership check → 204."""
        body = await _create_document(client, "user:alice")
        pk = body["pk"]

        resp = await client.delete(
            f"/v1/documents/{pk}",
            headers=_auth_header(_token_admin()),
        )
        assert resp.status_code == 204, resp.text

    async def test_non_owner_cannot_delete_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        """Non-owner delete attempt → 404 (not 403 — existence oracle prevention)."""
        body = await _create_document(client, "user:alice")
        pk = body["pk"]

        # bob is not the owner — existence oracle prevention yields 404
        resp = await client.delete(
            f"/v1/documents/{pk}",
            headers=_auth_header(_token_with_write("user:bob")),
        )
        assert resp.status_code == 404, resp.text

    async def test_delete_missing_document_returns_404(
        self, client: httpx.AsyncClient
    ) -> None:
        """DELETE on a non-existent pk → 404."""
        import uuid

        non_existent = str(uuid.uuid4())
        resp = await client.delete(
            f"/v1/documents/{non_existent}",
            headers=_auth_header(_token_with_write()),
        )
        assert resp.status_code == 404, resp.text


# ── TestNoToken ───────────────────────────────────────────────────────────────


class TestNoToken:
    """All endpoints require authentication — missing token → 401 or 403."""

    async def test_create_without_token_returns_401_or_403(
        self, client: httpx.AsyncClient
    ) -> None:
        """POST without Authorization header is denied."""
        resp = await client.post(
            "/v1/documents",
            json={"title": "No Auth"},
        )
        # JwtBearerAuth(required=True) rejects anonymous callers at middleware.
        # The exact status depends on how RequestContextMiddleware handles missing
        # credentials: 401 (Unauthorized) or 403 (Forbidden).
        assert resp.status_code in (401, 403), resp.text

    async def test_read_without_token_returns_401_or_403(
        self, client: httpx.AsyncClient
    ) -> None:
        """GET without Authorization header is denied."""
        import uuid

        resp = await client.get(f"/v1/documents/{uuid.uuid4()}")
        assert resp.status_code in (401, 403), resp.text

    async def test_delete_without_token_returns_401_or_403(
        self, client: httpx.AsyncClient
    ) -> None:
        """DELETE without Authorization header is denied."""
        import uuid

        resp = await client.delete(f"/v1/documents/{uuid.uuid4()}")
        assert resp.status_code in (401, 403), resp.text
