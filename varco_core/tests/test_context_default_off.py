"""
Red-mode tests for Plan 011 Phase 0, step 6 — RD-1's X1 proof.

Nothing configured -> current_request_context() is empty, current_locale()/
current_timezone() are None; importing varco_core.context must not touch
`_current_tenant`/`_correlation_id` (identity-checked against the existing
ContextVar objects); tenant_context()/correlation_context() behave unchanged
while a request_context() is active, and vice versa.
"""

from __future__ import annotations

from varco_core.service.tenant import _current_tenant, current_tenant, tenant_context
from varco_core.tracing import (
    _correlation_id,
    correlation_context,
    current_correlation_id,
)


def test_importing_context_module_does_not_touch_existing_contextvars() -> None:
    # Capture identity of the pre-existing module-level ContextVar objects,
    # then import varco_core.context and assert they are untouched (same
    # object identity, same current value).
    tenant_var_before = _current_tenant
    correlation_var_before = _correlation_id

    import varco_core.context  # noqa: F401  (import triggers module init)

    assert _current_tenant is tenant_var_before
    assert _correlation_id is correlation_var_before
    assert current_tenant() is None
    assert current_correlation_id() is None


def test_default_off_current_request_context_is_empty() -> None:
    from varco_core.context import (
        current_locale,
        current_request_context,
        current_timezone,
    )

    ctx = current_request_context()
    assert ctx.locale is None
    assert ctx.timezone is None
    assert current_locale() is None
    assert current_timezone() is None


def test_tenant_context_unaffected_by_active_request_context() -> None:
    from varco_core.context import request_context

    with request_context(locale="fr"), tenant_context("acme"):
        assert current_tenant() == "acme"
    assert current_tenant() is None


async def test_correlation_context_unaffected_by_active_request_context() -> None:
    from varco_core.context import request_context

    with request_context(locale="fr"):
        async with correlation_context("corr-1"):
            assert current_correlation_id() == "corr-1"
    assert current_correlation_id() is None


def test_request_context_unaffected_by_active_tenant_context() -> None:
    from varco_core.context import current_locale, request_context

    with tenant_context("acme"), request_context(locale="es"):
        assert current_locale() == "es"
        assert current_tenant() == "acme"
    assert current_locale() is None
