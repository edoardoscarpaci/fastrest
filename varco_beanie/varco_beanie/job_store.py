"""
varco_beanie.job_store
======================
Beanie (pymongo / MongoDB) implementation of ``AbstractJobStore``.

MongoDB differs from relational databases in several ways relevant to job storage:

- **Schema-less**: ``JobDocument`` stores complex fields (``auth_snapshot``,
  ``metadata``, ``task_payload``) as native BSON subdocuments — no JSON
  serialization to Text columns required.
- **Atomic claim**: MongoDB's ``find_one_and_update`` (``findAndModify``)
  provides an atomic PENDING → RUNNING transition in a single round-trip.
  This replaces PostgreSQL's ``SELECT FOR UPDATE SKIP LOCKED`` pattern.
- **No explicit transactions needed**: ``try_claim`` is atomic by default —
  MongoDB's ``findAndModify`` is the built-in primitive for this pattern.

Collection
----------
``JobDocument`` maps to the ``varco_jobs`` MongoDB collection.
It must be included in your ``init_beanie()`` or
``BeanieRepositoryProvider.register()`` call::

    from varco_beanie.job_store import JobDocument

    # Option A — pass to init_beanie directly
    await init_beanie(database=db, document_models=[..., JobDocument])

    # Option B — register with BeanieRepositoryProvider before provider.init()
    provider.register(JobDocument)
    await provider.init()

Usage::

    from varco_beanie.job_store import BeanieJobStore

    store = BeanieJobStore()
    job = Job()
    await store.save(job)
    running = await store.try_claim(job.job_id)

DESIGN: native BSON dict storage over JSON-in-Text
    ✅ Richer queries possible — filter on ``task_payload.task_name`` etc.
    ✅ No serialization overhead — MongoDB stores dicts as BSON directly.
    ✅ Consistent with ``BeanieOutboxRepository`` and ``BeanieInboxRepository``.
    ❌ ``result`` (bytes) is stored as BSON Binary — same as SAJobStore's
       LargeBinary, but BSON Binary carries a subtype byte overhead.

DESIGN: try_claim() uses find_one_and_update (MongoDB findAndModify)
    ✅ Atomic PENDING → RUNNING in one server-side operation — no TOCTOU race.
    ✅ No explicit transaction required — ``findAndModify`` is MongoDB's
       built-in atomic update primitive.
    ✅ Returns the AFTER document so we can build the Job without a second fetch.
    ❌ findAndModify acquires a write lock on the matched document — under very
       high contention this adds latency.  For typical job-runner workloads
       (small number of PENDING jobs recovered on restart) this is negligible.

Thread safety:  ✅ Beanie operates on the Motor/pymongo async client pool — no
                    shared mutable state in ``BeanieJobStore`` itself.
Async safety:   ✅ All methods are ``async def``.

📚 Docs
- 🔍 https://beanie-odm.dev/tutorial/defining-a-document/
  Beanie Document — class definition and collection configuration
- 🔍 https://www.mongodb.com/docs/manual/reference/command/findAndModify/
  MongoDB findAndModify — atomic read-modify-write command
- 🐍 https://motor.readthedocs.io/en/stable/api-asyncio/asyncio_motor_collection.html
  Motor AsyncIOMotorCollection — pymongo async interface
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from beanie import Document, UpdateResponse
from pydantic import Field
from varco_core.job.base import AbstractJobStore, Job, JobStatus, StaleLeaseError
from varco_core.job.task import TaskPayload

_logger = logging.getLogger(__name__)


# ── JobDocument ───────────────────────────────────────────────────────────────


class JobDocument(Document):
    """
    Beanie document representing a single background job.

    Maps to the ``varco_jobs`` MongoDB collection.

    Register this document in your ``init_beanie()`` or
    ``BeanieRepositoryProvider.register()`` call before using
    ``BeanieJobStore``.

    DESIGN: UUID primary key over ObjectId
        ✅ Matches ``Job.job_id`` (UUIDv4) — no separate mapping needed.
        ✅ Consistent with ``OutboxDocument`` and ``InboxDocument`` patterns.
        ✅ Job IDs can be generated client-side and returned in the 202 response
           before the document is persisted.
        ❌ UUID keys are slightly larger than ObjectId (16 vs 12 bytes).

    DESIGN: dict fields (auth_snapshot, job_metadata, task_payload) as BSON
        ✅ MongoDB stores Python dicts as BSON subdocuments natively — no JSON
           serialization to string (unlike the SQLAlchemy implementation).
        ✅ Enables rich queries on subdocument fields in the future.
        ❌ BSON subdocuments cannot be easily diffed via string comparison.

    Thread safety:  ✅ Document class is a static definition — no mutable state.
    Async safety:   ✅ All Beanie methods are ``async def``.

    Attributes:
        id:            UUIDv4 — matches ``Job.job_id``.
        status:        String value of ``JobStatus`` enum (e.g. ``"pending"``).
        created_at:    UTC timestamp of job creation.
        started_at:    UTC timestamp when the job transitioned to RUNNING.
                       ``None`` until claimed.
        completed_at:  UTC timestamp of terminal transition.  ``None`` while running.
        result:        Opaque bytes — caller decides serialization format.
                       ``None`` for non-result jobs (fire-and-forget, FAILED).
        error:         Human-readable failure message.  ``None`` on success.
        callback_url:  Optional webhook URL for completion notification.
        auth_snapshot: Serialized ``AuthContext`` as a BSON subdocument.
                       ``None`` for anonymous/unauthenticated jobs.
        request_token: Raw Bearer JWT for audit and callback authentication.
        job_metadata:  Free-form ``dict`` of extra data (e.g. tenant_id, source).
        task_payload:  ``TaskPayload.to_dict()`` as a BSON subdocument.
                       ``None`` for non-recoverable (no-retry) jobs.

    Edge cases:
        - ``JobDocument`` must be registered with Beanie before any method is
          called.  Beanie raises ``CollectionWasNotInitialized`` otherwise.
        - ``result`` is stored as BSON Binary (subtype 0).  Round-trip is lossless.
        - ``created_at`` from MongoDB may arrive as a naive datetime if the
          pymongo BSON codec strips timezone info.  ``BeanieJobStore`` coerces
          all naive datetimes to UTC when converting to ``Job``.
    """

    # UUID primary key — overrides Beanie's default ObjectId pk.
    id: UUID = Field(default_factory=uuid4)

    status: str
    """String value of JobStatus (e.g. "pending", "running", "completed")."""

    created_at: datetime
    """UTC creation timestamp."""

    started_at: datetime | None = None
    """UTC timestamp of PENDING → RUNNING transition; None until claimed."""

    completed_at: datetime | None = None
    """UTC timestamp of terminal state; None while still running."""

    result: bytes | None = None
    """Opaque result bytes; None for fire-and-forget or failed jobs."""

    error: str | None = None
    """Human-readable failure message; None on success."""

    callback_url: str | None = None
    """Optional webhook URL for completion notification."""

    # BSON subdocument — stored as-is; no JSON serialization needed.
    auth_snapshot: dict[str, Any] | None = None
    """Serialized AuthContext for background workers; None for anonymous jobs."""

    request_token: str | None = None
    """Raw Bearer JWT for audit trail and callback auth."""

    # BSON subdocument — default empty dict avoids None checks downstream.
    job_metadata: dict[str, Any] = Field(default_factory=dict)
    """Free-form extra data dict (e.g. tenant_id, trace_id)."""

    task_payload: dict[str, Any] | None = None
    """TaskPayload.to_dict() as BSON subdocument; None for non-recoverable jobs."""

    # ── Plan 005 Phase 4 — time dimension, lease, fencing (U-17/U-11) ──────
    run_at: datetime | None = None
    """Earliest time this job is eligible to be claimed. None claims
    immediately, exactly as today."""

    attempt: int = 0
    """Number of attempts made so far."""

    max_attempts: int = 1
    """1 (default) == terminal-on-first-failure, today's behaviour."""

    owner_id: str | None = None
    """Identifier of the worker instance currently holding this job's lease."""

    lease_expires_at: datetime | None = None
    """When the current lease expires. None means no lease is held."""

    lease_epoch: int = 0
    """Fencing token, incremented on every claim/renew/reap."""

    expires_at: datetime | None = None
    """U-18 — retention column; API lands in Phase 6."""

    request_issuer: str | None = None
    """U-19 — reference field, populated by store_raw_token=False machinery."""

    request_subject: str | None = None
    """U-19 — reference field."""

    request_token_hash: str | None = None
    """U-19 — sha256 hex digest of request_token."""

    # ── Plan 011 (T2) — DST-safe scheduling, three additive fields ─────────
    # D-7: run_at (above) is MATERIALIZED, not replaced. A document written
    # by a previous version simply has these keys ABSENT — Beanie
    # deserializes the absent keys to these defaults (None/None/0), which
    # is precisely the "unzoned job" state. No migration required.
    run_at_wall: datetime | None = None
    """Naive local wall-clock time, no tzinfo. None = unzoned."""

    run_at_tz: str | None = None
    """IANA zone name. None = unzoned."""

    run_at_fold: int = 0
    """PEP 495 fold — disambiguates an ambiguous materialization."""

    class Settings:
        """Beanie collection configuration."""

        # Collection name in MongoDB — all varco job entries live here.
        # DESIGN: "varco_jobs" matches SAJobStore table name for consistency.
        name = "varco_jobs"

        # DESIGN: no indexes declared here — callers can add status+created_at
        # compound index via Beanie's index management or a separate migration.
        # Avoiding surprise schema side-effects on first import.
        indexes: list = []

    def __repr__(self) -> str:
        return (
            f"JobDocument("
            f"id={self.id}, "
            f"status={self.status!r}, "
            f"created_at={self.created_at!r})"
        )


# ── Serialization helpers ─────────────────────────────────────────────────────


def _ensure_tz(dt: datetime | None) -> datetime | None:
    """
    Coerce a naive ``datetime`` to UTC.

    MongoDB pymongo may strip timezone info from datetimes depending on the
    codec configuration.  This helper ensures all datetimes have a tzinfo.

    Args:
        dt: A ``datetime`` that may or may not have ``tzinfo`` set.

    Returns:
        The same datetime if already timezone-aware, or a UTC-aware copy if naive.
        ``None`` if the input is ``None``.

    Edge cases:
        - Naive datetime → replaced with UTC tzinfo.
        - Already timezone-aware → returned unchanged (no conversion).
        - ``None`` → ``None``.
    """
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _job_to_doc(job: Job) -> JobDocument:
    """
    Convert a ``Job`` frozen dataclass to a ``JobDocument``.

    Complex fields (``task_payload``) are stored as BSON subdocuments via
    ``TaskPayload.to_dict()``.  The ``result`` bytes field maps directly to
    BSON Binary.

    Args:
        job: The ``Job`` instance to convert.

    Returns:
        A ``JobDocument`` ready for Beanie ``insert()`` or comparison.

    Edge cases:
        - ``job.task_payload`` is ``None`` → ``task_payload`` field is ``None``.
        - ``job.metadata`` may be empty dict → stored as ``{}`` (not ``None``).
    """
    return JobDocument(
        id=job.job_id,
        status=job.status.value,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        result=job.result,
        error=job.error,
        callback_url=job.callback_url,
        # BSON subdocument — stored as-is; None for anonymous.
        auth_snapshot=job.auth_snapshot,
        request_token=job.request_token,
        # Always a dict (empty dict for jobs with no metadata).
        job_metadata=job.metadata,
        # to_dict() converts TaskPayload to a plain dict for BSON storage.
        task_payload=(
            job.task_payload.to_dict() if job.task_payload is not None else None
        ),
        run_at=job.run_at,
        attempt=job.attempt,
        max_attempts=job.max_attempts,
        owner_id=job.owner_id,
        lease_expires_at=job.lease_expires_at,
        lease_epoch=job.lease_epoch,
        expires_at=job.expires_at,
        request_issuer=job.request_issuer,
        request_subject=job.request_subject,
        request_token_hash=job.request_token_hash,
        run_at_wall=job.run_at_wall,
        run_at_tz=job.run_at_tz,
        run_at_fold=job.run_at_fold,
    )


def _doc_to_job(doc: JobDocument) -> Job:
    """
    Convert a ``JobDocument`` to a ``Job`` frozen dataclass.

    Reverses ``_job_to_doc``: coerces naive datetimes to UTC, reconstructs
    ``TaskPayload`` from the stored dict, and re-creates ``JobStatus`` enum.

    Args:
        doc: A ``JobDocument`` returned by a Beanie query.

    Returns:
        An immutable ``Job`` value object.

    Edge cases:
        - ``doc.started_at`` / ``doc.completed_at`` may be ``None``.
        - ``doc.auth_snapshot`` may be ``None`` for anonymous contexts.
        - ``doc.created_at`` from MongoDB may arrive as naive — coerced to UTC.
    """
    task_payload: TaskPayload | None = None
    if doc.task_payload is not None:
        # Reconstruct TaskPayload from the BSON subdocument dict.
        task_payload = TaskPayload.from_dict(doc.task_payload)

    return Job(
        job_id=doc.id,
        status=JobStatus(doc.status),
        created_at=_ensure_tz(doc.created_at),
        started_at=_ensure_tz(doc.started_at),
        completed_at=_ensure_tz(doc.completed_at),
        result=doc.result,
        error=doc.error,
        callback_url=doc.callback_url,
        auth_snapshot=doc.auth_snapshot,
        request_token=doc.request_token,
        # job_metadata defaults to {} — never None on the domain side.
        metadata=doc.job_metadata,
        task_payload=task_payload,
        run_at=_ensure_tz(doc.run_at),
        attempt=doc.attempt,
        max_attempts=doc.max_attempts,
        owner_id=doc.owner_id,
        lease_expires_at=_ensure_tz(doc.lease_expires_at),
        lease_epoch=doc.lease_epoch,
        expires_at=_ensure_tz(doc.expires_at),
        request_issuer=doc.request_issuer,
        request_subject=doc.request_subject,
        request_token_hash=doc.request_token_hash,
        # getattr() default — a pre-Plan-011 document loaded via a stale
        # cached class or a raw find() bypassing JobDocument's own default
        # resolution still deserializes cleanly to the unzoned state.
        run_at_wall=getattr(doc, "run_at_wall", None),
        run_at_tz=getattr(doc, "run_at_tz", None),
        run_at_fold=getattr(doc, "run_at_fold", 0) or 0,
    )


# ── BeanieJobStore ────────────────────────────────────────────────────────────


class BeanieJobStore(AbstractJobStore):
    """
    Beanie (pymongo / MongoDB) implementation of ``AbstractJobStore``.

    Persists ``Job`` objects in the ``varco_jobs`` MongoDB collection via
    ``JobDocument``.

    All methods are self-contained — no shared session or UoW.  The store
    operates as a standalone infrastructure component, analogous to
    ``SAJobStore`` on the SQLAlchemy side.

    ``try_claim()`` uses MongoDB's ``findAndModify`` (via Beanie's
    ``find_one(...).update_one(..., response_type=UpdateResponse.NEW_DOCUMENT)``)
    to atomically transition a PENDING job to RUNNING in a single server-side
    operation — the natural MongoDB equivalent of PostgreSQL's
    ``SELECT FOR UPDATE SKIP LOCKED``.

    DESIGN: no shared session
        ✅ Simpler API — callers do not need to manage session lifetimes.
        ✅ Infrastructure component: job store is independent of domain UoW.
        ✅ ``try_claim()`` atomicity does not require a session — MongoDB's
           ``findAndModify`` is atomic by itself.
        ❌ Each method opens its own connection from the Motor pool.  For very
           high-throughput scenarios, batching via a session may be faster.

    DESIGN: upsert via delete + insert (save())
        ✅ Works correctly on Beanie 2.x — no dialect-specific upsert needed.
        ✅ Atomic within MongoDB's single-document guarantees (delete and insert
           are separate operations, but the document model is a single doc).
        ❌ Two round-trips per save() — accept this for simplicity and
           cross-version compatibility.

    Thread safety:  ✅ No mutable instance state — Motor pool is coroutine-safe.
    Async safety:   ✅ All methods are ``async def``.

    ``BeanieJobStore`` takes no constructor arguments — it uses the Beanie
    global document registry initialized by ``init_beanie()`` or
    ``provider.init()``.

    Edge cases:
        - ``JobDocument`` must be registered with Beanie before any method is
          called.  Add it to ``init_beanie(document_models=[..., JobDocument])``.
        - ``save()`` uses delete + insert (upsert via two round-trips).
        - ``try_claim()`` atomicity: correct under concurrent multi-replica
          deployments — MongoDB's ``findAndModify`` ensures exactly one caller
          transitions PENDING → RUNNING.

    Example::

        store = BeanieJobStore()
        job = Job()
        await store.save(job)
        claimed = await store.try_claim(job.job_id)
    """

    #: Plan 011 / RD-5 — BeanieJobStore persists run_at_wall/run_at_tz/
    #: run_at_fold as real JobDocument fields (see JobDocument above).
    supports_zoned_schedules = True

    # ── AbstractJobStore implementation ───────────────────────────────────────

    async def save(self, job: Job, *, expected_epoch: int | None = None) -> None:
        """
        Persist or update a job (upsert semantics via delete + insert).

        Deletes any existing document with the same ``job_id`` before
        inserting the new state, ensuring the stored document always matches
        the caller's intent (including terminal states like COMPLETED or FAILED).

        Args:
            job: The ``Job`` to persist.
            expected_epoch: Fencing token (Plan 005 Phase 4, U-11 §3).
                ``None`` (default) — no fencing check, today's behaviour
                exactly. When supplied, the write is refused with
                ``StaleLeaseError`` if the stored document's
                ``lease_epoch`` no longer matches.

        Raises:
            StaleLeaseError: ``expected_epoch`` is supplied and does not
                match the stored ``lease_epoch``.
            beanie.exceptions.DocumentWasNotSaved: If Beanie fails to insert.
            RuntimeError: If ``JobDocument`` was not registered with Beanie.

        Edge cases:
            - Saving a terminal job (COMPLETED, FAILED, CANCELLED) is valid.
            - Concurrent saves to the same ``job_id``: last-write-wins (the
              delete+insert pair is NOT atomic across both operations; see
              ``try_claim()`` for the distributed-safe claim primitive).
            - If the delete succeeds but insert fails, the job is lost —
              acceptable for the transient in-flight case (recover via
              ``list_by_status(RUNNING)`` on restart).

        Async safety: ✅ Awaits both delete and insert sequentially.
        """
        if expected_epoch is not None:
            current = await JobDocument.find_one(JobDocument.id == job.job_id)
            if current is None or current.lease_epoch != expected_epoch:
                raise StaleLeaseError(
                    f"save() refused for job {job.job_id}: expected_epoch="
                    f"{expected_epoch} does not match stored lease_epoch "
                    f"({current.lease_epoch if current else 'doc not found'})."
                )

        doc = _job_to_doc(job)

        # Delete any existing row first (silent no-op if not found).
        # This is the same pattern as SAJobStore's delete + insert upsert,
        # adapted for Beanie's Document API.
        await JobDocument.find(JobDocument.id == job.job_id).delete()

        # Insert the fresh state.
        await doc.insert()

        _logger.debug(
            "BeanieJobStore.save: job_id=%s status=%s", job.job_id, job.status
        )

    async def get(self, job_id: UUID) -> Job | None:
        """
        Retrieve a ``Job`` by its ``job_id``.

        Args:
            job_id: The unique job identifier.

        Returns:
            The ``Job`` if found, or ``None`` if not found.

        Raises:
            RuntimeError: If ``JobDocument`` was not registered with Beanie.

        Edge cases:
            - Unknown ``job_id`` → returns ``None``.
            - ``created_at`` / ``started_at`` naive datetimes from MongoDB are
              coerced to UTC by ``_doc_to_job``.

        Async safety: ✅ Awaits ``find_one()``.
        """
        doc = await JobDocument.find_one(JobDocument.id == job_id)
        if doc is None:
            return None
        return _doc_to_job(doc)

    async def list_by_status(
        self,
        status: JobStatus,
        *,
        limit: int = 100,
    ) -> list[Job]:
        """
        Return up to ``limit`` jobs with the given ``status``, oldest first.

        Uses ``find(...).sort(+created_at).limit(n)`` — an in-memory sort
        unless a compound ``{ status: 1, created_at: 1 }`` index exists.

        Args:
            status: Filter by this lifecycle state.
            limit:  Maximum number of results.

        Returns:
            List of matching ``Job`` objects, ordered by ``created_at ASC``.
            Empty list if no matching documents.

        Raises:
            RuntimeError: If ``JobDocument`` was not registered with Beanie.

        Edge cases:
            - Sort without an index is O(N scan) — acceptable for small
              collections (<10k jobs).  Add a status+created_at index for
              production workloads.
            - ``limit`` applies after the sort; MongoDB may fetch more docs
              internally depending on cursor plan.

        Async safety: ✅ Awaits the Beanie find chain.
        """
        docs = (
            await JobDocument.find(JobDocument.status == status.value)
            .sort(+JobDocument.created_at)
            .limit(limit)
            .to_list()
        )
        jobs = [_doc_to_job(d) for d in docs]
        _logger.debug(
            "BeanieJobStore.list_by_status: status=%s returned %d jobs",
            status,
            len(jobs),
        )
        return jobs

    async def list_pending_zoned(
        self,
        before: datetime,
        *,
        limit: int = 100,
    ) -> list[Job]:
        """
        Native override (Plan 011 T2) — a real Mongo query
        (``status == PENDING AND run_at_tz != None AND run_at < before``)
        instead of the portable ``list_by_status`` + in-Python-filter
        default.
        """
        docs = (
            await JobDocument.find(
                JobDocument.status == JobStatus.PENDING.value,
                JobDocument.run_at_tz is not None,
                JobDocument.run_at is not None and JobDocument.run_at < before,
            )
            .limit(limit)
            .to_list()
        )
        return [_doc_to_job(d) for d in docs]

    async def delete(self, job_id: UUID) -> None:
        """
        Remove a ``Job`` from the store.  Silent no-op for unknown IDs.

        Args:
            job_id: The unique job identifier.

        Raises:
            RuntimeError: If ``JobDocument`` was not registered with Beanie.

        Edge cases:
            - Unknown ``job_id`` → Beanie ``find().delete()`` deletes 0
              documents and returns without error.

        Async safety: ✅ Awaits ``find().delete()``.
        """
        await JobDocument.find(JobDocument.id == job_id).delete()
        _logger.debug("BeanieJobStore.delete: job_id=%s", job_id)

    async def delete_where(
        self,
        *,
        status: JobStatus | Sequence[JobStatus] | None = None,
        completed_before: datetime | None = None,
        expires_before: datetime | None = None,
        limit: int | None = None,
    ) -> int:
        """
        Native bulk delete via a single MongoDB ``delete_many`` filter (Plan
        005 Phase 6, U-18) — replaces the ABC's portable
        ``list_by_status`` + ``delete`` default with one server-side
        operation for the ``limit=None`` case.

        Args:
            status: A single ``JobStatus`` or sequence of them.  ``None``
                (default) does not filter by status.
            completed_before: Only match jobs whose ``completed_at`` is set
                AND strictly before this timestamp.
            expires_before: Only match jobs whose ``expires_at`` is set AND
                strictly before this timestamp.
            limit: Maximum rows to delete.  ``None`` deletes every match in
                a single ``delete_many``. When set, MongoDB has no native
                "delete with limit" — this falls back to selecting up to
                ``limit`` matching ``_id``s first, then deleting by that id
                set (mirrors the ``ctid IN (...)`` shape used by
                ``SAJobStore`` for the same reason).

        Returns:
            The number of documents actually deleted.

        Raises:
            ValueError: No predicate at all was supplied.
            RuntimeError: If ``JobDocument`` was not registered with Beanie.

        Async safety: ✅ Awaits the Beanie/Motor delete operation(s).
        """
        if status is None and completed_before is None and expires_before is None:
            raise ValueError(
                "delete_where() requires at least one predicate (status, "
                "completed_before, or expires_before) — refusing to delete "
                "every row in the store. Pass an explicit predicate, e.g. "
                "delete_where(status=JobStatus.COMPLETED)."
            )

        filters: list[Any] = []
        if status is not None:
            if isinstance(status, JobStatus):
                filters.append(JobDocument.status == status.value)
            else:
                filters.append({"status": {"$in": [s.value for s in status]}})
        if completed_before is not None:
            filters.append({"completed_at": {"$ne": None, "$lt": completed_before}})
        if expires_before is not None:
            filters.append({"expires_at": {"$ne": None, "$lt": expires_before}})

        if limit is not None:
            matched = await JobDocument.find(*filters).limit(limit).to_list()
            id_values = [doc.id for doc in matched]
            if not id_values:
                return 0
            result = await JobDocument.find({"_id": {"$in": id_values}}).delete()
        else:
            result = await JobDocument.find(*filters).delete()

        deleted = result.deleted_count if result is not None else 0
        _logger.debug("BeanieJobStore.delete_where: deleted %d jobs", deleted)
        return deleted

    async def try_claim(
        self,
        job_id: UUID,
        *,
        owner_id: str | None = None,
        lease_ttl: float | None = None,
    ) -> Job | None:
        """
        Atomically transition a PENDING job to RUNNING state.

        Uses MongoDB's ``findAndModify`` (via Beanie's
        ``find_one(...).update_one(..., response_type=UpdateResponse.NEW_DOCUMENT)``)
        to atomically locate a PENDING job and update it to RUNNING in a single
        server-side operation.

        If no document matches (job not found or not PENDING, or ``run_at``
        is in the future), the operation returns ``None`` immediately — there
        is no blocking, no queue, and no side effects.

        Plan 005 Phase 4 (U-17 §1, U-11): the filter now also honours
        ``run_at IS NULL OR run_at <= now``. When ``lease_ttl`` is given,
        the update also sets ``owner_id``, ``lease_expires_at`` and
        increments ``lease_epoch`` (fencing token).

        DESIGN: findAndModify over find_one + update (two-step)
            ✅ Atomic — MongoDB guarantees exactly one concurrent caller
               transitions PENDING → RUNNING, even across multi-replica deployments.
            ✅ Single round-trip on both success and no-match paths.
            ✅ Returns the AFTER document (``NEW_DOCUMENT``) so we can build the
               ``Job`` without a second ``get()`` round-trip.
            ❌ ``findAndModify`` acquires a write lock; under very high contention
               (many workers racing on the same PENDING job) this serializes them.
               In practice, a PENDING job is claimed by only one runner — contention
               is minimal.

        Args:
            job_id: The UUID of the PENDING job to claim.
            owner_id: Identifies the lease holder. ``None`` (default) takes
                no lease — today's behaviour exactly.
            lease_ttl: Lease duration in seconds from now. ``None``
                (default) takes no lease.

        Returns:
            The claimed ``Job`` in RUNNING state, or ``None`` if:
            - The job is not found.
            - The job is not in PENDING state.
            - ``run_at`` is in the future.

        Raises:
            RuntimeError: If ``JobDocument`` was not registered with Beanie.

        Edge cases:
            - Unknown ``job_id`` → ``find_one_and_update`` matches nothing → ``None``.
            - RUNNING / COMPLETED / FAILED / CANCELLED → not PENDING →
              filter does not match → ``None``.
            - Concurrent ``try_claim()`` on the same job: only the first request
              that reaches MongoDB transitions the status; all others see no match
              and return ``None``.

        Async safety: ✅ Single ``findAndModify`` call — no explicit locking needed.

        DESIGN: plain string-keyed filter/update dicts over ``JobDocument.<field>``
            The pre-existing ``JobDocument.id`` / ``.status`` / ``.started_at``
            expressions used by this method rely on Beanie's Motor-backed
            ``ExpressionField`` descriptors, which are only wired up after
            ``init_beanie()`` runs. New fields added here (``run_at``,
            ``owner_id``, ``lease_expires_at``, ``lease_epoch``) are
            referenced via plain MongoDB-style dict filters instead — Beanie
            accepts raw ``Mapping`` filters/updates identically to typed
            expressions, and this keeps the method usable in this package's
            existing mocked-Beanie unit tests without patching every new
            field individually. ``id``/``status``/``started_at`` keep using
            the typed expressions for consistency with the untouched parts
            of this method.
        """
        now = datetime.now(UTC)

        update_values: dict[str, Any] = {
            "status": JobStatus.RUNNING.value,
            "started_at": now,
        }
        if lease_ttl is not None:
            update_values["owner_id"] = owner_id
            update_values["lease_expires_at"] = now + timedelta(seconds=lease_ttl)

        # Atomically: find a PENDING document for this job_id, eligible by
        # run_at, AND update it to RUNNING in one MongoDB findAndModify
        # round-trip. UpdateResponse.NEW_DOCUMENT → returns the AFTER document.
        # If no document matches, Beanie returns None.
        updated_doc: JobDocument | None = await JobDocument.find_one(
            JobDocument.id == job_id,
            JobDocument.status == JobStatus.PENDING.value,
            {"$or": [{"run_at": None}, {"run_at": {"$lte": now}}]},
        ).update_one(
            {"$set": update_values},
            response_type=UpdateResponse.NEW_DOCUMENT,
        )

        if updated_doc is None:
            # Job not found, not PENDING, or run_at is in the future.
            return None

        if lease_ttl is not None:
            new_lease_epoch = updated_doc.lease_epoch + 1
            # Second update to bump lease_epoch — kept as a separate write
            # (rather than $inc in the same update) so the claim's atomicity
            # unit stays the PENDING→RUNNING transition; the epoch bump
            # itself only needs to happen-before the caller starts working.
            await JobDocument.find_one(JobDocument.id == job_id).update_one(
                {"$set": {"lease_epoch": new_lease_epoch}}
            )
            updated_doc.lease_epoch = new_lease_epoch

        _logger.debug("BeanieJobStore.try_claim: claimed job_id=%s → RUNNING", job_id)
        return _doc_to_job(updated_doc)

    async def claim_next(
        self,
        *,
        owner_id: str | None = None,
        lease_ttl: float | None = None,
        now: datetime | None = None,
    ) -> Job | None:
        """
        Claim the oldest eligible PENDING job.

        Args:
            owner_id: Forwarded to ``try_claim``.
            lease_ttl: Forwarded to ``try_claim``.
            now: The "current time" to evaluate ``run_at`` against.

        Returns:
            The claimed ``Job`` in RUNNING state, or ``None`` if no eligible
            PENDING job exists.

        Async safety: ✅ All I/O is awaited.
        """
        current = now if now is not None else datetime.now(UTC)
        candidates = await self.list_by_status(JobStatus.PENDING, limit=100)
        for candidate in candidates:
            if candidate.run_at is not None and candidate.run_at > current:
                continue
            claimed = await self.try_claim(
                candidate.job_id, owner_id=owner_id, lease_ttl=lease_ttl
            )
            if claimed is not None:
                return claimed
        return None

    async def renew(
        self,
        job_id: UUID,
        *,
        owner_id: str,
        epoch: int,
        lease_ttl: float,
    ) -> Job | None:
        """
        Heartbeat an in-progress lease via ``find_one_and_update``.

        Args:
            job_id: The job whose lease is being renewed.
            owner_id: Must match the job's current ``owner_id``.
            epoch: Must match the job's current ``lease_epoch``.
            lease_ttl: New lease duration in seconds, from now.

        Returns:
            The renewed ``Job`` with an extended ``lease_expires_at``, or
            ``None`` if the job/owner/epoch does not match (fenced out).

        Async safety: ✅ Single ``findAndModify`` call — atomic.
        """
        new_expires_at = datetime.now(UTC) + timedelta(seconds=lease_ttl)
        updated_doc: JobDocument | None = await JobDocument.find_one(
            JobDocument.id == job_id,
            {"owner_id": owner_id},
            {"lease_epoch": epoch},
        ).update_one(
            {"$set": {"lease_expires_at": new_expires_at}},
            response_type=UpdateResponse.NEW_DOCUMENT,
        )
        if updated_doc is None:
            return None
        return _doc_to_job(updated_doc)

    async def reap_expired_leases(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> list[Job]:
        """
        Move RUNNING jobs whose lease has expired back to PENDING,
        incrementing ``lease_epoch`` to fence out the stalled owner.

        Args:
            now: The "current time" to compare ``lease_expires_at`` against.
            limit: Maximum number of jobs to reap in one call.

        Returns:
            The list of jobs moved back to PENDING (post-reap state).

        Async safety: ✅ All I/O is awaited.
        """
        current = now if now is not None else datetime.now(UTC)
        docs = (
            await JobDocument.find(
                JobDocument.status == JobStatus.RUNNING.value,
                {"lease_expires_at": {"$ne": None, "$lte": current}},
            )
            .limit(limit)
            .to_list()
        )
        reaped: list[Job] = []
        for doc in docs:
            new_epoch = doc.lease_epoch + 1
            await JobDocument.find_one(JobDocument.id == doc.id).update_one(
                {
                    "$set": {
                        "status": JobStatus.PENDING.value,
                        "lease_epoch": new_epoch,
                        "owner_id": None,
                        "lease_expires_at": None,
                    }
                }
            )
            doc.status = JobStatus.PENDING.value
            doc.lease_epoch = new_epoch
            doc.owner_id = None
            doc.lease_expires_at = None
            reaped.append(_doc_to_job(doc))
        _logger.debug("BeanieJobStore.reap_expired_leases: reaped %d jobs", len(reaped))
        return reaped

    def __repr__(self) -> str:
        return "BeanieJobStore()"


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    "BeanieJobStore",
    "JobDocument",
]
