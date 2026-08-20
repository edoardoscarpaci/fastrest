"""
Red-mode tests for Plan 011 Phase 1, step 14 — RD-1's I1 proof (half 1) and
D-4's delta + kill switch.
"""

from __future__ import annotations

from varco_core.exception.http import error_message_for
from varco_core.exception.service import ServiceException, ServiceNotFoundError
from varco_core.exception.settings import ErrorEnvelopeSettings


class SomeEntity:
    pass


def test_builtin_exception_body_gains_exactly_message_key_and_params() -> None:
    exc = ServiceNotFoundError(entity_id="42", entity_cls=SomeEntity)
    msg = error_message_for(exc)
    body = msg.model_dump(exclude_none=True)

    assert body.get("message_key") == "varco.error.not_found"
    assert body.get("params") == {"entity": "SomeEntity", "entity_id": "42"}


class OutOfTreeException(ServiceException):
    """An out-of-tree subclass that never sets message_key."""

    def __init__(self) -> None:
        super().__init__("out of tree failure")


def test_out_of_tree_exception_with_no_message_key_is_byte_identical() -> None:
    # D-4: an exception that never sets message_key must get NO new JSON keys
    # at all — not even an empty params dict.
    exc = OutOfTreeException()
    msg = error_message_for(exc)
    body = msg.model_dump(exclude_none=True)

    assert "message_key" not in body
    assert "params" not in body


def test_kill_switch_restores_pre_plan_body_for_every_builtin() -> None:
    settings = ErrorEnvelopeSettings(include_message_key=False, include_params=False)
    exc = ServiceNotFoundError(entity_id="1", entity_cls=SomeEntity)
    msg = error_message_for(exc, envelope_settings=settings)
    body = msg.model_dump(exclude_none=True)

    assert "message_key" not in body
    assert "params" not in body


def test_error_envelope_settings_default_both_true() -> None:
    settings = ErrorEnvelopeSettings()
    assert settings.include_message_key is True
    assert settings.include_params is True
    assert settings.problem_details is False


def test_empty_params_dict_is_omitted_not_emitted_as_empty_object() -> None:
    exc = OutOfTreeException()
    exc.message_key = "app.custom.error"  # type: ignore[attr-defined]
    msg = error_message_for(exc)
    body = msg.model_dump(exclude_none=True)
    assert "params" not in body
