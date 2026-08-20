"""
Red-mode tests for Plan 011 Phase 2, step 29 — I2's five-source precedence
chain (query_param -> user_profile -> tenant_default -> accept_language ->
fallback), per step 28's resolve_locale().

Plan line (Design, I2 table): the chain differs from brief 002's Librarian
ordering — explicit `?lang=` beats a stored profile — asserted explicitly so
the deviation reads as intent (step 29).
"""

from __future__ import annotations

from varco_core.context.defaults import (
    NullTenantDefaults,
    StaticTenantDefaults,
    TenantLocalizationDefaults,
)
from varco_core.i18n.resolve import resolve_locale


async def test_query_param_wins_over_every_other_source() -> None:
    result = await resolve_locale(
        query_param="fr",
        user_profile_locale="de",
        tenant_id=None,
        tenant_defaults_provider=NullTenantDefaults(),
        accept_language_header="es",
        supported_locales=("fr", "de", "es", "en"),
        default_locale="en",
    )
    assert result.value == "fr"
    assert result.source == "query_param"


async def test_explicit_query_param_beats_stored_user_profile() -> None:
    # The plan's stated deviation from brief 002: explicit ?lang= must win
    # over a stale stored preference.
    result = await resolve_locale(
        query_param="es",
        user_profile_locale="de",
        tenant_id=None,
        tenant_defaults_provider=NullTenantDefaults(),
        accept_language_header=None,
        supported_locales=("es", "de", "en"),
        default_locale="en",
    )
    assert result.source == "query_param"


async def test_user_profile_wins_when_no_query_param() -> None:
    result = await resolve_locale(
        query_param=None,
        user_profile_locale="de",
        tenant_id=None,
        tenant_defaults_provider=NullTenantDefaults(),
        accept_language_header="es",
        supported_locales=("de", "es", "en"),
        default_locale="en",
    )
    assert result.source == "user_profile"


async def test_tenant_default_used_when_no_query_or_profile() -> None:
    provider = StaticTenantDefaults(
        {"acme": TenantLocalizationDefaults(locale="fr", timezone=None)}
    )
    result = await resolve_locale(
        query_param=None,
        user_profile_locale=None,
        tenant_id="acme",
        tenant_defaults_provider=provider,
        accept_language_header=None,
        supported_locales=("fr", "en"),
        default_locale="en",
    )
    assert result.source == "tenant_default"
    assert result.value == "fr"


async def test_accept_language_used_when_no_higher_source() -> None:
    result = await resolve_locale(
        query_param=None,
        user_profile_locale=None,
        tenant_id=None,
        tenant_defaults_provider=NullTenantDefaults(),
        accept_language_header="fr-CA",
        supported_locales=("fr", "en"),
        default_locale="en",
    )
    assert result.source == "accept_language"
    assert result.value == "fr"


async def test_fallback_used_when_nothing_matches() -> None:
    result = await resolve_locale(
        query_param=None,
        user_profile_locale=None,
        tenant_id=None,
        tenant_defaults_provider=NullTenantDefaults(),
        accept_language_header=None,
        supported_locales=("en",),
        default_locale="en",
    )
    assert result.source == "fallback"
    assert result.value == "en"


async def test_unsupported_explicit_lang_falls_through_never_400s() -> None:
    result = await resolve_locale(
        query_param="ja",  # not in supported_locales
        user_profile_locale=None,
        tenant_id=None,
        tenant_defaults_provider=NullTenantDefaults(),
        accept_language_header=None,
        supported_locales=("en",),
        default_locale="en",
    )
    assert result.source == "fallback"
    assert result.value == "en"
