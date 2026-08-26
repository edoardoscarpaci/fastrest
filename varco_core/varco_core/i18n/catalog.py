"""
varco_core.i18n.catalog
==========================
``MessageCatalog`` ABC + ``NullMessageCatalog`` / ``DictMessageCatalog`` —
Plan 011 D-1.

``MessageCatalog`` gets both patterns brief 002's Librarian's note asks for
(template return and structured-params formatting) with one abstract
method: the template path is ``get_message()``, the formatting path is
``format_message()`` with a working concrete default, and a
formatter-backed implementation (``GettextMessageCatalog``) overrides
``format_message`` for plural forms. Simple implementations write one
method.

``format_message``'s default uses a ``__missing__``-tolerant mapping so a
missing interpolation parameter leaves the literal placeholder (``{name}``)
visible instead of raising ``KeyError`` — this code runs inside an
exception-rendering path, where raising would turn a 404 into a 500.
"""

from __future__ import annotations

import abc
from typing import Any
from collections.abc import Mapping

__all__ = ["MessageCatalog", "NullMessageCatalog", "DictMessageCatalog"]


class _MissingTolerantDict(dict):
    """``str.format_map`` mapping whose ``__missing__`` leaves the
    placeholder visible instead of raising ``KeyError``."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class MessageCatalog(abc.ABC):
    """
    Renders localized message text for a ``(key, locale)`` pair.

    Implementations: ``NullMessageCatalog`` (the default — zero I/O, always
    ``None``), ``DictMessageCatalog`` (in-memory, tests/small apps),
    ``GettextMessageCatalog`` (stdlib ``gettext``, production default).
    """

    @abc.abstractmethod
    def get_message(self, key: str, locale: str) -> str | None:
        """
        Return the raw (unformatted) template for ``key`` under ``locale``,
        or ``None`` if no entry exists.
        """
        raise NotImplementedError

    def format_message(
        self, key: str, locale: str, params: Mapping[str, Any] | None = None
    ) -> str | None:
        """
        ``get_message()`` + ``str.format_map`` with a missing-key-tolerant
        mapping. Override for gettext plurals / ICU selectors.

        Returns:
            The rendered message, or ``None`` when ``get_message()`` finds
            no entry (never ``""``).
        """
        template = self.get_message(key, locale)
        if template is None:
            return None
        return template.format_map(_MissingTolerantDict(params or {}))

    def available_locales(self) -> frozenset[str]:
        """Locales this catalog has entries for. Default: empty."""
        return frozenset()

    async def start(self) -> None:
        """Hook for implementations that need blocking I/O (e.g. loading
        ``.mo`` files) — always called before the first request, never
        lazily inside the event loop. No-op by default."""

    async def stop(self) -> None:
        """Release any resources acquired in ``start()``. No-op by default."""


class NullMessageCatalog(MessageCatalog):
    """The default catalog — ``get_message`` always returns ``None``
    (server-side rendering falls back to ``default_message``). Zero I/O."""

    def get_message(self, key: str, locale: str) -> str | None:
        return None


class DictMessageCatalog(MessageCatalog):
    """
    In-memory ``{locale: {key: template}}`` catalog.

    The unit-test and small-app catalog — also what the feature doc's first
    example uses, so nobody needs ``pybabel`` to try the feature.
    """

    def __init__(self, mapping: Mapping[str, Mapping[str, str]]) -> None:
        self._mapping: dict[str, dict[str, str]] = {
            locale: dict(entries) for locale, entries in mapping.items()
        }

    def get_message(self, key: str, locale: str) -> str | None:
        return self._mapping.get(locale, {}).get(key)

    def available_locales(self) -> frozenset[str]:
        return frozenset(self._mapping.keys())
