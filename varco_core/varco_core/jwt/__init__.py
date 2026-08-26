"""
varco_core.jwt
=================

JWT model, builder, parser, and utility helpers for the varco service layer.

Public surface — importable directly from ``varco_core.jwt``::

    from varco_core.jwt import JsonWebToken, JwtBuilder, JwtParser, JwtUtil
    from varco_core.jwt import SYSTEM_ISSUER

Plan 002 adds a claim-transformation layer (env-driven or code-configured)
and named token profiles — both importable from here too::

    from varco_core.jwt import ClaimMapping, ClaimRule, CanonicalClaim
    from varco_core.jwt import ClaimTransformer, MappingClaimTransformer
    from varco_core.jwt import configure_claim_transforms, resolve_claim_transformer

Or from the top-level package::

    from varco_core import JsonWebToken, JwtBuilder, JwtParser, JwtUtil

Sub-module layout
-----------------
    varco_core/jwt/
    ├── model.py       — JsonWebToken dataclass + timestamp helpers + reserved-key set
    ├── builder.py     — JwtBuilder (fluent construction + signing + as_profile())
    ├── parser.py      — JwtParser (decoding + AuthContext + transform/profile pipeline)
    ├── util.py        — JwtUtil (predicate helpers) + SYSTEM_ISSUER constant
    ├── exceptions.py  — JwtException, ClaimTransformError, TokenProfileError
    ├── config.py      — JwtVerificationSettings (leeway/audience env defaults)
    ├── profile.py     — TokenProfile, TokenProfileRegistry, resolve_token_profile()
    └── transform/     — claim-transformation sub-package (see its own __init__.py)
"""

from varco_core.jwt.builder import JwtBuilder
from varco_core.jwt.config import JwtVerificationSettings
from varco_core.jwt.exceptions import (
    ClaimTransformError,
    JwtException,
    TokenProfileError,
)
from varco_core.jwt.model import JsonWebToken
from varco_core.jwt.parser import JwtParser
from varco_core.jwt.profile import (
    PROFILE_METADATA_KEY,
    TokenProfile,
    TokenProfileRegistry,
    configure_token_profiles,
    reset_token_profiles,
    resolve_token_profile,
)
from varco_core.jwt.transform.mapper import MappingClaimTransformer
from varco_core.jwt.transform.mapping import CanonicalClaim, ClaimMapping, ClaimRule
from varco_core.jwt.transform.path import ClaimPath, read_claim
from varco_core.jwt.transform.protocol import (
    IDENTITY,
    ClaimTransformer,
    IdentityClaimTransformer,
)
from varco_core.jwt.transform.runtime import (
    configure_claim_transforms,
    reset_claim_transforms,
    resolve_claim_transformer,
)
from varco_core.jwt.transform.shape import ValueShape
from varco_core.jwt.util import SYSTEM_ISSUER, JwtUtil

__all__ = [
    "SYSTEM_ISSUER",
    "JsonWebToken",
    "JwtBuilder",
    "JwtParser",
    "JwtUtil",
    "JwtException",
    "ClaimTransformError",
    "TokenProfileError",
    "JwtVerificationSettings",
    "PROFILE_METADATA_KEY",
    "TokenProfile",
    "TokenProfileRegistry",
    "configure_token_profiles",
    "reset_token_profiles",
    "resolve_token_profile",
    "ClaimPath",
    "read_claim",
    "ValueShape",
    "CanonicalClaim",
    "ClaimMapping",
    "ClaimRule",
    "ClaimTransformer",
    "IdentityClaimTransformer",
    "IDENTITY",
    "MappingClaimTransformer",
    "configure_claim_transforms",
    "reset_claim_transforms",
    "resolve_claim_transformer",
]
