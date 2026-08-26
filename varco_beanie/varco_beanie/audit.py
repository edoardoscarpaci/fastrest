"""
varco_beanie.audit
==================
Beanie (pymongo / MongoDB) implementation of ``AuditRepository``.

``BeanieAuditRepository`` maps ``AuditEntry`` value objects to the
``varco_audit_log`` MongoDB collection using the ``AuditDocument`` Beanie
document class.

Unlike the outbox pattern, audit records are append-only — there are no
``get_pending()`` / ``delete()`` operations.  The interface is simpler:
``save()`` inserts; ``list_for_entity()`` queries.

Collection
----------
``AuditDocument`` maps to the ``varco_audit_log`` collection.  Register it
with Beanie before use::

    from varco_beanie.audit import AuditDocument

    # Option A — init_beanie
    await init_beanie(database=db, document_models=[..., AuditDocument])

    # Option B — BeanieRepositoryProvider
    provider.register(AuditDocument)
    await provider.init()

Registering ``AuditDocument`` is the *only* required step — including for
``BeanieAuditRepository(hash_chain=True)``, whose ``varco_audit_seq`` counter
is reached through ``AuditDocument``'s own database (see ``_seq_collection``).

Usage::

    from varco_beanie.audit import BeanieAuditRepository
    from varco_core.service.audit import AuditConsumer

    consumer = AuditConsumer(audit_repo=BeanieAuditRepository())
    consumer.register_to(event_bus)

    # Direct query
    entries = await BeanieAuditRepository().list_for_entity("Order", str(order_id))

DESIGN: no-session default (session=None)
    ✅ Audit records are written by AuditConsumer asynchronously after the
       domain commit — they do NOT need to be in the same MongoDB transaction.
    ✅ ``session=None`` works on single-node MongoDB (no replica set required).
    ✅ Passing a session enables replica-set-transactional audit writes for
       strict-consistency scenarios.
    ❌ On single-node MongoDB without a session, there is no rollback if the
       consumer crashes after insert.  This is acceptable — at-least-once
       delivery means duplicates are possible; ``entry_id`` uniqueness guards
       against double-persist.

DESIGN: ``insert(..., ignore_errors=False)`` + no conflict handling on Beanie
    MongoDB does not have INSERT OR IGNORE equivalent — instead ``entry_id``
    uniqueness is enforced by a sparse unique index on ``_id`` (Beanie's default
    for ``id`` fields).  Duplicate inserts raise ``DuplicateKeyError`` which
    ``AuditConsumer`` should treat as already-processed (idempotent consumer).

Thread safety:  ⚠️ ``AsyncClientSession``, if provided, is NOT thread-safe.
                    Session-less instances are safe to share.
Async safety:   ✅ All methods are ``async def``.

📚 Docs
- 🔍 https://beanie-odm.dev/tutorial/defining-a-document/
  Beanie Document — collection configuration and field mapping
- 🔍 https://www.mongodb.com/docs/manual/core/transactions/
  MongoDB transactions — replica set requirement for multi-doc atomicity
- 🐍 https://pymongo.readthedocs.io/en/stable/api/pymongo/client_session.html
  AsyncClientSession — pymongo transaction API
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from beanie import Document
from pydantic import Field
from varco_core.service.audit import AuditEntry, AuditRepository

if TYPE_CHECKING:
    from pymongo.asynchronous.client_session import AsyncClientSession

_logger = logging.getLogger(__name__)


# ── AuditDocument ─────────────────────────────────────────────────────────────


class AuditDocument(Document):
    """
    Beanie document representing a single persisted audit log entry.

    Maps to the ``varco_audit_log`` MongoDB collection.

    Register this document in your ``init_beanie()`` or
    ``BeanieRepositoryProvider.register()`` call before using
    ``BeanieAuditRepository``.

    DESIGN: Beanie Document over raw pymongo collection
        ✅ Consistent with varco_beanie repository layer conventions.
        ✅ Beanie auto-generates the unique ``_id`` index on ``id`` —
           duplicate ``entry_id`` raises ``DuplicateKeyError`` (idempotency).
        ✅ Pydantic validation ensures ``diff`` is always a ``dict``.
        ❌ Requires ``init_beanie()`` at startup.

    Thread safety:  ✅ Document class is a static definition — no mutable state.
    Async safety:   ✅ All Beanie methods are ``async def``.

    Attributes:
        id:             UUIDv4 — matches ``AuditEntry.entry_id``.
        entity_type:    Entity class name (e.g. ``"Order"``).
        entity_id:      String representation of the entity primary key.
        action:         One of ``"create"``, ``"update"``, ``"delete"``.
        actor_id:       Identity of the mutation actor — ``None`` for system.
        diff:           Field-level change data.  Structure varies by action.
        occurred_at:    UTC timestamp when the service emitted the audit event.
        correlation_id: Optional request-tracing identifier.
        tenant_id:      Optional tenant identifier.

    Edge cases:
        - ``diff`` is stored as a plain BSON document (dict) — all values must
          be BSON-serializable.  Pydantic types (e.g. UUID, datetime) must be
          converted to JSON-compatible types before storing in ``diff``.
        - ``id`` collision (duplicate ``entry_id``) raises
          ``pymongo.errors.DuplicateKeyError`` — the caller should treat this
          as "already persisted" (at-least-once consumer idempotency).
        - No additional indexes are created by default.  For production
          ``list_for_entity()`` performance, add a compound index on
          ``{ entity_type: 1, entity_id: 1, occurred_at: -1 }``.
    """

    # Override Beanie's ObjectId pk with a UUID — matches AuditEntry.entry_id.
    id: UUID = Field(default_factory=uuid4)

    entity_type: str
    """Entity class name — e.g. ``"Order"``."""

    entity_id: str
    """String representation of the entity primary key."""

    action: str
    """Mutation action — one of ``"create"``, ``"update"``, ``"delete"``."""

    actor_id: str | None = None
    """Identity of the mutation actor — ``None`` for system-initiated mutations."""

    diff: dict[str, Any] = Field(default_factory=dict)
    """Field-level change data.  Structure varies by action."""

    occurred_at: datetime = Field(
        # Always UTC — avoids naive datetime ambiguity on round-trip.
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    """UTC timestamp of when the service emitted the audit event."""

    correlation_id: str | None = None
    """Optional request-tracing identifier."""

    tenant_id: str | None = None
    """Optional tenant identifier for multi-tenant deployments."""

    seq: int | None = None
    """Plan 009, Phase 12 (R8) — monotone chain sequence number. ``None``
    outside a chained deployment (``hash_chain=False``, the default)."""

    prev_hash: str | None = None
    """Plan 009, Phase 12 (R8) — the previous chain entry's ``entry_hash()``."""

    entry_hash: str | None = None
    """Plan 009, Phase 12 (R8) — this entry's own computed hash, stored so
    the NEXT chained write can read it as its ``prev_hash`` without
    recomputing (avoids datetime round-trip precision drift)."""

    class Settings:
        """Beanie collection and index configuration."""

        # Collection name — all audit log entries go here.
        name = "varco_audit_log"

        # DESIGN: no compound index declared here — callers can add
        # ``{ entity_type: 1, entity_id: 1, occurred_at: -1 }`` via a
        # Beanie migration or Atlas UI for production ``list_for_entity()``
        # performance.  Declaring indexes here would affect all deployments.
        indexes: list = []

    def __repr__(self) -> str:
        return (
            f"AuditDocument("
            f"id={self.id}, "
            f"entity={self.entity_type}/{self.entity_id}, "
            f"action={self.action!r})"
        )


class AuditSeqDocument(Document):
    """
    Singleton counter document backing ``BeanieAuditRepository(hash_chain=True)``'s
    monotone ``seq`` (Plan 009, Phase 12 / R8) — MongoDB has no
    ``BIGSERIAL``, so this is the "dedicated ``varco_audit_seq`` counter
    document" the plan names, incremented via
    ``find_one_and_update({"$inc": {"value": 1}}, upsert=True)``.

    Edge cases:
        - Missing on first write → created via ``upsert=True`` (no separate
          bootstrap step needed).

    Thread safety:  ✅ ``find_one_and_update`` is atomic at the Mongo level —
                        safe for concurrent chained ``save()`` calls across
                        processes (unlike SA's SQLite fallback, which needs
                        an additional in-process ``asyncio.Lock``).
    """

    id: str = Field(default="varco_audit_seq")
    value: int = 0

    class Settings:
        name = "varco_audit_seq"


_SEQ_COLLECTION_NAME = "varco_audit_seq"


def _to_bson_precision(value: datetime) -> datetime:
    """
    Truncate a datetime to whole milliseconds — BSON's datetime resolution.

    DESIGN: hash what MongoDB can actually store, not what Python holds
        ✅ ``AuditEntry.entry_hash()`` hashes ``occurred_at.isoformat()`` at
           microsecond precision, but BSON datetimes are millisecond-precision:
           a value saved as ``…121255`` reads back as ``…121000``.  Recomputing
           the hash from a loaded entry therefore produced a *different* digest
           and ``verify_chain()`` reported a ``HashMismatch`` on every single
           link — tamper evidence that always cries tamper is worse than none.
        ✅ Truncating before both hashing and persisting makes the stored value
           round-trip byte-exact, so the recomputed digest matches by
           construction.  This is a storage-precision concern, so it is fixed
           in the backend rather than by weakening the portable hash contract
           (SA/Postgres stores microseconds and must keep hashing them).
        ❌ Sub-millisecond ordering information is dropped from a chained
           Beanie audit entry.  Harmless: ``seq`` — not ``occurred_at`` — is
           the chain's ordering key.

    Args:
        value: A timezone-aware (or naive) datetime.

    Returns:
        The same datetime with sub-millisecond microseconds zeroed.

    Edge cases:
        - Already millisecond-aligned values are returned unchanged.
    """
    return value.replace(microsecond=(value.microsecond // 1000) * 1000)


def _seq_collection() -> Any:
    """
    Return the raw ``varco_audit_seq`` pymongo collection.

    DESIGN: derive the counter collection from ``AuditDocument``'s database
            instead of requiring ``AuditSeqDocument`` to be Beanie-initialised
        ✅ Enabling ``hash_chain=True`` stays a one-flag change.  Reaching the
           counter through ``AuditSeqDocument.get_pymongo_collection()`` made
           it a *two*-step change — the second step (adding
           ``AuditSeqDocument`` to ``init_beanie(document_models=...)``) was
           documented nowhere, so following the feature guide produced
           ``CollectionWasNotInitialized`` on the very first chained write.
           That also contradicted ``AuditSeqDocument``'s own documented
           contract ("no separate bootstrap step needed").
        ✅ The counter is only ever used through raw
           ``find_one_and_update({"$inc": ...}, upsert=True)`` — no Beanie ODM
           feature (validation, relations, indexes) is used on it, so the
           document registration bought nothing.
        ✅ The counter always lands in the same database as the audit log it
           sequences — a chain whose counter lived in another database would
           be silently wrong.
        ❌ ``AuditSeqDocument`` becomes schema documentation rather than the
           access path.  It is kept (and exported) so operators can register
           it deliberately for index/migration tooling.

    Returns:
        The pymongo ``AsyncCollection`` for ``varco_audit_seq``, resolved from
        the database ``AuditDocument`` is bound to.

    Raises:
        beanie.exceptions.CollectionWasNotInitialized: ``AuditDocument`` itself
            was never registered with Beanie — the one registration the
            feature guide does document.
    """
    return AuditDocument.get_pymongo_collection().database[_SEQ_COLLECTION_NAME]


# ── BeanieAuditRepository ─────────────────────────────────────────────────────


class BeanieAuditRepository(AuditRepository):
    """
    Beanie implementation of ``AuditRepository``.

    Persists audit entries in the ``varco_audit_log`` MongoDB collection via
    ``AuditDocument``.  Optionally accepts an ``AsyncClientSession`` so
    operations participate in the caller's MongoDB transaction (replica set
    only).

    Pass ``session=None`` (the default) for:
    - Single-node MongoDB deployments.
    - ``AuditConsumer`` use (asynchronous, no transaction required).
    - Testing against an in-memory / embedded MongoDB.

    Pass ``session=...`` for:
    - Strict-consistency audit writes inside a replica-set UoW transaction.

    Thread safety:  ⚠️ If ``session`` is provided, one instance per request.
                        ``session=None`` instances are safe to share.
    Async safety:   ✅ All methods are ``async def``.

    Args:
        session: Optional pymongo ``AsyncClientSession``.  Pass ``None``
                 (default) for non-transactional use.

    Edge cases:
        - ``AuditDocument`` must be registered with Beanie before calling
          any method.  Raises ``RuntimeError`` if not.
        - ``save()`` with a duplicate ``entry_id`` raises
          ``pymongo.errors.DuplicateKeyError`` (idempotency guard).
          ``AuditConsumer`` should be wired with a ``retry_policy`` that
          does NOT retry on ``DuplicateKeyError``.
        - ``list_for_entity()`` sorts in-memory if no compound index exists —
          acceptable for small collections, but add an index in production.

    Example::

        consumer = AuditConsumer(audit_repo=BeanieAuditRepository())
        consumer.register_to(event_bus)
    """

    def __init__(
        self,
        *,
        session: AsyncClientSession | None = None,
        hash_chain: bool = False,
    ) -> None:
        """
        Args:
            session:    Optional pymongo ``AsyncClientSession``.
                        Defaults to ``None`` — non-transactional use.
            hash_chain: Plan 009, Phase 12 (R8) — opt-in tamper-evidence.
                        When ``True``, every ``save()`` atomically increments
                        the ``varco_audit_seq`` counter document
                        (``find_one_and_update({"$inc": ...}, upsert=True)``)
                        to obtain the next ``seq``, reads the previous
                        entry's stored ``entry_hash`` as this entry's
                        ``prev_hash``, and stamps both before inserting —
                        Mongo's own atomic counter increment is the
                        serialization guarantee (RD-8), no extra
                        ``asyncio.Lock`` needed (unlike the SQLite fallback
                        in ``SAAuditRepository``).
        """
        # Stored as optional — passed to every Beanie operation that accepts it.
        self._session = session
        self._hash_chain = hash_chain

    async def save(self, entry: AuditEntry) -> None:
        """
        Insert ``entry`` into the ``varco_audit_log`` collection.

        When ``session`` is set, the insert joins the caller's transaction
        (replica set only).  Without a session, the insert is immediate.

        Args:
            entry: The ``AuditEntry`` to persist.

        Raises:
            pymongo.errors.DuplicateKeyError: If ``entry.entry_id`` already
                exists — treat as already-persisted (idempotency).
            RuntimeError: If ``AuditDocument`` was not registered with Beanie.

        Edge cases:
            - ``diff`` values must be BSON-serializable.  Pydantic model dumps
              (strings, numbers, lists, dicts) are fine.  Raw UUIDs or
              datetimes must be stringified before landing in ``diff``.
            - Without a transaction, the insert is immediately durable —
              no rollback if a subsequent step fails.

        Async safety: ✅ Awaits ``document.insert()`` (or, when
            ``hash_chain=True``, delegates to ``_save_chained()``).
        """
        if self._hash_chain:
            await self._save_chained(entry)
            return

        doc = AuditDocument(
            id=entry.entry_id,
            entity_type=entry.entity_type,
            entity_id=entry.entity_id,
            action=entry.action,
            actor_id=entry.actor_id,
            # Copy diff by value — AuditEntry.diff may be shared.
            diff=dict(entry.diff),
            occurred_at=entry.occurred_at,
            correlation_id=entry.correlation_id,
            tenant_id=entry.tenant_id,
        )
        # Pass session if available — routes the insert through the transaction.
        if self._session is not None:
            await doc.insert(session=self._session)
        else:
            await doc.insert()

        _logger.debug(
            "BeanieAuditRepository.save: inserted entry_id=%s entity=%s/%s action=%s",
            entry.entry_id,
            entry.entity_type,
            entry.entity_id,
            entry.action,
        )

    async def _save_chained(self, entry: AuditEntry) -> None:
        """
        Establish the hash-chain link for ``entry`` via the atomic
        ``varco_audit_seq`` counter and persist it (Plan 009, Phase 12 / R8).

        Args:
            entry: The ``AuditEntry`` to chain and persist.

        Edge cases:
            - Counter document missing → created on first write via
              ``upsert=True`` — no separate bootstrap step.
            - The counter collection is reached through ``AuditDocument``'s
              own already-initialised database (``_seq_collection()``), so
              enabling ``hash_chain=True`` needs no extra
              ``init_beanie(document_models=...)`` entry.

        Async safety: ✅ ``find_one_and_update`` is atomic at the Mongo
            level — safe under concurrent chained ``save()`` calls, including
            across processes (stronger than the SA/SQLite fallback).
        """
        from pymongo import ReturnDocument

        counter = await _seq_collection().find_one_and_update(
            {"_id": _SEQ_COLLECTION_NAME},
            {"$inc": {"value": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        next_seq = int(counter["value"])

        prev_hash: str | None = None
        if next_seq > 1:
            last_doc = await AuditDocument.find(
                AuditDocument.seq == next_seq - 1
            ).first_or_none()
            prev_hash = last_doc.entry_hash if last_doc is not None else None

        # occurred_at must be truncated to BSON's millisecond resolution
        # *before* the hash is computed — otherwise the stored timestamp
        # differs from the hashed one and every recomputed link mismatches.
        chained_entry = dataclasses.replace(
            entry,
            seq=next_seq,
            prev_hash=prev_hash,
            occurred_at=_to_bson_precision(entry.occurred_at),
        )
        computed_hash = chained_entry.entry_hash()

        doc = AuditDocument(
            id=chained_entry.entry_id,
            entity_type=chained_entry.entity_type,
            entity_id=chained_entry.entity_id,
            action=chained_entry.action,
            actor_id=chained_entry.actor_id,
            diff=dict(chained_entry.diff),
            occurred_at=chained_entry.occurred_at,
            correlation_id=chained_entry.correlation_id,
            tenant_id=chained_entry.tenant_id,
            seq=next_seq,
            prev_hash=prev_hash,
            entry_hash=computed_hash,
        )
        if self._session is not None:
            await doc.insert(session=self._session)
        else:
            await doc.insert()

        _logger.debug(
            "BeanieAuditRepository._save_chained: inserted entry_id=%s seq=%d",
            entry.entry_id,
            next_seq,
        )

    async def list_for_entity(
        self,
        entity_type: str,
        entity_id: str,
        *,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """
        Return audit entries for a specific entity, newest-first.

        Uses ``AuditDocument.find(entity_type=..., entity_id=...).sort(-occurred_at).limit(n)``.

        Args:
            entity_type: Entity class name to filter by (e.g. ``"Order"``).
            entity_id:   Entity primary key string to filter by.
            limit:       Maximum number of entries to return.  Default ``100``.

        Returns:
            List of ``AuditEntry`` objects ordered by ``occurred_at DESC``.
            Empty list if no audit records exist for the given entity.

        Edge cases:
            - Without a compound index on ``(entity_type, entity_id, occurred_at)``,
              this is an in-memory sort over all matching documents — O(N).
              Add the index for production workloads.
            - ``limit=0`` returns an empty list (Beanie / Motor behaviour).
            - If ``session`` is set, the query runs within the caller's
              transaction (replica set only).

        Async safety: ✅ Awaits Beanie ``find()`` cursor.
        """
        find_kwargs: dict[str, Any] = {}
        if self._session is not None:
            find_kwargs["session"] = self._session

        docs = (
            await AuditDocument.find(
                # Filter by entity identity — compound equality filter.
                AuditDocument.entity_type == entity_type,
                AuditDocument.entity_id == entity_id,
                **find_kwargs,
            )
            # -occurred_at = descending order (newest first).
            .sort(-AuditDocument.occurred_at)
            .limit(limit)
            .to_list()
        )

        entries = [
            AuditEntry(
                entry_id=doc.id,
                entity_type=doc.entity_type,
                entity_id=doc.entity_id,
                action=doc.action,
                actor_id=doc.actor_id,
                # Copy diff by value — document dict is mutable.
                diff=dict(doc.diff) if doc.diff else {},
                occurred_at=(
                    doc.occurred_at
                    if doc.occurred_at.tzinfo is not None
                    # Coerce naive datetimes to UTC (MongoDB can return naive
                    # datetimes depending on codec configuration).
                    else doc.occurred_at.replace(tzinfo=timezone.utc)
                ),
                correlation_id=doc.correlation_id,
                tenant_id=doc.tenant_id,
                seq=doc.seq,
                prev_hash=doc.prev_hash,
            )
            for doc in docs
        ]

        _logger.debug(
            "BeanieAuditRepository.list_for_entity: entity=%s/%s fetched %d entries",
            entity_type,
            entity_id,
            len(entries),
        )
        return entries

    def __repr__(self) -> str:
        return (
            f"BeanieAuditRepository(" f"session={'set' if self._session else 'None'})"
        )


# ── Public API ────────────────────────────────────────────────────────────────


__all__ = [
    "AuditDocument",
    "AuditSeqDocument",
    "BeanieAuditRepository",
]
