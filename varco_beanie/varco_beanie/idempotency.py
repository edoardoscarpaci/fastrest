"""
varco_beanie.idempotency
=========================
``BeanieIdempotencyStore`` — MongoDB/Beanie ``AbstractIdempotencyStore``
(Plan 029 / D1b, Step 11).

Atomicity (§D-D1-atomic) comes from a unique index on ``key`` plus catching
``DuplicateKeyError`` on a losing concurrent insert — the same pattern
``BeanieDeduplicator`` uses for event deduplication, adapted here to
distinguish "someone else is still running" (``IN_FLIGHT``) from "someone
else already finished" (``REPLAY``).

DESIGN: an application-side ``expires_at`` field over a MongoDB TTL index
    ✅ ``AbstractIdempotencyStore.reserve()`` takes a per-call ``ttl`` —
       a MongoDB TTL index's ``expireAfterSeconds`` is fixed at index-
       definition time (the same limitation ``BeanieDeduplicator``'s own
       DESIGN block documents), so it cannot honour a caller-chosen TTL
       that may differ per request/route.
    ❌ No free server-side reaping — ``delete_expired()`` must be called
       periodically by the application, same trade-off ``SAIdempotencyStore``
       makes for the identical reason.

Collection
----------
``IdempotencyDocument`` maps to the ``varco_idempotency`` MongoDB
collection. Register it in your ``init_beanie()`` call::

    from varco_beanie.idempotency import IdempotencyDocument
    await init_beanie(database=db, document_models=[..., IdempotencyDocument])

Usage::

    from varco_beanie.idempotency import BeanieIdempotencyStore

    store = BeanieIdempotencyStore()
    outcome = await store.reserve("order-42", fingerprint, ttl=86400.0)

Thread safety:  ⚠️ If ``session`` is set: one store per task (same rule as
                ``BeanieDeduplicator``). Session-less instances are safe to
                share.
Async safety:   ✅ All methods are ``async def``.

📚 Docs
- 🔍 https://www.mongodb.com/docs/manual/core/index-unique/
  MongoDB unique indexes — enforces one document per key at DB level.
- 🐍 https://pymongo.readthedocs.io/en/stable/api/pymongo/errors.html
  pymongo.errors.DuplicateKeyError — raised when a unique index is violated.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel
from pymongo.errors import DuplicateKeyError
from varco_core.idempotency.base import AbstractIdempotencyStore, ReserveOutcome
from varco_core.idempotency.record import IdempotencyRecord

if TYPE_CHECKING:
    from pymongo.asynchronous.client_session import AsyncClientSession

_logger = logging.getLogger(__name__)


class IdempotencyDocument(Document):
    """
    Beanie document backing ``BeanieIdempotencyStore``.

    Attributes:
        key:         The (already scoped) idempotency key — unique-indexed.
        fingerprint: The ``compute_fingerprint()`` output for the request
                     that reserved this key.
        state:       ``"reserved"`` or ``"completed"``.
        status:      HTTP status code, once completed. ``None`` while
                     reserved.
        body:        Raw response body bytes, once completed.
        headers:     Replay-filtered response headers, once completed.
        created_at:  UTC timestamp of first reservation.
        expires_at:  UTC timestamp after which this record is stale —
                     checked in application code (see the module DESIGN
                     block for why no MongoDB TTL index is used).

    Thread safety:  ✅ Document class is a static definition — no mutable state.
    Async safety:   ✅ All Beanie methods are ``async def``.
    """

    key: str
    fingerprint: str
    state: str
    status: int | None = None
    body: bytes | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    expires_at: datetime

    class Settings:
        name = "varco_idempotency"
        indexes = [
            IndexModel([("key", ASCENDING)], unique=True),
        ]


class BeanieIdempotencyStore(AbstractIdempotencyStore):
    """
    MongoDB/Beanie ``AbstractIdempotencyStore`` backed by
    ``IdempotencyDocument``.

    Args:
        session: Optional Beanie/pymongo session for transactional use.
                 ``None`` (default) — safe to share across requests.

    Thread safety:  ⚠️ See module docstring.
    Async safety:   ✅ All methods are ``async def``.
    """

    def __init__(self, *, session: AsyncClientSession | None = None) -> None:
        self._session = session

    async def reserve(self, key: str, fingerprint: str, *, ttl: float) -> ReserveOutcome:
        """See ``AbstractIdempotencyStore.reserve()``."""
        if ttl <= 0:
            raise ValueError(f"reserve() ttl must be > 0, got {ttl!r}.")
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl)

        if await self._try_insert(key, fingerprint, now, expires_at):
            return ReserveOutcome.ACQUIRED

        doc = await IdempotencyDocument.find_one(
            IdempotencyDocument.key == key, session=self._session
        )
        if doc is None:
            # Deleted between our failed insert and this find — retry once.
            return (
                ReserveOutcome.ACQUIRED
                if await self._try_insert(key, fingerprint, now, expires_at)
                else ReserveOutcome.IN_FLIGHT
            )

        if _ensure_tz(doc.expires_at) <= now:
            await doc.delete(session=self._session)
            return (
                ReserveOutcome.ACQUIRED
                if await self._try_insert(key, fingerprint, now, expires_at)
                else ReserveOutcome.IN_FLIGHT
            )

        if doc.state == "completed":
            return ReserveOutcome.REPLAY
        return ReserveOutcome.IN_FLIGHT

    async def _try_insert(
        self, key: str, fingerprint: str, now: datetime, expires_at: datetime
    ) -> bool:
        """Attempt the atomic insert; return ``True`` iff it won the race."""
        try:
            await IdempotencyDocument(
                key=key,
                fingerprint=fingerprint,
                state="reserved",
                created_at=now,
                expires_at=expires_at,
            ).insert(session=self._session)
            return True
        except DuplicateKeyError:
            return False

    async def complete(self, key: str, record: IdempotencyRecord) -> None:
        """See ``AbstractIdempotencyStore.complete()``."""
        doc = await IdempotencyDocument.find_one(
            IdempotencyDocument.key == key, session=self._session
        )
        if doc is None:
            return
        doc.state = "completed"
        doc.fingerprint = record.fingerprint
        doc.status = record.status
        doc.body = record.body
        doc.headers = dict(record.headers)
        await doc.save(session=self._session)

    async def get(self, key: str) -> IdempotencyRecord | None:
        """See ``AbstractIdempotencyStore.get()``."""
        doc = await IdempotencyDocument.find_one(
            IdempotencyDocument.key == key, session=self._session
        )
        if doc is None or doc.state != "completed":
            return None
        if _ensure_tz(doc.expires_at) <= datetime.now(UTC):
            return None
        return IdempotencyRecord(
            status=doc.status or 0,
            body=doc.body or b"",
            headers=doc.headers,
            fingerprint=doc.fingerprint,
            created_at=_ensure_tz(doc.created_at),
        )

    async def release(self, key: str) -> None:
        """See ``AbstractIdempotencyStore.release()``."""
        doc = await IdempotencyDocument.find_one(
            IdempotencyDocument.key == key, session=self._session
        )
        if doc is not None:
            await doc.delete(session=self._session)

    async def delete_expired(self) -> int:
        """See ``AbstractIdempotencyStore.delete_expired()`` — a real sweep,
        since no MongoDB TTL index is used (see module DESIGN block)."""
        now = datetime.now(UTC)
        result = await IdempotencyDocument.find(
            IdempotencyDocument.expires_at < now, session=self._session
        ).delete()
        return result.deleted_count if result is not None else 0


def _ensure_tz(dt: datetime) -> datetime:
    """Coerce a naive datetime (BSON round-trip) to UTC-aware."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


__all__ = ["BeanieIdempotencyStore", "IdempotencyDocument"]
