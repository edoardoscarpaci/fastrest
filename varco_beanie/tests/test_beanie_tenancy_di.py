"""
Failing test for varco_beanie.tenancy DI wiring (Plan 007, Phase 7, step 8).
"""

from __future__ import annotations


def test_scan_varco_beanie_tenancy_validates_bindings() -> None:
    import varco_beanie.tenancy  # noqa: F401
    from providify import DIContainer
    from varco_conformance.providify_health import assert_no_structural_di_issues

    container = DIContainer()
    container.scan("varco_beanie", recursive=True)
    container.validate_bindings()
    assert_no_structural_di_issues(container)
