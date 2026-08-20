"""
varco_core.tz.zones
======================
``validate_iana_zone`` — the shared "is this a real IANA zone name" gate
used by both the T1 resolution chain and (indirectly) T2's scheduling.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = ["validate_iana_zone"]


def validate_iana_zone(name: str | None) -> ZoneInfo | None:
    """
    Return a ``ZoneInfo`` for ``name``, or ``None`` if invalid.

    Args:
        name: An IANA zone name, e.g. ``"America/New_York"``.

    Returns:
        The resolved ``ZoneInfo`` (stdlib-cached after first use), or
        ``None`` for ``None``/empty/unrecognised names — never raises.
    """
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (KeyError, ZoneInfoNotFoundError, ValueError):
        return None
