"""
Failing tests for varco_core.tenancy.scope_guard (Plan 007, Phase 2, step 7).

validate_service_scope() — catches the TenantAwareService×GLOBAL mismatch in
both directions.
"""

from __future__ import annotations

import logging

import pytest


def test_tenant_aware_service_on_global_entity_raises() -> None:
    from varco_core.service.tenant import TenantAwareService
    from varco_core.tenancy.catalog import TenantIsolationError
    from varco_core.tenancy.scope_guard import validate_service_scope
    from varco_core.tenancy.settings import TenantScope

    class GlobalEntity:
        pass

    class BadService(TenantAwareService):
        pass

    with pytest.raises(TenantIsolationError) as exc:
        validate_service_scope(BadService, entity_cls=GlobalEntity, tenant_scope=TenantScope.GLOBAL)

    message = str(exc.value)
    assert "BadService" in message
    assert "GlobalEntity" in message


def test_tenant_scoped_entity_with_no_tenant_filtering_warns_not_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from varco_core.tenancy.scope_guard import validate_service_scope
    from varco_core.tenancy.settings import TenantScope

    class TenantEntity:
        pass

    class PlainService:
        pass

    with caplog.at_level(logging.WARNING):
        validate_service_scope(
            PlainService, entity_cls=TenantEntity, tenant_scope=TenantScope.TENANT
        )

    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_correct_pairings_are_silent(caplog: pytest.LogCaptureFixture) -> None:
    from varco_core.service.tenant import TenantAwareService
    from varco_core.tenancy.scope_guard import validate_service_scope
    from varco_core.tenancy.settings import TenantScope

    class TenantEntity:
        pass

    class GlobalEntity:
        pass

    class GoodTenantService(TenantAwareService):
        pass

    class GoodGlobalService:
        pass

    with caplog.at_level(logging.WARNING):
        validate_service_scope(
            GoodTenantService, entity_cls=TenantEntity, tenant_scope=TenantScope.TENANT
        )
        validate_service_scope(
            GoodGlobalService, entity_cls=GlobalEntity, tenant_scope=TenantScope.GLOBAL
        )

    assert caplog.records == []
