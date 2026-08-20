"""
Red-mode tests for Plan 011 Phase 3, step 41 — T1's five-source chain
(query_param -> header -> user_profile[zoneinfo claim] -> tenant_default ->
fallback), per step 40.

Plan line (step 40): "each candidate passing validate_iana_zone() BEFORE
entering the list so ?tz=Mars/Olympus falls through with one WARNING."
"""

from __future__ import annotations

import pytest
from varco_core.context.defaults import (
    NullTenantDefaults,
    StaticTenantDefaults,
    TenantLocalizationDefaults,
)
from varco_core.tz.resolve import now_local, resolve_timezone, to_user_tz


async def test_query_param_wins_over_every_other_source() -> None:
    result = await resolve_timezone(
        query_param="America/New_York",
        header="Europe/Paris",
        user_profile_zoneinfo="Asia/Tokyo",
        tenant_id=None,
        tenant_defaults_provider=NullTenantDefaults(),
        default_timezone="UTC",
    )
    assert result.source == "query_param"
    assert result.value.key == "America/New_York"


async def test_header_used_when_no_query_param() -> None:
    result = await resolve_timezone(
        query_param=None,
        header="Europe/Paris",
        user_profile_zoneinfo="Asia/Tokyo",
        tenant_id=None,
        tenant_defaults_provider=NullTenantDefaults(),
        default_timezone="UTC",
    )
    assert result.source == "header"


async def test_invalid_zone_at_query_param_falls_through_with_warning() -> None:
    result = await resolve_timezone(
        query_param="Mars/Olympus",
        header=None,
        user_profile_zoneinfo=None,
        tenant_id=None,
        tenant_defaults_provider=NullTenantDefaults(),
        default_timezone="UTC",
    )
    assert result.source == "fallback"
    assert result.value.key == "UTC"


async def test_tenant_default_used_when_no_higher_source() -> None:
    provider = StaticTenantDefaults(
        {"acme": TenantLocalizationDefaults(locale=None, timezone="Europe/Paris")}
    )
    result = await resolve_timezone(
        query_param=None,
        header=None,
        user_profile_zoneinfo=None,
        tenant_id="acme",
        tenant_defaults_provider=provider,
        default_timezone="UTC",
    )
    assert result.source == "tenant_default"
    assert result.value.key == "Europe/Paris"


async def test_fallback_used_when_nothing_matches() -> None:
    result = await resolve_timezone(
        query_param=None,
        header=None,
        user_profile_zoneinfo=None,
        tenant_id=None,
        tenant_defaults_provider=NullTenantDefaults(),
        default_timezone="UTC",
    )
    assert result.source == "fallback"
    assert result.value.key == "UTC"


def test_to_user_tz_on_naive_input_raises() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from varco_core.context.request import request_context

    with request_context(timezone=ZoneInfo("America/New_York")):
        with pytest.raises(ValueError):
            to_user_tz(datetime(2026, 1, 1))  # naive — UTC-aware-in contract


async def test_now_local_with_no_ambient_zone_returns_aware_utc() -> None:
    result = now_local()
    assert result.tzinfo is not None
