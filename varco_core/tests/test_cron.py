"""
Unit tests for varco_core.schedule.cron (Plan 032 / D6).

A hand-rolled, zero-dependency 5-field cron parser + next_after(). Covers
ranges/steps/lists per-field, invalid-expression rejection (loud, not a
best-effort guess), and next_after() correctness for representative cases.
"""

from __future__ import annotations

from datetime import datetime

import pytest


def _parse_cron():
    from varco_core.schedule.cron import parse_cron  # noqa: PLC0415

    return parse_cron


class TestCronFieldSyntax:
    def test_wildcard_every_field_matches_any_time(self) -> None:
        parse_cron = _parse_cron()
        schedule = parse_cron("* * * * *")
        nxt = schedule.next_after(datetime(2026, 1, 1, 0, 0))
        assert nxt == datetime(2026, 1, 1, 0, 1)

    def test_step_expression_every_fifteen_minutes(self) -> None:
        parse_cron = _parse_cron()
        schedule = parse_cron("*/15 * * * *")
        nxt = schedule.next_after(datetime(2026, 1, 1, 0, 1))
        assert nxt == datetime(2026, 1, 1, 0, 15)

    def test_range_expression_restricts_hours(self) -> None:
        parse_cron = _parse_cron()
        # Only fire once an hour, between 09:00 and 17:00 inclusive.
        schedule = parse_cron("0 9-17 * * *")
        nxt = schedule.next_after(datetime(2026, 1, 1, 17, 30))
        assert nxt == datetime(2026, 1, 2, 9, 0)

    def test_list_expression_matches_explicit_values(self) -> None:
        parse_cron = _parse_cron()
        schedule = parse_cron("0,30 * * * *")
        nxt = schedule.next_after(datetime(2026, 1, 1, 0, 5))
        assert nxt == datetime(2026, 1, 1, 0, 30)

    def test_day_of_week_field_restricts_to_weekday(self) -> None:
        parse_cron = _parse_cron()
        # 2026-01-05 is a Monday; run at 08:00 on Mondays only (dow=1).
        schedule = parse_cron("0 8 * * 1")
        nxt = schedule.next_after(datetime(2026, 1, 1, 0, 0))
        assert nxt == datetime(2026, 1, 5, 8, 0)


class TestCronInvalidExpressions:
    @pytest.mark.parametrize(
        "expr",
        [
            "",
            "* * * *",  # too few fields
            "* * * * * *",  # too many fields
            "60 * * * *",  # minute out of range
            "* 24 * * *",  # hour out of range
            "* * 0 * *",  # day-of-month out of range (1-31)
            "* * * 13 *",  # month out of range (1-12)
            "* * * * 7",  # dow out of range (0-6)
            "not-a-cron-expression",
        ],
    )
    def test_invalid_expression_rejected_loudly(self, expr: str) -> None:
        # An unparseable/out-of-range expression must raise, never silently
        # coerce to a best guess.
        parse_cron = _parse_cron()
        with pytest.raises(ValueError):
            parse_cron(expr)
