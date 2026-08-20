"""
Red-mode tests for Plan 011 Phase 5, step 58 — RD-1's T3 proof.

Plan line (step 58): "With no policy, coerce_datetime() returns exactly
what datetime.fromisoformat(value) returns — naive stays naive, tzinfo is
None — for the full existing input table; an already-aware input (...Z,
...-05:00) is returned verbatim under EVERY policy."
"""

from __future__ import annotations

from datetime import datetime

import pytest
from varco_core.query.visitor.type_coercion import coerce_datetime


@pytest.mark.parametrize(
    "raw",
    [
        "2026-01-01",
        "2026-01-01T00:00:00",
        "2026-06-15T14:30:00",
    ],
)
def test_naive_input_stays_naive_with_no_policy(raw: str) -> None:
    result = coerce_datetime(raw)
    assert result == datetime.fromisoformat(raw)
    assert result.tzinfo is None


@pytest.mark.parametrize(
    "raw",
    [
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:00:00-05:00",
        "2026-01-01T00:00:00+02:00",
    ],
)
def test_already_aware_input_returned_verbatim_with_no_policy(raw: str) -> None:
    result = coerce_datetime(raw)
    assert result.tzinfo is not None


def test_coerce_datetime_signature_accepts_policy_kwarg_defaulted_none() -> None:
    # Byte-identical default path: policy=None must exist and change nothing.
    result = coerce_datetime("2026-01-01T00:00:00", policy=None)
    assert result.tzinfo is None
