"""
Failing test: TenantIsolation.SCHEMA on Beanie is rejected loudly
(Plan 007, Phase 7, step 7).
"""

from __future__ import annotations

import pytest


def test_schema_isolation_on_beanie_raises_value_error_naming_mongodb() -> None:
    from varco_beanie.tenancy.pool import BeanieTenantPool
    from varco_core.tenancy.settings import TenantIsolation

    with pytest.raises(ValueError) as exc:
        BeanieTenantPool(client=object(), isolation=TenantIsolation.SCHEMA)

    assert "MongoDB" in str(exc.value)
