"""
Shared pytest fixtures for varco_fastapi/tests.

Autouse fixture (Plan 002): resets the process-global JWT claim-transformer
and token-profile registries (``varco_core.jwt.transform.runtime`` /
``varco_core.jwt.profile``) before and after every test, mirroring
``varco_core/tests/conftest.py``.

Without this, tests in different files that each ``monkeypatch.setenv`` a
different ``VARCO_JWT_TRANSFORM_*``/``VARCO_JWT_PROFILE__*`` value would leak
state into each other via the lazily-built, cached process-global registry —
whichever test resolves it first "wins" for the rest of the test session.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_jwt_globals():
    """Reset JWT claim-transform + token-profile globals around every test."""
    _reset_all()
    yield
    _reset_all()


def _reset_all() -> None:
    from varco_core.jwt.profile import reset_token_profiles
    from varco_core.jwt.transform.runtime import reset_claim_transforms

    reset_claim_transforms()
    reset_token_profiles()
