"""
Red-mode tests for Plan 011 Phase 4, step 49 — D-9's RFC 9557 emitter.

Plan line (step 49): "format_rfc9557(instant, zone) -> str emitting
'2026-03-08T09:00:00-05:00[America/New_York]'. No parser."
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from varco_core.tz.format import format_rfc9557


def test_format_rfc9557_emits_offset_and_bracketed_zone() -> None:
    # DEVIATION from the plan's literal D-9 worked example: brief 004's
    # illustrative string ("2026-03-08T09:00:00-05:00[...]") was paired
    # with a UTC source instant that, against REAL 2026 US tzdata, falls
    # AFTER the March 8 02:00 local DST transition (2026-03-08 is the
    # second Sunday in March) — Python's zoneinfo correctly reports -04:00
    # (EDT) at 14:00 UTC that day, not -05:00 (EST). Moved the fixture's
    # source instant to March 1 (before the transition) so the assertion
    # reflects real tzdata instead of an internally-inconsistent example;
    # the offset/bracket SHAPE the plan documents is unchanged and still
    # exercised end-to-end.
    instant = datetime(2026, 3, 1, 14, 0, tzinfo=UTC)
    zone = ZoneInfo("America/New_York")
    result = format_rfc9557(instant, zone)
    assert result == "2026-03-01T09:00:00-05:00[America/New_York]"


def test_format_rfc9557_no_parser_is_exposed() -> None:
    import varco_core.tz.format as fmt_module

    assert not hasattr(fmt_module, "parse_rfc9557")
