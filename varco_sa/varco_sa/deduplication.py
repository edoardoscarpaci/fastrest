"""
varco_sa.deduplication
======================
SQLAlchemy Core-backed message deduplication for the varco event system.

``SADeduplicator`` implements ``AbstractDeduplicator`` using a dedicated
``varco_dedup_log`` table.  Each processed event is recorded as a row with an
``expires_at`` timestamp.  Re-deliveries within the TTL window are detected via
a SELECT that checks ``expires_at > now()`` and suppressed.

Table
-----
``varco_dedup_log`` — one row per event_id, expires after ``ttl_seconds``::

    from varco_sa.deduplication import dedup_metadata
    target_metadata = [Base.metadata, dedup_metadata]

Strategy
--------
``is_duplicate``:  ``SELECT 1 WHERE event_id = ? AND expires_at > now()`` — point lookup on PK.
``mark_seen``:     ``INSERT ... ON CONFLICT DO NOTHING`` — atomic, idempotent.
``purge_expired``: ``DELETE WHERE expires_at < now()`` — best-effort cleanup; call periodically.

DESIGN: expires_at column over Redis-style TTL
    ✅ Runs on any SA-supported DB (PostgreSQL, SQLite) — no Redis dependency.
    ✅ Migration-friendly — ``dedup_metadata`` can be included in Alembic's
       target_metadata so the table is tracked alongside application schema.
    ✅ ``purge_expired()`` gives callers explicit control over cleanup scheduling.
    ❌ No automatic server-side TTL — ``purge_expired()`` must be called by the
       application (cron job, background task, or startup).  MongoDB/Redis handle
       TTL natively; SA/SQL does not.
    ❌ Slightly higher latency than Redis: a DB round-trip instead of an in-memory
       lookup.  Acceptable for event deduplication (not hot-path IOPS).

DESIGN: raw Core over ORM / SAModelFactory
    ✅ No dependency on application ``DeclarativeBase`` — this is infrastructure.
    ✅ ``ensure_table()`` for zero-migration startup convenience.
    ✅ Works with any async SQLAlchemy engine (PostgreSQL, SQLite for tests).
    ❌ Not auto-detected by Alembic unless ``dedup_metadata`` added to target.

DESIGN: cross-dialect upsert (ON CONFLICT DO NOTHING)
    PostgreSQL 9.5+ and SQLite 3.24+ both support
    ``INSERT ... ON CONFLICT DO NOTHING`` via SQLAlchemy's dialect-agnostic
    ``insert().on_conflict_do_nothing(index_elements=...)``.  This covers the
    two dialects used in this codebase (Postgres production, SQLite tests)
    without branching.

    Alternative considered: ``pg_insert`` + fallback plain INSERT (like audit.py)
        Rejected — the SA dialect-agnostic ``on_conflict_do_nothing`` is
        supported on SQLite 3.24+ (2018) and PostgreSQL 9.5+ (2016).  Both
        SQLite and Postgres versions in use are well above these minimums.
        Dialect branching adds complexity with no benefit here.

    Alternative considered: ``MERGE`` / ``UPSERT``
        Rejected — SQL standard MERGE is not universally supported across SA
        dialects; ``on_conflict_do_nothing`` is cleaner for an insert-only table.

Usage::

    from sqlalchemy.ext.asyncio import create_async_engine
    from varco_sa.deduplication import SADeduplicator, SADedupConfig

    engine = create_async_engine("postgresql+asyncpg://...")
    dedup = SADeduplicator(engine, config=SADedupConfig(ttl_seconds=3600))
    await dedup.ensure_table()

    class OrderConsumer(EventConsumer):
        @listen(OrderPlacedEvent, channel="orders", deduplicator=dedup)
        async def on_order(self, event: OrderPlacedEvent) -> None:
            await self._process(event)  # called at most once per event_id in TTL window

Thread safety:  ✅ ``AsyncEngine`` connection pool is coroutine-safe.
Async safety:   ✅ All methods are ``async def``.

📚 Docs
- 🔍 https://docs.sqlalchemy.org/en/20/core/dml.html#sqlalchemy.sql.expression.Insert.on_conflict_do_nothing
  SQLAlchemy Core ON CONFLICT DO NOTHING — cross-dialect idempotent insert.
- 🔍 https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
  SQLAlchemy async engine — AsyncEngine, begin(), connect().
- 🐍 https://microservices.io/patterns/communication-style/idempotent-consumer.html
  Idempotent Consumer pattern — the deduplication pattern this module implements.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import Column, DateTime, MetaData, Table
from sqlalchemy.ext.asyncio import AsyncEngine
from varco_core.event.deduplication import AbstractDeduplicator

from varco_sa.metadata import register_framework_metadata as _register_fw_metadata

_logger = logging.getLogger(__name__)

# Default deduplication window — mirrors RedisDeduplicator's default of 24 hours.
_DEFAULT_TTL_SECONDS: int = 86_400


# ── Table schema ──────────────────────────────────────────────────────────────

# Separate MetaData so varco_dedup_log never pollutes Base.metadata.
# Callers that want Alembic to track this table can add dedup_metadata to
# target_metadata alongside their own Base.metadata.
dedup_metadata = MetaData()

_dedup_table = Table(
    "varco_dedup_log",
    dedup_metadata,
    # event_id IS the primary key — no surrogate surrogate key needed.
    # UUID is the natural deduplication key; making it PK gives a free
    # clustered index on PostgreSQL (and a B-tree on SQLite).
    Column(
        "event_id",
        # sa.Uuid(as_uuid=True) maps to UUID on Postgres, VARCHAR(36) on SQLite —
        # consistent with how varco_sa.conversation handles UUIDs.
        sa.Uuid(as_uuid=True),
        primary_key=True,
        nullable=False,
    ),
    # expires_at drives two operations:
    #   1. is_duplicate — WHERE expires_at > now()  (not-yet-expired = still seen)
    #   2. purge_expired — WHERE expires_at < now()  (expired rows to delete)
    # An explicit index on expires_at makes the purge scan fast even when the
    # table has millions of rows.
    Column(
        "expires_at",
        DateTime(timezone=True),
        nullable=False,
        index=True,
    ),
)


# ── SADedupConfig ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SADedupConfig:
    """
    Configuration for ``SADeduplicator`` TTL window.

    DESIGN: frozen dataclass over plain kwargs
        ✅ Hashable — can be used as dict key or set member.
        ✅ Immutable — no accidental mutation after construction.
        ✅ Explicit — callers see the config shape when reading call sites.

    Attributes:
        ttl_seconds: Deduplication window in seconds.  Events processed within
                     this window are considered duplicates.  Default: 86 400 (24 h).

    Edge cases:
        - ``ttl_seconds=0`` would expire entries immediately after mark_seen —
          effectively disabling deduplication.  Use ``ttl_seconds >= 1``.
        - No upper bound is enforced — very large values keep rows indefinitely
          and may cause table bloat without periodic ``purge_expired()`` calls.
    """

    ttl_seconds: int = _DEFAULT_TTL_SECONDS


# ── SADeduplicator ────────────────────────────────────────────────────────────


class SADeduplicator(AbstractDeduplicator):
    """
    SQLAlchemy Core-backed deduplicator using the ``varco_dedup_log`` table.

    Implements the ``AbstractDeduplicator`` two-step API using a datetime-based
    TTL column instead of Redis's per-key TTL.  The ``expires_at`` column is
    set to ``now() + ttl_seconds`` at ``mark_seen`` time; ``is_duplicate``
    checks ``expires_at > now()`` to determine if the window is still open.

    DESIGN: DB-level datetime comparison over application-side TTL
        ✅ No background eviction thread required — the DB clock is the TTL.
        ✅ Durable — survives process restarts, unlike InMemoryDeduplicator.
        ✅ Visible in SQL tooling — DBAs can inspect / manually expire entries.
        ❌ Clock skew between app servers and DB server can affect TTL accuracy
           by a few seconds.  Acceptable for deduplication windows of minutes+.
        ❌ Table grows unboundedly unless ``purge_expired()`` is called.

    Thread safety:  ✅ ``AsyncEngine`` pool handles concurrent coroutines.
    Async safety:   ✅ All methods are ``async def``.

    Args:
        engine: An ``AsyncEngine`` instance (shared across all operations).
        config: ``SADedupConfig`` controlling the TTL window.
                Defaults to ``SADedupConfig()`` (24 h TTL).

    Edge cases:
        - Call ``ensure_table()`` once at startup before any other method.
          If the table does not exist, ``is_duplicate`` and ``mark_seen`` will
          raise ``sqlalchemy.exc.OperationalError``.
        - ``is_duplicate`` on DB error returns ``False`` (safe default — prefer
          processing the event over silently dropping it).
        - ``mark_seen`` MUST NOT raise — any error is logged and swallowed.
        - ``purge_expired()`` is best-effort cleanup — callers should schedule
          it periodically (e.g. nightly cron or startup task).

    Example::

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        dedup = SADeduplicator(engine, config=SADedupConfig(ttl_seconds=3600))
        await dedup.ensure_table()

        # Wire into a @listen handler:
        class OrderConsumer(EventConsumer):
            @listen(OrderPlacedEvent, channel="orders", deduplicator=dedup)
            async def on_order(self, event: OrderPlacedEvent) -> None:
                await process(event)
    """

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        config: SADedupConfig = SADedupConfig(),
    ) -> None:
        """
        Args:
            engine: Async SQLAlchemy engine — shared connection pool.
            config: Deduplication TTL configuration.
                    Defaults to ``SADedupConfig()`` (24-hour window).
        """
        self._engine = engine
        self._config = config

    # ── Schema lifecycle ──────────────────────────────────────────────────────

    async def ensure_table(self) -> None:
        """
        Create the ``varco_dedup_log`` table if it does not exist.

        Idempotent — safe to call multiple times (uses ``checkfirst=True``).
        Call once at application startup before any other method.

        Raises:
            sqlalchemy.exc.SQLAlchemyError: On database errors.

        Edge cases:
            - ``checkfirst=True`` skips table creation without raising if the
              table already exists.  Works on SQLite and PostgreSQL.
            - Include ``dedup_metadata`` in Alembic's ``target_metadata`` list
              if you prefer migration-managed schema instead of ``ensure_table``.

        Async safety: ✅ Single DDL operation in a ``begin()`` transaction.
        """
        async with self._engine.begin() as conn:
            await conn.run_sync(dedup_metadata.create_all, checkfirst=True)
        _logger.debug("SADeduplicator: varco_dedup_log table ensured.")

    # ── AbstractDeduplicator interface ────────────────────────────────────────

    async def is_duplicate(self, event_id: UUID) -> bool:
        """
        Return ``True`` if the event has been seen and its TTL window is still open.

        Executes::

            SELECT 1
            FROM varco_dedup_log
            WHERE event_id = :event_id
              AND expires_at > :now

        Args:
            event_id: Event UUID to check.

        Returns:
            ``True`` if a non-expired row exists for ``event_id``; ``False``
            if no row exists or if the row has expired (TTL elapsed).
            On DB error returns ``False`` — "process the event" is safer than
            silently dropping it.

        Thread safety:  ✅ Each call acquires its own connection from the pool.
        Async safety:   ✅ Single SELECT query.

        Edge cases:
            - Expired rows (``expires_at <= now``) are treated as non-existent —
              the event is considered new and will be re-processed.  This is
              correct: the TTL represents the deduplication window.
            - DB error: logs a warning and returns ``False`` — safe default.
        """
        now = datetime.now(UTC)
        stmt = sa.select(sa.literal(1)).where(
            sa.and_(
                _dedup_table.c.event_id == event_id,
                _dedup_table.c.expires_at > now,
            )
        )
        try:
            async with self._engine.connect() as conn:
                result = await conn.execute(stmt)
                row = result.fetchone()
            return row is not None
        except Exception as exc:
            # Safe default: process the event rather than silently drop it.
            # A false negative (returning False on error) means at-most-once
            # deduplication is relaxed to at-least-once — acceptable under
            # transient DB failures.
            _logger.warning(
                "SADeduplicator.is_duplicate failed for event_id=%s: %s. "
                "Returning False (will process event).",
                event_id,
                exc,
            )
            return False

    async def mark_seen(self, event_id: UUID) -> None:
        """
        Record that ``event_id`` has been successfully processed.

        Inserts a row with ``expires_at = now() + ttl_seconds``.  Uses
        ``ON CONFLICT DO NOTHING`` so a second call with the same ``event_id``
        is a silent no-op — idempotent by design.

        Does NOT raise — any exception is logged and swallowed, per the
        ``AbstractDeduplicator.mark_seen`` contract.

        Args:
            event_id: Event UUID to mark as processed.

        Thread safety:  ✅ Each call acquires its own connection from the pool.
        Async safety:   ✅ Single INSERT in a ``begin()`` transaction.

        Edge cases:
            - Already-seen ``event_id``: ``ON CONFLICT DO NOTHING`` — no update,
              no error.  The original ``expires_at`` is preserved.
            - DB unavailable: logs the error and returns without raising.
            - If ``ensure_table()`` was never called: the INSERT will raise
              ``OperationalError`` — caught and swallowed (safe default).
        """
        expires_at = datetime.now(UTC) + timedelta(seconds=self._config.ttl_seconds)

        try:
            async with self._engine.begin() as conn:
                # DESIGN: dialect-specific ON CONFLICT DO NOTHING
                # ``sa.insert()`` (generic) does not expose ``on_conflict_do_nothing``
                # — it is a dialect extension.  We detect the dialect at connection
                # time and use the matching dialect-specific insert.
                #
                # PostgreSQL 9.5+: ``sqlalchemy.dialects.postgresql.insert``
                # SQLite 3.24+:    ``sqlalchemy.dialects.sqlite.insert``
                # Both are well above minimum version requirements.
                #
                # Alternative considered: REPLACE INTO (SQLite) / UPSERT
                #   Rejected — REPLACE deletes + re-inserts, resetting expires_at.
                #   ON CONFLICT DO NOTHING preserves the original expires_at.
                dialect_name = conn.dialect.name
                if dialect_name == "sqlite":
                    from sqlalchemy.dialects.sqlite import insert as _insert
                elif dialect_name == "postgresql":
                    # mypy attaches the cross-dialect "Incompatible import" error to
                    # this opening line, not the `insert as _insert,` sub-line below
                    # — keep the ignore here even if a future ruff import-sort pass
                    # reflows this into a parenthesized multi-line import again.
                    from sqlalchemy.dialects.postgresql import (  # type: ignore[assignment]
                        insert as _insert,
                    )
                else:
                    # Fallback for other dialects: plain INSERT, may raise IntegrityError
                    # on conflict.  Caught below and swallowed per the never-raise contract.
                    from sqlalchemy import insert as _insert  # type: ignore[assignment]

                stmt = _insert(_dedup_table).values(
                    event_id=event_id, expires_at=expires_at
                )
                # on_conflict_do_nothing is available on pg and sqlite dialect inserts.
                if hasattr(stmt, "on_conflict_do_nothing"):
                    stmt = stmt.on_conflict_do_nothing(index_elements=["event_id"])

                await conn.execute(stmt)
        except Exception as exc:
            # mark_seen MUST NOT raise — contract from AbstractDeduplicator.
            # Log the error so it is visible in monitoring, then return cleanly.
            # The consequence is that the event may be re-processed on re-delivery
            # (at-least-once semantics instead of at-most-once).
            _logger.error(
                "SADeduplicator.mark_seen failed for event_id=%s: %s",
                event_id,
                exc,
            )

    # ── Maintenance ───────────────────────────────────────────────────────────

    async def purge_expired(self) -> int:
        """
        Delete rows whose TTL has elapsed (``expires_at < now()``).

        Should be called periodically — e.g. at application startup or via a
        scheduled background task — to prevent unbounded table growth.

        Returns:
            Number of rows deleted.  ``0`` if no rows were expired.

        Raises:
            sqlalchemy.exc.SQLAlchemyError: On database errors.

        Thread safety:  ✅ ``AsyncEngine`` pool is coroutine-safe.
        Async safety:   ✅ Single DELETE in a ``begin()`` transaction.

        Edge cases:
            - Calling on an empty table returns ``0`` — no-op.
            - Under high event volume, this may delete large batches; consider
              batching with LIMIT if table lock contention is a concern on
              production Postgres deployments.
            - Errors are NOT swallowed here (unlike ``mark_seen``) — callers
              should handle ``SQLAlchemyError`` and schedule a retry.
        """
        now = datetime.now(UTC)
        stmt = _dedup_table.delete().where(_dedup_table.c.expires_at < now)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        deleted = result.rowcount
        _logger.debug("SADeduplicator.purge_expired: deleted %d expired rows.", deleted)
        return deleted

    # ── Repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"SADeduplicator("
            f"engine={self._engine!r}, "
            f"ttl_seconds={self._config.ttl_seconds})"
        )


_register_fw_metadata("varco_sa.deduplication", dedup_metadata)


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    "SADeduplicator",
    "SADedupConfig",
    "dedup_metadata",
]
