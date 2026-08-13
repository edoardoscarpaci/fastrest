"""
varco_core.tenancy.global_scope
==================================
``GlobalUoWProvider`` — a distinct DI-token wrapper for the non-routed,
non-tenant UoW (Plan 007, Phase 2, step 3-4).

DESIGN: distinct type, not a parameter on ``IUoWProvider``
    See the plan's "DESIGN: GlobalUoWProvider as a distinct DI token"
    section — a full rationale lives there. Short version: no change to
    ``IUoWProvider``'s ABC, and a distinct wrapper type is how providify
    disambiguates two things that are both "a UoW provider".

RD-10: the app-facing global credential is read-only by default; the
SQLSTATE 42501 -> ``GlobalScopeReadOnlyError`` translation itself lives in
``varco_sa.tenancy.global_scope`` (driver specifics stay out of core) — this
module only owns the contract (the exception + ``is_global_entity``).
"""

from __future__ import annotations

from typing import Any

from varco_core.tenancy.catalog import TenantIsolationError
from varco_core.tenancy.settings import TenantScope


class GlobalScopeReadOnlyError(TenantIsolationError):
    """
    Raised when a write to a ``GLOBAL`` entity is denied by a read-only
    global credential (RD-10).

    The app-facing global credential is read-only by default — a legible
    error naming the entity and both remedies, instead of a raw driver
    SQLSTATE 42501 traceback.
    """

    def __init__(self, entity_name: str) -> None:
        super().__init__(
            f"Write to global-scoped entity {entity_name!r} was refused: "
            "the global credential is read-only by default (RD-10). "
            "Remedies: (1) route the write through the tenant control plane "
            "instead of an app pod, or (2) opt in explicitly with "
            "TenancySettings(global_writable=True) / "
            "VARCO_TENANCY_GLOBAL_WRITABLE=true."
        )
        self.entity_name = entity_name


def is_global_entity(entity_cls: type) -> bool:
    """
    Return ``True`` when ``entity_cls`` is declared ``TenantScope.GLOBAL``.

    Reads ``Meta.tenant_scope`` directly (mirrors ``MetaReader``'s reading
    convention) rather than requiring a full ``ParsedMeta`` — usable from
    call sites that only have the raw domain class.
    """
    meta_cls = getattr(entity_cls, "Meta", None)
    raw = getattr(meta_cls, "tenant_scope", TenantScope.TENANT)
    return TenantScope(raw) == TenantScope.GLOBAL


class GlobalUoWProvider:
    """
    ``IUoWProvider``-shaped wrapper for the non-routed global/shared scope.

    Deliberately **not** an ``IUoWProvider`` subclass registered under that
    same DI token — a distinct class is what lets providify bind
    ``Inject[GlobalUoWProvider]`` separately from ``Inject[IUoWProvider]``
    (or ``Inject[DynamicTenantUoWProvider]``).

    Args:
        delegate: The underlying non-tenant-routed UoW provider (e.g. an
                  ``SQLAlchemyRepositoryProvider`` bound to the global/
                  control-plane database).

    ``make_uow()`` never consults ``current_tenant()`` — it works, and
    behaves identically, whether or not a ``tenant_context()`` is active.
    """

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    def make_uow(self) -> Any:
        return self._delegate.make_uow()
