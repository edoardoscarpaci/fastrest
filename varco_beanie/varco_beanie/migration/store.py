"""
varco_beanie.migration.store
=============================
The ``varco_migrations`` collection: applied-migration records and the
lock document, both accessed via raw pymongo/Motor calls (no ``Document``
class) — matching what ``BeanieEncryptionKeyStore`` already does for its
own collection.

DESIGN: raw collection access, not a Beanie ``Document``
    ✅ The migration runner must work before ``init_beanie()`` has run (it
       IS part of getting the schema ready) — a ``Document`` class requires
       Beanie initialization first, which would be a chicken-and-egg
       problem for a migration collection.
    ✅ Owner-fenced ``release()`` — a reclaimed holder (past TTL) cannot
       delete the NEW holder's lock, mirroring the fenced job lease from
       Plan 005 Phase 4.

Thread safety:  ✅ Motor collections are safe for concurrent coroutine use.
Async safety:   ✅ All methods are ``async def``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

COLLECTION_NAME = "varco_migrations"
_LOCK_ID = "__lock__"


class MigrationStore:
    """
    Raw-pymongo access to the ``varco_migrations`` collection.

    Args:
        db: An ``AsyncIOMotorDatabase`` (or any object supporting
            ``__getitem__`` → a collection with ``find``,
            ``find_one_and_update``, ``insert_one``, ``delete_one``).
    """

    def __init__(self, db: Any) -> None:
        self._collection = db[COLLECTION_NAME]

    # ── Applied-migration records ────────────────────────────────────────

    async def applied_versions(self) -> set[str]:
        """Return the set of already-applied migration ``version`` strings."""
        versions: set[str] = set()
        async for doc in self._collection.find({}):
            _id = doc.get("_id")
            if isinstance(_id, str) and _id != _LOCK_ID:
                versions.add(_id)
        return versions

    async def get_record(self, version: str) -> dict[str, Any] | None:
        """Return the applied-migration record for ``version``, or ``None``."""
        return await self._collection.find_one({"_id": version})

    async def record_applied(
        self,
        version: str,
        *,
        name: str,
        checksum: str,
        duration_ms: float,
        applied_by: str,
    ) -> None:
        """Insert the applied-migration record for ``version``."""
        await self._collection.insert_one(
            {
                "_id": version,
                "name": name,
                "checksum": checksum,
                "applied_at": datetime.now(UTC),
                "duration_ms": duration_ms,
                "applied_by": applied_by,
            }
        )

    async def remove_record(self, version: str) -> None:
        """Delete the applied-migration record for ``version`` (used by ``downgrade``)."""
        await self._collection.delete_one({"_id": version})

    # ── Lock document ─────────────────────────────────────────────────────

    async def acquire(self, owner: str, ttl: float) -> bool:
        """
        Try to acquire the lock document.

        Uses a single conditional ``find_one_and_update(upsert=True)``
        matching ``{_id: "__lock__", $or: [{expires_at: {$lt: now}}, {owner: owner}]}``
        — ``_id`` uniqueness gives the atomicity.

        Args:
            owner: This holder's identity (``f"{hostname}:{pid}"`` by default).
            ttl:   Seconds until the lock is considered expired/reclaimable.

        Returns:
            ``True`` if acquired (or re-acquired by the same owner),
            ``False`` if held by another live owner.
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl)

        try:
            await self._collection.find_one_and_update(
                {
                    "_id": _LOCK_ID,
                    "$or": [{"expires_at": {"$lt": now}}, {"owner": owner}],
                },
                {
                    "$set": {
                        "owner": owner,
                        "acquired_at": now,
                        "expires_at": expires_at,
                        "heartbeat_at": now,
                    }
                },
                upsert=True,
            )
        except Exception as exc:  # pymongo.errors.DuplicateKeyError
            # DESIGN: MongoDB's upsert-on-no-match race
            #   ✅ When the filter (including the $or condition) matches NO
            #      document, Mongo attempts to INSERT a new one using only
            #      the query's equality fields (_id) — colliding with the
            #      OTHER, already-live lock document that has the same
            #      _id. That collision (E11000 DuplicateKeyError) is
            #      itself proof this call did NOT win the lock — another
            #      still-live owner holds it — so it is the correct signal
            #      to return False here, not an error to propagate.
            #   ❌ Importing pymongo.errors lazily (rather than at module
            #      scope) keeps this module usable against the hand-rolled
            #      fake collection in unit tests, which never raises it.
            from pymongo.errors import DuplicateKeyError

            if isinstance(exc, DuplicateKeyError):
                return False
            raise

        # find_one_and_update's return-document semantics differ between the
        # pre-image default and ReturnDocument.AFTER — never depend on it.
        # A confirm read after the conditional write is what actually
        # decides ownership either way.
        record = await self._collection.find_one({"_id": _LOCK_ID})
        return bool(record) and record.get("owner") == owner

    async def heartbeat(self, owner: str, ttl: float) -> None:
        """Renew ``expires_at``/``heartbeat_at`` for the current holder."""
        now = datetime.now(UTC)
        await self._collection.find_one_and_update(
            {"_id": _LOCK_ID, "owner": owner},
            {"$set": {"heartbeat_at": now, "expires_at": now + timedelta(seconds=ttl)}},
        )

    async def release(self, owner: str) -> None:
        """
        Release the lock — only if ``owner`` still matches (fencing).

        A reclaimed holder (past TTL, replaced by a new owner) cannot
        delete the new holder's lock — its ``owner`` no longer matches.
        """
        await self._collection.delete_one({"_id": _LOCK_ID, "owner": owner})


__all__ = ["COLLECTION_NAME", "MigrationStore"]
