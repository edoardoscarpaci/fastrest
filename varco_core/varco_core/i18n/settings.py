"""
varco_core.i18n.settings
===========================
``I18nSettings`` — off by default (Plan 011 RD-1's I2 row).
"""

from __future__ import annotations

from pydantic_settings import SettingsConfigDict

from varco_core.config import VarcoSettings

__all__ = ["I18nSettings"]


class I18nSettings(VarcoSettings):
    """
    Attributes:
        enabled: Master switch. ``False`` (default) — no catalog
            constructed, no middleware, no ``.mo`` read, no
            ``Content-Language`` header.
        default_locale: Fallback locale — the last step of the precedence
            chain.
        supported_locales: Only locales in this tuple are ever returned by
            the precedence chain; an unsupported explicit ``?lang=`` falls
            through with one DEBUG line rather than a 400.
        query_param: The query parameter name read for the explicit locale
            override (D-2's step 1).
        catalog_dir: Directory passed to ``GettextMessageCatalog`` when
            wired via ``varco_fastapi``. ``None`` means "use
            ``NullMessageCatalog``".
        domain: The ``gettext`` domain name.
        set_content_language: Whether to set the ``Content-Language``
            response header when i18n is enabled.
    """

    model_config = SettingsConfigDict(env_prefix="VARCO_I18N_")

    enabled: bool = False
    default_locale: str = "en"
    supported_locales: tuple[str, ...] = ("en",)
    query_param: str = "lang"
    catalog_dir: str | None = None
    domain: str = "messages"
    set_content_language: bool = True
