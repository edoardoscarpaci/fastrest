"""
varco_core.i18n.resolve
==========================
``resolve_locale()`` — I2's five-source precedence chain, a thin consumer
of X1's ``resolve_precedence`` (Plan 011 D-6).

Chain: ``query_param`` -> ``user_profile`` -> ``tenant_default`` ->
``accept_language`` -> ``fallback``. This differs from brief 002's
Librarian ordering (which lists a stored preference before ``?lang=``) —
brief 001 §"Precedence hierarchy" groups explicit user choice first: an
explicit ``?lang=`` is a deliberate per-request override that must not be
overruled by a stale stored profile. Deliberate deviation, recorded here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from varco_core.context.precedence import Resolved, resolve_precedence
from varco_core.i18n.negotiation import negotiate_locale

if TYPE_CHECKING:
    from varco_core.context.defaults import TenantDefaultsProvider

__all__ = ["resolve_locale"]


async def resolve_locale(
    *,
    query_param: str | None,
    user_profile_locale: str | None,
    tenant_id: str | None,
    tenant_defaults_provider: "TenantDefaultsProvider",
    accept_language_header: str | None,
    supported_locales: tuple[str, ...],
    default_locale: str,
) -> Resolved[str] | None:
    """
    Resolve a locale via the five-source precedence chain.

    Only locales in ``supported_locales`` are ever returned; an unsupported
    explicit ``?lang=`` (or stored profile) falls through to the next
    source rather than 400ing.

    Args:
        query_param: The raw ``?lang=`` value, if present.
        user_profile_locale: The caller's OIDC ``locale`` claim, if any.
        tenant_id: The active tenant, or ``None``.
        tenant_defaults_provider: Awaited **only** when ``tenant_id`` is set
            — an app paying nothing for RD-2 never triggers I/O.
        accept_language_header: The raw ``Accept-Language`` header.
        supported_locales: Locales this deployment has content for.
        default_locale: The final fallback.

    Returns:
        ``Resolved(value, source)`` — ``source`` is one of
        ``"query_param"``, ``"user_profile"``, ``"tenant_default"``,
        ``"accept_language"``, ``"fallback"``.
    """
    supported = set(supported_locales)

    def _supported(value: str | None) -> str | None:
        return value if value in supported else None

    tenant_default: str | None = None
    if tenant_id is not None:
        defaults = await tenant_defaults_provider.defaults_for(tenant_id)
        tenant_default = _supported(defaults.locale)

    accept_language = (
        negotiate_locale(
            accept_language_header, supported_locales, default=default_locale
        )
        if accept_language_header
        else None
    )

    candidates: list[tuple[str, str | None]] = [
        ("query_param", _supported(query_param)),
        ("user_profile", _supported(user_profile_locale)),
        ("tenant_default", tenant_default),
        ("accept_language", accept_language),
        ("fallback", default_locale),
    ]
    return resolve_precedence(candidates)
