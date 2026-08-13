"""
Failing test for varco_sa.tenancy DI wiring (Plan 007, Phase 3, step 9).
"""

from __future__ import annotations


def test_scan_varco_sa_tenancy_validates_bindings() -> None:
    import varco_sa.tenancy  # noqa: F401 -- module must exist before DI wiring can be verified
    from providify import DIContainer

    container = DIContainer()
    container.scan("varco_sa", recursive=True)
    container.validate_bindings()
