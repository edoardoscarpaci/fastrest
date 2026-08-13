"""
varco_core.tenancy.cache_key
==============================
``tenancy_cache_key()`` — namespaces a cache key iff the entity is
``TENANT``-scoped (Plan 007, Phase 2, step 9-10).

Symmetric pitfall this closes: a ``TENANT``-scoped key that is *not*
namespaced is a cross-tenant leak; a ``GLOBAL``-scoped key that *is*
namespaced is N× cache waste and N× DB load. Both directions are asserted.
"""

from __future__ import annotations


def tenancy_cache_key(entity_cls: type, key: str) -> str:
    """
    Build a cache key, namespaced by the active tenant iff ``entity_cls`` is
    ``TENANT``-scoped.

    Args:
        entity_cls: The domain entity whose ``Meta.tenant_scope`` decides
                    namespacing.
        key:        The entity-local key component (e.g. a primary key).

    Returns:
        ``f"tenant:{tenant_id}:{entity_cls.__name__}:{key}"`` for a
        ``TENANT``-scoped entity; ``f"global:{entity_cls.__name__}:{key}"``
        (identical across every tenant) for a ``GLOBAL``-scoped entity.

    Raises:
        RuntimeError: ``entity_cls`` is ``TENANT``-scoped and called outside
            any ``tenant_context()`` — fails closed rather than emitting an
            unnamespaced (leak-prone) key.
    """
    from varco_core.service.tenant import current_tenant
    from varco_core.tenancy.global_scope import is_global_entity

    if is_global_entity(entity_cls):
        return f"global:{entity_cls.__name__}:{key}"

    tenant_id = current_tenant()
    if tenant_id is None:
        raise RuntimeError(
            f"tenancy_cache_key({entity_cls.__name__!r}, ...) called outside "
            "a tenant_context() block. A TENANT-scoped cache key is never "
            "emitted unnamespaced — wrap the call with "
            "`with tenant_context(tenant_id): ...`."
        )
    return f"tenant:{tenant_id}:{entity_cls.__name__}:{key}"
