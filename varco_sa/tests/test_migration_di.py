"""
Failing test for varco_sa.migration DI bootstrap health (Plan 006, Phase 2,
step 28) — the per-package "scan + validate_bindings" convention from
CLAUDE.md's pitfall table (see e.g. varco_redis/tests/test_redis_di.py).
"""

from __future__ import annotations

from providify import DIContainer


async def test_container_scan_varco_sa_with_migration_module_validates_bindings() -> (
    None
):
    # Importing the migration package must not break DI bootstrap health for
    # the rest of varco_sa — e.g. a quoted @Provider return annotation would
    # silently disable injection container-wide (CLAUDE.md pitfall table).
    import varco_sa.migration  # noqa: F401 — presence is what's under test

    container = DIContainer()
    container.scan("varco_sa", recursive=True)

    container.validate_bindings()
