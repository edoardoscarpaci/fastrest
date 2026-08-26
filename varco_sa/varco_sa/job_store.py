"""
varco_sa.job_store
==================
SQLAlchemy async implementation of ``AbstractJobStore``.

Uses raw SQLAlchemy Core (not ORM / SAModelFactory) so it has no circular
dependency on the application's ``DeclarativeBase`` or ``DomainModel``.

Table
-----
``varco_jobs`` — created idempotently via ``ensure_table()`` or Alembic::

    from varco_sa.job_store import jobs_metadata
    target_metadata = [Base.metadata, jobs_metadata]

Usage::

    from sqlalchemy.ext.asyncio import create_async_engine
    from varco_sa.job_store import SAJobStore

    engine = create_async_engine("postgresql+asyncpg://...")
    store = SAJobStore(engine)
    await store.ensure_table()

    job = Job()
    await store.save(job)
    running = await store.try_claim(job.job_id)

DESIGN: raw Core over ORM / SAModelFactory
    ✅ No dependency on application ``DeclarativeBase`` — infrastructure table.
    ✅ ``ensure_table()`` for zero-migration startup convenience.
    ✅ Works with any async SQLAlchemy engine (PostgreSQL, SQLite for tests).
    ❌ No Alembic auto-detection unless ``jobs_metadata`` is added to target.

DESIGN: try_claim() uses a single transaction (no SKIP LOCKED in this impl)
    ✅ Works on SQLite (unit tests) and PostgreSQL alike.
    ✅ Correct for single-process runners — only one runner claims each job.
    ❌ Not distributed-safe under concurrent multi-replica restarts.
       Feature 12 (SAJobStore try_claim hardening) adds SELECT FOR UPDATE
       SKIP LOCKED for true distributed safety on PostgreSQL.

Thread safety:  ✅ AsyncEngine connection pool is coroutine-safe.
Async safety:   ✅ All methods are ``async def``.

📚 Docs
- 🐍 https://docs.sqlalchemy.org/en/20/core/connections.html#asyncio-support
  SQLAlchemy async Core — connection and transaction patterns
- 📐 https://use-the-index-luke.com/sql/select-for-update/postgresql-skip-locked
  PostgreSQL SELECT FOR UPDATE SKIP LOCKED — distributed job-queue pattern
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone, UTC
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.ext.asyncio import AsyncEngine
from varco_core.job.base import AbstractJobStore, Job, JobStatus, StaleLeaseError
from varco_core.job.task import TaskPayload

from varco_sa.metadata import register_framework_metadata as _register_fw_metadata

_logger = logging.getLogger(__name__)

# ── Table schema ──────────────────────────────────────────────────────────────

# Separate MetaData so varco_jobs never pollutes the application's Base.metadata.
# Users opt into Alembic management by including jobs_metadata in target_metadata.
_metadata = MetaData()

_jobs_table = Table(
    "varco_jobs",
    _metadata,
    # Primary key — matches Job.job_id (UUIDv4).
    Column("job_id", sa.Uuid, primary_key=True),
    # StrEnum value — PENDING / RUNNING / COMPLETED / FAILED / CANCELLED / DEAD.
    Column("status", String(32), nullable=False),
    # UTC timestamps for lifecycle tracking.
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    # Opaque bytes — caller decides serialization format.
    Column("result", LargeBinary, nullable=True),
    # Human-readable error message on FAILED — None otherwise.
    Column("error", Text, nullable=True),
    # Optional webhook URL for completion notification.
    Column("callback_url", Text, nullable=True),
    # JSON-serialized AuthContext snapshot — None for anonymous/unauthenticated.
    Column("auth_snapshot", Text, nullable=True),
    # Raw Bearer JWT — for audit trail and callback authentication.
    Column("request_token", Text, nullable=True),
    # JSON-serialized Job.metadata dict (extra data; not part of equality).
    Column("job_metadata", Text, nullable=True),
    # JSON-serialized TaskPayload.to_dict() — None for non-recoverable jobs.
    Column("task_payload", Text, nullable=True),
    # ── Plan 005 Phase 4 — time dimension, lease, fencing (U-17/U-11) ──────
    # All nullable or server-defaulted so existing rows are valid as-is
    # (this is the migration described in the Phase 4 revision docstring
    # below — see xxxx_job_lease_schedule_retention).
    Column("run_at", DateTime(timezone=True), nullable=True),
    Column("attempt", Integer, nullable=False, server_default="0"),
    Column("max_attempts", Integer, nullable=False, server_default="1"),
    Column("owner_id", String(255), nullable=True),
    Column("lease_expires_at", DateTime(timezone=True), nullable=True),
    Column("lease_epoch", Integer, nullable=False, server_default="0"),
    # ── Columns needed by Phase 6 (U-18/U-19) — added here so there is only
    # ONE job-table migration (plan 005 design note); API lands in Phase 6.
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("request_issuer", String(255), nullable=True),
    Column("request_subject", String(255), nullable=True),
    Column("request_token_hash", String(64), nullable=True),
    # ── Plan 011 (T2) — DST-safe scheduling, three additive columns ────────
    # D-7: run_at (above) is MATERIALIZED, not replaced — these three are
    # the *intent*. NO new index: the claim predicate is unchanged, so
    # ix_varco_jobs_claim above is still the right one.
    Column("run_at_wall", DateTime(timezone=False), nullable=True),
    Column("run_at_tz", String(64), nullable=True),
    Column("run_at_fold", Integer, nullable=False, server_default="0"),
    # ⚠️ Note there is currently no index at all on ``status`` — the three
    # indexes below are a free performance fix riding this migration.
    Index("ix_varco_jobs_claim", "status", "run_at", "created_at"),
    Index("ix_varco_jobs_lease", "status", "lease_expires_at"),
    Index("ix_varco_jobs_expires", "expires_at"),
)

# Expose metadata for Alembic integration — include in target_metadata.
jobs_metadata = _metadata


_register_fw_metadata("varco_sa.job_store", jobs_metadata)


# ── Serialization helpers ─────────────────────────────────────────────────────


def _dt_to_str(dt: datetime | None) -> str | None:
    """Serialize datetime to ISO-8601 string for JSON/Text column storage."""
    return dt.isoformat() if dt is not None else None


def _str_to_dt(s: str | None) -> datetime | None:
    """Deserialize an ISO-8601 string back to a timezone-aware UTC datetime."""
    if s is None:
        return None
    dt = datetime.fromisoformat(s)
    # Coerce naive datetimes from SQLite (no timezone stored) to UTC.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _job_to_row(job: Job) -> dict[str, Any]:
    """
    Convert a ``Job`` frozen dataclass to a flat dict of column values.

    All complex fields (auth_snapshot, metadata, task_payload) are
    JSON-serialized to Text.  ``result`` (bytes) is stored as-is in
    LargeBinary.  Datetimes are stored as timezone-aware ISO strings.

    Args:
        job: The ``Job`` instance to convert.

    Returns:
        Dict mapping column names to Python values ready for SQLAlchemy Core.
    """
    return {
        "job_id": job.job_id,
        "status": job.status.value,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "result": job.result,
        "error": job.error,
        "callback_url": job.callback_url,
        "auth_snapshot": (
            json.dumps(job.auth_snapshot) if job.auth_snapshot is not None else None
        ),
        "request_token": job.request_token,
        "job_metadata": json.dumps(job.metadata),
        "task_payload": (
            json.dumps(job.task_payload.to_dict())
            if job.task_payload is not None
            else None
        ),
        "run_at": job.run_at,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "owner_id": job.owner_id,
        "lease_expires_at": job.lease_expires_at,
        "lease_epoch": job.lease_epoch,
        "expires_at": job.expires_at,
        "request_issuer": job.request_issuer,
        "request_subject": job.request_subject,
        "request_token_hash": job.request_token_hash,
        "run_at_wall": job.run_at_wall,
        "run_at_tz": job.run_at_tz,
        "run_at_fold": job.run_at_fold,
    }


def _row_to_job(row: Any) -> Job:
    """
    Convert a SQLAlchemy Core row to a ``Job`` frozen dataclass.

    Reverses ``_job_to_row``: deserializes JSON fields, parses datetimes,
    and coerces the status string back to ``JobStatus``.

    Args:
        row: A row returned by ``conn.execute(select(_jobs_table))``.

    Returns:
        An immutable ``Job`` value object.

    Edge cases:
        - ``row.started_at`` / ``row.completed_at`` may be ``None`` (not yet started).
        - ``row.auth_snapshot`` may be ``None`` for anonymous contexts.
        - SQLite returns naive datetimes — coerced to UTC by ``_str_to_dt`` logic
          applied below via ``_ensure_tz()``.
    """

    def _ensure_tz(dt: datetime | None) -> datetime | None:
        """Coerce naive datetimes (SQLite) to UTC."""
        if dt is None:
            return None
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

    task_payload: TaskPayload | None = None
    if row.task_payload is not None:
        task_payload = TaskPayload.from_dict(json.loads(row.task_payload))

    auth_snapshot: dict[str, Any] | None = None
    if row.auth_snapshot is not None:
        auth_snapshot = json.loads(row.auth_snapshot)

    metadata: dict[str, Any] = {}
    if row.job_metadata is not None:
        metadata = json.loads(row.job_metadata)

    return Job(
        job_id=row.job_id,
        status=JobStatus(row.status),
        created_at=_ensure_tz(row.created_at),
        started_at=_ensure_tz(row.started_at),
        completed_at=_ensure_tz(row.completed_at),
        result=row.result,
        error=row.error,
        callback_url=row.callback_url,
        auth_snapshot=auth_snapshot,
        request_token=row.request_token,
        metadata=metadata,
        task_payload=task_payload,
        run_at=_ensure_tz(row.run_at),
        attempt=row.attempt,
        max_attempts=row.max_attempts,
        owner_id=row.owner_id,
        lease_expires_at=_ensure_tz(row.lease_expires_at),
        lease_epoch=row.lease_epoch,
        expires_at=_ensure_tz(row.expires_at),
        request_issuer=row.request_issuer,
        request_subject=row.request_subject,
        request_token_hash=row.request_token_hash,
        run_at_wall=row.run_at_wall,
        run_at_tz=row.run_at_tz,
        run_at_fold=row.run_at_fold if row.run_at_fold is not None else 0,
    )


# ── SAJobStore ────────────────────────────────────────────────────────────────


class SAJobStore(AbstractJobStore):
    """
    SQLAlchemy async implementation of ``AbstractJobStore``.

    Each method opens a fresh connection from the engine's pool and
    auto-commits.  There is no shared session — the job store is infrastructure
    (like ``OutboxRelay``) and does not participate in application UoW.

    DESIGN: AsyncEngine directly (not async_sessionmaker)
        ✅ Cleaner for infrastructure — no "session factory" wiring needed.
        ✅ ``ensure_table()`` only needs the engine, not a session.
        ✅ Consistent with ``SAEncryptionKeyStore`` pattern in this codebase.
        ❌ Slightly lower-level than ORM sessions — explicit SQL everywhere.

    Thread safety:  ✅ AsyncEngine pool is coroutine-safe; connections are per-call.
    Async safety:   ✅ All methods are ``async def``.

    Args:
        engine: Async SQLAlchemy engine for the target database.

    Edge cases:
        - Call ``await store.ensure_table()`` at startup, or add ``jobs_metadata``
          to your Alembic ``target_metadata``.
        - ``save()`` uses DELETE + INSERT (idempotent upsert) compatible with
          SQLite and PostgreSQL alike.
        - ``try_claim()`` atomicity: correct for single-process deployments.
          For distributed multi-replica deployments, apply Feature 12
          (SELECT FOR UPDATE SKIP LOCKED on PostgreSQL).

    Example::

        engine = create_async_engine("postgresql+asyncpg://...")
        store = SAJobStore(engine)
        await store.ensure_table()

        job = Job()
        await store.save(job)
        claimed = await store.try_claim(job.job_id)
    """

    #: Plan 011 / RD-5 — SAJobStore persists run_at_wall/run_at_tz/
    #: run_at_fold as real columns (see _jobs_table above).
    supports_zoned_schedules = True

    def __init__(self, engine: AsyncEngine) -> None:
        """
        Args:
            engine: Async SQLAlchemy engine — shared across all store operations.
        """
        self._engine = engine

    # ── Schema management ─────────────────────────────────────────────────────

    async def ensure_table(self) -> None:
        """
        Create the ``varco_jobs`` table if it does not already exist.

        Idempotent — safe to call at every startup.  For production deployments,
        prefer Alembic migrations (include ``jobs_metadata`` in target_metadata).

        Async safety: ✅ Uses ``engine.begin()`` — auto-commits the DDL.
        """
        async with self._engine.begin() as conn:
            await conn.run_sync(_metadata.create_all, checkfirst=True)
        _logger.debug("SAJobStore.ensure_table: varco_jobs table is ready.")

    # ── AbstractJobStore implementation ───────────────────────────────────────

    async def save(self, job: Job, *, expected_epoch: int | None = None) -> None:
        """
        Persist or update a job (upsert semantics).

        Implemented as DELETE + INSERT to be compatible with both SQLite
        (unit tests) and PostgreSQL (production).  Each call is its own
        committed transaction.

        Args:
            job: The ``Job`` to persist.
            expected_epoch: Fencing token (Plan 005 Phase 4, U-11 §3).
                ``None`` (default) — no fencing check, today's behaviour
                exactly. When supplied, the write is refused with
                ``StaleLeaseError`` if the stored row's ``lease_epoch`` no
                longer matches (or the row was deleted/never existed).

        Raises:
            StaleLeaseError: ``expected_epoch`` is supplied and does not
                match the stored ``lease_epoch``.

        Edge cases:
            - Saving a terminal job (COMPLETED, FAILED, CANCELLED) is valid.
            - Concurrent saves to the same ``job_id`` → last write wins
              (DELETE+INSERT within a transaction prevents partial writes),
              unless ``expected_epoch`` is supplied.

        Async safety: ✅ Uses ``engine.begin()`` — one atomic transaction.
        """
        row = _job_to_row(job)
        async with self._engine.begin() as conn:
            if expected_epoch is not None:
                current = await conn.execute(
                    sa.select(_jobs_table.c.lease_epoch).where(
                        _jobs_table.c.job_id == job.job_id
                    )
                )
                current_row = current.fetchone()
                if current_row is None or current_row.lease_epoch != expected_epoch:
                    raise StaleLeaseError(
                        f"save() refused for job {job.job_id}: expected_epoch="
                        f"{expected_epoch} does not match stored lease_epoch "
                        f"({current_row.lease_epoch if current_row else 'row not found'})."
                    )
            # DELETE existing row — silent no-op if not found.
            await conn.execute(
                sa.delete(_jobs_table).where(_jobs_table.c.job_id == job.job_id)
            )
            # INSERT fresh row — the DELETE ensures no IntegrityError.
            await conn.execute(sa.insert(_jobs_table).values(**row))
        _logger.debug("SAJobStore.save: job_id=%s status=%s", job.job_id, job.status)

    async def get(self, job_id: UUID) -> Job | None:
        """
        Retrieve a ``Job`` by its ``job_id``.

        Args:
            job_id: The unique job identifier.

        Returns:
            The ``Job`` if found, or ``None`` if not found.

        Async safety: ✅ Uses ``engine.connect()`` — read-only, no commit.
        """
        async with self._engine.connect() as conn:
            result = await conn.execute(
                sa.select(_jobs_table).where(_jobs_table.c.job_id == job_id)
            )
            row = result.fetchone()
        if row is None:
            return None
        return _row_to_job(row)

    async def list_by_status(
        self,
        status: JobStatus,
        *,
        limit: int = 100,
    ) -> list[Job]:
        """
        Return up to ``limit`` jobs with the given ``status``, oldest first.

        Args:
            status: Filter by this lifecycle state.
            limit:  Maximum number of results.

        Returns:
            List of matching ``Job`` objects, ordered by ``created_at ASC``.

        Async safety: ✅ Uses ``engine.connect()`` — read-only, no commit.
        """
        async with self._engine.connect() as conn:
            result = await conn.execute(
                sa.select(_jobs_table)
                .where(_jobs_table.c.status == status.value)
                .order_by(_jobs_table.c.created_at.asc())
                .limit(limit)
            )
            rows = result.fetchall()
        jobs = [_row_to_job(r) for r in rows]
        _logger.debug(
            "SAJobStore.list_by_status: status=%s returned %d jobs",
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
        Native override (Plan 011 T2) — real
        ``WHERE run_at_tz IS NOT NULL AND run_at < :before LIMIT :limit``
        instead of the portable ``list_by_status`` + in-Python-filter
        default. No new index — the claim predicate (and its index) is
        unchanged; this query rides the existing ``run_at`` column.
        """
        async with self._engine.connect() as conn:
            result = await conn.execute(
                sa.select(_jobs_table)
                .where(
                    _jobs_table.c.status == JobStatus.PENDING.value,
                    _jobs_table.c.run_at_tz.is_not(None),
                    _jobs_table.c.run_at < before,
                )
                .limit(limit)
            )
            rows = result.fetchall()
        return [_row_to_job(r) for r in rows]

    async def delete(self, job_id: UUID) -> None:
        """
        Remove a ``Job`` from the store.  Silent no-op for unknown IDs.

        Args:
            job_id: The unique job identifier.

        Async safety: ✅ Uses ``engine.begin()`` — single committed transaction.
        """
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.delete(_jobs_table).where(_jobs_table.c.job_id == job_id)
            )
        _logger.debug("SAJobStore.delete: job_id=%s", job_id)

    async def delete_where(
        self,
        *,
        status: JobStatus | Sequence[JobStatus] | None = None,
        completed_before: datetime | None = None,
        expires_before: datetime | None = None,
        limit: int | None = None,
    ) -> int:
        """
        Native single-statement ``DELETE`` (Plan 005 Phase 6, U-18) — replaces
        the ABC's portable ``list_by_status`` + ``delete`` default with one
        round-trip.

        On PostgreSQL, the ``limit`` form uses
        ``ctid IN (SELECT ctid FROM varco_jobs WHERE ... LIMIT n)`` — a
        correlated subquery is required because SQL ``DELETE`` has no native
        ``LIMIT`` clause on PostgreSQL (unlike MySQL). ``ctid`` (the physical
        row identifier) is index-friendly here given ``ix_varco_jobs_expires``
        / ``ix_varco_jobs_claim`` already cover ``status``/``expires_at``. On
        SQLite the ``rowid``-based equivalent is used instead, since SQLite
        has no ``ctid``.

        Args:
            status: A single ``JobStatus`` or sequence of them.  ``None``
                (default) does not filter by status.
            completed_before: Only match jobs whose ``completed_at`` is set
                AND strictly before this timestamp.
            expires_before: Only match jobs whose ``expires_at`` is set AND
                strictly before this timestamp.
            limit: Maximum rows to delete.  ``None`` deletes every match in
                one statement.

        Returns:
            The number of rows actually deleted.

        Raises:
            ValueError: No predicate at all was supplied.

        Async safety: ✅ Uses ``engine.begin()`` — one atomic transaction.
        """
        if status is None and completed_before is None and expires_before is None:
            raise ValueError(
                "delete_where() requires at least one predicate (status, "
                "completed_before, or expires_before) — refusing to delete "
                "every row in the store. Pass an explicit predicate, e.g. "
                "delete_where(status=JobStatus.COMPLETED)."
            )

        conditions = []
        if status is not None:
            if isinstance(status, JobStatus):
                conditions.append(_jobs_table.c.status == status.value)
            else:
                conditions.append(_jobs_table.c.status.in_([s.value for s in status]))
        if completed_before is not None:
            conditions.append(_jobs_table.c.completed_at.isnot(None))
            conditions.append(_jobs_table.c.completed_at < completed_before)
        if expires_before is not None:
            conditions.append(_jobs_table.c.expires_at.isnot(None))
            conditions.append(_jobs_table.c.expires_at < expires_before)

        async with self._engine.begin() as conn:
            if limit is not None:
                # No native DELETE ... LIMIT on PostgreSQL/SQLite — select the
                # target rows' physical identifier first, then delete by that
                # identifier set. ctid is index-friendly given the predicate
                # columns (status/expires_at) are already indexed.
                row_id_col = (
                    sa.literal_column("ctid")
                    if self._engine.dialect.name == "postgresql"
                    else sa.literal_column("rowid")
                )
                select_stmt = (
                    sa.select(row_id_col)
                    .select_from(_jobs_table)
                    .where(*conditions)
                    .limit(limit)
                )
                delete_stmt = sa.delete(_jobs_table).where(row_id_col.in_(select_stmt))
            else:
                delete_stmt = sa.delete(_jobs_table).where(*conditions)

            result = await conn.execute(delete_stmt)
            deleted = result.rowcount if result.rowcount is not None else 0

        _logger.debug("SAJobStore.delete_where: deleted %d jobs", deleted)
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

        On **PostgreSQL**, uses ``SELECT FOR UPDATE SKIP LOCKED`` so that
        concurrent workers never block each other: if the target row is
        already locked by another process, the select returns no rows and
        this method returns ``None`` immediately rather than waiting.

        On **SQLite** (and other dialects), falls back to a plain
        ``SELECT + UPDATE`` within a single transaction — sufficient for
        single-process deployments and unit tests.

        Plan 005 Phase 4 (U-17 §1, U-11): the claim predicate now also
        honours ``run_at IS NULL OR run_at <= now`` — a job scheduled for
        the future is not claimable even if PENDING. When ``lease_ttl`` is
        given, the UPDATE also sets ``owner_id``, ``lease_expires_at =
        now + lease_ttl`` and increments ``lease_epoch`` (fencing token).

        DESIGN: dialect-conditional SKIP LOCKED
            ✅ True distributed safety on PostgreSQL — no mutual blocking
               between concurrent runners claiming the same job.
            ✅ Backward-compatible — SQLite unit tests use the simple path.
            ✅ Both paths are fully atomic (``engine.begin()``).
            ❌ Dialect detection uses ``engine.dialect.name`` — callers
               using a non-PostgreSQL dialect that DOES support SKIP LOCKED
               (e.g. CockroachDB) won't get it automatically.  Override
               ``_use_skip_locked()`` or subclass if needed.

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
            - (PostgreSQL only) The row is already locked by a concurrent
                worker (SKIP LOCKED skips it).

        Async safety: ✅ Uses ``engine.begin()`` — fully atomic transaction.
        """
        use_skip_locked = self._engine.dialect.name == "postgresql"
        now = datetime.now(UTC)

        async with self._engine.begin() as conn:
            select_stmt = sa.select(_jobs_table).where(_jobs_table.c.job_id == job_id)
            if use_skip_locked:
                # PostgreSQL: acquire a row-level lock; skip if already locked.
                # Another runner holding this lock is currently claiming the job —
                # we return None immediately instead of waiting for its commit.
                select_stmt = select_stmt.with_for_update(skip_locked=True)

            result = await conn.execute(select_stmt)
            row = result.fetchone()

            if row is None or row.status != JobStatus.PENDING:
                # Job not found, already locked by another worker (SKIP LOCKED),
                # or already in a non-PENDING state — not claimable.
                return None

            if row.run_at is not None:
                row_run_at = row.run_at
                if row_run_at.tzinfo is None:
                    row_run_at = row_run_at.replace(tzinfo=UTC)
                if row_run_at > now:
                    # Scheduled for the future — not yet eligible.
                    return None

            update_values: dict[str, Any] = {
                "status": JobStatus.RUNNING,
                "started_at": now,
            }
            new_lease_epoch = row.lease_epoch
            if lease_ttl is not None:
                new_lease_epoch = row.lease_epoch + 1
                update_values["owner_id"] = owner_id
                update_values["lease_expires_at"] = now + timedelta(seconds=lease_ttl)
                update_values["lease_epoch"] = new_lease_epoch

            # Transition PENDING → RUNNING within the same transaction.
            await conn.execute(
                sa.update(_jobs_table)
                .where(_jobs_table.c.job_id == job_id)
                .values(**update_values)
            )
            # Re-build the Job with updated fields (frozen — can't mutate).
            job = _row_to_job(row)

        running_job = job.as_running()
        if lease_ttl is not None:
            running_job = dataclasses.replace(
                running_job,
                owner_id=owner_id,
                lease_expires_at=now + timedelta(seconds=lease_ttl),
                lease_epoch=new_lease_epoch,
            )
        _logger.debug(
            "SAJobStore.try_claim: claimed job_id=%s → RUNNING (skip_locked=%s)",
            job_id,
            use_skip_locked,
        )
        return running_job

    async def claim_next(
        self,
        *,
        owner_id: str | None = None,
        lease_ttl: float | None = None,
        now: datetime | None = None,
    ) -> Job | None:
        """
        Claim the oldest eligible PENDING job in a single round-trip.

        Plan 005 Phase 4 native implementation — selects the oldest PENDING
        row honouring ``run_at IS NULL OR run_at <= now`` (using
        ``SKIP LOCKED`` on PostgreSQL, the same as ``try_claim``) and
        delegates the actual claim to ``try_claim(job_id, ...)`` so the
        lease-write logic lives in exactly one place.

        Args:
            owner_id: Forwarded to ``try_claim``.
            lease_ttl: Forwarded to ``try_claim``.
            now: The "current time" to evaluate ``run_at`` against.
                Defaults to ``datetime.now(timezone.utc)``.

        Returns:
            The claimed ``Job`` in RUNNING state, or ``None`` if no eligible
            PENDING job exists.

        Async safety: ✅ All I/O is awaited.
        """
        use_skip_locked = self._engine.dialect.name == "postgresql"
        current = now if now is not None else datetime.now(UTC)

        async with self._engine.connect() as conn:
            select_stmt = (
                sa.select(_jobs_table.c.job_id)
                .where(_jobs_table.c.status == JobStatus.PENDING.value)
                .where(
                    sa.or_(
                        _jobs_table.c.run_at.is_(None),
                        _jobs_table.c.run_at <= current,
                    )
                )
                .order_by(_jobs_table.c.created_at.asc())
                .limit(1)
            )
            if use_skip_locked:
                select_stmt = select_stmt.with_for_update(skip_locked=True)
            result = await conn.execute(select_stmt)
            row = result.fetchone()

        if row is None:
            return None
        return await self.try_claim(row.job_id, owner_id=owner_id, lease_ttl=lease_ttl)

    async def renew(
        self,
        job_id: UUID,
        *,
        owner_id: str,
        epoch: int,
        lease_ttl: float,
    ) -> Job | None:
        """
        Heartbeat an in-progress lease as a single atomic UPDATE.

        Args:
            job_id: The job whose lease is being renewed.
            owner_id: Must match the job's current ``owner_id``.
            epoch: Must match the job's current ``lease_epoch``.
            lease_ttl: New lease duration in seconds, from now.

        Returns:
            The renewed ``Job`` with an extended ``lease_expires_at``, or
            ``None`` if the job/owner/epoch does not match (fenced out).

        Async safety: ✅ Uses ``engine.begin()`` — one atomic transaction.
        """
        now = datetime.now(UTC)
        new_expires_at = now + timedelta(seconds=lease_ttl)
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa.update(_jobs_table)
                .where(_jobs_table.c.job_id == job_id)
                .where(_jobs_table.c.owner_id == owner_id)
                .where(_jobs_table.c.lease_epoch == epoch)
                .values(lease_expires_at=new_expires_at)
            )
            if result.rowcount == 0:
                return None
            row_result = await conn.execute(
                sa.select(_jobs_table).where(_jobs_table.c.job_id == job_id)
            )
            row = row_result.fetchone()
        if row is None:
            return None
        return _row_to_job(row)

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
                Defaults to ``datetime.now(timezone.utc)``.
            limit: Maximum number of jobs to reap in one call.

        Returns:
            The list of jobs moved back to PENDING (post-reap state).

        Async safety: ✅ Uses ``engine.begin()`` — one atomic transaction.
        """
        current = now if now is not None else datetime.now(UTC)
        async with self._engine.begin() as conn:
            select_stmt = (
                sa.select(_jobs_table)
                .where(_jobs_table.c.status == JobStatus.RUNNING.value)
                .where(_jobs_table.c.lease_expires_at.isnot(None))
                .where(_jobs_table.c.lease_expires_at <= current)
                .limit(limit)
            )
            rows = (await conn.execute(select_stmt)).fetchall()

            reaped: list[Job] = []
            for row in rows:
                new_epoch = row.lease_epoch + 1
                await conn.execute(
                    sa.update(_jobs_table)
                    .where(_jobs_table.c.job_id == row.job_id)
                    .values(
                        status=JobStatus.PENDING.value,
                        lease_epoch=new_epoch,
                        owner_id=None,
                        lease_expires_at=None,
                    )
                )
                reaped.append(
                    dataclasses.replace(
                        _row_to_job(row),
                        status=JobStatus.PENDING,
                        lease_epoch=new_epoch,
                        owner_id=None,
                        lease_expires_at=None,
                    )
                )
        _logger.debug("SAJobStore.reap_expired_leases: reaped %d jobs", len(reaped))
        return reaped

    def __repr__(self) -> str:
        return f"SAJobStore(engine={self._engine!r})"


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    "SAJobStore",
    "jobs_metadata",
]
