"""
varco_sa.audit
==============
SQLAlchemy async implementation of ``AuditRepository``.

Provides a single class ``SAAuditRepository`` that writes ``AuditEntry``
value objects to a ``varco_audit_log`` table using an injected
``AsyncSession``.

Unlike the outbox pattern (which has two classes for service-layer vs relay
use), the audit repository is always written by ``AuditConsumer`` inside a
self-managed session — there is no service-layer same-transaction requirement
because audit records are persisted asynchronously after the domain commit.

Table
-----
``AuditEntryModel`` maps to the ``varco_audit_log`` table.
Include ``audit_metadata`` in your Alembic ``env.py``::

    from varco_sa.audit import audit_metadata

    target_metadata = [Base.metadata, outbox_metadata, audit_metadata]

Usage — with AuditConsumer::

    from sqlalchemy.ext.asyncio import async_sessionmaker
    from varco_sa.audit import SAAuditRepository
    from varco_core.service.audit import AuditConsumer

    session_factory = async_sessionmaker(engine)
    audit_repo = SAAuditRepository(session_factory)

    consumer = AuditConsumer(audit_repo=audit_repo)
    consumer.register_to(event_bus)

Usage — direct query::

    entries = await audit_repo.list_for_entity("Order", str(order_id), limit=50)

DESIGN: single-class over service/relay split (unlike SAOutboxRepository)
    ✅ AuditConsumer is the only writer — it always manages its own session.
    ✅ ``list_for_entity()`` is a read-only query — no UoW involvement.
    ✅ Simpler API surface — one class, one responsibility.
    ❌ If you need to write audit entries inside a UoW transaction (strict
       consistency), inject an ``AsyncSession`` directly via the private
       ``_from_session()`` factory instead.

Thread safety:  ⚠️ ``async_sessionmaker`` is shared; sessions created per op
                    are NOT thread-safe.  Use one relay/consumer per event loop.
Async safety:   ✅ All methods are ``async def``.

📚 Docs
- 🐍 https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
  SQLAlchemy async ORM — AsyncSession usage patterns
- 🐍 https://docs.sqlalchemy.org/en/20/core/type_basics.html#sqlalchemy.types.JSON
  SQLAlchemy JSON type — used for the ``diff`` column
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import datetime, timezone, UTC
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, String, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from varco_core.service.audit import AuditEntry, AuditRepository

from varco_sa.metadata import register_framework_metadata as _register_fw_metadata

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

_logger = logging.getLogger(__name__)

# ── ORM model ─────────────────────────────────────────────────────────────────


class _AuditBase(DeclarativeBase):
    """
    Isolated ``DeclarativeBase`` for the audit log model.

    DESIGN: separate base over reusing the app's DeclarativeBase
        ✅ varco_sa can define the audit schema without knowing the app's Base.
        ✅ Users opt-in to Alembic management by including ``audit_metadata``
           in their ``target_metadata`` — no silent schema pollution.
        ❌ Users must explicitly include ``audit_metadata`` in Alembic config.
           See module docstring for setup.
    """


class AuditEntryModel(_AuditBase):
    """
    SQLAlchemy ORM row for a single audit log entry.

    Table name: ``varco_audit_log``.

    Thread safety:  ✅ Mapped class definition is read-only after creation.
    Async safety:   ✅ Class definition; no I/O.

    Edge cases:
        - ``diff`` is stored as JSON — type varies per action (dict for
          create/update, empty dict for delete).  Use ``JSON`` rather than
          ``JSONB`` so the model works on SQLite (for tests) and Postgres.
        - ``actor_id``, ``correlation_id``, ``tenant_id`` are nullable —
          None values map to SQL NULL.
        - No composite index on ``(entity_type, entity_id, occurred_at)`` is
          defined here — add one in a migration for production workloads where
          ``list_for_entity()`` is called frequently.
    """

    __tablename__ = "varco_audit_log"

    # Primary key — matches AuditEntry.entry_id (UUIDv4).
    entry_id: Mapped[UUID] = mapped_column(primary_key=True)

    # Entity class name — e.g. "Order", "User".
    entity_type: Mapped[str] = mapped_column(String(255), nullable=False)

    # String representation of the entity primary key.
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Mutation action — one of "create", "update", "delete".
    action: Mapped[str] = mapped_column(String(16), nullable=False)

    # Identity of the actor who triggered the mutation — None for system jobs.
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Field-level change data — structure varies by action.
    diff: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # UTC timestamp when the audit event was emitted by the service.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Optional request-scoped tracing ID — groups audit records by request.
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Optional tenant identifier — for multi-tenant deployments.
    tenant_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Plan 009, Phase 12 (R8) — tamper-evidence hash chain. Both nullable:
    # a table with hash_chain=False (the default) never populates them, and
    # existing pre-Phase-12 rows on a table that later opts in have no
    # backfilled chain (verify_chain() reports the boundary, not a break).
    seq: Mapped[int | None] = mapped_column(nullable=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entry_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


# Expose the metadata so users can wire it into Alembic target_metadata.
audit_metadata = _AuditBase.metadata


_register_fw_metadata("varco_sa.audit", audit_metadata)


# ── Helper ─────────────────────────────────────────────────────────────────────


def _model_to_entry(row: AuditEntryModel) -> AuditEntry:
    """
    Convert an ``AuditEntryModel`` ORM row back to an ``AuditEntry`` value object.

    Args:
        row: ORM row fetched from the ``varco_audit_log`` table.

    Returns:
        An immutable ``AuditEntry`` with the same field values.

    Edge cases:
        - ``occurred_at`` is expected to be timezone-aware.  SQLite returns
          naive datetimes — coerced to UTC without raising.
    """
    # Ensure occurred_at is always timezone-aware — SQLite returns naive datetimes.
    occurred_at = row.occurred_at
    if occurred_at is not None and occurred_at.tzinfo is None:
        # Treat naive datetime as UTC — matches how AuditEntry stores it.
        occurred_at = occurred_at.replace(tzinfo=UTC)

    return AuditEntry(
        entry_id=row.entry_id,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        action=row.action,
        actor_id=row.actor_id,
        # diff is stored as JSON dict — copy by value to ensure immutability.
        diff=dict(row.diff) if row.diff else {},
        occurred_at=occurred_at,
        correlation_id=row.correlation_id,
        tenant_id=row.tenant_id,
        seq=row.seq,
        prev_hash=row.prev_hash,
    )


# ── SAAuditRepository ─────────────────────────────────────────────────────────


class SAAuditRepository(AuditRepository):
    """
    SQLAlchemy implementation of ``AuditRepository``.

    Creates a fresh ``AsyncSession`` per operation via the injected
    ``async_sessionmaker`` and auto-commits after ``save()``.  This design
    mirrors ``SARelayOutboxRepository`` — the consumer (or caller) does not
    manage DB sessions itself.

    DESIGN: session-per-operation over injected-session
        ✅ ``AuditConsumer`` has no concept of SQLAlchemy sessions —
           keeps the consumer infrastructure-agnostic.
        ✅ Each ``save()`` commits immediately — audit record is durable
           before the consumer ACKs the event bus message.
        ✅ ``list_for_entity()`` always reads fresh data (no stale cache).
        ❌ Slightly higher connection-pool churn vs a shared session.
           Audit logging is low-frequency enough that this is negligible.

    Alternative: ``_from_session(session)`` factory
        For strict-consistency use cases (audit inside the same UoW transaction),
        instantiate directly with an ``AsyncSession`` via ``_from_session()``.
        Note: that variant does NOT auto-commit — the caller must commit.

    Thread safety:  ⚠️ ``async_sessionmaker`` is shared; sessions created per
                        call are NOT thread-safe.  Use one instance per event loop.
    Async safety:   ✅ All methods are ``async def``.

    Args:
        session_factory: ``async_sessionmaker`` for creating sessions.

    Edge cases:
        - If the DB is unreachable, ``save()`` and ``list_for_entity()`` raise
          ``sqlalchemy.exc.OperationalError`` — ``AuditConsumer`` should be
          wired with a ``retry_policy`` to handle transient DB failures.
        - ``save()`` uses INSERT OR IGNORE (via ``ON CONFLICT DO NOTHING``) for
          Postgres.  On SQLite/MySQL the fallback is a plain INSERT (duplicate
          ``entry_id`` raises ``IntegrityError`` — harmless for at-least-once
          consumer delivery).

    Example::

        from sqlalchemy.ext.asyncio import async_sessionmaker
        from varco_sa.audit import SAAuditRepository
        from varco_core.service.audit import AuditConsumer

        repo = SAAuditRepository(async_sessionmaker(engine))
        consumer = AuditConsumer(audit_repo=repo)
        consumer.register_to(event_bus)
    """

    def __init__(
        self, session_factory: async_sessionmaker, *, hash_chain: bool = False
    ) -> None:
        """
        Args:
            session_factory: ``async_sessionmaker`` for creating ``AsyncSession``
                             instances.  Must target the same database as the
                             rest of the application.
            hash_chain:      Plan 009, Phase 12 (R8) — opt-in tamper-evidence.
                             When ``True``, every ``save()`` establishes the
                             chain link itself under a backend-level
                             serialization guarantee: a monotone ``seq`` +
                             ``SELECT ... ORDER BY seq DESC LIMIT 1 FOR
                             UPDATE`` (Postgres) — this caps audit write
                             throughput at one serialized write per record
                             (documented cost, RD-8). SQLite (used in tests)
                             has no row-level locking; an ``asyncio.Lock``
                             additionally serializes concurrent ``save()``
                             calls **within this process** so the 20-
                             concurrent-tasks test produces one unbroken
                             chain — this is NOT a substitute for
                             ``FOR UPDATE`` across multiple processes.

        Edge cases:
            - ``session_factory`` is not type-annotated with a generic parameter
              to avoid importing ``async_sessionmaker[AsyncSession]`` at runtime
              on Python < 3.12 without ``from __future__ import annotations``.
        """
        # session_factory is called once per operation — no shared session state.
        self._session_factory = session_factory
        self._hash_chain = hash_chain
        # Lazy asyncio.Lock — never created outside a running event loop.
        self._hash_chain_lock: asyncio.Lock | None = None

    def _get_hash_chain_lock(self) -> asyncio.Lock:
        if self._hash_chain_lock is None:
            self._hash_chain_lock = asyncio.Lock()
        return self._hash_chain_lock

    @classmethod
    def _from_session(cls, session: AsyncSession) -> _SAAuditRepositoryInSession:
        """
        Factory for strict-consistency use — wraps an existing ``AsyncSession``.

        Use this when you need to write audit entries inside the same UoW
        transaction as the domain entity write (synchronous audit trail).

        Args:
            session: Active ``AsyncSession`` from a caller's UoW.

        Returns:
            A ``_SAAuditRepositoryInSession`` that uses the provided session.
            The caller controls commit/rollback — this variant does NOT
            auto-commit.

        Edge cases:
            - If ``session`` is closed or the transaction is rolled back, all
              pending audit writes are discarded.
        """
        return _SAAuditRepositoryInSession(session)

    async def save(self, entry: AuditEntry) -> None:
        """
        Persist ``entry`` to the ``varco_audit_log`` table and commit.

        Uses ``INSERT ... ON CONFLICT DO NOTHING`` on Postgres to make the
        operation idempotent for at-least-once consumer delivery (where the same
        ``AuditEvent`` might be delivered twice after a consumer crash).  On
        other dialects, a plain INSERT is used — duplicate ``entry_id`` will
        raise ``IntegrityError``.

        Args:
            entry: The ``AuditEntry`` to persist.

        Raises:
            sqlalchemy.exc.OperationalError: If the DB is unreachable.
            sqlalchemy.exc.IntegrityError: On duplicate ``entry_id`` on
                                           non-Postgres dialects.

        Edge cases:
            - On Postgres, re-inserting the same ``entry_id`` is silently
              ignored — safe for at-least-once AuditConsumer delivery.
            - ``diff`` is serialized to JSON by SQLAlchemy — dict values must
              be JSON-serializable.

        Async safety: ✅ Each call creates, commits, and closes its own session
            (or, when ``hash_chain=True``, delegates to ``_save_chained()``,
            which serializes concurrent writers — see ``__init__``).
        """
        if self._hash_chain:
            await self._save_chained(entry)
            return

        async with self._session_factory() as session:
            # DESIGN: pg_insert with ON CONFLICT DO NOTHING for idempotency.
            # On non-Postgres dialects we fall back to a plain INSERT via
            # session.add() — the caller must handle IntegrityError if needed.
            try:
                # Use Postgres-specific upsert if available — most production
                # deployments of varco_sa use Postgres.
                stmt = (
                    pg_insert(AuditEntryModel)
                    .values(
                        entry_id=entry.entry_id,
                        entity_type=entry.entity_type,
                        entity_id=entry.entity_id,
                        action=entry.action,
                        actor_id=entry.actor_id,
                        diff=entry.diff,
                        occurred_at=entry.occurred_at,
                        correlation_id=entry.correlation_id,
                        tenant_id=entry.tenant_id,
                    )
                    .on_conflict_do_nothing(index_elements=["entry_id"])
                )
                await session.execute(stmt)
            except Exception:
                # pg_insert may not be supported on non-Postgres dialects
                # (e.g. SQLite used in tests) — fall back to plain ORM add().
                # Rollback any partial state from the failed execute() first.
                await session.rollback()
                session.add(
                    AuditEntryModel(
                        entry_id=entry.entry_id,
                        entity_type=entry.entity_type,
                        entity_id=entry.entity_id,
                        action=entry.action,
                        actor_id=entry.actor_id,
                        diff=entry.diff,
                        occurred_at=entry.occurred_at,
                        correlation_id=entry.correlation_id,
                        tenant_id=entry.tenant_id,
                    )
                )
            await session.commit()

        _logger.debug(
            "SAAuditRepository.save: committed entry_id=%s entity=%s/%s action=%s",
            entry.entry_id,
            entry.entity_type,
            entry.entity_id,
            entry.action,
        )

    async def _save_chained(self, entry: AuditEntry) -> None:
        """
        Establish the hash-chain link for ``entry`` and persist it
        (Plan 009, Phase 12 / R8) — RD-8's "chain is a repository concern".

        Under a single serialized write: read the last row (``ORDER BY seq
        DESC LIMIT 1``, ``FOR UPDATE`` on Postgres), compute
        ``seq = last.seq + 1`` (or ``1`` for the genesis row) and
        ``prev_hash = last.entry_hash`` (or ``None`` for genesis), stamp
        ``entry``, compute its own ``entry_hash()``, and INSERT.

        DESIGN: an ``asyncio.Lock`` in addition to ``FOR UPDATE``
            ✅ ``FOR UPDATE`` alone is a no-op on SQLite (no row locking) —
               the lock is what actually serializes the 20-concurrent-tasks
               test in-process.
            ✅ On Postgres, ``FOR UPDATE`` additionally serializes writers
               across *processes*, which the lock cannot do.
            ❌ Caps this repository's audit write throughput at one
               serialized write per record, in-process AND cross-process —
               documented cost (RD-8), opt-in via ``hash_chain=True``.

        Async safety: ✅ Serialized via ``self._get_hash_chain_lock()``.
        """
        async with self._get_hash_chain_lock(), self._session_factory() as session:
            stmt = select(AuditEntryModel).order_by(AuditEntryModel.seq.desc()).limit(1)
            try:
                stmt = stmt.with_for_update()
                last_row = (await session.execute(stmt)).scalars().first()
            except (
                Exception
            ):  # noqa: BLE001 — dialect-fallback guard, not error handling
                # with_for_update() is a no-op/unsupported on some
                # dialects (e.g. SQLite) — fall back to a plain read;
                # the asyncio.Lock above is the real guard there. Any
                # dialect-specific exception type is acceptable to catch
                # broadly here since the fallback path is always correct.
                await session.rollback()
                plain_stmt = (
                    select(AuditEntryModel)
                    .order_by(AuditEntryModel.seq.desc())
                    .limit(1)
                )
                last_row = (await session.execute(plain_stmt)).scalars().first()

            next_seq = (
                (last_row.seq + 1)
                if (last_row is not None and last_row.seq is not None)
                else 1
            )
            prev_hash = last_row.entry_hash if last_row is not None else None

            chained_entry = dataclasses.replace(
                entry, seq=next_seq, prev_hash=prev_hash
            )
            computed_hash = chained_entry.entry_hash()

            session.add(
                AuditEntryModel(
                    entry_id=chained_entry.entry_id,
                    entity_type=chained_entry.entity_type,
                    entity_id=chained_entry.entity_id,
                    action=chained_entry.action,
                    actor_id=chained_entry.actor_id,
                    diff=chained_entry.diff,
                    occurred_at=chained_entry.occurred_at,
                    correlation_id=chained_entry.correlation_id,
                    tenant_id=chained_entry.tenant_id,
                    seq=next_seq,
                    prev_hash=prev_hash,
                    entry_hash=computed_hash,
                )
            )
            await session.commit()

        _logger.debug(
            "SAAuditRepository._save_chained: committed entry_id=%s seq=%d",
            entry.entry_id,
            next_seq,
        )

    async def list_for_entity(
        self,
        entity_type: str,
        entity_id: str,
        *,
        limit: int = 100,
        tenant_id: str | None = None,
    ) -> list[AuditEntry]:
        """
        Return audit entries for a specific entity, newest-first.

        Opens a fresh session, executes a SELECT with ``WHERE entity_type=?
        AND entity_id=? ORDER BY occurred_at DESC LIMIT ?``, and closes the
        session.

        Args:
            entity_type: Entity class name to filter by (e.g. ``"Order"``).
            entity_id:   Entity primary key string to filter by.
            limit:       Maximum number of entries to return.  Default ``100``.
            tenant_id:   Plan 009 (R4) — optional tenant filter. ``None``
                         means no tenant filter.

        Returns:
            List of ``AuditEntry`` objects ordered by ``occurred_at DESC``.
            Empty list if no audit records exist for the given entity.

        Edge cases:
            - Without a DB index on ``(entity_type, entity_id, occurred_at)``
              this query is O(N) — add a composite index in production.
            - ``limit=0`` returns an empty list (handled by SQLAlchemy).

        Async safety: ✅ Each call creates and closes its own session.
        """
        async with self._session_factory() as session:
            stmt = (
                select(AuditEntryModel)
                .where(
                    # Filter by entity identity — both columns required.
                    AuditEntryModel.entity_type == entity_type,
                    AuditEntryModel.entity_id == entity_id,
                )
                # Newest-first — most recent mutations appear at the top.
                .order_by(AuditEntryModel.occurred_at.desc())
                .limit(limit)
            )
            if tenant_id is not None:
                stmt = stmt.where(AuditEntryModel.tenant_id == tenant_id)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            entries = [_model_to_entry(row) for row in rows]

        _logger.debug(
            "SAAuditRepository.list_for_entity: entity=%s/%s fetched %d entries",
            entity_type,
            entity_id,
            len(entries),
        )
        return entries

    async def list(
        self,
        *,
        actor_id: str | None = None,
        action: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        tenant_id: str | None = None,
        correlation_id: str | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEntry]:
        """General-purpose filtered scan (Plan 009, Phase 10 / R6). See ABC docstring."""
        stmt = select(AuditEntryModel).order_by(AuditEntryModel.occurred_at.desc())
        if actor_id is not None:
            stmt = stmt.where(AuditEntryModel.actor_id == actor_id)
        if action is not None:
            stmt = stmt.where(AuditEntryModel.action == action)
        if entity_type is not None:
            stmt = stmt.where(AuditEntryModel.entity_type == entity_type)
        if entity_id is not None:
            stmt = stmt.where(AuditEntryModel.entity_id == entity_id)
        if tenant_id is not None:
            stmt = stmt.where(AuditEntryModel.tenant_id == tenant_id)
        if correlation_id is not None:
            stmt = stmt.where(AuditEntryModel.correlation_id == correlation_id)
        if occurred_from is not None:
            stmt = stmt.where(AuditEntryModel.occurred_at >= occurred_from)
        if occurred_to is not None:
            stmt = stmt.where(AuditEntryModel.occurred_at <= occurred_to)
        stmt = stmt.limit(limit).offset(offset)

        async with self._session_factory() as session:
            result = await session.execute(stmt)
            rows = result.scalars().all()
        return [_model_to_entry(row) for row in rows]

    async def delete_where(
        self,
        *,
        older_than: datetime | None = None,
        entity_type: str | None = None,
        tenant_id: str | None = None,
        limit: int | None = None,
        allow_chain_break: bool = False,
    ) -> int:
        """
        Chunked-sweep-friendly bulk delete (Plan 009, Phase 2 / R3) — one
        predicate is required.

        On a ``hash_chain=True`` repository, pruning breaks the chain by
        construction (Plan 009, Phase 12 / R8) — refuses unless
        ``allow_chain_break=True``.
        """
        from sqlalchemy import delete as sa_delete

        if self._hash_chain and not allow_chain_break:
            raise ValueError(
                "This SAAuditRepository was constructed with hash_chain=True "
                "— delete_where() would break the tamper-evidence chain "
                "(a deleted row is indistinguishable from a ChainGap at "
                "verify_chain() time). Pass allow_chain_break=True to "
                "acknowledge and proceed anyway."
            )

        if older_than is None and entity_type is None and tenant_id is None:
            raise ValueError(
                "delete_where() requires at least one predicate "
                "(older_than/entity_type/tenant_id) — refusing to delete "
                "every entry."
            )
        if limit is not None and limit < 1:
            raise ValueError(f"delete_where limit must be ≥ 1, got {limit}.")

        select_stmt = select(AuditEntryModel.entry_id)
        if older_than is not None:
            select_stmt = select_stmt.where(AuditEntryModel.occurred_at < older_than)
        if entity_type is not None:
            select_stmt = select_stmt.where(AuditEntryModel.entity_type == entity_type)
        if tenant_id is not None:
            select_stmt = select_stmt.where(AuditEntryModel.tenant_id == tenant_id)
        if limit is not None:
            select_stmt = select_stmt.limit(limit)

        async with self._session_factory() as session:
            ids = [row[0] for row in (await session.execute(select_stmt)).fetchall()]
            if not ids:
                return 0
            await session.execute(
                sa_delete(AuditEntryModel).where(AuditEntryModel.entry_id.in_(ids))
            )
            await session.commit()
        return len(ids)

    def __repr__(self) -> str:
        return f"SAAuditRepository(session_factory={self._session_factory!r})"


# ── _SAAuditRepositoryInSession ───────────────────────────────────────────────


class _SAAuditRepositoryInSession(AuditRepository):
    """
    Private variant of ``SAAuditRepository`` that uses a provided session.

    Intended for strict-consistency use cases where audit entries must be
    written inside the same UoW transaction as the domain entity write.

    Obtained via ``SAAuditRepository._from_session(session)``.

    Thread safety:  ❌ AsyncSession is not thread-safe.  Use one per request.
    Async safety:   ✅ All methods are ``async def``.

    Edge cases:
        - ``save()`` does NOT commit — the caller's UoW controls the boundary.
        - ``list_for_entity()`` executes on the provided session — pending
          unsaved changes are flushed automatically (SA default).
    """

    def __init__(self, session: AsyncSession) -> None:
        """
        Args:
            session: Active ``AsyncSession``.  Typically ``uow.session``.
        """
        # Store session — all operations use this single session.
        self._session = session

    async def save(self, entry: AuditEntry) -> None:
        """
        Stage ``entry`` in the session without committing.

        The entry is persisted only when the enclosing UoW commits.

        Args:
            entry: The ``AuditEntry`` to stage.

        Edge cases:
            - If the UoW is rolled back, this entry is never written.
            - Duplicate ``entry_id`` raises ``IntegrityError`` at commit time.

        Async safety: ✅ Protected by the SA session lock.
        """
        self._session.add(
            AuditEntryModel(
                entry_id=entry.entry_id,
                entity_type=entry.entity_type,
                entity_id=entry.entity_id,
                action=entry.action,
                actor_id=entry.actor_id,
                diff=entry.diff,
                occurred_at=entry.occurred_at,
                correlation_id=entry.correlation_id,
                tenant_id=entry.tenant_id,
            )
        )
        _logger.debug(
            "_SAAuditRepositoryInSession.save: staged entry_id=%s (uncommitted)",
            entry.entry_id,
        )

    async def list_for_entity(
        self,
        entity_type: str,
        entity_id: str,
        *,
        limit: int = 100,
        tenant_id: str | None = None,
    ) -> list[AuditEntry]:
        """
        Return audit entries using the injected session.

        Args:
            entity_type: Entity class name to filter by.
            entity_id:   Entity primary key string to filter by.
            limit:       Maximum number of entries to return.
            tenant_id:   Plan 009 (R4) — optional tenant filter.

        Returns:
            List of ``AuditEntry`` objects ordered by ``occurred_at DESC``.

        Async safety: ✅ Awaits ``session.execute()``.
        """
        stmt = (
            select(AuditEntryModel)
            .where(
                AuditEntryModel.entity_type == entity_type,
                AuditEntryModel.entity_id == entity_id,
            )
            .order_by(AuditEntryModel.occurred_at.desc())
            .limit(limit)
        )
        if tenant_id is not None:
            stmt = stmt.where(AuditEntryModel.tenant_id == tenant_id)
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [_model_to_entry(row) for row in rows]

    def __repr__(self) -> str:
        return f"_SAAuditRepositoryInSession(session={self._session!r})"


# ── Public API ────────────────────────────────────────────────────────────────


__all__ = [
    "AuditEntryModel",
    "audit_metadata",
    "SAAuditRepository",
]
