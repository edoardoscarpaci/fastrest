"""
Red-mode tests for Plan 011 Phase 2, step 32 — RD-1's I2 proof.

Plan line (step 32): "I18nSettings() -> enabled is False; no catalog
constructed, no .mo read (asserted with a patched gettext.translation that
fails the test if called), no middleware added, no Content-Language header;
error_message_for() with no message_resolver returns default_message
exactly as today."
"""

from __future__ import annotations

import gettext as gettext_module

from varco_core.exception.http import error_message_for
from varco_core.exception.service import ServiceNotFoundError
from varco_core.i18n.settings import I18nSettings


class SomeEntity:
    pass


def test_i18n_settings_default_is_disabled() -> None:
    settings = I18nSettings()
    assert settings.enabled is False
    assert settings.default_locale == "en"
    assert settings.supported_locales == ("en",)


def test_gettext_translation_never_called_when_i18n_disabled(monkeypatch) -> None:
    called = {"hit": False}
    original = gettext_module.translation

    def _spy(*args, **kwargs):
        called["hit"] = True
        return original(*args, **kwargs)

    monkeypatch.setattr(gettext_module, "translation", _spy)

    # Nothing in the default (disabled) path should touch gettext at all.
    settings = I18nSettings()
    assert settings.enabled is False
    assert called["hit"] is False


def test_error_message_for_with_no_resolver_returns_default_message_unchanged() -> None:
    exc = ServiceNotFoundError(entity_id="1", entity_cls=SomeEntity)
    msg = error_message_for(exc)
    assert msg.message == "The requested resource was not found."
