"""
varco_beanie.deduplication
==========================
Beanie (pymongo / MongoDB) implementation of ``AbstractDeduplicator``.

``BeanieDeduplicator`` uses a ``DeduplicationDocument`` stored in the
``varco_dedup`` MongoDB collection.  Duplicate detection uses a
``find_one(event_id == ...)`` query; idempotent marking is achieved by
catching ``DuplicateKeyError`` on insert (the unique index on ``event_id``
makes double-inserts an error, which is the expected success path).

TTL cleanup
-----------
MongoDB's server-side TTL index on ``processed_at`` automatically removes
documents after ``expireAfterSeconds`` seconds without any application-side
cleanup job.  This mirrors Redis's per-key TTL expiry and is the main
advantage over the SA implementation (which requires explicit ``purge_expired()``).

DESIGN: MongoDB TTL index over application-side cleanup
    ✅ No background task or cron needed — MongoDB's TTL thread handles it.
    ✅ Server-side expiry is precise to ~60-second granularity (Mongo reaper).
    ✅ Scales to millions of events without table-growth concerns.
    ❌ TTL is fixed at index-definition time (``expireAfterSeconds=86400`` in
       ``Settings.indexes``).  Runtime-configurable TTL requires dropping and
       recreating the index — not supported by this implementation.
       The ``ttl_seconds`` constructor argument is accepted for documentation
       and testing purposes only; it does NOT alter the MongoDB index.

DESIGN: unique index on event_id + DuplicateKeyError catch for idempotency
    ✅ MongoDB enforces uniqueness at the DB level — no app-side check needed.
    ✅ ``mark_seen`` is a simple ``insert()`` — fast, no read-before-write.
    ✅ Concurrent ``mark_seen`` calls for the same event_id: only one succeeds,
       the rest get ``DuplicateKeyError`` which is caught and swallowed.
    ❌ ``DuplicateKeyError`` must be imported from ``pymongo.errors`` — a direct
       pymongo dependency (already transitive via beanie).

DESIGN: session-optional constructor
    ✅ ``session=None`` works on single-node MongoDB (the common case for
       deduplication — no transaction needed).
    ✅ Passing a session lets callers include ``mark_seen`` in a replica set
       transaction (e.g. atomically with a domain entity save).
    ❌ If ``session`` is provided, the same session constraints apply:
       one ``BeanieDeduplicator`` per request/task.

Collection
----------
``DeduplicationDocument`` maps to the ``varco_dedup`` MongoDB collection.
Include it in your ``init_beanie()`` call::

    from varco_beanie.deduplication import DeduplicationDocument

    await init_beanie(database=db, document_models=[..., DeduplicationDocument])

Usage::

    from varco_beanie.deduplication import BeanieDeduplicator

    dedup = BeanieDeduplicator()

    class OrderConsumer(EventConsumer):
        @listen(OrderPlacedEvent, channel="orders", deduplicator=dedup)
        async def on_order(self, event: OrderPlacedEvent) -> None:
            await self._process(event)   # called at most once per event_id in TTL

Thread safety:  ⚠️ If ``session`` is set: one ``BeanieDeduplicator`` per task.
                    Session-less (``session=None``) instances are safe to share.
Async safety:   ✅ All methods are ``async def``.

📚 Docs
- 🔍 https://beanie-odm.dev/tutorial/defining-a-document/
  Beanie Document — class definition and collection configuration
- 🔍 https://www.mongodb.com/docs/manual/core/index-ttl/
  MongoDB TTL indexes — server-side document expiry via expireAfterSeconds.
- 🔍 https://www.mongodb.com/docs/manual/core/index-unique/
  MongoDB unique indexes — enforces one document per event_id at DB level.
- 🐍 https://pymongo.readthedocs.io/en/stable/api/pymongo/errors.html
  pymongo.errors.DuplicateKeyError — raised when unique index is violated.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel
from varco_core.event.deduplication import AbstractDeduplicator

if TYPE_CHECKING:
    from pymongo.asynchronous.client_session import AsyncClientSession

_logger = logging.getLogger(__name__)

# Default TTL for the MongoDB TTL index — 24 hours, matching RedisDeduplicator.
# IMPORTANT: This constant is embedded in the class body (Settings.indexes) at
# class-definition time.  Changing it at runtime has no effect on the index;
# the index must be dropped and recreated on MongoDB.
_DEFAULT_TTL_SECONDS: int = 86_400


# ── DeduplicationDocument ─────────────────────────────────────────────────────


class DeduplicationDocument(Document):
    """
    Beanie document representing a single processed event for deduplication.

    Maps to the ``varco_dedup`` MongoDB collection.

    Register this document in your ``init_beanie()`` or
    ``BeanieRepositoryProvider.register()`` call before using
    ``BeanieDeduplicator``::

        from varco_beanie.deduplication import DeduplicationDocument
        await init_beanie(database=db, document_models=[..., DeduplicationDocument])

    DESIGN: event_id as a separate unique-indexed field (not as ``id``)
        ``id`` defaults to a random ``uuid4()`` — this keeps Beanie's document
        identity separate from the deduplication key.  ``event_id`` carries the
        unique constraint, and the TTL index is on ``processed_at``.  This
        matches the two-field pattern used by ``InboxDocument`` (where ``id``
        is the entry_id, not the event_id).

        Alternative considered: using ``event_id`` directly as ``id``
            Rejected — would require overriding Beanie's default PK handling
            and may cause issues with other Beanie internals that depend on
            ObjectId-like PK behaviour.  Separating concerns is safer.

    Thread safety:  ✅ Document class is a static definition — no mutable state.
    Async safety:   ✅ All Beanie methods are ``async def``.

    Attributes:
        id:           UUIDv4 surrogate key — internal document identity.
        event_id:     The event UUID being deduplicated — unique-indexed.
        processed_at: UTC timestamp of when the event was marked as seen.
                      This field is also the TTL index key — documents are
                      automatically deleted ``_DEFAULT_TTL_SECONDS`` after
                      ``processed_at``.

    Edge cases:
        - ``processed_at`` is always set at insert time — there is no
          ``None`` sentinel (unlike ``InboxDocument.processed_at``).
        - The unique index on ``event_id`` causes ``DuplicateKeyError`` on
          double-insert — ``BeanieDeduplicator.mark_seen`` catches this.
        - MongoDB's TTL reaper runs approximately every 60 seconds — documents
          may persist up to 60 s beyond ``processed_at + ttl``.
        - ``id`` (surrogate) must be included in ``init_beanie()`` calls so
          Beanie registers the collection and its indexes.
    """

    # Surrogate PK — separate from event_id to avoid Beanie PK complications.
    id: UUID = Field(default_factory=uuid4)  # type: ignore[assignment]

    # The actual deduplication key — unique-indexed at DB level.
    event_id: UUID

    # Timestamp used for BOTH human-readable audit AND the TTL index key.
    # MongoDB's TTL index expires the document ``expireAfterSeconds`` after
    # this field's value.
    processed_at: datetime

    class Settings:
        """Beanie collection and index configuration."""

        # Collection name — all varco deduplication state goes here.
        name = "varco_dedup"

        indexes = [
            # Unique index on event_id — enforces at-most-one record per event.
            # DuplicateKeyError from this index is how mark_seen achieves
            # idempotency without a read-before-write.
            IndexModel([("event_id", ASCENDING)], unique=True),
            # TTL index on processed_at — MongoDB automatically deletes
            # documents after expireAfterSeconds seconds past processed_at.
            # DESIGN: TTL is baked in at index-definition time (86 400 s = 24 h).
            # Runtime changes require dropping and recreating the index.
            IndexModel(
                [("processed_at", ASCENDING)],
                expireAfterSeconds=_DEFAULT_TTL_SECONDS,
            ),
        ]

    def __repr__(self) -> str:
        return (
            f"DeduplicationDocument(event_id={self.event_id}, processed_at={self.processed_at!r})"
        )


# ── BeanieDeduplicator ────────────────────────────────────────────────────────


class BeanieDeduplicator(AbstractDeduplicator):
    """
    Beanie/MongoDB-backed message deduplicator.

    Tracks processed events as ``DeduplicationDocument`` records in the
    ``varco_dedup`` collection.  MongoDB's TTL index provides automatic
    cleanup; no application-side purge task is needed.

    DESIGN: Beanie Document methods over raw pymongo collection operations
        ✅ Consistent with the rest of the varco_beanie layer.
        ✅ Built-in async methods (``find_one``, ``insert``) — no BSON marshalling.
        ✅ Beanie handles UUID ↔ BSON Binary mapping via the model config.
        ❌ Requires ``init_beanie()`` at startup with ``DeduplicationDocument``
           registered.  ``RuntimeError`` is raised by Beanie if not.

    Thread safety:  ⚠️ If ``session`` is set, the same ``AsyncClientSession``
                        constraints apply — use one instance per request/task.
                        Session-less instances are safe to share across tasks.
    Async safety:   ✅ All methods are ``async def``.

    Args:
        session:     Optional pymongo ``AsyncClientSession`` for transaction
                     support.  Pass ``None`` (default) for non-transactional use.
        ttl_seconds: Accepted for documentation and test configuration purposes.
                     Does NOT alter the MongoDB TTL index — the index uses the
                     value baked into ``DeduplicationDocument.Settings.indexes``
                     (``_DEFAULT_TTL_SECONDS`` = 86 400 s).  To change the
                     actual TTL, drop and recreate the index on MongoDB.

    Edge cases:
        - ``DeduplicationDocument`` must be registered with ``init_beanie()``
          before any method is called.  Beanie raises ``RuntimeError`` otherwise.
        - ``is_duplicate`` returns ``False`` on any error (safe default —
          prefer processing the event over silently dropping it).
        - ``mark_seen`` MUST NOT raise — errors are logged and swallowed.
        - Two concurrent ``mark_seen`` calls for the same ``event_id``: only
          one insert succeeds; the other gets ``DuplicateKeyError`` which is
          caught as an expected idempotency condition and swallowed.

    Example::

        dedup = BeanieDeduplicator()   # session-less, safe to share

        class OrderConsumer(EventConsumer):
            @listen(OrderPlacedEvent, channel="orders", deduplicator=dedup)
            async def on_order(self, event: OrderPlacedEvent) -> None:
                await process(event)
    """

    def __init__(
        self,
        *,
        session: AsyncClientSession | None = None,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        """
        Args:
            session:     Optional MongoDB session for transaction participation.
                         ``None`` for non-transactional (most common) use.
            ttl_seconds: For documentation / test configuration only — does NOT
                         change the MongoDB TTL index on the collection.
        """
        # Stored to pass through to Beanie operations that accept a session
        # keyword argument (replica set transaction support).
        self._session = session
        # Stored for __repr__ and documentation purposes.  Does not affect
        # the actual MongoDB TTL index which is defined at class/index level.
        self._ttl_seconds = ttl_seconds

    # ── AbstractDeduplicator interface ────────────────────────────────────────

    async def is_duplicate(self, event_id: UUID) -> bool:
        """
        Return ``True`` if a non-expired deduplication record exists for ``event_id``.

        Executes a ``find_one(event_id == ...)`` — MongoDB will not return
        documents that the TTL thread has already expired.

        Args:
            event_id: Event UUID to check.

        Returns:
            ``True`` if a ``DeduplicationDocument`` with this ``event_id``
            exists in the collection (and has not yet been TTL-expired).
            ``False`` if no record exists, the TTL has elapsed, or any error
            occurs.

        Thread safety:  ⚠️ Session-bearing instances: one per task.
        Async safety:   ✅ Single ``find_one`` await.

        Edge cases:
            - MongoDB's TTL reaper runs ~every 60 s — documents may persist
              slightly beyond their TTL.  ``is_duplicate`` may return ``True``
              for up to 60 s after the TTL expires.
            - Any DB error returns ``False`` — safe default (process the event
              rather than silently drop it on transient failure).
        """
        find_kwargs: dict[str, Any] = {}
        if self._session is not None:
            find_kwargs["session"] = self._session

        try:
            # DESIGN: raw pymongo filter dict over Beanie ExpressionField comparison
            # Using {"event_id": event_id} instead of
            # ``DeduplicationDocument.event_id == event_id`` avoids initializing
            # Beanie's ExpressionField machinery (which requires a live DB connection
            # from get_pymongo_collection()) in unit tests.  The raw dict is passed
            # through by Beanie's find_one() unchanged — behaviour is identical.
            doc = await DeduplicationDocument.find_one(
                {"event_id": event_id},
                **find_kwargs,
            )
            return doc is not None
        except Exception as exc:
            # Safe default: process the event rather than silently drop it.
            _logger.warning(
                "BeanieDeduplicator.is_duplicate failed for event_id=%s: %s. "
                "Returning False (will process event).",
                event_id,
                exc,
            )
            return False

    async def mark_seen(self, event_id: UUID) -> None:
        """
        Record that ``event_id`` has been successfully processed.

        Inserts a ``DeduplicationDocument`` with ``processed_at = now(UTC)``.
        If a document with the same ``event_id`` already exists, MongoDB raises
        ``DuplicateKeyError`` (unique index violation) — this is caught and
        treated as an idempotency success (the event was already marked).

        Does NOT raise — any exception (including unexpected DB errors) is
        logged and swallowed, per the ``AbstractDeduplicator.mark_seen`` contract.

        Args:
            event_id: Event UUID to mark as processed.

        Thread safety:  ⚠️ Session-bearing instances: one per task.
        Async safety:   ✅ Single ``insert`` await.

        Edge cases:
            - Already-seen ``event_id``: ``DuplicateKeyError`` — caught,
              logged at DEBUG level (not an error), and swallowed.
            - MongoDB unavailable: logs the error and returns without raising.
            - ``DeduplicationDocument`` not registered with ``init_beanie()``:
              Beanie raises ``RuntimeError`` — caught and swallowed (marks will
              be lost; events may be re-processed).
        """
        try:
            # Import here to avoid top-level import of pymongo.errors — keeps
            # the module importable even if pymongo is not installed.
            # In practice pymongo is a transitive dep of beanie, so this
            # import always succeeds in normal usage.

            now = datetime.now(UTC)
            doc = DeduplicationDocument(event_id=event_id, processed_at=now)

            insert_kwargs: dict[str, Any] = {}
            if self._session is not None:
                insert_kwargs["session"] = self._session

            await doc.insert(**insert_kwargs)

        except Exception as exc:
            # mark_seen MUST NOT raise — contract from AbstractDeduplicator.
            # Two cases warrant different log levels:
            #   1. DuplicateKeyError → DEBUG (expected idempotency path)
            #   2. Everything else   → ERROR (unexpected failure)
            try:
                from pymongo.errors import DuplicateKeyError as _DKE

                if isinstance(exc, _DKE):
                    _logger.debug(
                        "BeanieDeduplicator.mark_seen: event_id=%s already exists "
                        "(DuplicateKeyError — idempotent no-op).",
                        event_id,
                    )
                    return
            except ImportError:
                pass  # pymongo not importable — fall through to generic error log

            _logger.error(
                "BeanieDeduplicator.mark_seen failed for event_id=%s: %s",
                event_id,
                exc,
            )

    # ── Repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"BeanieDeduplicator("
            f"ttl_seconds={self._ttl_seconds}, "
            f"session={'set' if self._session else 'None'})"
        )


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    "BeanieDeduplicator",
    "DeduplicationDocument",
]
