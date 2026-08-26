"""
varco_beanie.conversation
=========================
Beanie (pymongo / MongoDB) implementation of ``AbstractConversationStore``.

Persists A2A multi-turn conversation history as individual documents in the
``varco_conversation_turns`` MongoDB collection.  Each turn is one document,
ordered by ``turn_ts`` so ``get()`` returns turns oldest-first.

DESIGN: one document per turn (vs embedded array in a conversation document)
    ✅ Append-only workload — a new insert never rewrites the parent document.
    ✅ turn_count() is a single O(1) count query against an indexed field.
    ✅ Mirrors the SA approach (one row per turn) — consistent across backends.
    ✅ Deletes are a single collection-level filter — no read-modify-write cycle.
    ❌ Reading the full history requires a multi-document find() + sort — slightly
       more expensive than an embedded array for short conversations.  In practice,
       conversation histories are short enough that this is negligible.
    Alternative considered: embedding turns in a ``{task_id, turns: [...]}``
       document.  Rejected because appending to a large array requires a read-
       modify-write cycle on the parent document, which is expensive and prone
       to write-conflict under concurrent append calls.

Collection
----------
``ConversationTurnDocument`` maps to the ``varco_conversation_turns`` MongoDB
collection.  It **must** be registered in your application's ``init_beanie()``
call (or via ``BeanieRepositoryProvider.register()``) before any store method
is invoked::

    from varco_beanie.conversation import ConversationTurnDocument

    # Option A — pass to init_beanie directly
    await init_beanie(database=db, document_models=[..., ConversationTurnDocument])

    # Option B — register with BeanieRepositoryProvider before provider.init()
    provider.register(ConversationTurnDocument)
    await provider.init()

Usage — pass the store to SkillAdapter::

    from varco_beanie.conversation import BeanieConversationStore

    store = BeanieConversationStore()

    adapter = SkillAdapter(
        MyRouter,
        agent_name="MyAgent",
        conversation_store=store,
    )

    # With a MongoDB session (replica set transactions)
    async with uow as session:
        store = BeanieConversationStore(session=session)
        await store.append(task_id, turn)

Thread safety:  ⚠️ ``AsyncClientSession`` is NOT thread-safe.  Use one
                    ``BeanieConversationStore`` per request/task when passing a
                    session.  Session-less (``session=None``) instances are safe.
Async safety:   ✅ All methods are ``async def``.

📚 Docs
- 🔍 https://beanie-odm.dev/tutorial/defining-a-document/
  Beanie Document — class definition and collection configuration
- 📐 https://google.github.io/A2A/specification/
  Google A2A specification — task_id and multi-turn message model
- 🔍 https://www.mongodb.com/docs/manual/core/transactions/
  MongoDB transactions — replica set requirement for sessions
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from beanie import Document
from pydantic import Field
from varco_core.service.conversation import AbstractConversationStore, ConversationTurn

if TYPE_CHECKING:
    from pymongo.asynchronous.client_session import AsyncClientSession

_logger = logging.getLogger(__name__)


# ── ConversationTurnDocument ───────────────────────────────────────────────────


class ConversationTurnDocument(Document):
    """
    Beanie document representing a single turn in an A2A conversation.

    Maps to the ``varco_conversation_turns`` MongoDB collection.  Each turn
    is a separate document — this enables O(1) appends and efficient
    count queries without loading all turns into memory.

    Register this document in your ``init_beanie()`` or
    ``BeanieRepositoryProvider.register()`` call before using
    ``BeanieConversationStore``.

    DESIGN: Beanie Document over raw pymongo collection operations
        ✅ Consistent with the rest of the varco_beanie layer.
        ✅ Built-in async methods (``insert``, ``find``) — no raw BSON
           marshalling needed.
        ✅ ``content: Any`` — Beanie/BSON stores dict, str, and list
           natively without JSON serialisation (unlike the Redis backend
           which must serialise to bytes).
        ❌ Requires ``init_beanie()`` at startup — cannot be used without it.

    Thread safety:  ✅ Document class is a static definition — no mutable state.
    Async safety:   ✅ All Beanie methods are ``async def``.

    Attributes:
        id:       UUIDv4 — unique per turn (auto-generated).
        task_id:  A2A task identifier — indexed for efficient range queries.
        role:     ``"user"`` or ``"agent"``.
        content:  Turn message content.  Stored as native BSON — any dict,
                  str, or list is accepted without manual JSON serialisation.
        turn_ts:  UTC datetime of the turn — used for oldest-first ordering.

    Edge cases:
        - ``content`` is typed ``Any`` because the A2A spec allows dict
          (``{"parts": [...]}``), plain strings, or other serialisable values.
          Beanie/BSON stores all of them without conversion.
        - ``turn_ts`` is used for ordering — all timestamps MUST be UTC to
          avoid comparison errors across deployments in different timezones.
        - The ``task_id`` index is declared in ``Settings.indexes`` so Beanie
          creates it when ``init_beanie()`` is called.  Without it, ``get()``
          and ``turn_count()`` degrade to full-collection scans.
    """

    # UUIDv4 primary key — one UUID per turn, independent of task_id.
    id: UUID = Field(default_factory=uuid4)  # type: ignore[assignment]

    # Indexed — all turn queries filter and delete by task_id.
    task_id: str

    role: str
    """Turn author: ``"user"`` or ``"agent"``."""

    # DESIGN: content typed as Any — BSON stores dict/str/list natively.
    # No JSON serialisation needed unlike Redis (which stores bytes).
    # The Any annotation intentionally accepts any JSON-serialisable value.
    content: Any
    """Raw turn content.  BSON handles dict, str, and list natively."""

    # Always UTC — avoids naive datetime ambiguity across deployments.
    turn_ts: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    """UTC datetime when this turn was recorded — used for oldest-first sort."""

    class Settings:
        """Beanie collection and index configuration."""

        # All conversation turn documents go into this single collection.
        name = "varco_conversation_turns"

        # Index on task_id — enables efficient filter + count per conversation.
        # Without this, get(), turn_count(), and delete() are full-collection
        # scans, which is unacceptable for large collections.
        indexes = ["task_id"]

    def __repr__(self) -> str:
        return (
            f"ConversationTurnDocument("
            f"id={self.id}, "
            f"task_id={self.task_id!r}, "
            f"role={self.role!r}, "
            f"turn_ts={self.turn_ts!r})"
        )


# ── BeanieConversationStore ────────────────────────────────────────────────────


class BeanieConversationStore(AbstractConversationStore):
    """
    Beanie implementation of ``AbstractConversationStore``.

    Persists A2A multi-turn conversation history in the
    ``varco_conversation_turns`` MongoDB collection via
    ``ConversationTurnDocument``.

    Each ``append()`` inserts one document.  ``get()`` retrieves all documents
    for the given ``task_id`` sorted ascending by ``turn_ts`` (oldest first).
    ``turn_count()`` issues a count query — O(1) on the indexed ``task_id``
    field — overriding the slow ABC default (which calls ``get()``).

    NOT a ``@Singleton`` — callers instantiate it directly and pass it to
    ``SkillAdapter``.  This matches the ``RedisConversationStore`` and
    ``SAConversationStore`` patterns.

    Thread safety:  ⚠️ If ``session`` is provided, the same constraints as
                        ``AsyncClientSession`` apply — one per request/task.
                        ``session=None`` instances are safe to share.
    Async safety:   ✅ All methods are ``async def``.

    Args:
        session: Optional pymongo ``AsyncClientSession``.  Pass the session
                 from ``BeanieUnitOfWork`` when running inside a replica set
                 transaction.  ``None`` for non-transactional use (single-node
                 MongoDB or simple stateless append).

    Edge cases:
        - ``ConversationTurnDocument`` must be registered with Beanie (via
          ``init_beanie()`` or ``provider.register()``) before any method is
          called.  A ``RuntimeError`` is raised by Beanie if not.
        - ``get()`` sorts by ``turn_ts`` — ties (same millisecond) are broken
          by MongoDB's natural insertion order but this is not guaranteed.  In
          practice, concurrent appends for the same task_id are rare.
        - ``delete()`` uses ``find(...).delete()`` which returns the deleted
          count.  The return value is intentionally discarded (no-op on zero).
        - ``turn_count()`` returns ``0`` for an unknown ``task_id`` — same
          contract as the ABC default.

    Example::

        from varco_beanie.conversation import BeanieConversationStore
        from varco_core.service.conversation import ConversationTurn

        store = BeanieConversationStore()
        turn = ConversationTurn(role="user", content="Hello!")
        await store.append("task-123", turn)
        history = await store.get("task-123")
        # history == [ConversationTurn(role="user", content="Hello!", ...)]
    """

    def __init__(
        self,
        *,
        session: AsyncClientSession | None = None,
    ) -> None:
        """
        Args:
            session: Optional ``AsyncClientSession`` for transaction support.
                     Pass ``None`` for single-node MongoDB or non-transactional
                     use (the common case).
        """
        # Stored as optional — passed to every Beanie operation that accepts it.
        # None means operations run outside any explicit MongoDB transaction.
        self._session = session

    async def append(self, task_id: str, turn: ConversationTurn) -> None:
        """
        Insert one ``ConversationTurnDocument`` for ``task_id``.

        When ``session`` is set, the insert joins the caller's transaction
        (replica set only).  Without a session, the insert is immediate.

        Args:
            task_id: A2A task identifier.
            turn:    The ``ConversationTurn`` to persist.

        Raises:
            beanie.exceptions.DocumentWasNotSaved: If Beanie fails to insert.
            RuntimeError: If ``ConversationTurnDocument`` was not registered
                          with Beanie via ``init_beanie()``.

        Edge cases:
            - If the session's transaction is aborted after this call, the
              document is never committed — the turn is cleanly rolled back.
            - Without a transaction, the insert is immediately visible.

        Async safety: ✅ Awaits ``document.insert()``.
        """
        doc = ConversationTurnDocument(
            task_id=task_id,
            role=turn.role,
            content=turn.content,
            turn_ts=turn.timestamp,
        )

        # Pass session if available — Beanie's insert() accepts an optional
        # session keyword arg that routes the operation through the transaction.
        if self._session is not None:
            await doc.insert(session=self._session)
        else:
            await doc.insert()

        _logger.debug(
            "BeanieConversationStore.append: inserted turn task_id=%r role=%r",
            task_id,
            turn.role,
        )

    async def get(self, task_id: str) -> list[ConversationTurn]:
        """
        Return all turns for ``task_id`` sorted oldest-first by ``turn_ts``.

        Uses ``find(task_id == ...).sort(+turn_ts).to_list()`` — an indexed
        scan on the ``task_id`` field followed by an in-memory sort by
        ``turn_ts``.  Adding a compound ``{task_id: 1, turn_ts: 1}`` index
        would eliminate the sort for large conversations.

        Args:
            task_id: A2A task identifier.

        Returns:
            List of ``ConversationTurn`` instances, oldest-first.
            Empty list if no conversation exists for this task.

        Edge cases:
            - ``turn_ts`` without timezone info is coerced to UTC — MongoDB
              may return naive datetimes depending on codec configuration.
            - Concurrent ``append()`` calls for the same ``task_id`` may
              produce ties in ``turn_ts``; ordering is best-effort in that case.

        Async safety: ✅ Awaits Beanie ``find()`` cursor.
        """
        find_kwargs: dict[str, Any] = {}
        if self._session is not None:
            find_kwargs["session"] = self._session

        # Filter on task_id (indexed), sort ascending by turn_ts for FIFO order.
        docs = (
            await ConversationTurnDocument.find(
                ConversationTurnDocument.task_id == task_id,
                **find_kwargs,
            )
            .sort(+ConversationTurnDocument.turn_ts)  # type: ignore[operator]
            .to_list()
        )

        turns = [
            ConversationTurn(
                role=doc.role,
                content=doc.content,
                timestamp=(
                    doc.turn_ts
                    if doc.turn_ts.tzinfo is not None
                    # Coerce naive datetimes to UTC — MongoDB can return naive
                    # datetimes depending on codec configuration.
                    else doc.turn_ts.replace(tzinfo=UTC)
                ),
            )
            for doc in docs
        ]

        _logger.debug(
            "BeanieConversationStore.get: fetched %d turns for task_id=%r",
            len(turns),
            task_id,
        )
        return turns

    async def delete(self, task_id: str) -> None:
        """
        Delete all turns for ``task_id`` from the collection.

        No-op if the conversation does not exist (Beanie's delete returns 0 —
        the return value is discarded).

        Args:
            task_id: A2A task identifier.

        Edge cases:
            - Returns silently if no documents match — idempotent.
            - Does NOT raise if ``task_id`` is unknown.

        Async safety: ✅ Awaits Beanie ``find().delete()``.
        """
        find_kwargs: dict[str, Any] = {}
        if self._session is not None:
            find_kwargs["session"] = self._session

        # find().delete() is a bulk-delete — one round-trip regardless of count.
        # The returned DeleteResult is ignored; the no-op contract requires this.
        await ConversationTurnDocument.find(
            ConversationTurnDocument.task_id == task_id,
            **find_kwargs,
        ).delete()

        _logger.debug(
            "BeanieConversationStore.delete: deleted turns for task_id=%r",
            task_id,
        )

    async def turn_count(self, task_id: str) -> int:
        """
        Return the number of turns for ``task_id`` via a count query.

        Overrides the ABC default (which calls ``get()`` and takes ``len()``)
        with a direct ``find(...).count()`` — O(1) on the indexed ``task_id``
        field instead of fetching and deserialising all turn documents.

        Args:
            task_id: A2A task identifier.

        Returns:
            Number of turns.  ``0`` if the conversation is unknown.

        Async safety: ✅ Awaits Beanie ``find().count()``.
        """
        find_kwargs: dict[str, Any] = {}
        if self._session is not None:
            find_kwargs["session"] = self._session

        # count() on an indexed field is O(1) — preferred over len(get()).
        count = await ConversationTurnDocument.find(
            ConversationTurnDocument.task_id == task_id,
            **find_kwargs,
        ).count()

        _logger.debug(
            "BeanieConversationStore.turn_count: task_id=%r count=%d",
            task_id,
            count,
        )
        return count

    def __repr__(self) -> str:
        return (
            f"BeanieConversationStore(" f"session={'set' if self._session else 'None'})"
        )


# ── Public API ────────────────────────────────────────────────────────────────


__all__ = [
    "ConversationTurnDocument",
    "BeanieConversationStore",
]
