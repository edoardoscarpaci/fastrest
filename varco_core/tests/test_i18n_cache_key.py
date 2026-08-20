"""
Red-mode tests for Plan 011 Phase 2, step 31 — RD-6's
varco_core.i18n.cache_key.localization_cache_key.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest
from varco_core.context.request import request_context
from varco_core.i18n.cache_key import localization_cache_key


def test_locale_true_with_no_ambient_locale_fails_closed() -> None:
    # RD-6: fails closed exactly like tenancy_cache_key() — never silently
    # omits the segment.
    with pytest.raises(RuntimeError):
        localization_cache_key("user:42", locale=True)


def test_locale_true_with_ambient_locale_namespaces_the_key() -> None:
    with request_context(locale="fr"):
        key = localization_cache_key("user:42", locale=True)
    assert "fr" in key
    assert "user:42" in key


def test_locale_false_default_does_not_namespace_by_locale() -> None:
    with request_context(locale="fr"):
        key = localization_cache_key("user:42")
    assert key == "user:42" or "fr" not in key


def test_timezone_true_with_no_ambient_timezone_fails_closed() -> None:
    with pytest.raises(RuntimeError):
        localization_cache_key("user:42", locale=False, timezone=True)


def test_timezone_true_with_ambient_timezone_namespaces_the_key() -> None:
    with request_context(timezone=ZoneInfo("America/New_York")):
        key = localization_cache_key("user:42", locale=False, timezone=True)
    assert "America/New_York" in key
