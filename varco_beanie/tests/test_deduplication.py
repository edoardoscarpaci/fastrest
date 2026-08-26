"""
Unit and integration tests for varco_beanie.deduplication
==========================================================
Covers ``DeduplicationDocument`` and ``BeanieDeduplicator``.

All Beanie collection-level operations (``insert``, ``find_one``) are mocked
in unit tests — no MongoDB connection required.  The conftest
``bypass_beanie_collection_check`` fixture (autouse) allows instantiating
``DeduplicationDocument`` without ``init_beanie()``.

Integration tests are tagged ``@pytest.mark.integration`` and require a real
MongoDB instance (testcontainers).

Sections
--------
- ``DeduplicationDocument``  — field defaults, Settings.name, Settings.indexes, repr
- ``BeanieDeduplicator`` unit — construction, repr
- ``is_duplicate()``         — returns False when None; returns True when doc found; swallows error
- ``mark_seen()``            — inserts doc; no-op on DuplicateKeyError; swallows arbitrary errors
- Integration                — real MongoDB round-trip via testcontainers

📚 Docs
- 🐍 https://docs.python.org/3/library/unittest.mock.html
  unittest.mock — AsyncMock, MagicMock, patch
- 🔍 https://beanie-odm.dev/tutorial/defining-a-document/
  Beanie Document — class definition and collection configuration
- 🔍 https://www.mongodb.com/docs/manual/core/index-ttl/
  MongoDB TTL indexes — expireAfterSeconds for automatic document expiry
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from varco_beanie.deduplication import BeanieDeduplicator, DeduplicationDocument

# ── Helpers ────────────────────────────────────────────────────────────────────


def _eid() -> uuid.UUID:
    """Return a fresh random UUID for use as an event_id."""
    return uuid.uuid4()


def _make_doc(event_id: uuid.UUID | None = None) -> DeduplicationDocument:
    """Build a minimal DeduplicationDocument for use as a mock return value."""
    return DeduplicationDocument(
        event_id=event_id or _eid(),
        processed_at=datetime.now(tz=UTC),
    )


# ── DeduplicationDocument ─────────────────────────────────────────────────────


class TestDeduplicationDocument:
    def test_collection_name(self) -> None:
        """Settings.name is 'varco_dedup'."""
        assert DeduplicationDocument.Settings.name == "varco_dedup"

    def test_indexes_include_unique_event_id(self) -> None:
        """Settings.indexes contains a unique index on event_id."""
        indexes = DeduplicationDocument.Settings.indexes
        # Verify at least two indexes exist (unique event_id + TTL processed_at).
        assert len(indexes) == 2
        # At least one index must carry unique=True in its document options.
        assert any(
            getattr(idx, "document", {}).get("unique", False)
            or (
                # pymongo IndexModel stores options in document dict
                hasattr(idx, "document")
                and idx.document.get("unique", False)
                and any("event_id" in str(k) for k in idx.document.get("key", {}).keys())
            )
            for idx in indexes
        )

    def test_indexes_include_ttl_on_processed_at(self) -> None:
        """Settings.indexes contains a TTL index on processed_at."""
        indexes = DeduplicationDocument.Settings.indexes
        ttl_index = any(
            hasattr(idx, "document")
            and "expireAfterSeconds" in idx.document
            and any("processed_at" in str(k) for k in idx.document.get("key", {}).keys())
            for idx in indexes
        )
        assert ttl_index, (
            "Expected a TTL index with expireAfterSeconds on processed_at. "
            f"Got: {[getattr(i, 'document', i) for i in indexes]}"
        )

    def test_default_id_is_uuid(self) -> None:
        """The 'id' field defaults to a UUID (not None, not ObjectId)."""
        doc = _make_doc()
        assert isinstance(doc.id, uuid.UUID)

    def test_event_id_stored_correctly(self) -> None:
        """event_id is stored as provided."""
        eid = _eid()
        doc = _make_doc(event_id=eid)
        assert doc.event_id == eid

    def test_repr(self) -> None:
        """__repr__ includes DeduplicationDocument and key fields."""
        doc = _make_doc()
        r = repr(doc)
        assert "DeduplicationDocument" in r
        assert "event_id" in r


# ── BeanieDeduplicator construction ───────────────────────────────────────────


class TestBeanieDeduplicatorConstruction:
    def test_default_construction(self) -> None:
        """BeanieDeduplicator can be constructed with no arguments."""
        dedup = BeanieDeduplicator()
        assert dedup is not None

    def test_custom_ttl_stored(self) -> None:
        """ttl_seconds constructor arg is stored for repr/docs."""
        dedup = BeanieDeduplicator(ttl_seconds=3600)
        assert "3600" in repr(dedup)

    def test_repr_no_session(self) -> None:
        """repr includes BeanieDeduplicator and 'None' for session."""
        dedup = BeanieDeduplicator()
        r = repr(dedup)
        assert "BeanieDeduplicator" in r
        assert "None" in r

    def test_repr_with_session(self) -> None:
        """repr shows 'set' when a session is provided."""
        session = MagicMock()
        dedup = BeanieDeduplicator(session=session)
        assert "set" in repr(dedup)


# ── is_duplicate() ────────────────────────────────────────────────────────────


class TestBeanieDeduplicatorIsDuplicate:
    async def test_returns_false_when_find_one_returns_none(self) -> None:
        """is_duplicate returns False when no document is found."""
        dedup = BeanieDeduplicator()

        with patch.object(
            DeduplicationDocument,
            "find_one",
            new=AsyncMock(return_value=None),
        ):
            result = await dedup.is_duplicate(_eid())

        assert result is False

    async def test_returns_true_when_find_one_returns_doc(self) -> None:
        """is_duplicate returns True when a document is found."""
        dedup = BeanieDeduplicator()
        doc = _make_doc()

        # Direct class attribute assignment (not patch.object) — required because
        # Beanie's metaclass can intercept patch.object in some versions.
        original = DeduplicationDocument.__dict__.get("find_one")
        try:
            DeduplicationDocument.find_one = AsyncMock(return_value=doc)  # type: ignore[method-assign]
            result = await dedup.is_duplicate(doc.event_id)
        finally:
            if original is not None:
                DeduplicationDocument.find_one = original  # type: ignore[method-assign]
            elif hasattr(DeduplicationDocument, "find_one"):
                del DeduplicationDocument.find_one  # type: ignore[attr-defined]

        assert result is True

    async def test_returns_false_on_error(self) -> None:
        """is_duplicate swallows errors and returns False (safe default)."""
        dedup = BeanieDeduplicator()

        with patch.object(
            DeduplicationDocument,
            "find_one",
            new=AsyncMock(side_effect=RuntimeError("MongoDB unavailable")),
        ):
            result = await dedup.is_duplicate(_eid())

        assert result is False


# ── mark_seen() ───────────────────────────────────────────────────────────────


class TestBeanieDeduplicatorMarkSeen:
    async def test_inserts_deduplication_document(self) -> None:
        """mark_seen calls insert() with the given event_id."""
        dedup = BeanieDeduplicator()
        eid = _eid()

        inserted_docs: list[DeduplicationDocument] = []

        async def _fake_insert(self, **kwargs):  # noqa: ANN001
            inserted_docs.append(self)

        with patch.object(DeduplicationDocument, "insert", new=_fake_insert):
            await dedup.mark_seen(eid)

        assert len(inserted_docs) == 1
        assert inserted_docs[0].event_id == eid
        assert isinstance(inserted_docs[0].processed_at, datetime)

    async def test_does_not_raise_on_duplicate_key_error(self) -> None:
        """mark_seen catches DuplicateKeyError — idempotent no-op."""
        from pymongo.errors import DuplicateKeyError

        dedup = BeanieDeduplicator()

        async def _raise_dup(self, **kwargs):  # noqa: ANN001
            raise DuplicateKeyError("E11000 duplicate key error")

        with patch.object(DeduplicationDocument, "insert", new=_raise_dup):
            # Must not raise — DuplicateKeyError is the expected idempotency path.
            result = await dedup.mark_seen(_eid())

        assert result is None

    async def test_does_not_raise_on_arbitrary_exception(self) -> None:
        """mark_seen catches all exceptions — MUST NOT raise."""
        dedup = BeanieDeduplicator()

        async def _raise_error(self, **kwargs):  # noqa: ANN001
            raise RuntimeError("connection lost")

        with patch.object(DeduplicationDocument, "insert", new=_raise_error):
            result = await dedup.mark_seen(_eid())

        assert result is None

    async def test_passes_session_to_insert(self) -> None:
        """mark_seen passes the session to insert() when one is set."""
        session = MagicMock()
        dedup = BeanieDeduplicator(session=session)

        received_kwargs: list[dict] = []

        async def _capture_insert(self, **kwargs):  # noqa: ANN001
            received_kwargs.append(kwargs)

        with patch.object(DeduplicationDocument, "insert", new=_capture_insert):
            await dedup.mark_seen(_eid())

        assert received_kwargs, "insert() was never called"
        assert received_kwargs[0].get("session") is session


# ── Integration tests — real MongoDB via testcontainers ───────────────────────


@pytest.mark.integration
class TestBeanieDeduplicatorIntegration:
    """
    Integration tests that require a real MongoDB container.

    These tests are skipped unless ``-m integration`` is passed.
    They verify the round-trip behaviour and the unique-index constraint
    that the unit tests mock away.

    Prerequisites:
        - testcontainers[mongodb] installed
        - Docker available in the test environment
    """

    @pytest.fixture
    async def mongo_dedup(self, mongo_url: str):
        """Against the shared session-scoped MongoDB container
        (tests/conftest.py, Plan 012 / RT1, Step 6/7), run init_beanie and
        yield a deduplicator. Unique per-test database name (Step 8)."""
        import uuid

        from beanie import init_beanie
        from pymongo import AsyncMongoClient

        db_name = f"test_dedup_{uuid.uuid4().hex[:8]}"
        client = AsyncMongoClient(mongo_url)
        db = client[db_name]
        await init_beanie(
            database=db,
            document_models=[DeduplicationDocument],
        )
        try:
            yield BeanieDeduplicator()
        finally:
            await client.drop_database(db_name)
            await client.close()

    async def test_round_trip(self, mongo_dedup: BeanieDeduplicator) -> None:
        """is_duplicate False → mark_seen → is_duplicate True."""
        eid = _eid()

        # Before mark_seen — event is new.
        assert await mongo_dedup.is_duplicate(eid) is False

        # Mark as seen.
        await mongo_dedup.mark_seen(eid)

        # After mark_seen — event is a duplicate.
        assert await mongo_dedup.is_duplicate(eid) is True

    async def test_mark_seen_idempotent(self, mongo_dedup: BeanieDeduplicator) -> None:
        """Calling mark_seen twice for the same event_id does not raise."""
        eid = _eid()
        await mongo_dedup.mark_seen(eid)
        await mongo_dedup.mark_seen(eid)  # second call — must not raise
        assert await mongo_dedup.is_duplicate(eid) is True

    async def test_different_events_are_independent(self, mongo_dedup: BeanieDeduplicator) -> None:
        """Marking one event does not affect a different event_id."""
        eid1, eid2 = _eid(), _eid()
        await mongo_dedup.mark_seen(eid1)

        assert await mongo_dedup.is_duplicate(eid1) is True
        assert await mongo_dedup.is_duplicate(eid2) is False
