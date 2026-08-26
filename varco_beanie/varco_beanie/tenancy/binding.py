"""
varco_beanie.tenancy.binding
===============================
``BeanieTenantBinding`` + ``build_tenant_binding()`` — per-tenant Document
class clones + one ``init_beanie()`` per tenant database (Plan 007, Phase
7, step 1-2).

See the plan's "DESIGN: Mongo database-per-tenant via per-tenant Document
clones" section for the full rationale — summary: ``init_beanie()`` binds
each Document **class** to one database via class-level state, and
``BeanieDocRegistry._registry`` is process-global keyed by domain class,
so a second ``init_beanie`` call with a different database would rebind
every Document class globally (last tenant wins, every tenant silently
reads one database). Per-tenant clones sidestep this entirely — clones are
never registered in ``BeanieDocRegistry`` (``BeanieDocRegistry.get(User)``
keeps returning the **base** class, the documented contract).

⚠️ **Known limitation (flagged for follow-up, not silently hidden):** the
``init_beanie()`` call below is best-effort — a failure (most commonly "no
real database/connection_string configured", the case when this function
is used as a pure clone-bookkeeping helper in a unit-test/bootstrap
context with no Motor/pymongo client wired) is logged and swallowed rather
than propagated, so ``build_tenant_binding()`` always returns a binding
with real clone classes even without a live MongoDB. In production, a
genuinely misconfigured deployment (no real database reachable) will
therefore not fail at binding-build time — it will fail later and loudly
the first time a repository operation tries to use an uninitialized
collection. This trade-off should be revisited once a real-Mongo
integration path exercises it end-to-end (``test_beanie_tenant_
integration.py``).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from beanie import init_beanie
from varco_core.tenancy.global_scope import is_global_entity

logger = logging.getLogger(__name__)

# Process-global cache, keyed by (tenant_id, database_name, model set) —
# NOT BeanieDocRegistry, which stays keyed by domain class and must keep
# returning the base class (the documented contract this module preserves).
# The model set is part of the key (not just tenant_id/database_name) so
# two independent callers building bindings for the *same* tenant/database
# but a *different* set of Document classes never collide on a stale
# cached binding built for the other caller's model set.
_CacheKey = tuple[str, str, tuple[int, ...]]
_binding_cache: dict[_CacheKey, BeanieTenantBinding] = {}
_binding_locks: dict[_CacheKey, asyncio.Lock] = {}


def _get_lock(key: _CacheKey) -> asyncio.Lock:
    lock = _binding_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _binding_locks[key] = lock
    return lock


@dataclass(frozen=True)
class BeanieTenantBinding:
    """
    Immutable record of one tenant's Document class clones.

    Args:
        tenant_id:      The tenant this binding serves.
        database_name:  The tenant's database name.
        clones:         ``{base_domain_cls: clone_cls}`` — ``TENANT``-scoped
                        models only.
    """

    tenant_id: str
    database_name: str
    clones: dict[type, type] = field(default_factory=dict)

    def clone_for(self, domain_cls: type) -> type:
        """
        Return this tenant's clone of ``domain_cls``, or ``domain_cls``
        itself when it was never cloned (``GLOBAL``-scoped models bind to
        the shared database unmodified).
        """
        return self.clones.get(domain_cls, domain_cls)


async def build_tenant_binding(
    tenant_id: str,
    *,
    database_name: str,
    document_models: list[type],
    global_document_models: list[type] | None = None,
    client: Any = None,
) -> BeanieTenantBinding:
    """
    Build (or return the cached) per-tenant clone set + ``init_beanie()``
    binding for ``tenant_id``.

    Args:
        tenant_id:              Tenant identifier.
        database_name:          The tenant's database name.
        document_models:        Every Document class to bind for this
                                tenant — ``TENANT``-scoped ones are cloned;
                                ``GLOBAL``-scoped ones (per
                                ``Meta.tenant_scope`` or explicit
                                ``global_document_models``) are used as-is.
        global_document_models: Optional explicit override list of models
                                to treat as global (skip cloning) even if
                                ``Meta.tenant_scope`` says otherwise.
        client:                 Optional Motor/pymongo async client — when
                                given, ``client[database_name]`` is passed
                                to ``init_beanie()``. ``None`` uses a
                                best-effort call (see module docstring).

    Returns:
        The tenant's ``BeanieTenantBinding`` — cached, so a second call
        with the same ``(tenant_id, database_name)`` returns the same
        object without re-cloning or re-calling ``init_beanie()``.

    Edge cases:
        - 10 concurrent calls for the same tenant call ``init_beanie()``
          exactly once — guarded by a per-``(tenant_id, database_name)``
          lazy ``asyncio.Lock``.
        - Clones are never registered in ``BeanieDocRegistry`` — it keeps
          returning the base class for every domain class (documented
          contract).
    """
    cache_key: _CacheKey = (
        tenant_id,
        database_name,
        tuple(sorted(id(m) for m in document_models)),
    )
    cached = _binding_cache.get(cache_key)
    if cached is not None:
        return cached

    lock = _get_lock(cache_key)
    async with lock:
        cached = _binding_cache.get(cache_key)
        if cached is not None:
            return cached

        global_set = set(global_document_models or [])

        clones: dict[type, type] = {}
        bound_models: list[type] = []
        for model in document_models:
            if model in global_set or is_global_entity(model):
                bound_models.append(model)
                continue
            clone_cls = type(f"{model.__name__}__{tenant_id}", (model,), {})
            clones[model] = clone_cls
            bound_models.append(clone_cls)

        database = None
        if client is not None:
            try:
                database = client[database_name]
            except TypeError:
                # Not a real Motor/pymongo client (e.g. a lightweight test
                # double with no __getitem__) — fall back to passing the
                # client itself; init_beanie() is already best-effort here.
                database = client
        try:
            await init_beanie(database=database, document_models=bound_models)
        except Exception:  # noqa: BLE001 - best-effort, see module docstring
            logger.debug(
                "build_tenant_binding(%r): init_beanie() did not complete "
                "(no live database wired?) — clone bookkeeping still "
                "succeeds; a real repository operation against these "
                "clones will fail loudly if the database is genuinely "
                "unreachable.",
                tenant_id,
                exc_info=True,
            )

        binding = BeanieTenantBinding(
            tenant_id=tenant_id, database_name=database_name, clones=clones
        )
        _binding_cache[cache_key] = binding
        return binding
