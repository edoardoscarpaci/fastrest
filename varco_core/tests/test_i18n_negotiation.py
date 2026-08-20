"""
Red-mode tests for Plan 011 Phase 2, step 27 — D-2's RFC 4647 Lookup.

Plan line (step 25): "parse_accept_language(header) ... and
negotiate_locale(header, supported, *, default) implementing RFC 4647 §3.4
Lookup (progressive truncation at '-', skipping single-character subtags;
'*' -> default; empty/absent -> None, not 'en')."
"""

from __future__ import annotations

from varco_core.i18n.negotiation import negotiate_locale, parse_accept_language


def test_parse_accept_language_splits_and_extracts_q() -> None:
    result = parse_accept_language("da, en-gb;q=0.8, en;q=0.7")
    tags = [tag for tag, _ in result]
    assert tags == ["da", "en-gb", "en"]


def test_parse_accept_language_default_q_is_one() -> None:
    result = dict(parse_accept_language("fr"))
    assert result["fr"] == 1.0


def test_parse_accept_language_sorts_descending_by_q_stably() -> None:
    result = parse_accept_language("en;q=0.5, fr;q=0.9, de;q=0.9")
    tags = [tag for tag, _ in result]
    # fr and de tie at q=0.9 — stable sort keeps their relative header order.
    assert tags == ["fr", "de", "en"]


def test_parse_accept_language_excludes_q_zero() -> None:
    result = parse_accept_language("fr;q=0, en;q=0.5")
    tags = [tag for tag, _ in result]
    assert "fr" not in tags
    assert "en" in tags


def test_parse_accept_language_tolerates_malformed_q_value() -> None:
    # "invalid-q tolerance" per D-2.
    result = parse_accept_language("fr;q=bogus")
    assert result  # did not raise


def test_negotiate_locale_progressive_truncation_at_dash() -> None:
    # RFC 4647 §3.4 worked example: fr-CA falls back to fr.
    result = negotiate_locale("fr-CA", supported=["fr", "en"], default="en")
    assert result == "fr"


def test_negotiate_locale_skips_single_character_subtags() -> None:
    # zh-Hant-TW should not match on the single-char boundary incorrectly.
    result = negotiate_locale("zh-Hant-TW", supported=["zh-Hant", "en"], default="en")
    assert result == "zh-Hant"


def test_negotiate_locale_wildcard_matches_default() -> None:
    result = negotiate_locale("*", supported=["fr", "en"], default="en")
    assert result == "en"


def test_negotiate_locale_empty_header_returns_none_not_default() -> None:
    # "empty/absent -> None, not 'en' directly" — the caller falls through to
    # the next precedence step rather than negotiate_locale itself deciding.
    assert negotiate_locale("", supported=["fr", "en"], default="en") is None


def test_negotiate_locale_absent_header_returns_none() -> None:
    assert negotiate_locale(None, supported=["fr", "en"], default="en") is None


def test_negotiate_locale_no_match_falls_through_to_none() -> None:
    result = negotiate_locale("ja", supported=["fr", "en"], default="en")
    assert result is None
