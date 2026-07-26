"""
varco_core.jwt.transform.protocol
======================================

``ClaimTransformer`` — the structural extension point for claim
transformation — plus ``IdentityClaimTransformer`` / ``IDENTITY``, the
zero-config hot-path default.

DESIGN: ``Protocol`` (structural) over an ABC (nominal)
    ✅ A user's existing adapter class (e.g. one that already has a
       ``transform(claims)`` method for an unrelated purpose) satisfies the
       protocol without inheriting from any varco base class.
    ✅ ``runtime_checkable`` allows ``isinstance()`` checks in tests without
       requiring subclassing.
    ❌ Structural typing only checks method *names*, not signatures — a
       user class with a same-named but incompatible ``transform()`` would
       pass ``isinstance()`` yet fail at call time.  Acceptable: the same
       tradeoff applies to every ``Protocol`` in Python's type system.

Thread safety:  ✅ ``IdentityClaimTransformer``/``IDENTITY`` are stateless.
Async safety:   ✅ Pure — no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ClaimTransformer(Protocol):
    """
    Structural protocol for claim transformation.

    Any object with a compatible ``transform(claims)`` method satisfies
    this protocol — implement it directly (no inheritance required) to
    plug in custom logic (call an internal user directory, decrypt a
    claim, etc.).
    """

    def transform(self, claims: Mapping[str, Any]) -> Mapping[str, Any]:
        """
        Transform raw JWT claims into canonically-named claims.

        Args:
            claims: The raw decoded claims dict (never mutated by a
                    well-behaved implementation).

        Returns:
            A claims mapping with canonical keys (``roles``, ``scopes``,
            ``grants``, ``tenant_id``, ``actor``, ``token_type``, …) added
            or overwritten.  Non-destructive implementations preserve every
            original key too (so ``extra_claims`` still shows foreign names).
        """
        ...


class IdentityClaimTransformer:
    """
    The zero-config default transformer — returns ``claims`` unchanged,
    with **no copy**.

    This is the hot-path guarantee: when no ``VARCO_JWT_TRANSFORM*`` env
    vars are set and no explicit ``transformer=`` is passed, parsing a
    canonical token costs exactly one dict-identity return — byte-for-byte
    today's (pre-Plan-002) parser behaviour.

    Thread safety:  ✅ Stateless.
    Async safety:   ✅ Pure — no I/O.
    """

    def transform(self, claims: Mapping[str, Any]) -> Mapping[str, Any]:
        """
        Return ``claims`` unchanged — the exact same object, not a copy.

        Args:
            claims: Raw decoded claims dict.

        Returns:
            ``claims`` itself (``is`` identity holds).
        """
        return claims


# Module-level singleton — reused everywhere the identity behaviour is needed.
IDENTITY = IdentityClaimTransformer()


__all__ = [
    "ClaimTransformer",
    "IdentityClaimTransformer",
    "IDENTITY",
]
