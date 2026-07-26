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
                        (default) — audience is NOT enforced, matching
                        today's ``JwtBearerAuth`` behaviour (opt-in
                        hardening, decision D-17).

    Thread safety:  ✅ ``frozen=True``.
    """

    model_config = SettingsConfigDict(
        env_prefix="VARCO_JWT_",
        frozen=True,
        extra="ignore",
    )

    leeway_seconds: float = 0.0
    audience: str | None = None


__all__ = [
    "JwtVerificationSettings",
]
