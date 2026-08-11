"""
varco_core.jwt.config
=========================

``JwtVerificationSettings`` — env-driven defaults for JWT *verification*
hardening: clock-skew leeway (C-1) and audience enforcement (C-2).

Deliberately a separate module from ``varco_core.jwt.transform.config``:
verification settings (how strict/lenient PyJWT's own temporal/audience
checks are) are not a claim-*transformation* concern — they configure
PyJWT's ``decode()`` call itself, not the claim-renaming layer that runs
after it.

Thread safety:  ✅ ``frozen=True`` — immutable after construction.
Async safety:   ✅ No I/O.
"""

from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import SettingsConfigDict

from varco_core.config import VarcoSettings


class JwtVerificationSettings(VarcoSettings):
    """
    ``VARCO_JWT_*`` env vars controlling PyJWT verification strictness.

    Attributes:
        leeway_seconds: Clock-skew leeway (seconds) applied to ``exp``/``nbf``
                        checks.  Default ``0.0`` — identical to today's
                        behaviour (no leeway).  A classic fix for cross-host
                        401s caused by clock drift; ``30`` is a common value.
        audience:       This service's expected ``aud`` claim value.  ``None``
                        (default) — audience is NOT enforced by
                        ``TrustedIssuerRegistry.verify()`` at this layer
                        (decision D-17). ``JwtBearerAuth`` (varco_fastapi)
                        layers a stricter, fail-closed rule on top — see
                        ``allow_any_audience`` below and Plan 005 Phase 2.
        enforce_issuer: Whether ``TrustedIssuerRegistry.verify()`` checks the
                        token's ``iss`` claim against the resolved issuer's
                        registered value.  Default ``True`` — Plan 005
                        Phase 2 / U-13, a BREAKING security-default change:
                        pre-Phase-2 releases never enforced ``iss`` here.
                        Set ``VARCO_JWT_ENFORCE_ISS=false`` (or pass
                        ``enforce_issuer=False`` to ``verify()``) to restore
                        the old behaviour.
        allow_any_audience: Whether ``JwtBearerAuth`` (varco_fastapi) may be
                        constructed with no configured audience. Default
                        ``False`` — Plan 005 Phase 2 / U-13, a BREAKING
                        security-default change: pre-Phase-2 releases logged
                        a warning and proceeded. Set
                        ``VARCO_JWT_ALLOW_ANY_AUDIENCE=true`` (or pass
                        ``allow_any_audience=True`` to ``JwtBearerAuth``) to
                        restore the old (warn + proceed) behaviour.

    Thread safety:  ✅ ``frozen=True``.
    """

    model_config = SettingsConfigDict(
        env_prefix="VARCO_JWT_",
        frozen=True,
        extra="ignore",
    )

    leeway_seconds: float = 0.0
    audience: str | None = None
    # Field name would otherwise map to VARCO_JWT_ENFORCE_ISSUER by the
    # default env_prefix + FIELD_NAME.upper() rule — the plan's chosen env
    # var name is the shorter VARCO_JWT_ENFORCE_ISS, so it needs an explicit
    # validation_alias naming the full var (bypasses the prefix rule).
    enforce_issuer: bool = Field(
        default=True, validation_alias=AliasChoices("VARCO_JWT_ENFORCE_ISS")
    )
    allow_any_audience: bool = False


__all__ = [
    "JwtVerificationSettings",
]
