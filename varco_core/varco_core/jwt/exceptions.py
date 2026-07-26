"""
varco_core.jwt.exceptions
=============================

Exception hierarchy for the JWT claim-transformation and token-profile layer
(Plan 002).

    JwtException(Exception)
      ├── ClaimTransformError(JwtException, ValueError)
      └── TokenProfileError(JwtException)

``ClaimTransformError`` also inherits from ``ValueError`` so existing
``except ValueError`` call sites (e.g. anything catching the old bare
``KeyError``/``ValueError`` from malformed ``grants`` claims) keep working
without modification.

Thread safety:  ✅ Plain exception classes — no shared state.
Async safety:   ✅ No I/O.
"""

from __future__ import annotations


class JwtException(Exception):
    """
    Base class for all varco JWT-layer exceptions.

    Constructed with a single human-readable, actionable message string
    (standard ``Exception(message)`` convention — no dedicated ``__init__``
    is declared). Every subclass constructs messages that name the
    offending claim/target/path — never a bare "invalid" message.
    """


class ClaimTransformError(JwtException, ValueError):
    """
    Raised when claim-transformation (shape normalization or ``ClaimMapping``
    application) fails.

    Inherits from ``ValueError`` in addition to ``JwtException`` so callers
    that historically caught the bare ``ValueError``/``KeyError`` raised by
    malformed ``grants`` claims (``parser.py`` pre-Plan-002) continue to
    catch this new, more specific exception without code changes.

    Edge cases:
        - Messages always name the target canonical claim and, where
          relevant, every source path that was tried (fallback chain) or
          the offending list index (``ValueShape.GRANTS``).
    """


class TokenProfileError(JwtException):
    """
    Raised for ``TokenProfile`` / ``TokenProfileRegistry`` configuration and
    lookup errors — e.g. an unknown profile name, or a condition-less
    profile declared via env vars (a match-everything footgun).

    Edge cases:
        - ``TokenProfileRegistry.get()`` messages list all known profile
          names to speed up debugging typos.
    """


__all__ = [
    "JwtException",
    "ClaimTransformError",
    "TokenProfileError",
]
