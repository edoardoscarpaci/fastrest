"""
auth.py
=======
JWT authority + test-token helper for the api-gateway-guards example.

Builds a local ``JwtAuthority`` (RSA-2048, generated at import time),
registers it in a ``TrustedIssuerRegistry``, and exposes a ``mint_token``
helper that tests use to produce tokens with specific scopes, roles, and
subject identifiers.

Nothing here is request-scoped — all objects are module-level singletons
constructed once when the module is first imported.  This is safe because:
- ``JwtAuthority`` holds immutable key material.
- ``TrustedIssuerRegistry`` is populated synchronously; ``load_all()`` is
  deferred to ``create_app()`` (inside the lifespan).
- ``mint_token`` is synchronous (``authority.sign()`` is pure CPU).

DESIGN: module-level authority singleton over DI binding
    ✅ Tests can ``from auth import mint_token`` without a DI container.
    ✅ Authority is not request-scoped — no lifecycle concerns.
    ✅ Identical to the pattern in ``example 00`` (avoids reinventing the wheel).
    ❌ Not injectable by DI — acceptable for a quickstart with no DI at all.

Thread safety:  ✅ All module-level objects are read-only after import.
Async safety:   ✅ ``mint_token`` is synchronous; no event loop required.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from varco_core.auth.base import AuthContext
from varco_core.authority.jwt_authority import JwtAuthority
from varco_core.authority.registry import TrustedIssuerRegistry

_logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_KID = "gateway-example"
_ISSUER = "example-gateway"
_TOKEN_LIFETIME = timedelta(hours=1)


# ── Authority construction ────────────────────────────────────────────────────


def _build_authority() -> JwtAuthority:
    """
    Generate an ephemeral RSA-2048 signing authority.

    Generates a fresh key pair every time the module is imported.  Tokens
    are invalidated on every process restart — this is intentional for a
    self-contained example that needs no persistent key store.

    Returns:
        Fully initialised ``JwtAuthority`` ready to sign and verify tokens.

    Edge cases:
        - ``cryptography`` is a transitive dependency of PyJWT — always available.
        - The generated key is 2048-bit RSA; sufficient for examples and tests.
    """
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: PLC0415

    _logger.debug("auth: generating ephemeral RSA-2048 key pair for gateway example")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return JwtAuthority.from_pem(pem_bytes, kid=_KID, issuer=_ISSUER, algorithm="RS256")


# Module-level singletons — constructed once per process.
authority: JwtAuthority = _build_authority()

# Registry is populated synchronously; load_all() runs inside create_app()'s lifespan.
registry: TrustedIssuerRegistry = TrustedIssuerRegistry()
registry.register_authority(authority, label="GATEWAY_EXAMPLE")


# ── Token helper ──────────────────────────────────────────────────────────────


def mint_token(
    subject: str,
    *,
    roles: frozenset[str] = frozenset(),
    scopes: frozenset[str] = frozenset(),
) -> str:
    """
    Sign a JWT with the example authority, embedding roles and scopes.

    Intended for use in tests — creates a real RS256 JWT that the
    ``JwtBearerAuth`` middleware will accept on the running app.

    Args:
        subject: The ``sub`` claim value (e.g. ``"user:alice"`` or
                 ``"svc:my-service"``).  Used as the identity in ``AuthContext``.
        roles:   Role set to embed in the JWT ``roles`` claim.
        scopes:  OAuth scope set to embed in the JWT ``scopes`` claim.

    Returns:
        Signed JWT string ready to include as ``Authorization: Bearer <token>``.

    Edge cases:
        - Tokens are valid for ``_TOKEN_LIFETIME`` (1 hour).
        - ``with_auth_ctx`` serialises ``roles`` and ``scopes`` into the JWT
          payload so ``JwtParser._from_raw_claims`` reconstructs the full
          ``AuthContext`` on verification — including ``has_scope()`` / ``has_role()``.
    """
    ctx = AuthContext(user_id=subject, roles=roles, scopes=scopes)
    builder = (
        authority.token()
        .subject(subject)
        .issued_now()
        .expires_in(_TOKEN_LIFETIME)
        .with_auth_ctx(ctx)
    )
    return authority.sign(builder)


__all__ = ["authority", "registry", "mint_token"]
