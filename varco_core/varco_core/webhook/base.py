"""
varco_core.webhook.base
========================
``WebhookSubscriptionRepository`` — the storage contract for
``WebhookSubscription`` (Plan 031 / D4a, Step 1, §D-D4-entity), plus
``InMemoryWebhookSubscriptionRepository``, the default single-process
implementation used by unit tests and the fast dev path.

DESIGN: a dedicated ABC over reusing ``AsyncRepository[WebhookSubscription]``
    ✅ ``WebhookSubscription`` is a framework-owned table (like the DLQ, the
       outbox, the idempotency store) with exactly two access patterns the
       dispatcher/admin surface actually need — by id and by tenant — not
       the full generic-CRUD-plus-query-AST surface ``AsyncRepository``
       exposes for application entities.
    ✅ Keeps ``varco_sa``/``varco_beanie`` implementations symmetrical with
       every other framework table (``SAIdempotencyStore``, ``SADlq``, …):
       own table, own ``MetaData``, ``register_framework_metadata()``, a
       manual dataclass↔row mapping — never the ``@register`` ORM
       generator.
    ❌ A second repository shape in the codebase — accepted; the framework
       table convention already has ten precedents.

Thread safety:  ⚠️ Implementation-defined — see each concrete subclass.
Async safety:   ✅ All methods are ``async def``.
"""

from __future__ import annotations

import abc
import asyncio
from copy import deepcopy
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from varco_core.webhook.models import WebhookSubscription

__all__ = ["WebhookSubscriptionRepository", "InMemoryWebhookSubscriptionRepository"]


class WebhookSubscriptionRepository(abc.ABC):
    """
    Storage contract for ``WebhookSubscription`` (§D-D4-entity).

    Implementations MUST scope ``find_by_tenant`` strictly — a subscription
    belonging to tenant A must never be returned for tenant B (guarded by
    the conformance-style tests in every backend's integration suite and by
    the admin surface's cross-tenant test).

    Async safety: ✅ All methods are ``async def``.
    """

    @abc.abstractmethod
    async def save(self, subscription: WebhookSubscription) -> WebhookSubscription:
        """
        Insert (``pk is None``) or update (``pk`` set) ``subscription``.

        Args:
            subscription: The entity to persist.

        Returns:
            The persisted entity, with ``pk`` populated on insert.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def find_by_id(self, pk: object) -> WebhookSubscription | None:
        """
        Return the subscription with primary key ``pk``, or ``None``.

        Args:
            pk: The primary key to look up.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def find_by_tenant(self, tenant_id: str) -> list[WebhookSubscription]:
        """
        Return every subscription owned by ``tenant_id``.

        Args:
            tenant_id: The owning tenant.

        Returns:
            A list, possibly empty. Never includes another tenant's rows.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def find_active_matching(
        self, event_type: str, *, tenant_id: str | None = None
    ) -> list[WebhookSubscription]:
        """
        Return every ``ACTIVE`` subscription whose ``event_patterns`` match
        ``event_type``.

        Args:
            event_type: The event type name to match (e.g. ``"order.created"``).
            tenant_id:  Optional tenant scope. ``None`` matches across all
                        tenants — used by the dispatcher, which receives
                        events already carrying their own tenant context in
                        the payload rather than at the bus level.

        Returns:
            A list of matching, ``ACTIVE`` subscriptions. ``DISABLED``
            subscriptions are never returned here.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def delete(self, pk: object) -> None:
        """
        Delete the subscription with primary key ``pk``.

        Edge cases:
            - Deleting an unknown ``pk`` is a no-op.
        """
        raise NotImplementedError


def _matches_pattern(event_type: str, pattern: str) -> bool:
    """
    Glob-style match (``"order.*"`` matches ``"order.created"``).

    Uses ``fnmatch`` semantics via a minimal manual implementation to avoid
    importing ``fnmatch`` for a single ``*`` wildcard use case — kept
    intentionally simple; a future pattern language upgrade is a
    same-signature swap.
    """
    import fnmatch

    return fnmatch.fnmatch(event_type, pattern)


class InMemoryWebhookSubscriptionRepository(WebhookSubscriptionRepository):
    """
    Single-process ``WebhookSubscriptionRepository`` backed by a plain dict.

    ⚠️ **Single-process only** — same warning as every other
    ``InMemory*`` primitive in this codebase (``InMemoryIdempotencyStore``,
    ``InMemoryRateLimiter``). Use ``SAWebhookSubscriptionRepository`` or
    ``BeanieWebhookSubscriptionRepository`` for any multi-process deployment.

    Thread safety:  ❌ Not thread-safe across OS threads.
    Async safety:   ✅ Coroutine-safe within one event loop via a lazily
                       created ``asyncio.Lock`` (CLAUDE.md: never construct
                       a lock at module scope or in ``__init__``).
    """

    def __init__(self) -> None:
        self._rows: dict[object, WebhookSubscription] = {}
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def save(self, subscription: WebhookSubscription) -> WebhookSubscription:
        """See ``WebhookSubscriptionRepository.save()``."""
        async with self._get_lock():
            if subscription.pk is None:
                object.__setattr__(subscription, "pk", uuid4())
            object.__setattr__(subscription, "_raw_orm", object())
            self._rows[subscription.pk] = deepcopy(subscription)
            return deepcopy(subscription)

    async def find_by_id(self, pk: object) -> WebhookSubscription | None:
        """See ``WebhookSubscriptionRepository.find_by_id()``."""
        async with self._get_lock():
            row = self._rows.get(pk)
            return deepcopy(row) if row is not None else None

    async def find_by_tenant(self, tenant_id: str) -> list[WebhookSubscription]:
        """See ``WebhookSubscriptionRepository.find_by_tenant()``."""
        async with self._get_lock():
            return [deepcopy(r) for r in self._rows.values() if r.tenant_id == tenant_id]

    async def find_active_matching(
        self, event_type: str, *, tenant_id: str | None = None
    ) -> list[WebhookSubscription]:
        """See ``WebhookSubscriptionRepository.find_active_matching()``."""
        async with self._get_lock():
            results = []
            for row in self._rows.values():
                if row.status != "ACTIVE":
                    continue
                if tenant_id is not None and row.tenant_id != tenant_id:
                    continue
                if any(_matches_pattern(event_type, p) for p in row.event_patterns):
                    results.append(deepcopy(row))
            return results

    async def delete(self, pk: object) -> None:
        """See ``WebhookSubscriptionRepository.delete()``."""
        async with self._get_lock():
            self._rows.pop(pk, None)
