"""
Failing test for Plan 008 Phase 3, step 6 — scanning ``varco_core`` and
validating bindings must stay green with the new
``varco_core.tenancy.control.readiness`` module present.
"""

from __future__ import annotations


def test_scan_varco_core_validates_bindings_with_readiness_module_present() -> None:
    import varco_core.tenancy.control.readiness  # noqa: F401
    from providify import DIContainer
    from varco_conformance.providify_health import assert_no_structural_di_issues

    container = DIContainer()
    container.scan("varco_core", recursive=True)
    container.validate_bindings()
    assert_no_structural_di_issues(container)
