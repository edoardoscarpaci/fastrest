"""
Red-mode tests for Plan 011 Phase 1, step 10 — the anti-rename guard for
FastrestErrorCodes.

Encodes D-5: `code` string values are NOT renamed; every member gains a
`varco.error.*`-prefixed message_key; `VarcoErrorCodes` is a bare alias
(identical object) to `FastrestErrorCodes`.
"""

from __future__ import annotations

from varco_core.exception.codes import ErrorCode, FastrestErrorCodes

# Frozen literal table — the codes must never change.
EXPECTED_CODES = {
    "NOT_FOUND": "FASTREST_001",
    "UNAUTHORIZED": "FASTREST_002",
    "CONFLICT": "FASTREST_003",
    "VALIDATION_ERROR": "FASTREST_004",
    "INTERNAL_ERROR": "FASTREST_500",
}


def test_every_member_code_string_is_unchanged() -> None:
    for name, expected_code in EXPECTED_CODES.items():
        member = FastrestErrorCodes[name]
        assert member.code == expected_code


def test_every_member_has_a_non_none_varco_prefixed_message_key() -> None:
    for member in FastrestErrorCodes:
        assert member.value.message_key is not None
        assert member.value.message_key.startswith("varco.error.")


def test_message_keys_are_unique_across_members() -> None:
    keys = [member.value.message_key for member in FastrestErrorCodes]
    assert len(keys) == len(set(keys))


def test_error_code_constructs_positionally_with_trailing_message_key_defaulted() -> (
    None
):
    # message_key must be trailing and defaulted — positional construction of
    # ErrorCode(code, http_status, default_message) must keep working
    # unchanged for any out-of-tree caller.
    code = ErrorCode("APP_001", 400, "Something went wrong.")
    assert code.message_key is None


def test_varco_error_codes_is_the_identical_object_as_fastrest_error_codes() -> None:
    from varco_core.exception.codes import VarcoErrorCodes

    assert VarcoErrorCodes is FastrestErrorCodes


def test_isinstance_and_list_over_alias_behave_identically() -> None:
    from varco_core.exception.codes import VarcoErrorCodes

    assert list(VarcoErrorCodes) == list(FastrestErrorCodes)
    assert isinstance(VarcoErrorCodes.NOT_FOUND, FastrestErrorCodes)
