"""
varco_core.jwt.transform.path
==================================

``ClaimPath`` — a dotted (or custom-separator) path into a nested claims
dict, plus the ``MISSING`` sentinel and the ``read_claim()`` public helper.

DESIGN: dotted-path syntax over JSONPath / a JSONPath dependency
    ✅ No new dependency — the stdlib is enough for "walk N nested dict keys".
    ✅ Covers the overwhelming majority of real-world claim shapes seen in
       Keycloak (``realm_access.roles``), Cognito, Auth0, and RFC 8693 actor
       claims (``act.sub``).
    ❌ No array indexing (``roles[0]``) or JSONPath filters/wildcards — a
       claim path can only walk *mapping* keys, never list elements.
    Alternative considered: depend on a JSONPath library (e.g. jsonpath-ng).
    Rejected — no observed real-world claim needs indexing, and pulling in a
    JSONPath engine for "read three dotted keys" is disproportionate (D-9).

Thread safety:  ✅ ``ClaimPath`` is frozen — immutable, safe to share/cache.
Async safety:   ✅ Pure — no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final


# ── MISSING sentinel ──────────────────────────────────────────────────────────


class _MissingType:
    """
    Sentinel type distinguishing "claim absent" from a legitimate ``None``
    claim value.

    DESIGN: dedicated sentinel class over ``None``
        ✅ A claim can legitimately be ``null`` in JSON — using ``None`` as
           "missing" would make that value indistinguishable from absence.
        ✅ ``repr()`` is self-documenting in test failures / debug logs.
        ❌ One more concept for callers to learn — mitigated by exporting a
           single module-level singleton (``MISSING``) rather than the class.
    """

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "MISSING"

    def __bool__(self) -> bool:
        # MISSING is always falsy — lets callers write `if not value` checks.
        return False


# Module-level singleton — the only instance that should ever exist.
MISSING: Final[_MissingType] = _MissingType()


# ── ClaimPath ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ClaimPath:
    """
    An immutable, pre-parsed dotted path into a nested claims ``Mapping``.

    Attributes:
        segments: The path's individual key names, in walk order —
                  ``"realm_access.roles"`` → ``("realm_access", "roles")``.

    Thread safety:  ✅ frozen=True — safe to share/cache across requests.
    Async safety:   ✅ Pure value object — no I/O.

    Example::

        path = ClaimPath.parse("realm_access.roles")
        path.read({"realm_access": {"roles": ["editor"]}})  # -> ["editor"]
    """

    segments: tuple[str, ...]

    @classmethod
    def parse(cls, spec: str, *, separator: str = ".") -> ClaimPath:
        """
        Parse a dotted path spec into a ``ClaimPath``.

        A backslash immediately before ``separator`` escapes it as a literal
        character inside the current segment (``r"a\\.b"`` → one segment
        ``"a.b"``, not two).

        Args:
            spec:      The path spec, e.g. ``"realm_access.roles"``.
            separator: Path separator.  Defaults to ``"."``; override for a
                       claim mapping that itself contains literal dots and
                       chooses a different ``VARCO_JWT_TRANSFORM_PATH_SEPARATOR``
                       (e.g. ``":"``).

        Returns:
            A ``ClaimPath`` with the parsed ``segments``.

        Raises:
            ValueError: ``spec`` is empty, or contains an empty intermediate
                        segment (``"a..b"``) — a config error, not a
                        legitimately "missing" path, so it fails fast at
                        parse time rather than silently reading as MISSING
                        forever.

        Edge cases:
            - A spec with no ``separator`` at all → single-segment path.
            - Trailing/leading empty segments (``".a"``, ``"a."``) are
              rejected the same way as ``"a..b"``.
        """
        if not spec:
            raise ValueError(
                "ClaimPath.parse() requires a non-empty spec, got "
                f"{spec!r}. Example: 'realm_access.roles'."
            )

        segments: list[str] = []
        current: list[str] = []
        i = 0
        n = len(spec)
        while i < n:
            ch = spec[i]
            if ch == "\\" and i + 1 < n and spec[i + 1] == separator:
                # Escaped separator — treat as a literal character in the
                # current segment, not a boundary.
                current.append(separator)
                i += 2
                continue
            if ch == separator:
                segments.append("".join(current))
                current = []
                i += 1
                continue
            current.append(ch)
            i += 1
        segments.append("".join(current))

        if any(seg == "" for seg in segments):
            raise ValueError(
                f"ClaimPath spec {spec!r} contains an empty segment "
                f"(double separator {separator!r}, or a leading/trailing "
                f"separator). Each segment must be non-empty."
            )

        return cls(segments=tuple(segments))

    def read(self, claims: Mapping[str, Any]) -> Any:
        """
        Walk ``claims`` following ``segments``, returning ``MISSING`` on any
        absent key or non-mapping intermediate value.

        Args:
            claims: The (raw or partially-transformed) claims dict to read.

        Returns:
            The value found at the end of the path, or ``MISSING`` — never
            raises for a missing/malformed path; callers decide policy
            (fallback chain, default, or ``required=True`` error).

        Edge cases:
            - An intermediate value that is not a ``Mapping`` (e.g.
              ``{"a": 5}`` at path ``"a.b"``) returns ``MISSING`` rather than
              raising ``TypeError`` — claim shapes are attacker/issuer
              controlled and must never crash the parser.
        """
        current: Any = claims
        for segment in self.segments:
            if not isinstance(current, Mapping):
                return MISSING
            if segment not in current:
                return MISSING
            current = current[segment]
        return current


# ── Public helper ──────────────────────────────────────────────────────────────


def read_claim(
    claims: Mapping[str, Any],
    spec: str,
    *,
    default: Any = None,
    separator: str = ".",
) -> Any:
    """
    Parse ``spec`` and read it from ``claims`` in one call.

    Convenience wrapper for application code that wants nested-claim access
    without building a ``ClaimPath`` explicitly.

    Args:
        claims:    Claims dict to read.
        spec:      Dotted path spec (see ``ClaimPath.parse``).
        default:   Value returned when the path is missing.  Defaults to
                   ``None``.
        separator: Path separator — see ``ClaimPath.parse``.

    Returns:
        The claim value, or ``default`` if the path is missing.

    Raises:
        ValueError: ``spec`` is malformed — see ``ClaimPath.parse``.

    Example::

        tenant = read_claim(raw_claims, "org.id", default="unknown")
    """
    path = ClaimPath.parse(spec, separator=separator)
    value = path.read(claims)
    if value is MISSING:
        return default
    return value


__all__ = [
    "MISSING",
    "ClaimPath",
    "read_claim",
]
