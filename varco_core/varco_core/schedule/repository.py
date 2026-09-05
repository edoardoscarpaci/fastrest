"""
varco_core.schedule.repository
=================================
``AbstractScheduleRepository`` — the storage contract for ``Schedule``
(Plan 032 / D6, Step 10), plus ``InMemoryScheduleRepository``, the default
single-process implementation used by unit tests.

DESIGN: a dedicated ABC over reusing ``AsyncRepository[Schedule]``
    Same reasoning as ``varco_core.webhook.base``'s
    ``WebhookSubscriptionRepository``: ``Schedule`` is a framework-owned
    table with a small, fixed access pattern (by id, all enabled — for a
    materializer sweep) rather than the full generic-CRUD-plus-query-AST
    surface ``AsyncRepository`` exposes for application entities. Keeps
    ``varco_sa``/``varco_beanie`` implementations symmetrical with every
    other framework table.
"""

from __future__ import annotations

import abc
import asyncio
from copy import deepcopy
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from varco_core.schedule.entity import Schedule

__all__ = ["AbstractScheduleRepository", "InMemoryScheduleRepository"]


class AbstractScheduleRepository(abc.ABC):
    """
    Storage contract for ``Schedule``.

    Async safety: ✅ All methods are ``async def``.
    """

    @abc.abstractmethod
    async def save(self, schedule: Schedule) -> Schedule:
        """
        Insert (``pk is None``) or update (``pk`` set) ``schedule``.

        Args:
            schedule: The entity to persist.

        Returns:
            The persisted entity, with ``pk`` populated on insert.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def find_by_id(self, pk: object) -> Schedule | None:
        """Return the schedule with primary key ``pk``, or ``None``."""
        raise NotImplementedError

    @abc.abstractmethod
    async def find_all_enabled(self) -> list[Schedule]:
        """
        Return every ``enabled=True`` schedule — the materializer sweep's
        entry point.

        Returns:
            A list, possibly empty. Never includes a disabled schedule.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def delete(self, pk: object) -> None:
        """
        Delete the schedule with primary key ``pk``.

        Edge cases:
            - Deleting an unknown ``pk`` is a no-op.
        """
        raise NotImplementedError


class InMemoryScheduleRepository(AbstractScheduleRepository):
    """
    Single-process ``AbstractScheduleRepository`` backed by a plain dict.

    ⚠️ **Single-process only** — same warning as every other ``InMemory*``
    primitive in this codebase (``InMemoryWebhookSubscriptionRepository``,
    ``InMemoryIdempotencyStore``, ...): no cross-process visibility, no
    durability.

    Thread safety:  ✅ A lazily-created ``asyncio.Lock`` guards every
                       mutation.
    Async safety:   ✅ All methods are ``async def``.
    """

    def __init__(self) -> None:
        self._schedules: dict[object, Schedule] = {}
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def save(self, schedule: Schedule) -> Schedule:
        async with self._get_lock():
            if schedule.pk is None:
                schedule.pk = uuid4()
            schedule._raw_orm = object()
            self._schedules[schedule.pk] = deepcopy(schedule)
        return deepcopy(schedule)

    async def find_by_id(self, pk: object) -> Schedule | None:
        found = self._schedules.get(pk)
        return deepcopy(found) if found is not None else None

    async def find_all_enabled(self) -> list[Schedule]:
        return [deepcopy(s) for s in self._schedules.values() if s.enabled]

    async def delete(self, pk: object) -> None:
        async with self._get_lock():
            self._schedules.pop(pk, None)
