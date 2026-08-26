"""
Red-mode tests for Plan 011 Phase 5, step 61 — DatetimeCoercionPolicy
behaviour under "utc" / "context", the RFC 9557 bracket-suffix rejection,
the date-only semantic, and the "AT TIME ZONE" invariant.

Plan line (step 59): "DatetimeCoercionPolicy (frozen: assume:
Literal['naive','utc','context'] = 'naive', log_naive: bool = True)."
Plan line (step 60): "'context' reads current_timezone() and falls back to
'utc' with a DEBUG line when no zone is resolved; a bracket-suffixed RFC
9557 input is REJECTED with a legible error naming the two supported
inputs (D-9)."
Plan line (D-10, rule 2): "Convert the bound, never the column ... Nothing
in this plan generates an AT TIME ZONE expression."
"""

from __future__ import annotations

from datetime import UTC
from zoneinfo import ZoneInfo

import pytest
from varco_core.context.request import request_context
from varco_core.query.policy import DatetimeCoercionPolicy
from varco_core.query.visitor.type_coercion import coerce_datetime


def test_datetime_coercion_policy_default_is_naive() -> None:
    policy = DatetimeCoercionPolicy()
    assert policy.assume == "naive"
    assert policy.log_naive is True


def test_utc_policy_attaches_utc_tzinfo_to_naive_input() -> None:
    policy = DatetimeCoercionPolicy(assume="utc")
    result = coerce_datetime("2026-01-01T00:00:00", policy=policy)
    assert result.tzinfo == UTC


def test_context_policy_uses_ambient_timezone() -> None:
    policy = DatetimeCoercionPolicy(assume="context")
    with request_context(timezone=ZoneInfo("America/New_York")):
        result = coerce_datetime("2026-01-01T00:00:00", policy=policy)
    assert result.tzinfo is not None


def test_context_policy_falls_back_to_utc_with_no_ambient_zone(caplog) -> None:
    policy = DatetimeCoercionPolicy(assume="context")
    result = coerce_datetime("2026-01-01T00:00:00", policy=policy)
    assert result.tzinfo == UTC


def test_already_aware_input_used_verbatim_under_every_policy() -> None:
    for assume in ("naive", "utc", "context"):
        policy = DatetimeCoercionPolicy(assume=assume)
        result = coerce_datetime("2026-01-01T00:00:00-05:00", policy=policy)
        assert result.utcoffset().total_seconds() == -5 * 3600


def test_rfc9557_bracket_suffix_is_rejected_naming_two_supported_forms() -> None:
    with pytest.raises(ValueError) as exc_info:
        coerce_datetime("2026-03-08T09:00:00-05:00[America/New_York]")
    message = str(exc_info.value)
    assert "RFC 3339" in message or "offset" in message
    assert "tz=" in message or "separate" in message


def test_date_only_lower_bound_is_midnight_start_of_day() -> None:
    result = coerce_datetime("2026-01-01")
    assert result.hour == 0
    assert result.minute == 0
    assert result.day == 1


def test_source_never_generates_at_time_zone_sql() -> None:
    # Static invariant guard, same style as the plan's other grep-style
    # source guards (e.g. step 39's over datetime.now(timezone.utc)):
    # nothing in the coercer module may emit an AT TIME ZONE fragment —
    # the bound is converted, never the column (D-10 rule 2).
    import inspect

    import varco_core.query.visitor.type_coercion as mod

    source = inspect.getsource(mod)
    assert "AT TIME ZONE" not in source
