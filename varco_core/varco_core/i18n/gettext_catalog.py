"""
varco_core.i18n.gettext_catalog
==================================
``GettextMessageCatalog`` — the production-default ``MessageCatalog``
(Plan 011 D-1): stdlib ``gettext`` only, zero new runtime dependency.

Loading is **blocking file I/O** and therefore happens in ``start()``,
never lazily on the first request inside the event loop.

**No process-global ``install()``/``activate()``** — D-1's Flask-Babel
``force_locale`` note (issue #117: a process-global "active locale" leaks
across requests). The locale lives only in X1's request-scoped
``ContextVar``; this catalog holds an immutable ``dict`` after ``start()``
and every lookup takes the locale as an explicit argument.
"""

from __future__ import annotations

import gettext as gettext_module
import logging
from collections.abc import Mapping
from typing import Any

from varco_core.i18n.catalog import MessageCatalog, _MissingTolerantDict

logger = logging.getLogger(__name__)

__all__ = ["GettextMessageCatalog"]


class GettextMessageCatalog(MessageCatalog):
    """
    Loads one stdlib ``gettext.GNUTranslations`` per locale in ``start()``.

    Args:
        directory: Root directory containing ``<locale>/LC_MESSAGES/<domain>.mo``
            files (the standard ``gettext`` layout — what ``pybabel compile``
            produces).
        domain: The ``.mo`` domain name. Default ``"messages"``.
        locales: The locales to attempt to load. A locale with no ``.mo``
            file is skipped with one WARNING — ``start()`` never raises for
            a missing locale.
    """

    def __init__(
        self, directory: str, *, domain: str = "messages", locales: Any = ()
    ) -> None:
        self._directory = directory
        self._domain = domain
        self._locales = tuple(locales)
        # Populated (and made effectively immutable) by start(); never
        # mutated afterwards — see the module docstring's no-activate() note.
        self._translations: dict[str, gettext_module.NullTranslations] = {}

    async def start(self) -> None:
        translations: dict[str, gettext_module.NullTranslations] = {}
        for locale in self._locales:
            try:
                translations[locale] = gettext_module.translation(
                    self._domain,
                    localedir=self._directory,
                    languages=[locale],
                    fallback=False,
                )
            except OSError:
                logger.warning(
                    "GettextMessageCatalog: no .mo file for domain=%r locale=%r "
                    "under %r; skipping",
                    self._domain,
                    locale,
                    self._directory,
                )
        self._translations = translations

    async def stop(self) -> None:
        self._translations = {}

    def available_locales(self) -> frozenset[str]:
        return frozenset(self._translations.keys())

    def get_message(self, key: str, locale: str) -> str | None:
        translation = self._translations.get(locale)
        if translation is None:
            return None
        catalog = getattr(translation, "_catalog", {})
        return catalog.get(key)

    def format_message(
        self, key: str, locale: str, params: Mapping[str, Any] | None = None
    ) -> str | None:
        translation = self._translations.get(locale)
        if translation is None:
            return None
        params = params or {}
        count = params.get("count")
        if isinstance(count, int) and not isinstance(count, bool):
            # ngettext gives CLDR plural forms for free when the .mo's
            # plural msgid/msgid_plural pair was compiled from `key`.
            template = translation.ngettext(key, key, count)
        else:
            template = self.get_message(key, locale)
            if template is None:
                return None
        return template.format_map(_MissingTolerantDict(params))
