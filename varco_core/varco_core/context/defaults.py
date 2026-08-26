"""
varco_core.context.defaults
==============================
RD-2 — per-tenant locale/timezone defaults, **without** a
``varco_tenants`` schema change.

``TenantDescriptor`` (``varco_core.tenancy.catalog``) intentionally has no
locale/timezone fields — adding them there means an Alembic revision, a
Beanie document change, and a migration obligation for every existing
deployment, for a value most tenants will never set.

Instead: ``TenantDefaultsProvider``, a ``runtime_checkable`` ``Protocol``.
Apps that keep tenant preferences in their own table implement it in ~ten
lines; apps that don't pay nothing. varco does **not** cache the result
implicitly — an implicit per-tenant cache with no invalidation path is a
support ticket. Wrap the call in the app's own cache if it needs one.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

__all__ = [
    "TenantLocalizationDefaults",
    "TenantDefaultsProvider",
    "NullTenantDefaults",
    "StaticTenantDefaults",
]


@dataclasses.dataclass(frozen=True)
class TenantLocalizationDefaults:
    """A tenant's default locale/timezone, both optional."""

    locale: str | None = None
    timezone: str | None = None


@runtime_checkable
class TenantDefaultsProvider(Protocol):
    """
    Resolves a tenant's default locale/timezone.

    Implementations are awaited by the I2/T1 precedence chains for the
    ``tenant_default`` candidate slot.
    """

    async def defaults_for(self, tenant_id: str) -> TenantLocalizationDefaults:
        """Return the locale/timezone defaults for ``tenant_id``."""
        ...


class NullTenantDefaults:
    """
    The default binding — returns ``TenantLocalizationDefaults(None, None)``
    for every tenant. Zero I/O.
    """

    async def defaults_for(self, tenant_id: str) -> TenantLocalizationDefaults:
        return TenantLocalizationDefaults(locale=None, timezone=None)


class StaticTenantDefaults:
    """
    An in-memory ``{tenant_id: TenantLocalizationDefaults}`` mapping —
    convenient for tests and small, static deployments.

    Args:
        mapping: Tenant ID to defaults mapping. A tenant absent from the
            mapping resolves to ``TenantLocalizationDefaults(None, None)``,
            same as ``NullTenantDefaults``.
    """

    def __init__(self, mapping: Mapping[str, TenantLocalizationDefaults]) -> None:
        self._mapping = dict(mapping)

    async def defaults_for(self, tenant_id: str) -> TenantLocalizationDefaults:
        return self._mapping.get(tenant_id, TenantLocalizationDefaults(locale=None, timezone=None))
