"""
varco_core.jwt.transform.mapper
====================================

``MappingClaimTransformer`` — the ``ClaimTransformer`` implementation that
wraps a code- or env-configured ``ClaimMapping``.

Thread safety:  ✅ Immutable ``ClaimMapping`` wrapped; no mutable state.
Async safety:   ✅ Pure — no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from varco_core.jwt.transform.mapping import ClaimMapping


class MappingClaimTransformer:
    """
    ``ClaimTransformer`` that applies a ``ClaimMapping`` to raw claims.

    This is the config-driven default — both the code-configured path
    (Phase 1) and the env-var-driven path (``JwtTransformConfig.to_registry()``,
    Phase 2) construct one of these per resolved mapping.

    Args:
        mapping: The ``ClaimMapping`` to apply on every ``transform()`` call.

    Thread safety:  ✅ ``mapping`` is frozen; safe to share across requests.
    Async safety:   ✅ Pure — no I/O.
    """

    __slots__ = ("_mapping",)

    def __init__(self, mapping: ClaimMapping) -> None:
        self._mapping = mapping

    @property
    def mapping(self) -> ClaimMapping:
        """The wrapped ``ClaimMapping``."""
        return self._mapping

    def transform(self, claims: Mapping[str, Any]) -> Mapping[str, Any]:
        """
        Apply the wrapped ``ClaimMapping`` to ``claims``.

        Args:
            claims: Raw decoded claims dict.

        Returns:
            ``dict(claims)`` with canonical keys added/overwritten — see
            ``ClaimMapping.apply()``.

        Raises:
            ClaimTransformError: A ``required=True`` rule found no value.
        """
        return self._mapping.apply(dict(claims))

    def __repr__(self) -> str:
        return f"MappingClaimTransformer({self._mapping!r})"


__all__ = [
    "MappingClaimTransformer",
]
