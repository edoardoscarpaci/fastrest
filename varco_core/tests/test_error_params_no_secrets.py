"""
Red-mode tests for Plan 011 Phase 1, step 16 — the no-secrets guard on
error_params().
"""

from __future__ import annotations

import pytest
from varco_core.exception.http import error_message_for
from varco_core.exception.service import (
    ServiceAuthorizationError,
    ServiceConflictError,
    ServiceNotFoundError,
    ServiceValidationError,
)

FORBIDDEN_KEYS = {"reason", "password", "token", "secret"}


class SomeEntity:
    pass


def test_service_authorization_error_params_excludes_reason() -> None:
    exc = ServiceAuthorizationError(
        "delete", SomeEntity, reason="internal ownership check failed"
    )
    params = exc.error_params()
    assert "reason" not in params


def test_service_authorization_error_reason_absent_from_serialized_body() -> None:
    exc = ServiceAuthorizationError(
        "delete", SomeEntity, reason="internal ownership check failed"
    )
    msg = error_message_for(exc)
    body = msg.model_dump(exclude_none=True)
    serialized = str(body)
    assert "internal ownership check failed" not in serialized


@pytest.mark.parametrize(
    "exc",
    [
        ServiceNotFoundError(entity_id="1", entity_cls=SomeEntity),
        ServiceAuthorizationError("read", SomeEntity, reason="x"),
        ServiceConflictError("duplicate email"),
        ServiceValidationError("bad field", field="name"),
    ],
)
def test_no_builtin_params_contain_sensitive_keys(exc) -> None:
    params = exc.error_params()
    assert not (FORBIDDEN_KEYS & set(params.keys()))
