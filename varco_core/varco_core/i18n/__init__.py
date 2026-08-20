"""
varco_core.i18n
==================
I2 — ``MessageCatalog`` + ``Accept-Language`` negotiation (Plan 011 Phase 2).
Off by default: ``I18nSettings.enabled = False``.
"""

from __future__ import annotations

from varco_core.i18n.cache_key import localization_cache_key
from varco_core.i18n.catalog import (
    DictMessageCatalog,
    MessageCatalog,
    NullMessageCatalog,
)
from varco_core.i18n.gettext_catalog import GettextMessageCatalog
from varco_core.i18n.negotiation import negotiate_locale, parse_accept_language
from varco_core.i18n.resolve import resolve_locale
from varco_core.i18n.settings import I18nSettings

__all__ = [
    "MessageCatalog",
    "NullMessageCatalog",
    "DictMessageCatalog",
    "GettextMessageCatalog",
    "negotiate_locale",
    "parse_accept_language",
    "resolve_locale",
    "I18nSettings",
    "localization_cache_key",
]
