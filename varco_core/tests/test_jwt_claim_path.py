"""
Unit tests for varco_core.jwt.transform.path — ClaimPath + read_claim().

Covers (Plan 002, Phase 0, step 1):
    - ClaimPath.parse() dotted-path parsing, escaped-dot literal, custom separator.
    - ClaimPath.parse() error cases (empty spec, double-separator).
    - ClaimPath.read() hit / missing-top / missing-nested / non-mapping-intermediate.
    - read_claim() public helper with a default.

These tests are written RED — ``varco_core.jwt.transform.path`` does not exist yet.
They must fail with ImportError until Phase 0 step 2 lands.
"""

from __future__ import annotations

import pytest

# ── ClaimPath.parse ────────────────────────────────────────────────────────────


class TestClaimPathParse:
    def test_parse_single_segment(self):
        # Simplest case — no separator present at all.
        from varco_core.jwt.transform.path import ClaimPath

        assert ClaimPath.parse("roles").segments == ("roles",)

    def test_parse_nested_segments(self):
        from varco_core.jwt.transform.path import ClaimPath

        assert ClaimPath.parse("realm_access.roles").segments == (
            "realm_access",
            "roles",
        )

    def test_parse_escaped_dot_is_literal(self):
        # "\." escapes a literal dot inside a single segment name.
        from varco_core.jwt.transform.path import ClaimPath

        assert ClaimPath.parse(r"a\.b").segments == ("a.b",)

    def test_parse_mixed_escaped_and_unescaped_dots(self):
        from varco_core.jwt.transform.path import ClaimPath

        assert ClaimPath.parse(r"a.b\.c.d").segments == ("a", "b.c", "d")

    def test_parse_custom_separator(self):
        from varco_core.jwt.transform.path import ClaimPath

        assert ClaimPath.parse("a:b:c", separator=":").segments == ("a", "b", "c")

    def test_parse_empty_spec_raises_value_error(self):
        from varco_core.jwt.transform.path import ClaimPath

        with pytest.raises(ValueError, match=r""):
            ClaimPath.parse("")

    def test_parse_double_separator_raises_value_error_naming_spec(self):
        # "a..b" — an empty intermediate segment is a config error, not a
        # legitimate "missing" path — fail fast at parse time.
        from varco_core.jwt.transform.path import ClaimPath

        with pytest.raises(ValueError, match="a..b"):
            ClaimPath.parse("a..b")


# ── ClaimPath.read ─────────────────────────────────────────────────────────────


class TestClaimPathRead:
    def test_read_single_segment_hit(self):
        from varco_core.jwt.transform.path import ClaimPath

        path = ClaimPath.parse("roles")
        assert path.read({"roles": ["editor"]}) == ["editor"]

    def test_read_nested_hit(self):
        from varco_core.jwt.transform.path import ClaimPath

        path = ClaimPath.parse("realm_access.roles")
        claims = {"realm_access": {"roles": ["editor", "viewer"]}}
        assert path.read(claims) == ["editor", "viewer"]

    def test_read_missing_top_key_is_missing(self):
        from varco_core.jwt.transform.path import MISSING, ClaimPath

        path = ClaimPath.parse("roles")
        assert path.read({}) is MISSING

    def test_read_missing_nested_key_is_missing(self):
        from varco_core.jwt.transform.path import MISSING, ClaimPath

        path = ClaimPath.parse("realm_access.roles")
        assert path.read({"realm_access": {}}) is MISSING

    def test_read_intermediate_not_a_mapping_is_missing_not_typeerror(self):
        # {"a": 5} at path "a.b" — reading through a scalar must return
        # MISSING, never raise TypeError.
        from varco_core.jwt.transform.path import MISSING, ClaimPath

        path = ClaimPath.parse("a.b")
        assert path.read({"a": 5}) is MISSING


# ── read_claim() public helper ─────────────────────────────────────────────────


class TestReadClaimHelper:
    def test_read_claim_hit(self):
        from varco_core.jwt.transform.path import read_claim

        assert read_claim({"a": {"b": 1}}, "a.b") == 1

    def test_read_claim_missing_returns_default(self):
        from varco_core.jwt.transform.path import read_claim

        sentinel = object()
        assert read_claim({}, "a.b", default=sentinel) is sentinel

    def test_read_claim_missing_default_is_none(self):
        from varco_core.jwt.transform.path import read_claim

        assert read_claim({}, "a.b") is None
