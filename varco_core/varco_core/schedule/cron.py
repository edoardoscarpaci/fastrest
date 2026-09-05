"""
varco_core.schedule.cron
==========================
A hand-rolled, zero-dependency 5-field cron parser + next/previous
occurrence search (Plan 032 / D6, §D-D6-cron).

**Cron only — RRULE is out of scope.** A 5-field expression is a small,
well-understood grammar (~200 lines here); a complete RFC 5545 recurrence
engine means ``dateutil.rrule`` — a new runtime dependency for a 🟢 backlog
row, in a repo whose standing rule is zero new runtime dependencies in
``varco_core``. Parked with a trigger (see the plan's Parked table).

Fields, in order: ``minute hour day-of-month month day-of-week``. Each
field accepts ``*``, ``*/N``, ``a-b``, ``a-b/N``, a bare integer, or a
comma-separated list of any of those. When *both* day-of-month and
day-of-week are restricted (neither is a bare ``*``), a date matches if
*either* field matches — this is standard (if famously surprising) Unix
cron semantics, not a bug.

DESIGN: two-phase (date, then time-of-day) search over brute-force minute
stepping
    ✅ ``next_after``/``at_or_before`` are O(days-until-match) in the outer
       loop (bounded to ~4 years, a generous safety cap) plus a constant-time
       inner search over at most 1440 (hour, minute) pairs — a yearly cron
       resolves in a handful of iterations, not ~500,000 per-minute steps.
    ✅ Matches how every real cron implementation works (find the next valid
       calendar day, then the next valid time on it).
    ❌ More code than "just increment a minute in a loop". Accepted — the
       materializer's spring-forward/fall-back tests exercise exactly the
       yearly-cron-with-a-far-past-anchor case this optimization is for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

__all__ = ["CronSchedule", "parse_cron"]

_FIELD_BOUNDS: tuple[tuple[str, int, int], ...] = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day-of-month", 1, 31),
    ("month", 1, 12),
    ("day-of-week", 0, 6),
)

# Generous safety cap on the outer date-search loop — no valid 5-field cron
# combination should ever need more than a handful of years to find a match
# (the only way to loop this long is a self-contradictory expression like
# "day-of-month=31, month=2", which never matches — this bound turns that
# into a loud ValueError instead of an infinite loop).
_MAX_DAY_SEARCH = 4 * 366 + 10


def _parse_field(text: str, lo: int, hi: int, name: str) -> frozenset[int]:
    """
    Parse one cron field into the set of integers it matches.

    Args:
        text: The raw field text (e.g. ``"*/15"``, ``"9-17"``, ``"0,30"``).
        lo: Inclusive lower bound for this field.
        hi: Inclusive upper bound for this field.
        name: Human-readable field name, for error messages.

    Returns:
        The non-empty set of matching integers.

    Raises:
        ValueError: ``text`` is empty, malformed, or any resolved value
            falls outside ``[lo, hi]`` — rejected loudly, never coerced to a
            best guess.
    """
    if not text:
        raise ValueError(f"{name} field must not be empty")

    values: set[int] = set()
    for item in text.split(","):
        if item == "":
            raise ValueError(f"{name} field has an empty list element: {text!r}")

        step = 1
        base = item
        if "/" in item:
            base, step_text = item.split("/", 1)
            if not step_text.isdigit() or int(step_text) <= 0:
                raise ValueError(f"{name} field has an invalid step: {item!r}")
            step = int(step_text)

        if base == "*":
            range_lo, range_hi = lo, hi
        elif "-" in base:
            lo_text, _, hi_text = base.partition("-")
            if not (lo_text.isdigit() and hi_text.isdigit()):
                raise ValueError(f"{name} field has an invalid range: {item!r}")
            range_lo, range_hi = int(lo_text), int(hi_text)
            if range_lo > range_hi:
                raise ValueError(f"{name} field range is inverted: {item!r}")
        elif base.isdigit():
            range_lo = range_hi = int(base)
        else:
            raise ValueError(f"{name} field has an invalid value: {item!r}")

        if range_lo < lo or range_hi > hi:
            raise ValueError(f"{name} field value {item!r} is out of range [{lo}, {hi}]")
        values.update(range(range_lo, range_hi + 1, step))

    if not values:  # pragma: no cover - unreachable given the checks above
        raise ValueError(f"{name} field resolved to no values: {text!r}")
    return frozenset(values)


def _cron_dow(day: date) -> int:
    """Map a ``date`` to cron's day-of-week numbering (0=Sunday..6=Saturday)."""
    return (day.weekday() + 1) % 7


@dataclass(frozen=True)
class CronSchedule:
    """
    A parsed 5-field cron expression — immutable, hashable.

    Construct via ``parse_cron()``, never directly.

    Thread safety:  ✅ Frozen dataclass of frozensets — fully immutable.
    Async safety:   ✅ Pure computation, no I/O.
    """

    minute: frozenset[int]
    hour: frozenset[int]
    day: frozenset[int]
    month: frozenset[int]
    dow: frozenset[int]
    expr: str

    def _day_matches(self, day: date) -> bool:
        """
        ``True`` iff ``day`` matches this schedule's day-of-month/day-of-week
        fields, applying standard cron's OR-when-both-restricted rule.
        """
        dom_restricted = self.day != frozenset(range(1, 32))
        dow_restricted = self.dow != frozenset(range(0, 7))
        dom_match = day.day in self.day
        dow_match = _cron_dow(day) in self.dow
        if dom_restricted and dow_restricted:
            return dom_match or dow_match
        return dom_match and dow_match

    def _next_matching_date(self, start: date) -> date:
        """Return the first date ``>= start`` matching month/day/dow."""
        candidate = start
        for _ in range(_MAX_DAY_SEARCH):
            if candidate.month in self.month and self._day_matches(candidate):
                return candidate
            candidate += timedelta(days=1)
        raise ValueError(
            f"no matching date found for cron {self.expr!r} within "
            f"{_MAX_DAY_SEARCH} days — the expression may be self-contradictory "
            "(e.g. day-of-month=31 with month=2)"
        )

    def _previous_matching_date(self, start: date) -> date:
        """Return the last date ``<= start`` matching month/day/dow."""
        candidate = start
        for _ in range(_MAX_DAY_SEARCH):
            if candidate.month in self.month and self._day_matches(candidate):
                return candidate
            candidate -= timedelta(days=1)
        raise ValueError(
            f"no matching date found for cron {self.expr!r} within "
            f"{_MAX_DAY_SEARCH} days before {start!r}"
        )

    def next_after(self, after: datetime) -> datetime:
        """
        Return the earliest occurrence strictly after ``after``.

        Args:
            after: A naive ``datetime`` (seconds/microseconds ignored).

        Returns:
            The next matching naive ``datetime``, minute precision.

        Raises:
            ValueError: No matching date exists within the search bound.
        """
        cursor_minute = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        date_cursor = cursor_minute.date()
        floor: tuple[int, int] | None = (cursor_minute.hour, cursor_minute.minute)

        for _ in range(2):  # at most: same-day-with-floor, then any-day-without
            matched_date = self._next_matching_date(date_cursor)
            use_floor = floor if matched_date == date_cursor else None
            time_of_day = self._earliest_time_on_or_after(use_floor)
            if time_of_day is not None:
                hour, minute = time_of_day
                return datetime(
                    matched_date.year, matched_date.month, matched_date.day, hour, minute
                )
            date_cursor = matched_date + timedelta(days=1)
            floor = None
        raise ValueError(f"no matching time found for cron {self.expr!r} after {after!r}")

    def at_or_before(self, before: datetime) -> datetime | None:
        """
        Return the latest occurrence at-or-before ``before``.

        Args:
            before: A naive ``datetime`` (seconds/microseconds ignored).
                Inclusive — an occurrence exactly at this minute matches.

        Returns:
            The latest matching naive ``datetime``, or ``None`` if no
            occurrence exists within the search bound (a self-contradictory
            expression).
        """
        cursor_minute = before.replace(second=0, microsecond=0)
        date_cursor = cursor_minute.date()
        ceiling: tuple[int, int] | None = (cursor_minute.hour, cursor_minute.minute)

        for _ in range(2):
            try:
                matched_date = self._previous_matching_date(date_cursor)
            except ValueError:
                return None
            use_ceiling = ceiling if matched_date == date_cursor else None
            time_of_day = self._latest_time_on_or_before(use_ceiling)
            if time_of_day is not None:
                hour, minute = time_of_day
                return datetime(
                    matched_date.year, matched_date.month, matched_date.day, hour, minute
                )
            date_cursor = matched_date - timedelta(days=1)
            ceiling = None
        return None

    def _earliest_time_on_or_after(self, floor: tuple[int, int] | None) -> tuple[int, int] | None:
        """Earliest ``(hour, minute)`` matching this schedule, ``>= floor``."""
        hours = sorted(self.hour)
        minutes = sorted(self.minute)
        if floor is None:
            return (hours[0], minutes[0])
        floor_hour, floor_minute = floor
        for hour in hours:
            if hour < floor_hour:
                continue
            for minute in minutes:
                if hour == floor_hour and minute < floor_minute:
                    continue
                return (hour, minute)
        return None

    def _latest_time_on_or_before(self, ceiling: tuple[int, int] | None) -> tuple[int, int] | None:
        """Latest ``(hour, minute)`` matching this schedule, ``<= ceiling``."""
        hours = sorted(self.hour, reverse=True)
        minutes = sorted(self.minute, reverse=True)
        if ceiling is None:
            return (hours[0], minutes[0])
        ceiling_hour, ceiling_minute = ceiling
        for hour in hours:
            if hour > ceiling_hour:
                continue
            for minute in minutes:
                if hour == ceiling_hour and minute > ceiling_minute:
                    continue
                return (hour, minute)
        return None


def parse_cron(expr: str) -> CronSchedule:
    """
    Parse a 5-field cron expression.

    Args:
        expr: ``"minute hour day-of-month month day-of-week"``, whitespace
            separated.

    Returns:
        A ``CronSchedule`` ready for ``next_after``/``at_or_before``.

    Raises:
        ValueError: ``expr`` does not have exactly 5 whitespace-separated
            fields, or any field is malformed/out of range. Never a silent
            best-effort guess.

    Example::

        schedule = parse_cron("0 9-17 * * 1-5")  # hourly, business hours, weekdays
    """
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"cron expression must have exactly 5 fields, got {len(fields)}: {expr!r}")

    minute, hour, day, month, dow = (
        _parse_field(field_text, lo, hi, name)
        for field_text, (name, lo, hi) in zip(fields, _FIELD_BOUNDS, strict=True)
    )
    return CronSchedule(minute=minute, hour=hour, day=day, month=month, dow=dow, expr=expr)
