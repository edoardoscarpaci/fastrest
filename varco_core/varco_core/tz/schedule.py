"""
varco_core.tz.schedule
=========================
DST gap/overlap detection and resolution — Plan 011 D-8.

**No ``dateutil`` dependency.** A time is *ambiguous* iff
``utcoffset(fold=0) != utcoffset(fold=1)``; a time is *nonexistent* iff
round-tripping it through UTC and back does not reproduce it — both ~8
lines over stdlib ``zoneinfo``, per D-8.

**Overlap (fall back): run once at ``fold=0``** (``OverlapPolicy.FIRST``,
adopted verbatim from brief 004's Librarian's note — "Store with fold=0
(default): use the first occurrence"). Contrast Quartz, which fires both.

**Gap (spring forward): default ``NEXT_VALID``, deviating from brief 004**,
which recommends "skip" — correct for a *recurring* occurrence but wrong
for T2's one-shot ``Job`` (D-7): "skip" would mean the job is never
executed and never fails — silent data loss, the exact class of defect
``OutboxRelay(max_attempts=…)`` refuses to construct without a ``dlq=`` to
avoid. ``NEXT_VALID`` rolls forward to the first valid instant after the
gap and logs one WARNING; ``PREVIOUS_VALID`` rolls backward;
``GapPolicy.SKIP`` transitions the job to a terminal state with a named
``ScheduleGapError`` — skipping is allowed, but never silently;
``GapPolicy.ERROR`` refuses at enqueue time.
"""

from __future__ import annotations

import enum
import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

__all__ = [
    "GapPolicy",
    "OverlapPolicy",
    "ScheduleGapError",
    "datetime_exists",
    "datetime_ambiguous",
    "resolve_zoned",
]

# Bound on the linear gap-search — no real IANA DST gap is anywhere close
# to a day wide; this is a generous safety cap, not a tuned constant.
_MAX_GAP_SEARCH_MINUTES = 24 * 60


class GapPolicy(enum.Enum):
    """How to resolve a wall-clock time that does not exist (spring-forward gap)."""

    #: Roll forward to the first valid instant after the gap (default — D-8).
    NEXT_VALID = "next_valid"
    #: Roll backward to the last valid instant before the gap.
    PREVIOUS_VALID = "previous_valid"
    #: Never execute the job for this occurrence — but never silently: the
    #: caller must transition the job to a terminal state with
    #: ``ScheduleGapError``.
    SKIP = "skip"
    #: Refuse at enqueue time.
    ERROR = "error"


class OverlapPolicy(enum.Enum):
    """How to resolve a wall-clock time that occurs twice (fall-back overlap)."""

    #: Use the first (pre-transition) occurrence — fold=0. Default (D-8).
    FIRST = "first"
    #: Use the second (post-transition) occurrence — fold=1.
    LAST = "last"


class ScheduleGapError(Exception):
    """Raised when a wall-clock time falls in a DST gap and the policy is
    ``GapPolicy.SKIP`` or ``GapPolicy.ERROR``."""


def datetime_exists(wall: datetime, zone: "ZoneInfo") -> bool:
    """
    ``True`` iff ``wall`` (naive) is a real wall-clock instant in ``zone``.

    A nonexistent time (spring-forward gap) does not round-trip through
    UTC and back to itself.
    """
    aware = wall.replace(tzinfo=zone)
    back = aware.astimezone(dt_timezone.utc).astimezone(zone).replace(tzinfo=None)
    return back == wall


def datetime_ambiguous(wall: datetime, zone: "ZoneInfo") -> bool:
    """``True`` iff ``wall`` (naive) occurs twice in ``zone`` (fall-back overlap)."""
    offset0 = wall.replace(tzinfo=zone, fold=0).utcoffset()
    offset1 = wall.replace(tzinfo=zone, fold=1).utcoffset()
    return offset0 != offset1


def _search_valid(wall: datetime, zone: "ZoneInfo", *, forward: bool) -> datetime:
    step = timedelta(minutes=1) if forward else -timedelta(minutes=1)
    candidate = wall
    for _ in range(_MAX_GAP_SEARCH_MINUTES):
        candidate += step
        if datetime_exists(candidate, zone):
            return candidate
    raise ScheduleGapError(
        f"No valid wall-clock time found within {_MAX_GAP_SEARCH_MINUTES} "
        f"minutes of {wall!r} in zone {zone!r} — this indicates a corrupt "
        "tzdata database, not a normal DST gap."
    )


def resolve_zoned(
    wall: datetime,
    zone: "ZoneInfo",
    *,
    fold: int = 0,
    gap: GapPolicy = GapPolicy.NEXT_VALID,
    overlap: OverlapPolicy = OverlapPolicy.FIRST,
) -> datetime:
    """
    Materialize a naive wall-clock time + IANA zone into an aware UTC instant.

    Args:
        wall: Naive local wall-clock time.
        zone: The IANA zone to interpret ``wall`` in.
        fold: PEP 495 fold — used verbatim only for an *unambiguous* time
            (irrelevant to the result); for an *ambiguous* time, ``overlap``
            determines the effective fold instead.
        gap: Policy for a nonexistent (spring-forward) wall time.
        overlap: Policy for an ambiguous (fall-back) wall time.

    Returns:
        An aware ``datetime`` in ``zone`` (the resolved fold/gap already
        applied) — callers convert to UTC themselves
        (``resolve_zoned(...).astimezone(timezone.utc)``) to materialize
        ``Job.run_at``.

    Raises:
        ScheduleGapError: ``wall`` falls in a DST gap and ``gap`` is
            ``SKIP`` or ``ERROR``.
    """
    if not datetime_exists(wall, zone):
        if gap in (GapPolicy.SKIP, GapPolicy.ERROR):
            raise ScheduleGapError(
                f"{wall!r} does not exist in zone {zone!r} (DST spring-forward "
                f"gap) and GapPolicy={gap.value!r} refuses to resolve it."
            )
        forward = gap != GapPolicy.PREVIOUS_VALID
        resolved_wall = _search_valid(wall, zone, forward=forward)
        logger.warning(
            "resolve_zoned: %r does not exist in zone %s (DST gap); "
            "resolved to %r via GapPolicy.%s",
            wall,
            zone,
            resolved_wall,
            gap.name,
        )
        return resolved_wall.replace(tzinfo=zone)

    effective_fold = fold
    if datetime_ambiguous(wall, zone):
        effective_fold = 1 if overlap == OverlapPolicy.LAST else 0

    return wall.replace(tzinfo=zone, fold=effective_fold)
