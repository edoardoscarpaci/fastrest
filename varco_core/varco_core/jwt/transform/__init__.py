"""
varco_core.jwt.transform
=============================

Claim-transformation layer (Plan 002) — maps foreign-shaped JWT claims
(Keycloak, Cognito, Auth0, a bespoke ``sofy-roles`` claim, …) onto the
canonical claim names ``varco_core.jwt`` understands, either code-configured
(``ClaimMapping``) or environment-variable-driven
(``JwtTransformConfig.from_env()``).

Sub-module layout
------------------
    varco_core/jwt/transform/
    ├── path.py       — ClaimPath, MISSING sentinel, read_claim()
    ├── shape.py      — ValueShape + normalize()
    ├── mapping.py    — CanonicalClaim, ClaimRule, ClaimMapping
    ├── protocol.py   — ClaimTransformer Protocol, IdentityClaimTransformer, IDENTITY
    ├── mapper.py     — MappingClaimTransformer
    ├── registry.py   — ClaimTransformerRegistry (per-issuer lookup)
    ├── config.py     — JwtTransformSettings (env) + JwtTransformConfig
    └── runtime.py    — process-global resolve/configure/reset functions
"""

from __future__ import annotations

from varco_core.jwt.transform.config import JwtTransformConfig, JwtTransformSettings
from varco_core.jwt.transform.mapper import MappingClaimTransformer
from varco_core.jwt.transform.mapping import CanonicalClaim, ClaimMapping, ClaimRule
from varco_core.jwt.transform.path import MISSING, ClaimPath, read_claim
from varco_core.jwt.transform.protocol import (
    IDENTITY,
    ClaimTransformer,
    IdentityClaimTransformer,
)
from varco_core.jwt.transform.registry import ClaimTransformerRegistry
from varco_core.jwt.transform.runtime import (
    configure_claim_transforms,
    configure_jwt_from_env,
    reset_claim_transforms,
    resolve_claim_transformer,
)
from varco_core.jwt.transform.shape import ValueShape, normalize

__all__ = [
    "MISSING",
    "ClaimPath",
    "read_claim",
    "ValueShape",
    "normalize",
    "CanonicalClaim",
    "ClaimMapping",
    "ClaimRule",
    "ClaimTransformer",
    "IdentityClaimTransformer",
    "IDENTITY",
    "MappingClaimTransformer",
    "ClaimTransformerRegistry",
    "JwtTransformConfig",
    "JwtTransformSettings",
    "configure_claim_transforms",
    "configure_jwt_from_env",
    "reset_claim_transforms",
    "resolve_claim_transformer",
]
