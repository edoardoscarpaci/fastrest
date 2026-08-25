"""
Failing test for varco_fastapi.tenancy DI wiring (Plan 007, Phase 5, step 11).
"""

from __future__ import annotations


def test_scan_varco_fastapi_tenancy_validates_bindings() -> None:
    import varco_fastapi.tenancy  # noqa: F401
    from providify import DIContainer
    from varco_conformance.providify_health import assert_no_structural_di_issues

    container = DIContainer()
    container.scan("varco_fastapi", recursive=True)
    container.validate_bindings()
    assert_no_structural_di_issues(container)
