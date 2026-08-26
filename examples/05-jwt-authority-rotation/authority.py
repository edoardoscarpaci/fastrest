"""
authority.py
============
JWT authority setup for the ``05-jwt-authority-rotation`` example.

Builds two RSA-2048 key pairs (A and B) at import time, constructs a
``MultiKeyAuthority`` starting with key A, and registers it in a
``TrustedIssuerRegistry``.  Exposes helpers to rotate and retire keys,
demonstrating zero-downtime key rotation.

Nothing here is request-scoped — all objects are module-level singletons
constructed once when the module is first imported.  The only async step
(``registry.load_all()``) is deferred to ``create_app()``'s lifespan, or
called explicitly in test fixtures.

DESIGN: module-level MultiKeyAuthority singleton over DI binding
    ✅ Tests can import ``multi_authority`` and ``registry`` without a DI container.
    ✅ ``MultiKeyAuthority`` is thread-safe for sign/verify; rotation uses a lock.
    ✅ ``TrustedIssuerRegistry.register_authority()`` is synchronous — only
       ``load_all()`` needs an event loop.
    ❌ Not injectable by DI — acceptable for a self-contained quickstart.

Thread safety:  ✅ All module-level objects are safe after construction:
                   ``JwtAuthority`` is read-only; ``MultiKeyAuthority`` uses a
                   ``threading.Lock`` for rotate/retire.
Async safety:   ✅ ``mint_token`` and key helpers are synchronous; no event
                   loop required at call time.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from varco_core.authority.jwt_authority import JwtAuthority
from varco_core.authority.multi_key_authority import MultiKeyAuthority
from varco_core.authority.registry import TrustedIssuerRegistry

_logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_KID_A = "rotation-example:key-A"
_KID_B = "rotation-example:key-B"
_ISSUER = "example-rotation-svc"
_TOKEN_LIFETIME = timedelta(hours=1)


# ── Key-pair generation ───────────────────────────────────────────────────────


def _generate_pem() -> bytes:
    """
    Generate an ephemeral RSA-2048 private key and return PEM bytes.

    Uses PKCS#8 format (``BEGIN PRIVATE KEY``) — accepted by
    ``JwtAuthority.from_pem()``.

    Returns:
        PEM-encoded RSA-2048 private key bytes (unencrypted PKCS#8).

    Edge cases:
        - ``cryptography`` is a transitive dependency of PyJWT — always
          available when varco_core is installed.
        - A new key pair is generated on every call; keys are ephemeral.
    """
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: PLC0415

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def build_authority(kid: str) -> JwtAuthority:
    """
    Generate a fresh ``JwtAuthority`` with a new RSA-2048 key pair.

    Args:
        kid: Key ID to embed in JWT headers signed by this authority.

    Returns:
        Fully initialised ``JwtAuthority`` for signing and verifying tokens.

    Edge cases:
        - Each call generates a distinct key pair — tokens from one authority
          cannot be verified by another.
    """
    _logger.debug("authority: generating ephemeral RSA-2048 key pair kid=%r", kid)
    pem = _generate_pem()
    return JwtAuthority.from_pem(pem, kid=kid, issuer=_ISSUER, algorithm="RS256")


# ── Module-level singletons ───────────────────────────────────────────────────

# Key A — the initial signing key.
authority_a: JwtAuthority = build_authority(_KID_A)

# MultiKeyAuthority starts with key A as the active signing key.
multi_authority: MultiKeyAuthority = MultiKeyAuthority(authority_a)

# Registry populated synchronously; load_all() deferred to create_app() lifespan.
registry: TrustedIssuerRegistry = TrustedIssuerRegistry()
registry.register_authority(multi_authority, label="ROTATION_EXAMPLE")


# ── Token helper ──────────────────────────────────────────────────────────────


def mint_token(subject: str, *, expires_in: timedelta = _TOKEN_LIFETIME) -> str:
    """
    Sign a JWT using the currently active key in ``multi_authority``.

    Args:
        subject:    The ``sub`` claim value (e.g. ``"user:alice"``).
        expires_in: Token lifetime.  Defaults to one hour.

    Returns:
        Signed JWT string, signed with whichever key is currently active
        in ``multi_authority``.  The ``kid`` header reflects the active key.

    Edge cases:
        - If ``rotate()`` has been called since the last ``mint_token()``, new
          tokens carry the new kid.  Tokens with the old kid remain verifiable
          until ``retire()`` is called for that kid.
    """
    builder = multi_authority.token().subject(subject).issued_now().expires_in(expires_in)
    return multi_authority.sign(builder)


__all__ = [
    "authority_a",
    "build_authority",
    "multi_authority",
    "registry",
    "mint_token",
    "_KID_A",
    "_KID_B",
    "_ISSUER",
]
