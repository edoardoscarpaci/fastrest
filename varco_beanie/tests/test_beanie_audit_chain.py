"""
Integration tests for varco_beanie.audit — Plan 009, Phase 12 (R8) hash chaining.
====================================================================================
``BeanieAuditRepository(hash_chain=True)`` uses a dedicated
``varco_audit_seq`` counter document (``find_one_and_update({$inc})``) to
establish the chain — mirrors ``varco_sa/tests/test_sa_audit_chain.py``.

RED until ``hash_chain=`` lands on ``BeanieAuditRepository``. Requires a real
MongoDB container (testcontainers[mongodb]); skipped without Docker.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC

import pytest
from varco_core.service.audit import AuditEntry, AuditRepository


def _entry(**kwargs) -> AuditEntry:
    defaults = dict(entity_type="Order", entity_id="1", action="create")
    defaults.update(kwargs)
    return AuditEntry(**defaults)


class TestBeanieAuditSeqCollectionResolution:
    """
    Unit-level guard (no Docker) for the ``CollectionWasNotInitialized``
    regression: ``hash_chain=True`` must not require a second Beanie
    registration for ``AuditSeqDocument``.
    """

    def test_regression_seq_collection_rides_on_audit_documents_database(self, monkeypatch) -> None:
        """
        User reports: the first ``hash_chain=True`` save raised
        ``beanie.exceptions.CollectionWasNotInitialized`` after following the
        feature guide, which only ever documents registering ``AuditDocument``.
        Correct behaviour is that the ``varco_audit_seq`` counter is resolved
        from ``AuditDocument``'s own (already initialised) database, because
        the counter is used purely as a raw pymongo ``find_one_and_update``
        target — no ODM feature of ``AuditSeqDocument`` is ever exercised.
        """
        from varco_beanie import audit as audit_mod

        class _FakeDatabase:
            def __init__(self) -> None:
                self.requested: list[str] = []

            def __getitem__(self, name: str) -> str:
                self.requested.append(name)
                return f"collection:{name}"

        fake_db = _FakeDatabase()

        class _FakeCollection:
            database = fake_db

        monkeypatch.setattr(
            audit_mod.AuditDocument,
            "get_pymongo_collection",
            classmethod(lambda cls: _FakeCollection()),
        )

        assert audit_mod._seq_collection() == "collection:varco_audit_seq"
        assert fake_db.requested == ["varco_audit_seq"]

    def test_regression_audit_seq_document_is_exported(self) -> None:
        """
        ``AuditSeqDocument`` was reachable but absent from ``__all__`` and from
        every doc, so nobody could discover the registration the old code
        silently required.  It stays exported as schema documentation.
        """
        from varco_beanie import audit as audit_mod

        assert "AuditSeqDocument" in audit_mod.__all__
        assert audit_mod.AuditSeqDocument.Settings.name == "varco_audit_seq"


class TestBeanieAuditChainBsonPrecision:
    """
    Unit-level guard (no Docker) for the BSON millisecond-truncation
    regression that made ``verify_chain()`` report a ``HashMismatch`` on every
    link of a Beanie-persisted chain.
    """

    def test_regression_occurred_at_truncated_to_bson_millisecond_resolution(
        self,
    ) -> None:
        """
        User reports: a freshly written Beanie hash chain fails
        ``verify_chain()`` with a ``HashMismatch`` on every entry.  Correct
        behaviour is an unbroken chain, because ``entry_hash()`` must be
        recomputable from what was *stored* — and BSON datetimes hold only
        milliseconds, so a microsecond-precision ``occurred_at`` never
        round-trips.
        """
        from datetime import datetime

        from varco_beanie.audit import _to_bson_precision

        dt = datetime(2026, 8, 18, 21, 38, 35, 121255, tzinfo=UTC)
        assert _to_bson_precision(dt).microsecond == 121000
        # Idempotent — an already-aligned value is untouched.
        assert _to_bson_precision(_to_bson_precision(dt)) == _to_bson_precision(dt)

    def test_regression_hash_survives_a_mongo_round_trip(self) -> None:
        """
        The digest computed at save time must equal the digest recomputed from
        the value MongoDB gives back (simulated here by truncating to ms).
        """
        from varco_beanie.audit import _to_bson_precision

        entry = _entry(prev_hash=None, seq=1)
        stored = dataclasses.replace(entry, occurred_at=_to_bson_precision(entry.occurred_at))
        # What Mongo returns on read-back: the truncated timestamp.
        round_tripped = dataclasses.replace(stored)

        assert stored.entry_hash() == round_tripped.entry_hash()
        # And the untruncated original really does hash differently — proving
        # the guard is testing something real.
        if entry.occurred_at.microsecond % 1000:
            assert entry.entry_hash() != stored.entry_hash()

    def test_regression_verify_chain_holds_over_truncated_timestamps(self) -> None:
        """Two chained entries, both ms-truncated, verify as one chain."""
        from varco_beanie.audit import _to_bson_precision

        first = dataclasses.replace(
            _entry(entity_id="0"),
            seq=1,
            prev_hash=None,
        )
        first = dataclasses.replace(first, occurred_at=_to_bson_precision(first.occurred_at))
        second = dataclasses.replace(_entry(entity_id="1"), seq=2, prev_hash=first.entry_hash())
        second = dataclasses.replace(second, occurred_at=_to_bson_precision(second.occurred_at))

        assert AuditRepository.verify_chain([first, second]) is True


@pytest.mark.integration
class TestBeanieAuditChainIntegration:
    @pytest.fixture
    async def chained_repo(self, mongo_url: str):
        # The local per-test MongoDbContainer was replaced by the shared
        # session-scoped mongo_url fixture (tests/conftest.py, Plan 012 /
        # RT1, Step 6/7) — per-test namespacing rule: a unique database name
        # per test, dropped on teardown.
        import uuid

        from beanie import init_beanie
        from pymongo import AsyncMongoClient
        from varco_beanie.audit import AuditDocument, BeanieAuditRepository

        db_name = f"test_beanie_audit_chain_{uuid.uuid4().hex[:8]}"
        client = AsyncMongoClient(mongo_url)
        db = client[db_name]
        await init_beanie(database=db, document_models=[AuditDocument])
        try:
            yield BeanieAuditRepository(hash_chain=True)
        finally:
            await client.drop_database(db_name)
            await client.close()

    async def test_sequential_saves_produce_a_verifiable_chain(self, chained_repo) -> None:
        for i in range(5):
            await chained_repo.save(_entry(entity_id=str(i)))

        _ = await chained_repo.list_for_entity("Order", "0")
        all_entries = []
        for i in range(5):
            all_entries.extend(await chained_repo.list_for_entity("Order", str(i)))
        # Order isn't guaranteed across entities -- verify_chain needs seq order.
        all_entries.sort(key=lambda e: e.seq or 0)
        assert AuditRepository.verify_chain(all_entries) is True

    async def test_counter_document_created_with_upsert_on_first_write(self, chained_repo) -> None:
        """Beanie's counter document missing -> created on first write with
        upsert=True."""
        await chained_repo.save(_entry())
        entries = await chained_repo.list_for_entity("Order", "1")
        assert len(entries) == 1
        assert entries[0].seq is not None
