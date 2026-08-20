"""
varco_core.i18n.negotiation
==============================
Hand-rolled RFC 4647 §3.4 Lookup — Plan 011 D-2. No standard Python library
implements Lookup; ``language_tags`` does BCP 47 validation, not matching,
and WebOb implements Basic Filtering (§3.3.1), not Lookup. ~60 lines,
tested against RFC 4647's own worked examples.
"""

from __future__ import annotations

__all__ = ["parse_accept_language", "negotiate_locale"]


def parse_accept_language(header: str) -> list[tuple[str, float]]:
    """
    Parse an ``Accept-Language`` header into ``(tag, q)`` pairs, sorted
    descending by ``q`` (stable — equal-``q`` entries keep header order).

    Args:
        header: The raw header value, e.g. ``"da, en-gb;q=0.8, en;q=0.7"``.

    Returns:
        ``[(tag, q), ...]``, descending by ``q``. ``q=0`` entries are
        excluded (explicitly rejected per RFC 9110 §12.5.4). Malformed
        ``q=`` values are tolerated — treated as ``q=1.0``.
    """
    if not header:
        return []
    parsed: list[tuple[str, float]] = []
    for raw_part in header.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if ";" in part:
            tag, _, param = part.partition(";")
            tag = tag.strip()
            q = 1.0
            param = param.strip()
            if param.startswith("q="):
                try:
                    q = float(param[2:].strip())
                except ValueError:
                    q = 1.0  # tolerate a malformed q= value
        else:
            tag = part
            q = 1.0
        if not tag:
            continue
        if q <= 0:
            continue
        parsed.append((tag, q))
    # Stable sort — Python's sort is guaranteed stable, so equal-q entries
    # keep their original (header) relative order.
    parsed.sort(key=lambda pair: pair[1], reverse=True)
    return parsed


def negotiate_locale(
    header: str | None, supported: "list[str] | tuple[str, ...]", *, default: str
) -> str | None:
    """
    RFC 4647 §3.4 Lookup over an ``Accept-Language`` header.

    For each candidate tag (highest ``q`` first), progressively truncate at
    ``-`` boundaries (``fr-CA-x-foo`` -> ``fr-CA`` -> ``fr``), skipping a
    truncation that would leave a single-character subtag, and return the
    first truncation present in ``supported``. ``*`` matches ``default``.

    Args:
        header: The raw ``Accept-Language`` header, or ``None``/empty.
        supported: The locales this deployment actually has content for.
        default: Used only for a ``*`` (wildcard) match.

    Returns:
        The negotiated locale tag, or ``None`` if nothing in ``header``
        matches ``supported`` — callers fall through to the next
        precedence step, **never** directly to ``"en"``.
    """
    if not header:
        return None

    supported_set = set(supported)
    for tag, _q in parse_accept_language(header):
        if tag == "*":
            return default
        candidate = tag
        while candidate:
            if candidate in supported_set:
                return candidate
            if "-" not in candidate:
                break
            candidate, _, last = candidate.rpartition("-")
            # RFC 4647 §3.4: skip a truncation ending in a single-character
            # subtag (it's a private-use/extension singleton, not a real
            # match boundary) — keep truncating past it.
            if len(last) == 1:
                continue
    return None
