"""
tests/test_smoke.py
===================
Smoke tests for the ``05-jwt-authority-rotation`` example.

Exercises every aspect of the JWT authority / rotation flow:

Test organisation
-----------------
``TestTokenIssuance``    — POST /auth/token returns a valid JWT
``TestMeEndpoint``       — GET /me requires a valid token; returns subject + kid
``TestExpiredToken``     — expired tokens are rejected with 401
``TestKeyRotation``      — zero-downtime rotation: old token still verifies after
                           rotate(); new tokens carry new kid; old kid verifiable
                           until retire()

Integration is via ``httpx.AsyncClient`` with ``ASGITransport`` — no real TCP
socket, no Docker needed.

All tests are ``async def`` and run under ``asyncio_mode = "auto"``
(configured in ``examples/pyproject.toml``), so no ``@pytest.mark.asyncio``
decorator is needed.

Thread safety:  ✅ Each test creates a fresh ``httpx.AsyncClient``.
Async safety:   ✅ All test methods are ``async def``.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from app import create_app  # noqa: E402
from authority import (  # noqa: E402
    _ISSUER,
    _KID_A,
    _KID_B,
    authority_a,
    build_authority,
    mint_token,
    multi_authority,
    registry,
)
from httpx import ASGITransport

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def _reset_multi_authority():
    """
    Reset ``multi_authority`` to key A after every test.

    Tests that call ``multi_authority.rotate()`` or ``multi_authority.retire()``
    mutate the module-level singleton.  This fixture restores the initial state
    so tests are independent of execution order.

    The reset strategy:
    1. If key B is still registered (from a rotate()), retire it (if it is not
       the active key) or rotate back to key A first.
    2. Ensure key A is the active key.

    Yields:
        Nothing — this is a teardown-only fixture.
    """
    yield
    # Teardown: restore to key A active, key B removed.
    # After test: multi_authority may have key A only, key B only, or both.
    current_kids = set(multi_authority.kids)

    if multi_authority.active_kid != _KID_A:
        # Active is not A — rotate back to A if A is still registered;
        # otherwise re-add A.
        if _KID_A not in current_kids:
            # Key A was retired — re-add it.
            multi_authority._authorities[_KID_A] = authority_a  # type: ignore[attr-defined]
        multi_authority.rotate(authority_a)

    # Now active_kid == _KID_A.  Remove key B if present.
    if _KID_B in multi_authority.kids:
        multi_authority.retire(_KID_B)


@pytest.fixture
async def client():
    """
    Yield an ``httpx.AsyncClient`` backed by a fresh ASGI app instance.

    Uses ``ASGITransport`` — httpx does NOT trigger the ASGI lifespan when
    using this transport.  We call ``registry.load_all()`` explicitly here so
    ``JwtBearerAuth`` can verify tokens.

    Yields:
        Active ``httpx.AsyncClient`` connected to the example app.

    Edge cases:
        - ``registry.load_all()`` is idempotent — safe to call multiple times.
        - ``registry`` is a module-level singleton; calling ``load_all()`` here
          once is sufficient for all tests in the session.
    """
    app = create_app()
    await registry.load_all()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as c:
        yield c


def _bearer(token: str) -> dict[str, str]:
    """Return an ``Authorization`` header dict for httpx requests."""
    return {"Authorization": f"Bearer {token}"}


# ── TestTokenIssuance ─────────────────────────────────────────────────────────


class TestTokenIssuance:
    """POST /auth/token returns a signed JWT with the active kid."""

    async def test_issue_token_returns_token_and_kid(
        self, client: httpx.AsyncClient
    ) -> None:
        """POST /auth/token → 200 with token and active_kid fields."""
        resp = await client.post("/auth/token", params={"subject": "user:alice"})
        assert resp.status_code == 200
        body = resp.json()
        assert "token" in body
        assert "active_kid" in body
        assert body["active_kid"] == _KID_A
        # Token must be a non-empty JWT string (three segments)
        assert len(body["token"].split(".")) == 3

    async def test_issue_token_default_subject(self, client: httpx.AsyncClient) -> None:
        """POST /auth/token with no subject → uses default subject."""
        resp = await client.post("/auth/token")
        assert resp.status_code == 200
        assert "token" in resp.json()

    async def test_issued_token_verifiable_by_registry(
        self, client: httpx.AsyncClient
    ) -> None:
        """Token from /auth/token must be verifiable by the registry."""
        resp = await client.post("/auth/token", params={"subject": "user:bob"})
        raw = resp.json()["token"]
        verified = await registry.verify(raw)
        assert verified.sub == "user:bob"
        assert verified.iss == _ISSUER


# ── TestMeEndpoint ────────────────────────────────────────────────────────────


class TestMeEndpoint:
    """GET /me requires a valid JWT and returns subject + kid."""

    async def test_me_returns_subject_and_kid(self, client: httpx.AsyncClient) -> None:
        """GET /me with a valid token → 200; body contains subject and kid."""
        token = mint_token("user:alice")
        resp = await client.get("/me", headers=_bearer(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["subject"] == "user:alice"
        assert body["kid"] == _KID_A

    async def test_me_without_token_returns_401(
        self, client: httpx.AsyncClient
    ) -> None:
        """GET /me without a token → 401 (JwtBearerAuth required=True)."""
        resp = await client.get("/me")
        assert resp.status_code == 401

    async def test_me_with_invalid_token_returns_401(
        self, client: httpx.AsyncClient
    ) -> None:
        """GET /me with a garbage token → 401."""
        resp = await client.get(
            "/me", headers={"Authorization": "Bearer not.a.real.token"}
        )
        assert resp.status_code == 401

    async def test_me_with_wrong_bearer_format_returns_401(
        self, client: httpx.AsyncClient
    ) -> None:
        """GET /me with a malformed Authorization header → 401."""
        resp = await client.get("/me", headers={"Authorization": "Basic dXNlcjpwYXNz"})
        assert resp.status_code == 401


# ── TestExpiredToken ──────────────────────────────────────────────────────────


class TestExpiredToken:
    """Expired tokens must be rejected with 401."""

    async def test_expired_token_rejected(self, client: httpx.AsyncClient) -> None:
        """GET /me with an already-expired token → 401."""
        # mint a token that expired 1 second in the past
        expired_token = mint_token("user:expired", expires_in=timedelta(seconds=-1))
        resp = await client.get("/me", headers=_bearer(expired_token))
        assert resp.status_code == 401


# ── TestKeyRotation ───────────────────────────────────────────────────────────


class TestKeyRotation:
    """
    Zero-downtime key rotation: old tokens remain verifiable after rotate();
    only after retire() are old tokens rejected.
    """

    async def test_token_from_key_a_verifiable_after_rotate_to_b(
        self, client: httpx.AsyncClient
    ) -> None:
        """
        Token signed by key A must still verify after rotating to key B.

        Phase 1 model:
            _authorities = {A: auth_A, B: auth_B}  ← both present
            _active_kid  = "B"
            ← old tokens (kid=A) still verify; new tokens get kid=B
        """
        # Sign a token with key A (currently active)
        token_a = mint_token("user:alice")
        assert multi_authority.active_kid == _KID_A

        # Rotate to key B
        auth_b = build_authority(_KID_B)
        multi_authority.rotate(auth_b)
        assert multi_authority.active_kid == _KID_B

        # Reload the registry so it picks up the new key from the MultiKeyAuthority
        await registry.load_all()

        # token_a (signed by key A) must still verify — A is still registered
        resp = await client.get("/me", headers=_bearer(token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert body["subject"] == "user:alice"
        assert body["kid"] == _KID_A  # signed by A, still verified via A

    async def test_new_token_after_rotation_carries_new_kid(
        self, client: httpx.AsyncClient
    ) -> None:
        """
        Tokens minted after rotation to key B carry kid=B.
        """
        auth_b = build_authority(_KID_B)
        multi_authority.rotate(auth_b)
        await registry.load_all()

        token_b = mint_token("user:bob")
        resp = await client.get("/me", headers=_bearer(token_b))
        assert resp.status_code == 200
        body = resp.json()
        assert body["subject"] == "user:bob"
        assert body["kid"] == _KID_B

    async def test_jwks_contains_both_keys_after_rotation(
        self, client: httpx.AsyncClient
    ) -> None:
        """
        After rotation, /jwks must expose both key A and key B.
        """
        auth_b = build_authority(_KID_B)
        multi_authority.rotate(auth_b)

        resp = await client.get("/jwks")
        assert resp.status_code == 200
        keys = resp.json()["keys"]
        kids = {k["kid"] for k in keys}
        assert _KID_A in kids
        assert _KID_B in kids

    async def test_token_from_retired_key_rejected(
        self, client: httpx.AsyncClient
    ) -> None:
        """
        After retiring key A, tokens signed by A must be rejected with 401.

        Phase 2 model:
            _authorities = {B: auth_B}   ← A removed
            _active_kid  = "B"
            ← tokens with kid=A → UnknownKidError → 401
        """
        # Sign with key A first
        token_a = mint_token("user:alice")

        # Rotate to B, then retire A
        auth_b = build_authority(_KID_B)
        multi_authority.rotate(auth_b)
        multi_authority.retire(_KID_A)

        # Reload registry — A is no longer in the MultiKeyAuthority's JWKS
        await registry.load_all()

        # token_a (kid=A) can no longer be verified — A is gone
        resp = await client.get("/me", headers=_bearer(token_a))
        assert resp.status_code == 401

    async def test_token_from_b_verifiable_after_a_retired(
        self, client: httpx.AsyncClient
    ) -> None:
        """
        Tokens signed by B are verifiable even after key A is retired.
        """
        auth_b = build_authority(_KID_B)
        multi_authority.rotate(auth_b)
        multi_authority.retire(_KID_A)
        await registry.load_all()

        token_b = mint_token("user:charlie")
        resp = await client.get("/me", headers=_bearer(token_b))
        assert resp.status_code == 200
        body = resp.json()
        assert body["subject"] == "user:charlie"
        assert body["kid"] == _KID_B

    async def test_retire_active_key_raises_value_error(self) -> None:
        """
        Retiring the currently active key raises ``ValueError``.

        Guards against accidentally retiring a key that is still being used
        to sign new tokens — callers must rotate first.
        """
        with pytest.raises(ValueError, match="Cannot retire the active kid"):
            multi_authority.retire(_KID_A)

    async def test_active_kid_reported_by_token_endpoint(
        self, client: httpx.AsyncClient
    ) -> None:
        """
        POST /auth/token reflects the active kid before and after rotation.
        """
        # Before rotation
        resp = await client.post("/auth/token", params={"subject": "u"})
        assert resp.json()["active_kid"] == _KID_A

        # Rotate
        auth_b = build_authority(_KID_B)
        multi_authority.rotate(auth_b)

        # After rotation
        resp = await client.post("/auth/token", params={"subject": "u"})
        assert resp.json()["active_kid"] == _KID_B
