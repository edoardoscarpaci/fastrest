"""
varco_core.tenancy.provider
=============================
``DynamicTenantUoWProvider`` — an ``IUoWProvider`` that routes ``make_uow()``
through a ``TenantResourcePool`` keyed by the ambient ``tenant_context()``
(Plan 007, Phase 1, step 7-8).

DESIGN: a *new* class, not a change to ``TenantUoWProvider``
    ✅ Backwards compatibility is absolute — ``varco_core/service/tenant.py``
       is not modified at all; ``TenantUoWProvider`` (static/registered
       providers) keeps working exactly as today.
    ✅ Reuses ``current_tenant()``/``tenant_context()`` unchanged — one
       ContextVar-based routing mechanism for both static and dynamic
       tenant routing.
    ❌ Two "tenant UoW provider" classes now exist. Accepted — they solve
       different problems (static registration vs. pool-backed dynamic
       per-tenant engines/bindings under ``SCHEMA``/``DATABASE``).

**Asserted (never a default-DB fallback):** a tenant that is *active* but
was never ``ensure()``d by the pool raises ``RuntimeError`` naming
``ensure()`` — the async pre-warm step this call is a consequence of
(``init_beanie`` is async; ``make_uow()`` is sync, so it can never call
``ensure()`` itself).

Thread safety:  N/A — single-event-loop async design.
Async safety:   ✅ Reads ``current_tenant()`` (ContextVar) and ``pool.peek()``
                   (sync, never creates) — ``make_uow()`` itself does no I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from varco_core.tenancy.pool import TenantResourcePool


class _PeekablePool(Protocol):
    def peek(self, tenant_id: str) -> Any: ...


class DynamicTenantUoWProvider:
    """
    ``IUoWProvider`` that routes ``make_uow()`` to a pool-resident
    per-tenant ``IUoWProvider``.

    Args:
        pool: A ``TenantResourcePool`` (or any object exposing a sync
              ``peek(tenant_id)``) whose cached resources are themselves
              ``IUoWProvider``-like objects (e.g. an
              ``SQLAlchemyRepositoryProvider`` wired for one tenant's
              engine).

    Raises:
        RuntimeError: ``make_uow()`` called outside ``tenant_context()``,
            or with an active tenant that was never ``ensure()``d by the
            pool (asserted — this provider never falls back to a default
            provider).
    """

    def __init__(self, pool: "TenantResourcePool[Any] | _PeekablePool") -> None:
        self._pool = pool

    def make_uow(self) -> Any:
        # Imported lazily (not at module scope) to avoid a circular import:
        # varco_core.meta imports varco_core.tenancy.settings for
        # ParsedMeta.tenant_scope, and varco_core.service.tenant transitively
        # imports varco_core.model, which imports varco_core.meta.
        from varco_core.service.tenant import current_tenant

        tenant_id = current_tenant()
        if tenant_id is None:
            raise RuntimeError(
                "DynamicTenantUoWProvider.make_uow() called outside a "
                "tenant_context() block. Wrap each request with: "
                "with tenant_context(tenant_id): ..."
            )

        provider = self._pool.peek(tenant_id)
        if provider is None:
            raise RuntimeError(
                f"Tenant {tenant_id!r} is active but its resource was never "
                f"pre-warmed via pool.ensure({tenant_id!r}). "
                "DynamicTenantUoWProvider never falls back to a default "
                "provider — call `await pool.ensure(tenant_id)` (e.g. from "
                "TenantResolutionMiddleware) before routing requests for "
                "this tenant."
            )

        make_uow = getattr(provider, "make_uow", None)
        if make_uow is None:
            # The pooled resource is itself the object identifying this
            # tenant's UoW (rather than a provider wrapping one) — return
            # it directly rather than requiring every pooled resource type
            # to expose a make_uow() method.
            return provider
        return make_uow()
