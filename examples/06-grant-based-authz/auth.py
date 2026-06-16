"""
auth.py
=======
JWT authority + test-token helper for the grant-based-authz example.

Builds a local ``JwtAuthority`` (RSA-2048, generated at import time),
registers it in a ``TrustedIssuerRegistry``, and exposes a ``mint_token``
helper that tests use to produce tokens with specific grants, roles, and
subject identifiers.

Token structure emitted by ``mint_token``
-----------------------------------------
The JWT payload carries ``grants`` claims serialised from ``ResourceGrant``
objects embedded in the ``AuthContext``.  On verification,
``JwtParser._from_raw_claims`` reconstructs the full ``AuthContext``
(including ``grants``) so ``ctx.can(action, "documents")`` works without any
server-side store.

DESIGN: module-level authority singleton over DI binding
    ✅ Tests can ``from auth import mint_token`` without a DI container.
    ✅ Authority is not request-scoped — no lifecycle concerns.
    ✅ Identical to the pattern in ``02-api-gateway-guards/auth.py``.
    ❌ Not injectable by DI — acceptable for a quickstart example.

Thread safety:  ✅ All module-level objects are read-only after import.
Async safety:   ✅ ``mint_token`` is synchronous; no event loop required.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from varco_core.auth.base import Action, AuthContext, ResourceGrant
from varco_core.authority.jwt_authority import JwtAuthority
from varco_core.authority.registry import TrustedIssuerRegistry

_logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_KID = "docs-example"
_ISSUER = "example-docs"
_TOKEN_LIFETIME = timedelta(hours=1)


# ── Authority construction ────────────────────────────────────────────────────


def _build_authority() -> JwtAuthority:
    """
    Generate an ephemeral RSA-2048 signing authority.

    Generates a fresh key pair every time the module is imported.  Tokens
    are invalidated on every process restart — intentional for a
    self-contained example that needs no persistent key store.

    Returns:
        Fully initialised ``JwtAuthority`` ready to sign and verify tokens.

    Edge cases:
        - ``cryptography`` is a transitive dependency of PyJWT — always present.
        - The generated key is 2048-bit RSA; sufficient for examples and tests.
    """
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: PLC0415

    _logger.debug("auth: generating ephemeral RSA-2048 key pair for docs example")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return JwtAuthority.from_pem(pem_bytes, kid=_KID, issuer=_ISSUER, algorithm="RS256")


# Module-level singletons — constructed once per process.
authority: JwtAuthority = _build_authority()

# Registry is populated synchronously; load_all() runs inside the app lifespan.
registry: TrustedIssuerRegistry = TrustedIssuerRegistry()
registry.register_authority(authority, label="DOCS_EXAMPLE")


# ── Convenience grant constants ────────────────────────────────────────────────

# Read grant — allows LIST and GET operations on documents.
DOCS_READ_GRANT = ResourceGrant(
    resource="documents",
    actions=frozenset({Action.READ, Action.LIST}),
)

# Write grant — allows CREATE, UPDATE, and DELETE on documents.
# DELETE is permitted at the type-level by this grant; whether the caller
# can actually delete a *specific* document is gated by ownership in
# DocumentService._check_entity (owner or admin role required).
DOCS_WRITE_GRANT = ResourceGrant(
    resource="documents",
    actions=frozenset({Action.CREATE, Action.UPDATE, Action.DELETE}),
)

# Admin grant — all actions on all resources via the wildcard key.
ADMIN_GRANT = ResourceGrant(
    resource="*",
    actions=frozenset(Action),  # all Action values
)


# ── Token helper ──────────────────────────────────────────────────────────────


def mint_token(
    subject: str,
    *,
    grants: tuple[ResourceGrant, ...] = (),
    roles: frozenset[str] = frozenset(),
    scopes: frozenset[str] = frozenset(),
) -> str:
    """
    Sign a JWT with the example authority, embedding grants, roles, and scopes.

    Intended for use in tests — creates a real RS256 JWT that the
    ``JwtBearerAuth`` middleware will accept.  Grants are serialised into
    the ``grants`` JWT claim and reconstructed into ``AuthContext.grants``
    on verification, so ``ctx.can(action, resource_key)`` works end-to-end.

    Args:
        subject: The ``sub`` claim value (e.g. ``"user:alice"``).
                 Becomes ``ctx.user_id`` after verification.
        grants:  ``ResourceGrant`` objects to embed.  Use the module-level
                 constants (``DOCS_READ_GRANT``, ``DOCS_WRITE_GRANT``,
                 ``ADMIN_GRANT``) for convenience.
        roles:   Role set to embed in the JWT ``roles`` claim.
                 ``"admin"`` enables the admin bypass in ``_check_entity``.
        scopes:  OAuth scope set to embed (not checked by this example
                 but included for completeness).

    Returns:
        Signed JWT string ready to include as ``Authorization: Bearer <token>``.

    Edge cases:
        - Tokens are valid for ``_TOKEN_LIFETIME`` (1 hour).
        - ``with_auth_ctx`` serialises ``grants``, ``roles``, and ``scopes``
          into the JWT payload so ``JwtParser`` reconstructs them on verify.
        - An empty ``grants`` tuple means the caller has no document permissions;
          all service operations requiring grants will be denied (403).

    Example::

        token = mint_token("user:alice", grants=(DOCS_READ_GRANT, DOCS_WRITE_GRANT))
        token = mint_token("user:admin", roles=frozenset({"admin"}), grants=(ADMIN_GRANT,))
    """
    # Embed grants, roles, and scopes via with_auth_ctx() — the JWT parser
    # deserialises them into AuthContext so ctx.can() / ctx.has_role() work.
    ctx = AuthContext(
        user_id=subject,
        roles=roles,
        scopes=scopes,
        grants=grants,
    )
    builder = (
        authority.token()
        .subject(subject)
        .issued_now()
        .expires_in(_TOKEN_LIFETIME)
        .with_auth_ctx(ctx)
    )
    return authority.sign(builder)


__all__ = [
    "authority",
    "registry",
    "mint_token",
    "DOCS_READ_GRANT",
    "DOCS_WRITE_GRANT",
    "ADMIN_GRANT",
]
