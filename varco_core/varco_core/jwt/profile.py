"""
varco_core.jwt.profile
==========================

``TokenProfile`` + ``TokenProfileRegistry`` — a named-profile replacement
for the single ``JwtUtil.SYSTEM_ISSUER`` class variable (Plan 002 §B).

A deployment can recognise many kinds of special/internal tokens
(``system``, ``internal``, ``partner``, ``service-mesh``, …) by issuer,
token type, audience, and/or required claims, and authorize on the matched
profile name at the route layer (``varco_fastapi.auth.guard.require_token_profile``).

DESIGN: process-global registry, mirroring ``transform/runtime.py``
    ✅ Same rationale as the claim-transform registry: ``JwtParser`` stays
       stateless classmethods, works with zero DI via ``from_env()``, and
       the explicit ``profiles=`` parameter always wins for testability.
    ❌ Same tradeoffs: mutable module state, config read at first-use.
       Mitigated identically — ``reset_token_profiles()`` in the autouse
       test fixture, explicit ``profiles=`` parameter as an escape hatch.
    No ``asyncio.Lock``: set once at startup, read afterwards; dict/
    attribute assignment is atomic under the GIL.

Thread safety:  ⚠️ Module-global state — same caveats as
                   ``transform/runtime.py``.
Async safety:   ✅ No I/O.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from varco_core.jwt.exceptions import TokenProfileError

if TYPE_CHECKING:
    from varco_core.jwt.model import JsonWebToken


# The metadata key a matched profile's name is stored under on
# ``AuthContext.metadata`` — the contract ``require_token_profile()``
# (varco_fastapi.auth.guard) reads.
PROFILE_METADATA_KEY: Final[str] = "token_profile"

# Env var prefix for labelled profile declarations:
# VARCO_JWT_PROFILE__<NAME>__{ISS,TOKEN_TYPE,AUD,REQUIRED_CLAIMS,ROLES,SCOPES}
_ENV_PREFIX: Final[str] = "VARCO_JWT_PROFILE__"

# Field suffixes that count as a "condition" — a profile with none of these
# set is a match-everything footgun and is rejected at load time.
_CONDITION_SUFFIXES: Final[frozenset[str]] = frozenset(
    {"ISS", "TOKEN_TYPE", "AUD", "REQUIRED_CLAIMS"}
)


# ── TokenProfile ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TokenProfile:
    """
    A named, declarative condition set identifying a class of special
    tokens (system, internal, partner, service-mesh, …).

    Attributes:
        name:            Profile name — the value stored under
                         ``AuthContext.metadata[PROFILE_METADATA_KEY]`` when
                         this profile matches.
        issuers:         Any-of match against ``token.iss``.  Empty =
                         matches any issuer.
        token_type:      Exact match against ``token.token_type`` when set.
        audiences:       Any-of match against ``token.aud`` (handles both
                         the ``str`` and ``frozenset`` forms transparently).
        required_claims: Canonical claim names (``"roles"``, ``"scopes"``,
                         ``"tenant_id"``, ``"actor"``) or raw
                         ``extra_claims``/metadata keys that must be present
                         (non-empty) for this profile to match.
        implied_roles:   Merged into ``AuthContext.roles`` when this profile
                         matches — for internal/system tokens only (see the
                         plan's Risks section).
        implied_scopes:  Merged into ``AuthContext.scopes`` when matched.

    Thread safety:  ✅ frozen=True.
    """

    name: str
    issuers: frozenset[str] = frozenset()
    token_type: str | None = None
    audiences: frozenset[str] = frozenset()
    required_claims: frozenset[str] = frozenset()
    implied_roles: frozenset[str] = frozenset()
    implied_scopes: frozenset[str] = frozenset()

    def matches(self, token: "JsonWebToken") -> bool:
        """
        Return ``True`` iff every declared condition on this profile holds
        for ``token``.

        Args:
            token: The decoded ``JsonWebToken`` to test.

        Returns:
            ``True`` iff all declared conditions (issuers/token_type/
            audiences/required_claims) match.  Empty ``issuers``/
            ``audiences``/``required_claims`` are vacuously satisfied.
        """
        return self.explain(token) is None

    def explain(self, token: "JsonWebToken") -> str | None:
        """
        Return a human-readable description of the FIRST failing condition,
        or ``None`` if ``token`` matches every declared condition.

        Args:
            token: The decoded ``JsonWebToken`` to test.

        Returns:
            ``None`` on a full match; otherwise a message naming the first
            unmet condition (issuer → token_type → audience →
            required_claims, in that order) — useful for diagnosing "why
            didn't my token match this profile" during setup.
        """
        if self.issuers and token.iss not in self.issuers:
            return (
                f"issuer {token.iss!r} is not in the profile {self.name!r}'s "
                f"allowed issuers {sorted(self.issuers)!r}"
            )

        if self.token_type is not None and token.token_type != self.token_type:
            return (
                f"token_type {token.token_type!r} does not match the profile "
                f"{self.name!r}'s required token_type {self.token_type!r}"
            )

        if self.audiences:
            token_auds: frozenset[str]
            if isinstance(token.aud, frozenset):
                token_auds = token.aud
            elif token.aud is not None:
                token_auds = frozenset({token.aud})
            else:
                token_auds = frozenset()
            if not (self.audiences & token_auds):
                return (
                    f"audience {token.aud!r} does not intersect the profile "
                    f"{self.name!r}'s allowed audiences {sorted(self.audiences)!r}"
                )

        for claim in self.required_claims:
            if not _has_claim(token, claim):
                return (
                    f"required claim {claim!r} is missing for profile " f"{self.name!r}"
                )

        return None


def _has_claim(token: "JsonWebToken", claim: str) -> bool:
    """
    Return ``True`` if ``claim`` is present (and non-empty, for
    collections) on ``token`` — checked across ``extra_claims``,
    ``auth_ctx.metadata``, and the well-known canonical auth fields.
    """
    if claim in token.extra_claims:
        return True
    ctx = token.auth_ctx
    if ctx is None:
        return False
    if claim in ctx.metadata:
        return True
    if claim == "tenant_id":
        return "tenant_id" in ctx.metadata
    if claim == "actor":
        return "actor" in ctx.metadata
    if claim == "roles":
        return bool(ctx.roles)
    if claim == "scopes":
        return bool(ctx.scopes)
    if claim == "grants":
        return bool(ctx.grants)
    return False


# ── TokenProfileRegistry ─────────────────────────────────────────────────────────


class TokenProfileRegistry:
    """
    Ordered registry of ``TokenProfile``s — ``resolve()`` returns the first
    match in registration order (overlapping profiles are documented as
    first-wins).

    Thread safety:  ✅ Registration expected at startup; ``resolve()``/
                       ``matches()``/``get()`` are read-only afterwards.
    """

    __slots__ = ("_profiles",)

    def __init__(self) -> None:
        # Insertion-ordered dict (Python 3.7+ guarantee) — preserves
        # registration order for resolve()'s first-match semantics.
        self._profiles: dict[str, TokenProfile] = {}

    def register(self, profile: TokenProfile) -> None:
        """
        Register a ``TokenProfile``.

        Args:
            profile: The profile to register.

        Edge cases:
            - Re-registering the same ``name`` replaces the previous entry
              IN PLACE (keeps its original registration-order position) —
              consistent with a ``dict``'s key-update semantics.
        """
        self._profiles[profile.name] = profile

    def get(self, name: str) -> TokenProfile:
        """
        Return the registered profile named ``name``.

        Args:
            name: Profile name.

        Returns:
            The registered ``TokenProfile``.

        Raises:
            TokenProfileError: No profile named ``name`` is registered —
                message lists every known name.
        """
        profile = self._profiles.get(name)
        if profile is None:
            raise TokenProfileError(
                f"No token profile named {name!r} is registered. "
                f"Known profiles: {list(self._profiles.keys())!r}."
            )
        return profile

    def names(self) -> tuple[str, ...]:
        """Return every registered profile name, in registration order."""
        return tuple(self._profiles.keys())

    def resolve(self, token: "JsonWebToken") -> TokenProfile | None:
        """
        Return the first registered profile that matches ``token``.

        Args:
            token: The decoded ``JsonWebToken`` to resolve.

        Returns:
            The first matching ``TokenProfile`` in registration order, or
            ``None`` if no registered profile matches.
        """
        for profile in self._profiles.values():
            if profile.matches(token):
                return profile
        return None

    def matches(self, name: str, token: "JsonWebToken") -> bool:
        """
        Return ``True`` iff the named profile matches ``token``.

        Args:
            name:  Registered profile name.
            token: The decoded ``JsonWebToken`` to test.

        Returns:
            ``True`` iff ``self.get(name).matches(token)``.

        Raises:
            TokenProfileError: ``name`` is not registered.
        """
        return self.get(name).matches(token)

    @classmethod
    def from_env(cls) -> TokenProfileRegistry:
        """
        Build a ``TokenProfileRegistry`` from ``VARCO_JWT_PROFILE__<NAME>__*``
        env vars.

        Returns:
            A populated registry — empty if no ``VARCO_JWT_PROFILE__*`` vars
            are set (the zero-config case: ``resolve()`` always returns
            ``None``).

        Raises:
            TokenProfileError: A labelled profile declares NO condition at
                all (no ``ISS``/``TOKEN_TYPE``/``AUD``/``REQUIRED_CLAIMS``)
                — a match-everything profile is rejected as a footgun that
                would silently grant ``implied_roles``/``implied_scopes`` to
                every token.

        Edge cases:
            - ``<NAME>`` is lowercased to form the profile name
              (``INTERNAL`` → ``"internal"``).
            - ``ISS``/``AUD``/``REQUIRED_CLAIMS``/``ROLES``/``SCOPES`` are
              comma-separated lists; ``TOKEN_TYPE`` is a single string.
        """
        groups: dict[str, dict[str, str]] = {}
        for key, value in os.environ.items():
            if not key.startswith(_ENV_PREFIX):
                continue
            rest = key[len(_ENV_PREFIX) :]
            if "__" not in rest:
                continue
            label, suffix = rest.rsplit("__", 1)
            groups.setdefault(label, {})[suffix] = value.strip()

        registry = cls()
        for label, fields in groups.items():
            if not (_CONDITION_SUFFIXES & fields.keys()):
                raise TokenProfileError(
                    f"Token profile label {label!r} (VARCO_JWT_PROFILE__{label}__*) "
                    f"declares no condition at all (no ISS/TOKEN_TYPE/AUD/"
                    f"REQUIRED_CLAIMS). A condition-less profile would match "
                    f"every token — set at least one condition."
                )

            registry.register(
                TokenProfile(
                    name=label.lower(),
                    issuers=_split_set(fields.get("ISS")),
                    token_type=fields.get("TOKEN_TYPE"),
                    audiences=_split_set(fields.get("AUD")),
                    required_claims=_split_set(fields.get("REQUIRED_CLAIMS")),
                    implied_roles=_split_set(fields.get("ROLES")),
                    implied_scopes=_split_set(fields.get("SCOPES")),
                )
            )
        return registry

    def __repr__(self) -> str:
        return f"TokenProfileRegistry(names={self.names()!r})"


def _split_set(value: str | None) -> frozenset[str]:
    """Parse a comma-separated env-var value into a ``frozenset[str]``."""
    if not value:
        return frozenset()
    return frozenset(chunk.strip() for chunk in value.split(",") if chunk.strip())


# ── Process-global resolution (mirrors transform/runtime.py) ────────────────────

_registry: TokenProfileRegistry | None = None


def resolve_token_profile(
    token: "JsonWebToken", *, registry: TokenProfileRegistry | None = None
) -> "JsonWebToken":
    """
    Resolve the matching profile for ``token`` and augment/materialise its
    ``auth_ctx`` accordingly.

    Called from ``JwtParser._from_raw_claims`` after building the base
    token — this is the funnel that gives both SEAM 1 (``parse()``) and
    SEAM 2 (``TrustedIssuerRegistry.verify()``) profile support for free.

    Args:
        token:    The already-built ``JsonWebToken`` (pre-profile-resolution).
        registry: Explicit ``TokenProfileRegistry`` — always wins over the
                  process-global registry.  ``None`` lazily builds/caches the
                  global registry from ``VARCO_JWT_PROFILE__*`` env vars.

    Returns:
        ``token`` unchanged if no profile matches; otherwise a token whose
        ``auth_ctx`` has ``metadata[PROFILE_METADATA_KEY]`` set to the
        matched profile's name and ``implied_roles``/``implied_scopes``
        merged in.

    Edge cases:
        - A token with no auth claims at all that matches a profile with
          ``implied_roles``/``implied_scopes`` gets a **materialised**
          ``AuthContext(user_id=token.sub, ...)`` — this is the "system
          token with elevated trust" case (⚠️ documented behaviour change
          vs. pre-Plan-002, see the plan's Edge cases table).
        - A token that matches a profile with NO implied roles/scopes still
          gets ``metadata[PROFILE_METADATA_KEY]`` set, but only if an
          ``auth_ctx`` already exists (or the profile forces one via
          implied roles/scopes) — a fully bare token matching a completely
          inert profile keeps ``auth_ctx is None`` (today's behaviour).
    """
    from dataclasses import replace

    from varco_core.auth import AuthContext

    reg = registry if registry is not None else _resolve_global_registry()
    profile = reg.resolve(token)
    if profile is None:
        return token

    if (
        token.auth_ctx is None
        and not profile.implied_roles
        and not profile.implied_scopes
    ):
        # Nothing to add and nothing to materialise for — keep auth_ctx as
        # None, matching today's behaviour for a profile that carries no
        # elevated trust.
        return token

    base_ctx = token.auth_ctx or AuthContext(user_id=token.sub)
    new_ctx = replace(
        base_ctx,
        roles=base_ctx.roles | profile.implied_roles,
        scopes=base_ctx.scopes | profile.implied_scopes,
        metadata={**base_ctx.metadata, PROFILE_METADATA_KEY: profile.name},
    )
    return replace(token, auth_ctx=new_ctx)


def _resolve_global_registry() -> TokenProfileRegistry:
    global _registry
    if _registry is None:
        _registry = TokenProfileRegistry.from_env()
    return _registry


def configure_token_profiles(registry: TokenProfileRegistry | None = None) -> None:
    """
    Install ``registry`` as the process-global token-profile registry.

    Args:
        registry: The registry to install.  ``None`` eagerly rebuilds from
                  the current environment immediately.
    """
    global _registry
    if registry is None:
        _registry = TokenProfileRegistry.from_env()
    else:
        _registry = registry


def reset_token_profiles() -> None:
    """
    Clear the process-global token-profile registry, restoring lazy
    env-driven resolution.  Intended for an autouse test fixture (see
    ``varco_core/tests/conftest.py``).
    """
    global _registry
    _registry = None


__all__ = [
    "PROFILE_METADATA_KEY",
    "TokenProfile",
    "TokenProfileRegistry",
    "TokenProfileError",
    "resolve_token_profile",
    "configure_token_profiles",
    "reset_token_profiles",
]
