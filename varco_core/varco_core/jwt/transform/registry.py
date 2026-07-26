"""
varco_core.jwt.transform.registry
======================================

``ClaimTransformerRegistry`` — maps the token's ``iss`` claim value to a
resolved ``ClaimTransformer``, with a default for unmapped/absent issuers.

Thread safety:  ✅ Read-mostly after construction; registration is expected
                   at startup (single-threaded), same as every other varco
                   registry (``TrustedIssuerRegistry``, ``TaskRegistry``).
Async safety:   ✅ No I/O — pure in-memory lookup.
"""

from __future__ import annotations

from varco_core.jwt.transform.protocol import IDENTITY, ClaimTransformer


class ClaimTransformerRegistry:
    """
    Registry of per-issuer ``ClaimTransformer``s plus a global default.

    Keyed by the ``iss`` claim value (not by env-var label) — decision D-6:
    the parser only has claims to work with, not registry labels, so keying
    by ``iss`` keeps ``varco_core.jwt`` independent of
    ``varco_core.authority``.

    Thread safety:  ✅ Registration is expected at startup; lookups
                       (``for_issuer``) are read-only afterwards.
    """

    __slots__ = ("_by_issuer", "_default")

    def __init__(self) -> None:
        self._by_issuer: dict[str, ClaimTransformer] = {}
        # Defaults to IDENTITY — the zero-config hot path when nothing has
        # been registered at all.
        self._default: ClaimTransformer = IDENTITY

    def register(self, iss: str, transformer: ClaimTransformer) -> None:
        """
        Register a transformer for tokens carrying ``iss == iss``.

        Args:
            iss:         Issuer claim value this transformer applies to.
            transformer: The ``ClaimTransformer`` to use for that issuer.

        Edge cases:
            - Re-registering the same ``iss`` replaces the previous entry.
        """
        self._by_issuer[iss] = transformer

    def set_default(self, transformer: ClaimTransformer) -> None:
        """
        Set the transformer used for tokens whose ``iss`` is absent or does
        not match any registered issuer.

        Args:
            transformer: The fallback ``ClaimTransformer``.
        """
        self._default = transformer

    def for_issuer(self, iss: str | None) -> ClaimTransformer:
        """
        Resolve the transformer to use for a token with the given ``iss``.

        Args:
            iss: The token's ``iss`` claim, or ``None`` if absent.

        Returns:
            The registered per-issuer transformer, or the registry's
            default (``IDENTITY`` unless ``set_default()`` was called).

        Edge cases:
            - ``iss is None`` always resolves to the default — per-issuer
              lookup is skipped entirely.
            - An ``iss`` that matches no registered entry resolves to the
              default — never an error (unmapped issuer is a normal case).
        """
        if iss is None:
            return self._default
        return self._by_issuer.get(iss, self._default)

    def __repr__(self) -> str:
        return (
            f"ClaimTransformerRegistry(issuers={list(self._by_issuer)!r}, "
            f"has_custom_default={self._default is not IDENTITY})"
        )


__all__ = [
    "ClaimTransformerRegistry",
]
