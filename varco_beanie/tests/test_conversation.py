"""
Unit tests for varco_beanie.conversation
=========================================
Covers ``ConversationTurnDocument`` and ``BeanieConversationStore``.

All Beanie collection-level operations (``insert``, ``find``, ``delete``,
``count``) are mocked — no MongoDB connection required.  The conftest
``bypass_beanie_collection_check`` fixture allows instantiating
``ConversationTurnDocument`` without ``init_beanie()``.

Sections
--------
- ``ConversationTurnDocument``  — field defaults, Settings.name, Settings.indexes, repr
- ``BeanieConversationStore``   — construction, repr
- ``append()``                  — inserts doc with correct fields; passes session if set
- ``get()``                     — returns turns oldest-first; empty list when absent
- ``delete()``                  — calls find().delete(); no-op when absent
- ``turn_count()``              — returns count from find().count(); 0 for unknown task
- Integration                   — real MongoDB round-trip via testcontainers

📚 Docs
- 🐍 https://docs.python.org/3/library/unittest.mock.html
  unittest.mock — AsyncMock, MagicMock, patch
- 🔍 https://beanie-odm.dev/tutorial/defining-a-document/
  Beanie Document — Beanie's class and collection configuration
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from varco_beanie.conversation import BeanieConversationStore, ConversationTurnDocument
from varco_core.service.conversation import ConversationTurn


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_turn(
    role: str = "user",
    content: Any = "Hello!",
    ts: datetime | None = None,
) -> ConversationTurn:
    """Build a minimal ConversationTurn for tests."""
    kwargs: dict[str, Any] = {"role": role, "content": content}
    if ts is not None:
        kwargs["timestamp"] = ts
    return ConversationTurn(**kwargs)


def _make_doc(
    task_id: str = "task-1",
    role: str = "user",
    content: Any = "Hello!",
    ts: datetime | None = None,
) -> ConversationTurnDocument:
    """Build a ConversationTurnDocument for mock return values."""
    return ConversationTurnDocument(
        task_id=task_id,
        role=role,
        content=content,
        turn_ts=ts or datetime.now(tz=timezone.utc),
    )


def _make_find_chain(
    docs: list[ConversationTurnDocument],
    *,
    count_value: int | None = None,
) -> MagicMock:
    """
    Build a mock find() chain that supports .sort().to_list(), .delete(),
    and .count() — the three terminal operations used by BeanieConversationStore.

    DESIGN: builds bottom-up so each step's return value is fully configured
    before being set as the return value of the preceding step.

    Args:
        docs:        Documents returned by to_list().
        count_value: Value returned by count().  Defaults to len(docs).

    Returns:
        A MagicMock that behaves like the object returned by
        ``ConversationTurnDocument.find(...)``.
    """
    if count_value is None:
        count_value = len(docs)

    # Terminal: to_list() is async
    fake_to_list = AsyncMock(return_value=docs)
    # Terminal: count() is async
    fake_count = AsyncMock(return_value=count_value)
    # Terminal: delete() is async
    fake_delete = AsyncMock(return_value=None)

    # after_sort: the object returned by .sort(...); exposes .to_list()
    fake_after_sort = MagicMock()
    fake_after_sort.to_list = fake_to_list
    # count() and delete() are available directly on the find result too
    fake_after_sort.count = fake_count
    fake_after_sort.delete = fake_delete

    # find() result: exposes .sort(), .count(), .delete()
    fake_chain = MagicMock()
    fake_chain.sort = MagicMock(return_value=fake_after_sort)
    fake_chain.count = fake_count
    fake_chain.delete = fake_delete

    return fake_chain


# ── ConversationTurnDocument ────────────────────────────────────────────────────


class TestConversationTurnDocument:
    def test_collection_name(self) -> None:
        """Settings.name must match the agreed collection name."""
        assert ConversationTurnDocument.Settings.name == "varco_conversation_turns"

    def test_indexes_include_task_id(self) -> None:
        """task_id must be indexed for efficient range queries."""
        assert "task_id" in ConversationTurnDocument.Settings.indexes

    def test_id_is_uuid(self) -> None:
        doc = _make_doc()
        assert isinstance(doc.id, uuid.UUID)

    def test_id_is_unique_per_instance(self) -> None:
        """Each document gets a fresh UUID — no shared default."""
        doc1 = _make_doc()
        doc2 = _make_doc()
        assert doc1.id != doc2.id

    def test_turn_ts_is_utc_by_default(self) -> None:
        before = datetime.now(tz=timezone.utc)
        doc = _make_doc()
        after = datetime.now(tz=timezone.utc)
        assert before <= doc.turn_ts <= after
        assert doc.turn_ts.tzinfo is not None

    def test_content_accepts_dict(self) -> None:
        """content typed Any — dict (A2A message) must be stored without error."""
        doc = _make_doc(content={"parts": [{"text": "hi"}]})
        assert doc.content == {"parts": [{"text": "hi"}]}

    def test_content_accepts_string(self) -> None:
        """content typed Any — plain string must be stored without error."""
        doc = _make_doc(content="plain text")
        assert doc.content == "plain text"

    def test_repr_contains_key_fields(self) -> None:
        doc = _make_doc(task_id="task-abc", role="agent")
        r = repr(doc)
        assert "ConversationTurnDocument" in r
        assert "task-abc" in r
        assert "agent" in r


# ── BeanieConversationStore construction ───────────────────────────────────────


class TestBeanieConversationStoreConstruction:
    def test_default_no_session(self) -> None:
        store = BeanieConversationStore()
        assert store._session is None

    def test_repr_no_session(self) -> None:
        store = BeanieConversationStore()
        r = repr(store)
        assert "BeanieConversationStore" in r
        assert "None" in r

    def test_repr_with_session(self) -> None:
        fake_session = MagicMock()
        store = BeanieConversationStore(session=fake_session)
        assert "set" in repr(store)


# ── append() ──────────────────────────────────────────────────────────────────


class TestBeanieConversationStoreAppend:
    async def test_append_inserts_document_with_correct_fields(self) -> None:
        """append() must create a document with task_id, role, content, turn_ts."""
        store = BeanieConversationStore()
        turn = _make_turn(role="user", content="Hello!")

        inserted_docs: list[ConversationTurnDocument] = []

        async def _fake_insert(doc: ConversationTurnDocument, **_: Any) -> None:
            inserted_docs.append(doc)

        # Capture the constructed Document instance via a class-level spy on
        # insert() — patching __init__ is fragile with Pydantic metaclasses.
        captured: list[ConversationTurnDocument] = []

        original_insert = ConversationTurnDocument.insert

        async def capturing_insert(self_doc: Any, **kwargs: Any) -> None:
            captured.append(self_doc)

        ConversationTurnDocument.insert = capturing_insert  # type: ignore[method-assign]
        try:
            await store.append("task-99", turn)
        finally:
            ConversationTurnDocument.insert = original_insert  # type: ignore[method-assign]

        assert len(captured) == 1
        doc = captured[0]
        assert doc.task_id == "task-99"
        assert doc.role == "user"
        assert doc.content == "Hello!"
        assert doc.turn_ts == turn.timestamp

    async def test_append_passes_session_to_insert(self) -> None:
        """When session is set, insert() must receive session=<session>."""
        fake_session = MagicMock()
        store = BeanieConversationStore(session=fake_session)
        turn = _make_turn()

        insert_kwargs: list[dict[str, Any]] = []

        async def capturing_insert(self_doc: Any, **kwargs: Any) -> None:
            insert_kwargs.append(kwargs)

        ConversationTurnDocument.insert = capturing_insert  # type: ignore[method-assign]
        try:
            await store.append("task-1", turn)
        finally:
            del ConversationTurnDocument.insert  # type: ignore[attr-defined]

        assert len(insert_kwargs) == 1
        assert insert_kwargs[0].get("session") is fake_session

    async def test_append_no_session_insert_called_without_session(self) -> None:
        """Without a session, insert() must NOT receive a session keyword."""
        store = BeanieConversationStore()
        turn = _make_turn()

        insert_kwargs: list[dict[str, Any]] = []

        async def capturing_insert(self_doc: Any, **kwargs: Any) -> None:
            insert_kwargs.append(kwargs)

        ConversationTurnDocument.insert = capturing_insert  # type: ignore[method-assign]
        try:
            await store.append("task-1", turn)
        finally:
            del ConversationTurnDocument.insert  # type: ignore[attr-defined]

        assert len(insert_kwargs) == 1
        assert "session" not in insert_kwargs[0]


# ── get() ─────────────────────────────────────────────────────────────────────


class TestBeanieConversationStoreGet:
    async def test_get_returns_turns_oldest_first(self) -> None:
        """get() must return ConversationTurn objects in ascending turn_ts order."""
        store = BeanieConversationStore()

        ts1 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        ts2 = datetime(2024, 1, 1, 12, 0, 1, tzinfo=timezone.utc)
        doc1 = _make_doc(role="user", content="first", ts=ts1)
        doc2 = _make_doc(role="agent", content="second", ts=ts2)

        original_task_id = ConversationTurnDocument.__dict__.get("task_id")
        original_turn_ts = ConversationTurnDocument.__dict__.get("turn_ts")
        try:
            # Patch task_id so the == comparison in find() works with a mock
            ConversationTurnDocument.task_id = MagicMock()  # type: ignore[assignment]
            # Patch turn_ts to support __pos__ (__pos__ is used by .sort(+field))
            ConversationTurnDocument.turn_ts = MagicMock()  # type: ignore[assignment]
            ConversationTurnDocument.find = MagicMock(  # type: ignore[method-assign]
                return_value=_make_find_chain([doc1, doc2])
            )
            result = await store.get("task-1")
        finally:
            if original_task_id is not None:
                ConversationTurnDocument.task_id = original_task_id  # type: ignore[assignment]
            elif hasattr(ConversationTurnDocument, "task_id"):
                delattr(ConversationTurnDocument, "task_id")
            if original_turn_ts is not None:
                ConversationTurnDocument.turn_ts = original_turn_ts  # type: ignore[assignment]
            elif hasattr(ConversationTurnDocument, "turn_ts"):
                delattr(ConversationTurnDocument, "turn_ts")
            if hasattr(ConversationTurnDocument, "find"):
                del ConversationTurnDocument.find  # type: ignore[attr-defined]

        assert len(result) == 2
        assert result[0].role == "user"
        assert result[0].content == "first"
        assert result[0].timestamp == ts1
        assert result[1].role == "agent"
        assert result[1].content == "second"
        assert result[1].timestamp == ts2

    async def test_get_returns_empty_list_when_no_turns(self) -> None:
        """get() must return [] for an unknown task_id."""
        store = BeanieConversationStore()

        original_task_id = ConversationTurnDocument.__dict__.get("task_id")
        original_turn_ts = ConversationTurnDocument.__dict__.get("turn_ts")
        try:
            ConversationTurnDocument.task_id = MagicMock()  # type: ignore[assignment]
            ConversationTurnDocument.turn_ts = MagicMock()  # type: ignore[assignment]
            ConversationTurnDocument.find = MagicMock(  # type: ignore[method-assign]
                return_value=_make_find_chain([])
            )
            result = await store.get("task-unknown")
        finally:
            if original_task_id is not None:
                ConversationTurnDocument.task_id = original_task_id  # type: ignore[assignment]
            elif hasattr(ConversationTurnDocument, "task_id"):
                delattr(ConversationTurnDocument, "task_id")
            if original_turn_ts is not None:
                ConversationTurnDocument.turn_ts = original_turn_ts  # type: ignore[assignment]
            elif hasattr(ConversationTurnDocument, "turn_ts"):
                delattr(ConversationTurnDocument, "turn_ts")
            if hasattr(ConversationTurnDocument, "find"):
                del ConversationTurnDocument.find  # type: ignore[attr-defined]

        assert result == []

    async def test_get_coerces_naive_datetime_to_utc(self) -> None:
        """get() must coerce naive turn_ts to UTC — MongoDB can return naive datetimes."""
        store = BeanieConversationStore()

        doc = _make_doc(role="user", content="hi")
        # Simulate MongoDB returning a naive datetime (no tzinfo).
        doc.turn_ts = datetime(2024, 6, 1, 10, 0, 0)  # naive — no tzinfo

        original_task_id = ConversationTurnDocument.__dict__.get("task_id")
        original_turn_ts = ConversationTurnDocument.__dict__.get("turn_ts")
        try:
            ConversationTurnDocument.task_id = MagicMock()  # type: ignore[assignment]
            ConversationTurnDocument.turn_ts = MagicMock()  # type: ignore[assignment]
            ConversationTurnDocument.find = MagicMock(  # type: ignore[method-assign]
                return_value=_make_find_chain([doc])
            )
            result = await store.get("task-1")
        finally:
            if original_task_id is not None:
                ConversationTurnDocument.task_id = original_task_id  # type: ignore[assignment]
            elif hasattr(ConversationTurnDocument, "task_id"):
                delattr(ConversationTurnDocument, "task_id")
            if original_turn_ts is not None:
                ConversationTurnDocument.turn_ts = original_turn_ts  # type: ignore[assignment]
            elif hasattr(ConversationTurnDocument, "turn_ts"):
                delattr(ConversationTurnDocument, "turn_ts")
            if hasattr(ConversationTurnDocument, "find"):
                del ConversationTurnDocument.find  # type: ignore[attr-defined]

        assert result[0].timestamp.tzinfo is not None


# ── delete() ──────────────────────────────────────────────────────────────────


class TestBeanieConversationStoreDelete:
    async def test_delete_calls_delete_on_find_result(self) -> None:
        """delete() must call find().delete() for the given task_id."""
        store = BeanieConversationStore()
        fake_chain = _make_find_chain([])

        original_task_id = ConversationTurnDocument.__dict__.get("task_id")
        try:
            ConversationTurnDocument.task_id = MagicMock()  # type: ignore[assignment]
            ConversationTurnDocument.find = MagicMock(return_value=fake_chain)  # type: ignore[method-assign]
            await store.delete("task-1")
        finally:
            if original_task_id is not None:
                ConversationTurnDocument.task_id = original_task_id  # type: ignore[assignment]
            elif hasattr(ConversationTurnDocument, "task_id"):
                delattr(ConversationTurnDocument, "task_id")
            if hasattr(ConversationTurnDocument, "find"):
                del ConversationTurnDocument.find  # type: ignore[attr-defined]

        # delete() must have been awaited on the find result
        fake_chain.delete.assert_called_once()

    async def test_delete_calls_find_delete_even_when_no_turns_exist(self) -> None:
        """delete() on an unknown task_id must not raise."""
        store = BeanieConversationStore()
        fake_chain = _make_find_chain([])

        original_task_id = ConversationTurnDocument.__dict__.get("task_id")
        try:
            ConversationTurnDocument.task_id = MagicMock()  # type: ignore[assignment]
            ConversationTurnDocument.find = MagicMock(return_value=fake_chain)  # type: ignore[method-assign]
            await store.delete("task-unknown")  # must not raise
        finally:
            if original_task_id is not None:
                ConversationTurnDocument.task_id = original_task_id  # type: ignore[assignment]
            elif hasattr(ConversationTurnDocument, "task_id"):
                delattr(ConversationTurnDocument, "task_id")
            if hasattr(ConversationTurnDocument, "find"):
                del ConversationTurnDocument.find  # type: ignore[attr-defined]

        # Verify delete was still called (the no-op is handled by MongoDB itself)
        fake_chain.delete.assert_called_once()

    async def test_delete_passes_session_to_find(self) -> None:
        """When a session is set, find() must receive session=<session>."""
        fake_session = MagicMock()
        store = BeanieConversationStore(session=fake_session)
        fake_chain = _make_find_chain([])

        find_mock = MagicMock(return_value=fake_chain)

        original_task_id = ConversationTurnDocument.__dict__.get("task_id")
        try:
            ConversationTurnDocument.task_id = MagicMock()  # type: ignore[assignment]
            ConversationTurnDocument.find = find_mock  # type: ignore[method-assign]
            await store.delete("task-1")
        finally:
            if original_task_id is not None:
                ConversationTurnDocument.task_id = original_task_id  # type: ignore[assignment]
            elif hasattr(ConversationTurnDocument, "task_id"):
                delattr(ConversationTurnDocument, "task_id")
            if hasattr(ConversationTurnDocument, "find"):
                del ConversationTurnDocument.find  # type: ignore[attr-defined]

        call_kwargs = find_mock.call_args[1]
        assert call_kwargs.get("session") is fake_session


# ── turn_count() ───────────────────────────────────────────────────────────────


class TestBeanieConversationStoreTurnCount:
    async def test_turn_count_returns_count_from_find(self) -> None:
        """turn_count() must return the integer from find().count()."""
        store = BeanieConversationStore()
        # Simulate 3 existing turns
        fake_chain = _make_find_chain([], count_value=3)

        original_task_id = ConversationTurnDocument.__dict__.get("task_id")
        try:
            ConversationTurnDocument.task_id = MagicMock()  # type: ignore[assignment]
            ConversationTurnDocument.find = MagicMock(return_value=fake_chain)  # type: ignore[method-assign]
            count = await store.turn_count("task-1")
        finally:
            if original_task_id is not None:
                ConversationTurnDocument.task_id = original_task_id  # type: ignore[assignment]
            elif hasattr(ConversationTurnDocument, "task_id"):
                delattr(ConversationTurnDocument, "task_id")
            if hasattr(ConversationTurnDocument, "find"):
                del ConversationTurnDocument.find  # type: ignore[attr-defined]

        assert count == 3

    async def test_turn_count_returns_zero_for_unknown_task(self) -> None:
        """turn_count() must return 0 when no turns exist for the task."""
        store = BeanieConversationStore()
        fake_chain = _make_find_chain([], count_value=0)

        original_task_id = ConversationTurnDocument.__dict__.get("task_id")
        try:
            ConversationTurnDocument.task_id = MagicMock()  # type: ignore[assignment]
            ConversationTurnDocument.find = MagicMock(return_value=fake_chain)  # type: ignore[method-assign]
            count = await store.turn_count("task-never-seen")
        finally:
            if original_task_id is not None:
                ConversationTurnDocument.task_id = original_task_id  # type: ignore[assignment]
            elif hasattr(ConversationTurnDocument, "task_id"):
                delattr(ConversationTurnDocument, "task_id")
            if hasattr(ConversationTurnDocument, "find"):
                del ConversationTurnDocument.find  # type: ignore[attr-defined]

        assert count == 0

    async def test_turn_count_uses_count_not_get(self) -> None:
        """turn_count() must call find().count(), NOT fetch all docs via get()."""
        store = BeanieConversationStore()
        fake_chain = _make_find_chain([], count_value=7)
        find_mock = MagicMock(return_value=fake_chain)

        original_task_id = ConversationTurnDocument.__dict__.get("task_id")
        try:
            ConversationTurnDocument.task_id = MagicMock()  # type: ignore[assignment]
            ConversationTurnDocument.find = find_mock  # type: ignore[method-assign]
            count = await store.turn_count("task-1")
        finally:
            if original_task_id is not None:
                ConversationTurnDocument.task_id = original_task_id  # type: ignore[assignment]
            elif hasattr(ConversationTurnDocument, "task_id"):
                delattr(ConversationTurnDocument, "task_id")
            if hasattr(ConversationTurnDocument, "find"):
                del ConversationTurnDocument.find  # type: ignore[attr-defined]

        assert count == 7
        # count() was called; to_list() (the get() path) was NOT called
        fake_chain.count.assert_called_once()
        # sort().to_list() must NOT have been called
        fake_chain.sort.assert_not_called()


# ── Integration tests ──────────────────────────────────────────────────────────
#
# These tests require Docker.  Run with:
#   pytest varco_beanie/tests/test_conversation.py -m integration
# Or:
#   VARCO_RUN_INTEGRATION=1 pytest varco_beanie/tests/test_conversation.py


pytestmark_integration = pytest.mark.integration

if not os.environ.get("VARCO_RUN_INTEGRATION"):
    # Skip integration section without raising at module level — the unit tests
    # above run normally.  Individual tests below carry their own mark so the
    # -m integration filter still works.
    pass


@pytest.fixture
async def conversation_store_integration(mongo_container_module):  # type: ignore[name-defined]
    """
    Initialise Beanie with ConversationTurnDocument against a real MongoDB.

    Yields a ``BeanieConversationStore`` and drops the database afterwards.

    Depends on the ``mongo_container_module`` fixture defined below.
    """
    from beanie import init_beanie  # noqa: PLC0415
    from pymongo import AsyncMongoClient  # noqa: PLC0415

    db_name = f"test_conv_{uuid.uuid4().hex[:8]}"
    connection_string = mongo_container_module.get_connection_url()

    client = AsyncMongoClient(connection_string)
    await init_beanie(
        database=client[db_name],
        document_models=[ConversationTurnDocument],
    )

    yield BeanieConversationStore()

    await client.drop_database(db_name)
    await client.close()


@pytest.fixture(scope="module")
def mongo_container_module():
    """
    Module-scoped MongoDB testcontainer — shared across all integration tests
    to avoid per-test container startup overhead (~3-5s).

    Requires Docker daemon and testcontainers[mongo].
    """
    if not os.environ.get("VARCO_RUN_INTEGRATION"):
        pytest.skip("Integration tests disabled — set VARCO_RUN_INTEGRATION=1")

    from testcontainers.mongodb import MongoDbContainer  # noqa: PLC0415

    with MongoDbContainer() as mongo:
        yield mongo


@pytest.mark.integration
async def test_integration_append_get_count_delete(
    conversation_store_integration: BeanieConversationStore,
) -> None:
    """
    Full round-trip: append several turns, get returns them in order,
    turn_count matches, delete empties the conversation.
    """
    store = conversation_store_integration
    task_id = f"task-{uuid.uuid4().hex[:8]}"

    # Append three turns with explicit timestamps for deterministic ordering.
    ts1 = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    ts2 = datetime(2024, 1, 1, 10, 0, 1, tzinfo=timezone.utc)
    ts3 = datetime(2024, 1, 1, 10, 0, 2, tzinfo=timezone.utc)

    turn1 = ConversationTurn(role="user", content="First", timestamp=ts1)
    turn2 = ConversationTurn(role="agent", content="Second", timestamp=ts2)
    turn3 = ConversationTurn(role="user", content="Third", timestamp=ts3)

    await store.append(task_id, turn1)
    await store.append(task_id, turn2)
    await store.append(task_id, turn3)

    # get() must return all turns in ascending turn_ts order
    history = await store.get(task_id)
    assert len(history) == 3
    assert history[0].role == "user"
    assert history[0].content == "First"
    assert history[1].role == "agent"
    assert history[1].content == "Second"
    assert history[2].role == "user"
    assert history[2].content == "Third"

    # turn_count() must match without fetching all docs
    count = await store.turn_count(task_id)
    assert count == 3

    # delete() must remove all turns
    await store.delete(task_id)

    history_after = await store.get(task_id)
    assert history_after == []

    count_after = await store.turn_count(task_id)
    assert count_after == 0


@pytest.mark.integration
async def test_integration_get_unknown_task_returns_empty(
    conversation_store_integration: BeanieConversationStore,
) -> None:
    """get() on a never-seen task_id must return []."""
    store = conversation_store_integration
    result = await store.get("task-never-seen-xyz")
    assert result == []


@pytest.mark.integration
async def test_integration_turn_count_unknown_task_returns_zero(
    conversation_store_integration: BeanieConversationStore,
) -> None:
    """turn_count() on a never-seen task_id must return 0."""
    store = conversation_store_integration
    count = await store.turn_count("task-never-seen-abc")
    assert count == 0


@pytest.mark.integration
async def test_integration_delete_unknown_task_is_noop(
    conversation_store_integration: BeanieConversationStore,
) -> None:
    """delete() on a never-seen task_id must not raise."""
    store = conversation_store_integration
    await store.delete("task-never-seen-delete")  # must not raise


@pytest.mark.integration
async def test_integration_content_dict_round_trips(
    conversation_store_integration: BeanieConversationStore,
) -> None:
    """dict content (A2A message format) must survive a MongoDB round-trip."""
    store = conversation_store_integration
    task_id = f"task-dict-{uuid.uuid4().hex[:8]}"
    content = {
        "parts": [{"text": "Hello!"}, {"image_url": "http://example.com/img.png"}]
    }

    turn = ConversationTurn(role="user", content=content)
    await store.append(task_id, turn)

    history = await store.get(task_id)
    assert len(history) == 1
    assert history[0].content == content
