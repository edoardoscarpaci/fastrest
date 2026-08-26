"""
Unit tests for varco_fastapi.auth.server_auth — fail-closed audience enforcement.
====================================================================================

Plan 005, Phase 2, Step 27 — failing tests first.

RED until Step 28 lands: ``JwtBearerAuth.__init__`` refuses to construct when
no ``audience`` is supplied and ``VARCO_JWT_AUDIENCE`` is unset, unless the
caller opts out via ``allow_any_audience=True`` / ``VARCO_JWT_ALLOW_ANY_AUDIENCE=true``.
Today's behaviour (``varco_fastapi/tests/milestone_a/test_server_auth.py``) is
"log a warning and proceed" — this file encodes the new, stricter contract.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from varco_fastapi.auth.server_auth import JwtBearerAuth


def _make_registry() -> MagicMock:
    return MagicMock()


class TestJwtBearerAuthRequiresAudienceByDefault:
    def test_no_audience_and_no_env_var_raises_value_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VARCO_JWT_AUDIENCE", raising=False)
        monkeypatch.delenv("VARCO_JWT_ALLOW_ANY_AUDIENCE", raising=False)

        with pytest.raises(ValueError) as exc:
            JwtBearerAuth(registry=_make_registry())

        # Message must name both the env var and the opt-out, per Step 27.
        message = str(exc.value)
        assert "VARCO_JWT_AUDIENCE" in message
        assert (
            "allow_any_audience" in message or "VARCO_JWT_ALLOW_ANY_AUDIENCE" in message
        )

    def test_allow_any_audience_true_constructs_and_logs_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.delenv("VARCO_JWT_AUDIENCE", raising=False)
        monkeypatch.delenv("VARCO_JWT_ALLOW_ANY_AUDIENCE", raising=False)

        with caplog.at_level(logging.WARNING):
            auth = JwtBearerAuth(registry=_make_registry(), allow_any_audience=True)

        assert auth is not None
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 1

    def test_env_var_allow_any_audience_true_constructs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VARCO_JWT_AUDIENCE", raising=False)
        monkeypatch.setenv("VARCO_JWT_ALLOW_ANY_AUDIENCE", "true")

        auth = JwtBearerAuth(registry=_make_registry())
        assert auth is not None

    def test_audience_set_via_kwarg_constructs_without_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VARCO_JWT_AUDIENCE", raising=False)
        monkeypatch.delenv("VARCO_JWT_ALLOW_ANY_AUDIENCE", raising=False)

        auth = JwtBearerAuth(registry=_make_registry(), audience="my-service")
        assert auth is not None

    def test_audience_set_via_env_var_constructs_without_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VARCO_JWT_AUDIENCE", "my-service")
        monkeypatch.delenv("VARCO_JWT_ALLOW_ANY_AUDIENCE", raising=False)

        auth = JwtBearerAuth(registry=_make_registry())
        assert auth is not None
