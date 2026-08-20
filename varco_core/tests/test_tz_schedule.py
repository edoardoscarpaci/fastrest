"""
Red-mode tests for Plan 011 Phase 4, step 47 — varco_core.tz.schedule
(resolve_zoned, datetime_exists/datetime_ambiguous, GapPolicy, OverlapPolicy).

D-8: "No dateutil dependency ... Overlap (fall back): run once at fold=0 ...
Gap (spring forward): default NEXT_VALID, not SKIP."
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from varco_core.tz.schedule import (
    GapPolicy,
    OverlapPolicy,
    ScheduleGapError,
    datetime_ambiguous,
    datetime_exists,
    resolve_zoned,
)

LA = ZoneInfo("America/Los_Angeles")


def test_datetime_exists_true_for_ordinary_time() -> None:
    assert datetime_exists(datetime(2026, 6, 1, 12, 0), LA) is True


def test_datetime_exists_false_in_spring_forward_gap() -> None:
    # 2026-03-08 02:30 America/Los_Angeles is the brief 004 worked-example gap.
    assert datetime_exists(datetime(2026, 3, 8, 2, 30), LA) is False


def test_datetime_ambiguous_true_in_fall_back_overlap() -> None:
    # 2026-11-01 01:30 America/Los_Angeles is the brief 004 worked-example overlap.
    assert datetime_ambiguous(datetime(2026, 11, 1, 1, 30), LA) is True


def test_datetime_ambiguous_false_for_ordinary_time() -> None:
    assert datetime_ambiguous(datetime(2026, 6, 1, 12, 0), LA) is False


def test_resolve_zoned_gap_default_policy_rolls_forward_to_next_valid() -> None:
    resolved = resolve_zoned(datetime(2026, 3, 8, 2, 30), LA)
    assert resolved.astimezone(LA).replace(tzinfo=None) == datetime(2026, 3, 8, 3, 0)


def test_resolve_zoned_gap_previous_valid_rolls_backward() -> None:
    # D-8 names PREVIOUS_VALID but not its exact boundary value; the plan
    # only pins down NEXT_VALID's worked example (03:00 local). Asserting
    # only the documented invariant here: the resolved wall time must be
    # BEFORE the gap start (02:00 local), not after it.
    resolved = resolve_zoned(
        datetime(2026, 3, 8, 2, 30), LA, gap=GapPolicy.PREVIOUS_VALID
    )
    local = resolved.astimezone(LA).replace(tzinfo=None)
    assert local < datetime(2026, 3, 8, 2, 0)


def test_resolve_zoned_gap_skip_raises_schedule_gap_error() -> None:
    with pytest.raises(ScheduleGapError):
        resolve_zoned(datetime(2026, 3, 8, 2, 30), LA, gap=GapPolicy.SKIP)


def test_resolve_zoned_gap_error_policy_refuses_at_enqueue_time() -> None:
    with pytest.raises(ScheduleGapError):
        resolve_zoned(datetime(2026, 3, 8, 2, 30), LA, gap=GapPolicy.ERROR)


def test_resolve_zoned_overlap_default_fold_zero_first_occurrence() -> None:
    resolved = resolve_zoned(datetime(2026, 11, 1, 1, 30), LA, fold=0)
    # First occurrence — PDT (UTC-7) offset.
    assert resolved.utcoffset().total_seconds() == -7 * 3600


def test_resolve_zoned_overlap_last_uses_fold_one() -> None:
    resolved = resolve_zoned(
        datetime(2026, 11, 1, 1, 30), LA, overlap=OverlapPolicy.LAST
    )
    assert resolved.utcoffset().total_seconds() == -8 * 3600


def test_resolve_zoned_normal_time_unaffected() -> None:
    resolved = resolve_zoned(datetime(2026, 6, 1, 12, 0), LA)
    assert resolved.astimezone(LA).replace(tzinfo=None) == datetime(2026, 6, 1, 12, 0)


def test_resolve_zoned_zone_with_no_dst_is_unaffected() -> None:
    utc = ZoneInfo("UTC")
    resolved = resolve_zoned(datetime(2026, 3, 8, 2, 30), utc)
    assert resolved.astimezone(utc).replace(tzinfo=None) == datetime(2026, 3, 8, 2, 30)


def test_resolve_zoned_southern_hemisphere_zone() -> None:
    sydney = ZoneInfo("Australia/Sydney")
    # Ordinary, unambiguous time — should simply materialize.
    resolved = resolve_zoned(datetime(2026, 6, 1, 12, 0), sydney)
    assert resolved.astimezone(sydney).replace(tzinfo=None) == datetime(
        2026, 6, 1, 12, 0
    )
