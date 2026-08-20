"""
varco_core.tz.resolve
========================
T1's five-source precedence chain + rendering helpers.

**varco does not change what it stores.** Everything keeps being written
as aware-UTC (``datetime.now(timezone.utc)``). T1 is a *rendering and
interpretation* context only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone as dt_timezone
from typing import TYPE_CHECKING

from varco_core.context.precedence import Resolved, resolve_precedence
from varco_core.context.request import current_timezone
from varco_core.tz.zones import validate_iana_zone

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo

    from varco_core.context.defaults import TenantDefaultsProvider

logger = logging.getLogger(__name__)

__all__ = ["resolve_timezone", "current_timezone", "to_user_tz", "now_local"]


async def resolve_timezone(
    *,
    query_param: str | None,
    header: str | None,
    user_profile_zoneinfo: str | None,
    tenant_id: str | None,
    tenant_defaults_provider: "TenantDefaultsProvider",
    default_timezone: str,
) -> Resolved["ZoneInfo"] | None:
    """
    Resolve a timezone via the five-source precedence chain
    (``query_param`` -> ``header`` -> ``user_profile`` -> ``tenant_default``
    -> ``fallback``).

    Every candidate passes ``validate_iana_zone()`` **before** entering the
    candidate list, so a garbage zone name falls through to the next source
    with one WARNING rather than raising.

    Args:
        query_param: The raw ``?tz=`` value, if present.
        header: The raw ``X-Timezone`` header value, if present.
        user_profile_zoneinfo: The caller's OIDC ``zoneinfo`` claim.
        tenant_id: The active tenant, or ``None``.
        tenant_defaults_provider: Awaited only when ``tenant_id`` is set.
        default_timezone: The final fallback IANA zone name.
    """

    def _valid(name: str | None, *, source: str) -> "ZoneInfo | None":
        zone = validate_iana_zone(name)
        if name and zone is None:
            logger.warning(
                "invalid IANA zone %r from source=%s; falling through", name, source
            )
        return zone

    tenant_default: "ZoneInfo | None" = None
    if tenant_id is not None:
        defaults = await tenant_defaults_provider.defaults_for(tenant_id)
        tenant_default = _valid(defaults.timezone, source="tenant_default")

    candidates: list[tuple[str, "ZoneInfo | None"]] = [
        ("query_param", _valid(query_param, source="query_param")),
        ("header", _valid(header, source="header")),
        ("user_profile", _valid(user_profile_zoneinfo, source="user_profile")),
        ("tenant_default", tenant_default),
        ("fallback", _valid(default_timezone, source="fallback")),
    ]
    return resolve_precedence(candidates)


def to_user_tz(instant: datetime) -> datetime:
    """
    Convert a UTC-aware ``instant`` to the ambient timezone.

    Args:
        instant: An **aware** ``datetime`` (any timezone — UTC by
            convention, since that's how varco stores everything).

    Returns:
        ``instant`` converted to the ambient timezone, or ``instant``
        unchanged (identity) when no ambient timezone is resolved.

    Raises:
        ValueError: ``instant`` is naive — this is a UTC-aware-in contract.
    """
    if instant.tzinfo is None:
        raise ValueError("to_user_tz() requires an aware datetime, got a naive one")
    zone = current_timezone()
    if zone is None:
        return instant
    return instant.astimezone(zone)


def now_local() -> datetime:
    """``datetime.now()`` in the ambient timezone, or aware-UTC if none is resolved."""
    now = datetime.now(dt_timezone.utc)
    zone = current_timezone()
    return now.astimezone(zone) if zone is not None else now
