"""
Red-mode tests for Plan 011 Phase 0, step 4 —
``varco_core.context.precedence.resolve_precedence`` / ``Resolved``.

Encodes: first non-None candidate wins and reports its source; all-None and
empty sequences return None; a falsy-but-not-None value ("", 0) IS selected
(the `or`-chain bug this function exists to avoid).
"""

from __future__ import annotations

from varco_core.context.precedence import Resolved, resolve_precedence


def test_first_non_none_candidate_wins_and_reports_source() -> None:
    result = resolve_precedence(
        [
            ("query_param", None),
            ("user_profile", "fr"),
            ("fallback", "en"),
        ]
    )
    assert result == Resolved(value="fr", source="user_profile")


def test_all_none_candidates_return_none() -> None:
    result = resolve_precedence(
        [
            ("query_param", None),
            ("user_profile", None),
        ]
    )
    assert result is None


def test_empty_candidate_sequence_returns_none() -> None:
    assert resolve_precedence([]) is None


def test_falsy_but_not_none_value_is_selected() -> None:
    # The whole reason this helper exists rather than an `or`-chain: "" and 0
    # are legitimate resolved values, not "absent".
    result = resolve_precedence([("query_param", ""), ("fallback", "en")])
    assert result == Resolved(value="", source="query_param")


def test_falsy_zero_value_is_selected_over_later_candidates() -> None:
    result = resolve_precedence([("header", 0), ("fallback", 42)])
    assert result == Resolved(value=0, source="header")


def test_earlier_candidate_order_takes_precedence_over_later_non_none() -> None:
    result = resolve_precedence(
        [
            ("first", "a"),
            ("second", "b"),
        ]
    )
    assert result.source == "first"
    assert result.value == "a"


def test_resolved_is_frozen_dataclass() -> None:
    import dataclasses

    resolved = Resolved(value="x", source="s")
    assert dataclasses.is_dataclass(resolved)
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        resolved.value = "y"  # type: ignore[misc]
