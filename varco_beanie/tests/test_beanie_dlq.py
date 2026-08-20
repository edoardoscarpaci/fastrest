"""
Unit + integration tests for varco_beanie.dlq — Plan 009, Phase 5 (R7).
==========================================================================
``BeanieDeadLetterQueue`` / ``DeadLetterDocument`` — mirrors
``varco_redis/tests/test_redis_dlq.py`` structure.

RED until ``varco_beanie/dlq.py`` lands.

Unit tests rely on the autouse ``bypass_beanie_collection_check`` fixture
(``conftest.py``) so ``DeadLetterDocument`` can be constructed without a real
MongoDB. Integration tests (``@pytest.mark.integration``) need a real Mongo
container (testcontainers[mongodb]) and are skipped without ``-m
integration``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from varco_core.event import Event
from varco_core.event.dlq import DeadLetterEntry


class OrderPlacedEvent(Event):
    __event_type__ = "test.order.placed.beanie_dlq"
    order_id: str = "ord-1"


def _entry(**kwargs) -> DeadLetterEntry:
    defaults = dict(
        event=OrderPlacedEvent(),
        channel="orders",
        handler_name="H.h",
        error_type="RuntimeError",
        error_message="boom",
        attempts=1,
    )
    defaults.update(kwargs)
    return DeadLetterEntry(**defaults)


class TestDeadLetterDocumentSettings:
    def test_collection_name_matches_sa_table(self) -> None:
        from varco_beanie.dlq import DeadLetterDocument

        assert DeadLetterDocument.Settings.name == "varco_dead_letters"

    def test_no_ttl_index_declared_by_default(self) -> None:
        """RD-2: no TTL index by default -- dead letters are never silently
        deleted."""
        from varco_beanie.dlq import DeadLetterDocument

        indexes = getattr(DeadLetterDocument.Settings, "indexes", [])
        assert not any("expireAfterSeconds" in str(idx) for idx in indexes)


class TestBeanieDeadLetterQueueConstruction:
    def test_supports_random_access_is_true(self) -> None:
        from varco_beanie.dlq import BeanieDeadLetterQueue

        dlq = BeanieDeadLetterQueue()
        assert dlq.supports_random_access is True

    def test_ttl_seconds_logs_warning_at_construction(self, caplog) -> None:
        import logging

        from varco_beanie.dlq import BeanieDeadLetterQueue

        with caplog.at_level(logging.WARNING):
            BeanieDeadLetterQueue(ttl_seconds=3600)
        assert any(
            "data loss" in r.message.lower() or "ttl" in r.message.lower()
            for r in caplog.records
        )


class TestBeanieDIBindingHealth:
    async def test_scan_varco_beanie_and_validate_bindings(self) -> None:
        """No-Docker unit test: DI binding health for the DLQ registration in
        bootstrap() (the per-package 'green suite, dead container' guard)."""
        from providify import DIContainer
        from varco_beanie.dlq import BeanieDeadLetterQueue
        from varco_core.event.dlq import AbstractDeadLetterQueue

        container = DIContainer()
        container.scan("varco_beanie", recursive=True)
        container.validate_bindings()

        resolved = await container.aget(AbstractDeadLetterQueue)
        assert isinstance(resolved, BeanieDeadLetterQueue)


# ── Integration tests — real MongoDB via testcontainers ────────────────────────


@pytest.mark.integration
class TestBeanieDeadLetterQueueIntegration:
    @pytest.fixture
    async def dlq(self, mongo_url: str):
        # The local per-test MongoDbContainer was replaced by the shared
        # session-scoped mongo_url fixture (tests/conftest.py, Plan 012 /
        # RT1, Step 6/7) — unique per-test database name (Step 8).
        import uuid

        from beanie import init_beanie
        from pymongo import AsyncMongoClient

        from varco_beanie.dlq import BeanieDeadLetterQueue, DeadLetterDocument

        db_name = f"test_beanie_dlq_{uuid.uuid4().hex[:8]}"
        client = AsyncMongoClient(mongo_url)
        db = client[db_name]
        await init_beanie(database=db, document_models=[DeadLetterDocument])
        try:
            yield BeanieDeadLetterQueue()
        finally:
            await client.drop_database(db_name)
            await client.close()

    async def test_push_pop_ack_count_round_trip(self, dlq) -> None:
        entry = _entry()
        await dlq.push(entry)
        assert await dlq.count() == 1

        batch = await dlq.pop_batch(limit=10)
        assert len(batch) == 1
        assert await dlq.count() == 1  # pop_batch is non-destructive (RD-2)

        await dlq.ack(entry.entry_id)
        assert await dlq.count() == 0

    async def test_get_by_id(self, dlq) -> None:
        entry = _entry()
        await dlq.push(entry)

        fetched = await dlq.get(entry.entry_id)
        assert fetched is not None
        assert fetched.entry_id == entry.entry_id

    async def test_list_entries_filters(self, dlq) -> None:
        await dlq.push(_entry(channel="orders"))
        await dlq.push(_entry(channel="payments"))

        results = await dlq.list_entries(channel="orders")
        assert all(e.channel == "orders" for e in results)

    async def test_delete_where_chunked_sweep(self, dlq) -> None:
        import asyncio

        for _ in range(5):
            await dlq.push(_entry())

        cutoff = datetime.now(timezone.utc)
        await asyncio.sleep(0.01)

        total = 0
        while True:
            deleted = await dlq.delete_where(older_than=cutoff, limit=2)
            total += deleted
            if deleted == 0:
                break
        assert total == 5

    async def test_duplicate_push_is_idempotent(self, dlq) -> None:
        entry = _entry()
        await dlq.push(entry)
        await dlq.push(entry)  # same entry_id -- DuplicateKeyError swallowed
        assert await dlq.count() == 1

    async def test_push_swallows_induced_write_error(self, dlq, monkeypatch) -> None:
        from varco_beanie.dlq import DeadLetterDocument

        async def _boom(self, *args, **kwargs):
            raise RuntimeError("induced write failure")

        monkeypatch.setattr(DeadLetterDocument, "insert", _boom, raising=False)
        # push() must never raise -- the ABC contract.
        await dlq.push(_entry())
