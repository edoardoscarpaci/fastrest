"""
varco_sa.dlq
============
SQLAlchemy async implementation of ``AbstractDeadLetterQueue`` (Plan 005
Phase 3, Step 42 — a deliberate scope addition, not an upstream ask).

⚠️ **Scope note** (see plan 005, Phase 3, Step 42): U-6 explicitly states a
durable DLQ is *not* an upstream request — the filer builds their own over
the ABC. We ship one anyway because ``OutboxRelay`` (Step 38) now needs
somewhere durable to put poison entries, and the only shipped implementation
before this module was ``InMemoryDeadLetterQueue`` — a ``deque(maxlen=10_000)``
lost on process restart. This module makes that useful out of the box without
changing what the filer asked for.

Table
-----
``varco_dead_letters`` — created idempotently via ``ensure_table()`` or
Alembic::

    from varco_sa.dlq import dead_letters_metadata
    target_metadata = [Base.metadata, dead_letters_metadata]

Columns: ``entry_id``, ``source``, ``source_ref``, ``channel``,
``handler_name``, ``event_type``, ``payload``, ``error_type``,
``error_message``, ``attempts``, ``first_failed_at``, ``last_failed_at``.

The nested ``DeadLetterEntry.event`` (a typed ``Event``, when present) is
serialized via ``JsonEventSerializer`` into the ``payload`` column — the same
approach ``RedisDLQ`` uses. A relay-sourced entry that never had a
deserializable event (``event=None``, raw ``payload`` bytes) stores those
raw bytes directly instead.

DESIGN: raw Core over ORM (same rationale as SAJobStore)
    ✅ No dependency on the application's ``DeclarativeBase`` — infrastructure
       table, just like ``varco_jobs`` and ``varco_outbox``.
    ✅ ``ensure_table()`` for zero-migration startup convenience.
    ❌ No Alembic auto-detection unless ``dead_letters_metadata`` is added to
       ``target_metadata``.

Usage::

    from sqlalchemy.ext.asyncio import create_async_engine
    from varco_sa.dlq import SADeadLetterQueue

    engine = create_async_engine("postgresql+asyncpg://...")
    dlq = SADeadLetterQueue(engine)
    await dlq.ensure_table()

    await dlq.push(entry)                       # never raises — logs+swallows
    entries = await dlq.pop_batch(limit=10)
    await dlq.ack(entries[0].entry_id)

Thread safety:  ✅ AsyncEngine connection pool is coroutine-safe.
Async safety:   ✅ All methods are ``async def``.

📚 Docs
- 📐 https://microservices.io/patterns/observability/audit-logging.html
  Poison-message handling is the audit-log analogue for messaging systems.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.ext.asyncio import AsyncEngine

from varco_core.event.dlq import (
    AbstractDeadLetterQueue,
    DeadLetterEntry,
    DeadLetterSource,
)
from varco_core.event.serializer import JsonEventSerializer
from varco_sa.metadata import register_framework_metadata as _register_fw_metadata

_logger = logging.getLogger(__name__)

# ── Table schema ──────────────────────────────────────────────────────────────

# Separate MetaData so varco_dead_letters never pollutes the application's
# Base.metadata — same pattern as SAJobStore / varco_sa.outbox.
_metadata = MetaData()

_dead_letters_table = Table(
    "varco_dead_letters",
    _metadata,
    Column("entry_id", sa.Uuid, primary_key=True),
    # DeadLetterSource value — consumer / outbox_relay / job.
    Column("source", String(32), nullable=False),
    # Opaque source-record identifier (outbox entry_id / job id) — None for
    # consumer-sourced entries (the event itself carries identity).
    Column("source_ref", String(255), nullable=True),
    Column("channel", String(255), nullable=False),
    Column("handler_name", Text, nullable=False),
    # Class name of the failed event, or "" when event could not be
    # deserialized at all (payload-only entries).
    Column("event_type", String(255), nullable=True),
    # Serialized Event JSON bytes, OR raw undecodable payload bytes when
    # ``event is None`` (relay's failed-to-deserialize path).
    Column("payload", LargeBinary, nullable=True),
    Column("error_type", String(255), nullable=False),
    Column("error_message", Text, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("first_failed_at", DateTime(timezone=True), nullable=False),
    Column("last_failed_at", DateTime(timezone=True), nullable=False),
)

# Expose metadata for Alembic integration — include in target_metadata.
dead_letters_metadata = _metadata


_register_fw_metadata("varco_sa.dlq", dead_letters_metadata)


def _ensure_tz(dt: datetime | None) -> datetime | None:
    """Coerce naive datetimes (SQLite) to UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# ── SADeadLetterQueue ────────────────────────────────────────────────────────


class SADeadLetterQueue(AbstractDeadLetterQueue):
    """
    SQLAlchemy async ``AbstractDeadLetterQueue`` backed by
    ``varco_dead_letters``.

    Each method opens a fresh connection from the engine's pool and
    auto-commits — the DLQ is infrastructure (like ``OutboxRelay`` /
    ``SAJobStore``) and does not participate in application UoW.

    Args:
        engine: Async SQLAlchemy engine for the target database.

    Edge cases:
        - Call ``await dlq.ensure_table()`` at startup, or add
          ``dead_letters_metadata`` to your Alembic ``target_metadata``.
        - ``push()`` never raises — per the ``AbstractDeadLetterQueue``
          contract, restated here because ``OutboxRelay`` and the job
          runner cannot recover from a DLQ failure either (Plan 005 Phase 3).

    Thread safety:  ✅ AsyncEngine pool is coroutine-safe; connections are per-call.
    Async safety:   ✅ All methods are ``async def``.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._serializer = JsonEventSerializer()

    async def ensure_table(self) -> None:
        """
        Create the ``varco_dead_letters`` table if it does not already exist.

        Idempotent — safe to call at every startup. For production
        deployments, prefer an Alembic migration (include
        ``dead_letters_metadata`` in ``target_metadata``).
        """
        async with self._engine.begin() as conn:
            await conn.run_sync(_metadata.create_all, checkfirst=True)
        _logger.debug("SADeadLetterQueue.ensure_table: varco_dead_letters is ready.")

    async def push(self, entry: DeadLetterEntry) -> None:
        """
        Persist ``entry`` in ``varco_dead_letters``.

        **Must never raise** — the ABC contract (``AbstractDeadLetterQueue``
        docstring) is doubly load-bearing here: the retry wrapper, the relay,
        and the job runner all call ``push()`` from a place that cannot
        meaningfully recover from a DLQ failure. Any exception is logged and
        swallowed.

        Args:
            entry: The ``DeadLetterEntry`` to persist.

        Async safety: ✅ Uses ``engine.begin()`` — one atomic transaction,
            wrapped in a try/except so failures never propagate.
        """
        try:
            if entry.event is not None:
                payload = self._serializer.serialize(entry.event)
                event_type = type(entry.event).__name__
            else:
                payload = entry.payload
                event_type = None

            row = {
                "entry_id": entry.entry_id,
                "source": entry.source.value,
                "source_ref": entry.source_ref,
                "channel": entry.channel,
                "handler_name": entry.handler_name,
                "event_type": event_type,
                "payload": payload,
                "error_type": entry.error_type,
                "error_message": entry.error_message,
                "attempts": entry.attempts,
                "first_failed_at": entry.first_failed_at,
                "last_failed_at": entry.last_failed_at,
            }
            async with self._engine.begin() as conn:
                await conn.execute(
                    sa.delete(_dead_letters_table).where(
                        _dead_letters_table.c.entry_id == entry.entry_id
                    )
                )
                await conn.execute(sa.insert(_dead_letters_table).values(**row))
            _logger.debug(
                "SADeadLetterQueue.push: stored entry_id=%s source=%s handler=%r",
                entry.entry_id,
                entry.source,
                entry.handler_name,
            )
        except Exception as exc:  # noqa: BLE001 — push MUST NOT propagate
            _logger.error(
                "SADeadLetterQueue.push() failed unexpectedly — entry dropped "
                "(entry_id=%s): %s",
                entry.entry_id,
                exc,
                exc_info=True,
            )

    async def pop_batch(self, *, limit: int = 10) -> list[DeadLetterEntry]:
        """
        Return up to ``limit`` entries, oldest-first (by ``first_failed_at``).

        Entries are NOT removed — call ``ack()`` after successful processing.

        Args:
            limit: Maximum number of entries to return. Must be ≥ 1.

        Raises:
            ValueError: ``limit`` < 1.

        Async safety: ✅ Uses ``engine.connect()`` — read-only, no commit.
        """
        if limit < 1:
            raise ValueError(f"pop_batch limit must be ≥ 1, got {limit}.")
        async with self._engine.connect() as conn:
            result = await conn.execute(
                sa.select(_dead_letters_table)
                .order_by(_dead_letters_table.c.first_failed_at.asc())
                .limit(limit)
            )
            rows = result.fetchall()
        return [self._row_to_entry(row) for row in rows]

    async def ack(self, entry_id: UUID) -> None:
        """
        Remove the entry identified by ``entry_id``. Idempotent.

        Args:
            entry_id: The ``DeadLetterEntry.entry_id`` to acknowledge.

        Async safety: ✅ Uses ``engine.begin()`` — single committed transaction.
        """
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.delete(_dead_letters_table).where(
                    _dead_letters_table.c.entry_id == entry_id
                )
            )
        _logger.debug("SADeadLetterQueue.ack: acknowledged entry_id=%s", entry_id)

    async def count(self) -> int:
        """
        Return the exact number of entries in ``varco_dead_letters``.

        Async safety: ✅ Uses ``engine.connect()`` — read-only, no commit.
        """
        async with self._engine.connect() as conn:
            result = await conn.execute(
                sa.select(sa.func.count()).select_from(_dead_letters_table)
            )
            return int(result.scalar_one())

    def _row_to_entry(self, row: Any) -> DeadLetterEntry:
        """Convert a Core row back to a ``DeadLetterEntry`` value object."""
        event = None
        payload = row.payload
        if row.event_type is not None and row.payload is not None:
            try:
                event = self._serializer.deserialize(row.payload)
                payload = None
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "SADeadLetterQueue: failed to deserialize event for "
                    "entry_id=%s: %s — returning raw payload instead.",
                    row.entry_id,
                    exc,
                )
        return DeadLetterEntry(
            entry_id=row.entry_id,
            event=event,
            channel=row.channel,
            handler_name=row.handler_name,
            error_type=row.error_type,
            error_message=row.error_message,
            attempts=row.attempts,
            first_failed_at=_ensure_tz(row.first_failed_at),
            last_failed_at=_ensure_tz(row.last_failed_at),
            source=DeadLetterSource(row.source),
            source_ref=row.source_ref,
            payload=payload,
        )

    def __repr__(self) -> str:
        return f"SADeadLetterQueue(engine={self._engine!r})"


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    "SADeadLetterQueue",
    "dead_letters_metadata",
]
