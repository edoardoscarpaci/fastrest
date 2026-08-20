"""
varco_core.tz
================
T1 (per-request timezone resolution) + T2 (DST-safe scheduling) +
D-9 (RFC 9557 emitter). Off by default: ``TimezoneSettings.enabled = False``.
"""

from __future__ import annotations

from varco_core.tz.format import format_rfc9557
from varco_core.tz.resolve import now_local, resolve_timezone, to_user_tz
from varco_core.tz.schedule import (
    GapPolicy,
    OverlapPolicy,
    ScheduleGapError,
    datetime_ambiguous,
    datetime_exists,
    resolve_zoned,
)
from varco_core.tz.settings import TimezoneSettings
from varco_core.tz.zones import validate_iana_zone

__all__ = [
    "validate_iana_zone",
    "TimezoneSettings",
    "resolve_timezone",
    "to_user_tz",
    "now_local",
    "GapPolicy",
    "OverlapPolicy",
    "ScheduleGapError",
    "datetime_exists",
    "datetime_ambiguous",
    "resolve_zoned",
    "format_rfc9557",
]
