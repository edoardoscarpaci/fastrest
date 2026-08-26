"""
Failing tests for the tenant status -> routing-decision mapping (Plan 007,
Phase 4, step 8).
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "status,expected_code",
    [
        ("pending", 503),
        ("suspended", 403),
        ("deprovisioning", 410),
        ("deleted", 404),
        ("unknown", 404),
    ],
)
def test_status_maps_to_documented_http_code(status: str, expected_code: int) -> None:
    from varco_core.tenancy.routing import routing_decision_for_status

    decision = routing_decision_for_status(status)

    assert decision.http_status == expected_code


async def test_non_active_tenant_never_causes_pool_ensure_to_run() -> None:
    from varco_core.tenancy.catalog import TenantDescriptor
    from varco_core.tenancy.routing import route_request
    from varco_core.tenancy.settings import TenantStatus

    class _CountingPool:
        def __init__(self) -> None:
            self.ensure_calls = 0

        async def ensure(self, tenant_id: str):
            self.ensure_calls += 1
            return object()

    class _StaticCatalog:
        async def get(self, tenant_id: str):
            return TenantDescriptor(tenant_id=tenant_id, status=TenantStatus.SUSPENDED)

    pool = _CountingPool()
    catalog = _StaticCatalog()

    with pytest.raises(Exception):  # noqa: B017 - routing must reject before ensure()
        await route_request(catalog=catalog, pool=pool, tenant_id="acme")

    assert pool.ensure_calls == 0
