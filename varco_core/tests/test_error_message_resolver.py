"""
Red-mode tests for Plan 011 Phase 1, step 15 — error_message_for's new
message_resolver= keyword.
"""

from __future__ import annotations

from varco_core.exception.codes import FastrestErrorCodes
from varco_core.exception.http import error_message_for
from varco_core.exception.service import ServiceNotFoundError


class SomeEntity:
    pass


def test_resolver_returning_none_falls_back_to_default_message() -> None:
    exc = ServiceNotFoundError(entity_id="1", entity_cls=SomeEntity)

    def resolver(key: str, params: dict) -> str | None:
        return None

    msg = error_message_for(exc, message_resolver=resolver)
    assert msg.message == FastrestErrorCodes.NOT_FOUND.default_message


def test_resolver_rendering_with_params_is_used_as_message() -> None:
    exc = ServiceNotFoundError(entity_id="1", entity_cls=SomeEntity)

    def resolver(key: str, params: dict) -> str | None:
        assert key == "varco.error.not_found"
        return f"Not found: {params['entity']}"

    msg = error_message_for(exc, message_resolver=resolver)
    assert msg.message == "Not found: SomeEntity"


def test_resolver_that_raises_is_swallowed_and_falls_back() -> None:
    # Rendering an error must never raise — a broken catalog must not turn
    # into a 500 while handling a 404.
    exc = ServiceNotFoundError(entity_id="1", entity_cls=SomeEntity)

    def resolver(key: str, params: dict) -> str | None:
        raise RuntimeError("catalog exploded")

    msg = error_message_for(exc, message_resolver=resolver)
    assert msg.message  # did not raise, fell back to some default text


def test_translator_still_receives_the_code_not_the_message_key() -> None:
    exc = ServiceNotFoundError(entity_id="1", entity_cls=SomeEntity)
    received: list[str] = []

    def translator(code: str) -> str:
        received.append(code)
        return "translated"

    error_message_for(exc, translator=translator)
    assert received == ["FASTREST_001"]
