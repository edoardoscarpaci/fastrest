"""
Unit tests for varco_sa.dlq — Plan 009, Phase 2 (R3) / Phase 4 (R1) additions.
================================================================================
Covers ``SADeadLetterQueue.get`` / ``list_entries`` (filters) /
``supports_random_access`` / ``delete_where`` (SQLite chunked sweep) using an
in-memory SQLite database (aiosqlite) — same pattern as ``test_sa_outbox.py``.

RED until these methods land on ``SADeadLetterQueue``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, UTC

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from varco_core.event import Event
from varco_core.event.dlq import DeadLetterEntry
from varco_sa.dlq import SADeadLetterQueue


class OrderPlacedEvent(Event):
    __event_type__ = "test.order.placed.sa_dlq"
    order_id: str = "ord-1"


@pytest_asyncio.fixture
async def dlq():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    d = SADeadLetterQueue(engine)
    await d.ensure_table()
    yield d
    await engine.dispose()


def _entry(channel: str = "orders", **kwargs) -> DeadLetterEntry:
    defaults = dict(
        event=OrderPlacedEvent(),
        channel=channel,
        handler_name="H.h",
        error_type="RuntimeError",
        error_message="boom",
        attempts=1,
    )
    defaults.update(kwargs)
    return DeadLetterEntry(**defaults)


class TestSADeadLetterQueueRandomAccessFlag:
    async def test_supports_random_access_is_true(self, dlq: SADeadLetterQueue) -> None:
        assert dlq.supports_random_access is True


class TestSADeadLetterQueueGet:
    async def test_get_returns_pushed_entry(self, dlq: SADeadLetterQueue) -> None:
        entry = _entry()
        await dlq.push(entry)
        fetched = await dlq.get(entry.entry_id)
        assert fetched is not None
        assert fetched.entry_id == entry.entry_id

    async def test_get_unknown_id_returns_none(self, dlq: SADeadLetterQueue) -> None:
        import uuid

        assert await dlq.get(uuid.uuid4()) is None


class TestSADeadLetterQueueListEntries:
    async def test_list_entries_is_non_destructive(
        self, dlq: SADeadLetterQueue
    ) -> None:
        await dlq.push(_entry())
        entries = await dlq.list_entries()
        assert len(entries) == 1
        assert await dlq.count() == 1  # still there

    async def test_list_entries_filters_by_channel(
        self, dlq: SADeadLetterQueue
    ) -> None:
        await dlq.push(_entry(channel="orders"))
        await dlq.push(_entry(channel="payments"))
        entries = await dlq.list_entries(channel="payments")
        assert len(entries) == 1
        assert entries[0].channel == "payments"


class TestSADeadLetterQueueDeleteWhereSweep:
    async def test_chunked_sweep_deletes_all_matching(
        self, dlq: SADeadLetterQueue
    ) -> None:
        for _ in range(5):
            await dlq.push(_entry())

        cutoff = datetime.now(UTC) + timedelta(seconds=1)

        total = 0
        while True:
            deleted = await dlq.delete_where(older_than=cutoff, limit=2)
            total += deleted
            if deleted == 0:
                break

        assert total == 5
        assert await dlq.count() == 0

    async def test_delete_where_no_predicate_raises_value_error(
        self, dlq: SADeadLetterQueue
    ) -> None:
        import pytest

        with pytest.raises(ValueError):
            await dlq.delete_where()
