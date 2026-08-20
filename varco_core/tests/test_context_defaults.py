"""
Red-mode tests for Plan 011 Phase 0, step 7 — RD-2's
``varco_core.context.defaults`` (TenantDefaultsProvider Protocol +
NullTenantDefaults / StaticTenantDefaults).
"""

from __future__ import annotations

from varco_core.context.defaults import (
    NullTenantDefaults,
    StaticTenantDefaults,
    TenantDefaultsProvider,
    TenantLocalizationDefaults,
)


async def test_null_tenant_defaults_returns_both_none() -> None:
    # RD-2: the default binding — apps that don't set tenant preferences pay
    # nothing and get an all-None result.
    provider = NullTenantDefaults()
    result = await provider.defaults_for("acme")
    assert result == TenantLocalizationDefaults(locale=None, timezone=None)


async def test_static_tenant_defaults_returns_configured_mapping() -> None:
    provider = StaticTenantDefaults(
        {"acme": TenantLocalizationDefaults(locale="fr", timezone="Europe/Paris")}
    )
    result = await provider.defaults_for("acme")
    assert result.locale == "fr"
    assert result.timezone == "Europe/Paris"


async def test_static_tenant_defaults_returns_null_for_unknown_tenant() -> None:
    provider = StaticTenantDefaults({})
    result = await provider.defaults_for("unknown-tenant")
    assert result == TenantLocalizationDefaults(locale=None, timezone=None)


def test_null_tenant_defaults_satisfies_protocol_structurally() -> None:
    # runtime_checkable Protocol — isinstance() must work structurally.
    assert isinstance(NullTenantDefaults(), TenantDefaultsProvider)


def test_tenant_localization_defaults_is_frozen() -> None:
    import dataclasses

    obj = TenantLocalizationDefaults(locale=None, timezone=None)
    assert dataclasses.is_dataclass(obj)
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        obj.locale = "de"  # type: ignore[misc]
