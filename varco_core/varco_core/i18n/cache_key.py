"""
varco_core.i18n.cache_key
============================
RD-6 — locale/timezone are **never** implicit cache-key components.

``localization_cache_key`` mirrors ``tenancy_cache_key()``'s shape and
fails closed the same way: a ``locale=True`` (or ``timezone=True``) request
with no ambient value raises ``RuntimeError`` rather than silently omitting
the segment. Caching a ``fr``-rendered body under a key that doesn't
mention ``fr`` and serving it to an ``en`` client is the i18n analogue of a
cross-tenant cache leak — and easier to hit, because localization happens
far from the cache call.
"""

from __future__ import annotations

from varco_core.context.request import current_locale, current_timezone

__all__ = ["localization_cache_key"]


def localization_cache_key(base: str, *, locale: bool = False, timezone: bool = False) -> str:
    """
    Compose the ambient locale/timezone into ``base``.

    Args:
        base: The unlocalized cache key.
        locale: Namespace by the ambient locale. Raises if none is
            resolved — never silently omits the segment.
        timezone: Namespace by the ambient timezone. Same fail-closed rule.

    Returns:
        ``base`` with `:locale:<tag>` and/or `:tz:<zone>` segments appended.

    Raises:
        RuntimeError: ``locale=True``/``timezone=True`` requested with no
            ambient value resolved.
    """
    key = base
    if locale:
        active_locale = current_locale()
        if active_locale is None:
            raise RuntimeError(
                "localization_cache_key(locale=True) called with no ambient "
                "locale resolved — refusing to silently omit the segment "
                "(RD-6). Ensure this runs inside a request_context(locale=...) "
                "or LocalizationMiddleware scope."
            )
        key = f"{key}:locale:{active_locale}"
    if timezone:
        active_timezone = current_timezone()
        if active_timezone is None:
            raise RuntimeError(
                "localization_cache_key(timezone=True) called with no "
                "ambient timezone resolved — refusing to silently omit the "
                "segment (RD-6)."
            )
        key = f"{key}:tz:{active_timezone}"
    return key
