"""
varco_core.tz.format
=======================
``format_rfc9557`` — an RFC 9557 (IXDTF) *output* helper only (Plan 011 D-9).

**No parser ships.** Brief 004 §A4 + Evidence Gap 1: no production-ready
Python RFC 9557 parser exists. Storage is the three columns of D-7,
independent of this wire format; a parser landing later is an additive
branch in the query-layer coercer (T3), never a storage change.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo

__all__ = ["format_rfc9557"]


def format_rfc9557(instant: datetime, zone: ZoneInfo) -> str:
    """
    Format ``instant`` (converted to ``zone``) as RFC 9557, e.g.
    ``"2026-03-08T09:00:00-05:00[America/New_York]"``.

    Args:
        instant: An aware ``datetime`` (any timezone).
        zone: The IANA zone to render the bracket suffix for.

    Returns:
        The RFC 9557 string — ISO 8601 with numeric offset, plus a
        bracketed IANA zone name.
    """
    local = instant.astimezone(zone)
    return f"{local.isoformat()}[{zone.key}]"
