"""
varco_fastapi.i18n
=====================
``I18nLifecycle`` — starts/stops a ``MessageCatalog`` (Plan 011, step 35).

Loading a catalog (e.g. ``GettextMessageCatalog``'s ``.mo`` files) is
blocking file I/O — this component ensures it happens once at startup,
never lazily inside the event loop on the first request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from varco_core.i18n.catalog import MessageCatalog

__all__ = ["I18nLifecycle"]


class I18nLifecycle:
    """``AbstractLifecycle``-shaped component wrapping a ``MessageCatalog``."""

    def __init__(self, catalog: MessageCatalog) -> None:
        self._catalog = catalog

    async def start(self) -> None:
        await self._catalog.start()

    async def stop(self) -> None:
        await self._catalog.stop()


__all__ = ["I18nLifecycle"]
