"""
Shared pytest fixtures for varco_core/tests.

Autouse fixture (Plan 002, Phase 1 step 13 / Phase 3): resets the
process-global claim-transformer and token-profile registries before and
after every test, so no test leaks env-driven or explicitly-configured JWT
transform/profile state into another test.

This module is imported at collection time. Until Phase 1/3 land,
``varco_core.jwt.transform.runtime`` and ``varco_core.jwt.profile`` do not
exist yet — the imports are done lazily inside the fixture body (not at
module scope) so this conftest does not itself break collection of the
whole varco_core test suite while those modules are still red.
"""

from __future__ import annotations

import pytest

# Plan 027 / Step 1 — registers tls_fixtures.py's session-scoped `pki_bundle` fixture
# for the mTLS/PKCS#12/client-adapter suites.
pytest_plugins = ["tls_fixtures"]


@pytest.fixture(autouse=True)
def _reset_jwt_globals():
    """Reset JWT claim-transform + token-profile globals around every test."""
    _reset_all()
    yield
    _reset_all()


def _reset_all() -> None:
    try:
        from varco_core.jwt.transform.runtime import reset_claim_transforms

        reset_claim_transforms()
    except ImportError:
        # Phase 1 not yet implemented — nothing to reset.
        pass

    try:
        from varco_core.jwt.profile import reset_token_profiles

        reset_token_profiles()
    except ImportError:
        # Phase 3 not yet implemented — nothing to reset.
        pass
