"""
varco_redis.job_store
=====================
Redis-backed implementation of ``AbstractJobStore``.

Persistence model
-----------------
Each ``Job`` is stored as a JSON string at the key::

    {key_prefix}{job_id}

Example: ``varco:job:550e8400-e29b-41d4-a716-446655440000``

Status index
------------
To enable efficient ``list_by_status()``, a Redis Sorted Set is maintained
per status::

    {key_prefix}status:{status}   →  SortedSet { job_id: created_at_timestamp }

Entries are added/removed on every ``save()`` call.  This makes
``list_by_status()`` O(log N + K) (ZRANGEBYSCORE) instead of O(N) (SCAN).

Claim key (for try_claim atomicity)
-------------------------------------
``try_claim()`` uses a claim guard key::

    {key_prefix}claim:{job_id}   →  "1"  (SET NX EX {claim_ttl_seconds})

Only the first caller that successfully sets the claim key proceeds to
transition the job to RUNNING.  All concurrent callers get ``None`` (NX
fails for them).  The EX TTL ensures the claim key expires automatically if
the runner crashes between claiming and updating the job JSON.

DESIGN: JSON string per job over Redis Hash
    ✅ Single GET / SET per read/write — one round-trip.
    ✅ No schema evolution issues — JSON is self-describing.
    ✅ Simple to inspect in redis-cli (GETDEL / GET).
    ❌ Updates replace the entire value — no partial field patching.
       For background jobs (infrequent saves, small payloads) this is fine.

DESIGN: Sorted Set index per status
    ✅ list_by_status() is O(log N + K) — no full SCAN needed.
    ✅ Oldest-first ordering via created_at timestamp as ZSET score.
    ❌ Index and job value can diverge if a write crashes between the two ops.
       This is a known tradeoff — eventual consistency is acceptable for a
       job store (the poller re-examines jobs on the next tick).

Thread safety:  ✅ redis.asyncio.Redis is coroutine-safe across tasks.
Async safety:   ✅ All methods are ``async def``.

📚 Docs
- 🐍 https://redis-py.readthedocs.io/en/stable/commands.html
  redis-py async command reference
- 📐 https://redis.io/docs/latest/commands/set/ (NX, EX options)
  SET NX EX — atomic conditional set with TTL
- 📐 https://redis.io/docs/latest/commands/zadd/
  ZADD — sorted set upsert
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime, timedelta, timezone, UTC
from typing import TYPE_CHECKING, Any
from collections.abc import Sequence
from uuid import UUID

from redis.exceptions import WatchError
from varco_core.job.base import AbstractJobStore, Job, JobStatus, StaleLeaseError
from varco_core.job.task import TaskPayload

if TYPE_CHECKING:
    import redis.asyncio as aioredis

_logger = logging.getLogger(__name__)

# Default TTL in seconds for the claim guard key.
# A runner that crashes between SET NX and the RUNNING update releases the
# claim after this many seconds — preventing permanent lock-out.
_DEFAULT_CLAIM_TTL: int = 30


# ── Serialization helpers ─────────────────────────────────────────────────────


def _dt_to_str(dt: datetime | None) -> str | None:
    """Serialize datetime to ISO-8601 string."""
    return dt.isoformat() if dt is not None else None


def _str_to_dt(s: str | None) -> datetime | None:
    """Deserialize ISO-8601 string to a timezone-aware UTC datetime."""
    if s is None:
        return None
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _job_to_json(job: Job) -> str:
    """Serialize a ``Job`` to a JSON string for Redis storage."""
    return json.dumps(
        {
            "job_id": str(job.job_id),
            "status": job.status.value,
            "created_at": _dt_to_str(job.created_at),
            "started_at": _dt_to_str(job.started_at),
            "completed_at": _dt_to_str(job.completed_at),
            # bytes → hex string; None → None
            "result": job.result.hex() if job.result is not None else None,
            "error": job.error,
            "callback_url": job.callback_url,
            "auth_snapshot": job.auth_snapshot,
            "request_token": job.request_token,
            "metadata": job.metadata,
            "task_payload": (
                job.task_payload.to_dict() if job.task_payload is not None else None
            ),
            "run_at": _dt_to_str(job.run_at),
            "attempt": job.attempt,
            "max_attempts": job.max_attempts,
            "owner_id": job.owner_id,
            "lease_expires_at": _dt_to_str(job.lease_expires_at),
            "lease_epoch": job.lease_epoch,
            "expires_at": _dt_to_str(job.expires_at),
            "request_issuer": job.request_issuer,
            "request_subject": job.request_subject,
            "request_token_hash": job.request_token_hash,
        }
    )


def _json_to_job(raw: str | bytes) -> Job:
    """Deserialize a JSON string from Redis storage back to a ``Job``."""
    data: dict[str, Any] = json.loads(raw)

    task_payload: TaskPayload | None = None
    if data.get("task_payload") is not None:
        task_payload = TaskPayload.from_dict(data["task_payload"])

    result: bytes | None = None
    if data.get("result") is not None:
        result = bytes.fromhex(data["result"])

    return Job(
        job_id=UUID(data["job_id"]),
        status=JobStatus(data["status"]),
        created_at=_str_to_dt(data["created_at"]) or datetime.now(UTC),
        started_at=_str_to_dt(data.get("started_at")),
        completed_at=_str_to_dt(data.get("completed_at")),
        result=result,
        error=data.get("error"),
        callback_url=data.get("callback_url"),
        auth_snapshot=data.get("auth_snapshot"),
        request_token=data.get("request_token"),
        metadata=data.get("metadata") or {},
        task_payload=task_payload,
        run_at=_str_to_dt(data.get("run_at")),
        attempt=data.get("attempt", 0),
        max_attempts=data.get("max_attempts", 1),
        owner_id=data.get("owner_id"),
        lease_expires_at=_str_to_dt(data.get("lease_expires_at")),
        lease_epoch=data.get("lease_epoch", 0),
        expires_at=_str_to_dt(data.get("expires_at")),
        request_issuer=data.get("request_issuer"),
        request_subject=data.get("request_subject"),
        request_token_hash=data.get("request_token_hash"),
    )


# ── RedisJobStore ─────────────────────────────────────────────────────────────


class RedisJobStore(AbstractJobStore):
    """
    Redis-backed implementation of ``AbstractJobStore``.

    Stores each job as a JSON string and maintains a Sorted Set index per
    status for efficient ``list_by_status()`` queries.  Uses ``SET NX EX``
    for atomic ``try_claim()`` without requiring Lua scripts or WATCH/MULTI.

    Thread safety:  ✅ ``redis.asyncio.Redis`` is coroutine-safe.
    Async safety:   ✅ All methods are ``async def``.

    Args:
        client:         An ``aioredis.Redis`` client instance.
        key_prefix:     Namespace prefix for all Redis keys.
                        Default: ``"varco:job:"``.
        claim_ttl:      TTL in seconds for the claim guard key.
                        Default: ``30``.  Shorter values increase the risk of
                        a slow runner losing its claim; longer values increase
                        the lock-out window on crash.

    Edge cases:
        - ``save()`` is not atomic across the JSON SET and the ZSET updates.
          A crash mid-write may leave the status index stale.  The correctness
          impact is minor — the job value is always authoritative; the index
          is a secondary view for ``list_by_status()``.
        - ``try_claim()`` is atomic via the NX claim key.  Two concurrent
          callers for the same ``job_id`` — only one succeeds; the other gets
          ``None``.  The EX TTL bounds the claim window on crashes.
        - ``delete()`` also removes the job from all status index keys.

    Example::

        import redis.asyncio as aioredis
        from varco_redis.job_store import RedisJobStore

        client = aioredis.from_url("redis://localhost:6379/0")
        store = RedisJobStore(client)

        job = Job()
        await store.save(job)
        claimed = await store.try_claim(job.job_id)
    """

    def __init__(
        self,
        client: aioredis.Redis,
        *,
        key_prefix: str = "varco:job:",
        claim_ttl: int = _DEFAULT_CLAIM_TTL,
    ) -> None:
        """
        Args:
            client:     Async Redis client — shared across all operations.
            key_prefix: Prefix for all Redis keys managed by this store.
            claim_ttl:  TTL in seconds for the claim guard key used in
                        ``try_claim()``.
        """
        self._client = client
        self._prefix = key_prefix
        self._claim_ttl = claim_ttl

    # ── Key helpers ───────────────────────────────────────────────────────────

    def _job_key(self, job_id: UUID) -> str:
        """Redis key for a single job's JSON value."""
        return f"{self._prefix}{job_id}"

    def _status_key(self, status: JobStatus) -> str:
        """Redis Sorted Set key for a given status index."""
        return f"{self._prefix}status:{status.value}"

    def _claim_key(self, job_id: UUID) -> str:
        """Redis key for the claim guard (SET NX EX)."""
        return f"{self._prefix}claim:{job_id}"

    # ── AbstractJobStore implementation ───────────────────────────────────────

    async def save(self, job: Job, *, expected_epoch: int | None = None) -> None:
        """
        Persist or update a job (upsert semantics).

        Writes the serialized job JSON and updates the status Sorted Set index.
        If the job's status changed, the old status key is also cleaned up.

        DESIGN: GET old status + SET new + ZADD/ZREM (3 commands, not atomic)
            ✅ No Lua script required — simple and debuggable.
            ✅ Correct under non-concurrent saves (the job runner guarantees
               that only one task holds a job at a time).
            ❌ A crash mid-write leaves the index stale — acceptable tradeoff.

        Args:
            job: The ``Job`` to persist.
            expected_epoch: Fencing token (Plan 005 Phase 4, U-11 §3).
                ``None`` (default) — no fencing check, the plain GET-then-SET
                path below, byte-identical to pre-fencing behaviour. When
                supplied, the epoch-check-then-write is done inside a
                ``WATCH``/``MULTI``/``EXEC`` transaction on the job key so a
                stalled worker that resumes after being reaped cannot race a
                concurrent claim/renew between the check and the write — the
                same atomicity requirement ``try_claim()``'s docstring
                documents for Redis (WATCH + MULTI).

        Raises:
            StaleLeaseError: ``expected_epoch`` is supplied and does not
                match the stored ``lease_epoch`` (or the row does not
                exist), including when a concurrent writer touched the job
                key between the check and the write (``WatchError``).

        Async safety: ✅ The unfenced path's awaits are independent — no
            shared lock needed. The fenced path uses a client-side
            transaction (``WATCH``) — safe to call concurrently; only one
            of two racing fenced saves for the same key can win.
        """
        job_key = self._job_key(job.job_id)

        if expected_epoch is not None:
            async with self._client.pipeline(transaction=True) as pipe:
                await pipe.watch(job_key)
                existing_raw = await pipe.get(job_key)
                current_job = (
                    _json_to_job(existing_raw) if existing_raw is not None else None
                )
                if current_job is None or current_job.lease_epoch != expected_epoch:
                    await pipe.reset()
                    raise StaleLeaseError(
                        f"save() refused for job {job.job_id}: expected_epoch="
                        f"{expected_epoch} does not match stored lease_epoch "
                        f"({current_job.lease_epoch if current_job is not None else 'row not found'})."
                    )
                old_status = current_job.status
                pipe.multi()
                pipe.set(job_key, _job_to_json(job))
                if old_status != job.status:
                    pipe.zrem(self._status_key(old_status), str(job.job_id))
                score = job.created_at.timestamp()
                pipe.zadd(self._status_key(job.status), {str(job.job_id): score})
                try:
                    await pipe.execute()
                except WatchError as exc:
                    # Another writer touched the job key between our epoch
                    # check and the write — this caller no longer has a
                    # guarantee its epoch is still current. Fail closed,
                    # exactly like a directly-detected stale epoch.
                    raise StaleLeaseError(
                        f"save() refused for job {job.job_id}: expected_epoch="
                        f"{expected_epoch} — a concurrent write raced this "
                        "fenced save (WATCH detected a change)."
                    ) from exc

            _logger.debug(
                "RedisJobStore.save: job_id=%s status=%s expected_epoch=%s",
                job.job_id,
                job.status,
                expected_epoch,
            )
            return

        # Read the existing value to detect status changes (for index cleanup).
        existing_raw = await self._client.get(job_key)
        old_status: JobStatus | None = None
        if existing_raw is not None:
            try:
                old_job = _json_to_job(existing_raw)
                old_status = old_job.status
            except Exception:
                pass  # Corrupted value — treat as non-existent

        # Write the updated job JSON.
        await self._client.set(job_key, _job_to_json(job))

        # Update status index: remove from old status set, add to new status set.
        if old_status is not None and old_status != job.status:
            await self._client.zrem(self._status_key(old_status), str(job.job_id))

        # ZADD uses created_at as the score for oldest-first ordering.
        score = job.created_at.timestamp()
        await self._client.zadd(self._status_key(job.status), {str(job.job_id): score})

        _logger.debug("RedisJobStore.save: job_id=%s status=%s", job.job_id, job.status)

    async def get(self, job_id: UUID) -> Job | None:
        """
        Retrieve a ``Job`` by its ``job_id``.

        Args:
            job_id: The unique job identifier.

        Returns:
            The ``Job`` if found, or ``None`` if not in Redis.

        Async safety: ✅ Single GET command.
        """
        raw = await self._client.get(self._job_key(job_id))
        if raw is None:
            return None
        try:
            return _json_to_job(raw)
        except Exception as exc:
            _logger.error(
                "RedisJobStore.get: failed to deserialize job_id=%s: %s", job_id, exc
            )
            return None

    async def list_by_status(
        self,
        status: JobStatus,
        *,
        limit: int = 100,
    ) -> list[Job]:
        """
        Return up to ``limit`` jobs matching ``status``, ordered oldest first.

        Uses the Sorted Set index (score = created_at timestamp) so the query
        is O(log N + K) — no full scan needed.

        Args:
            status: Filter by this lifecycle state.
            limit:  Maximum number of results.

        Returns:
            List of matching ``Job`` objects, ordered by ``created_at ASC``.

        Edge cases:
            - If the index is stale (crash mid-write), the job value is still
              returned if the JSON key exists.  Stale index entries pointing to
              non-existent keys return ``None`` from GET and are skipped.

        Async safety: ✅ ZRANGE + N GETs (pipeline for efficiency).
        """
        status_key = self._status_key(status)
        # ZRANGE with BYSCORE ascending returns oldest IDs first.
        job_id_strs: list[bytes] = await self._client.zrange(status_key, 0, limit - 1)
        if not job_id_strs:
            return []

        jobs: list[Job] = []
        for jid_bytes in job_id_strs:
            raw = await self._client.get(self._job_key(UUID(jid_bytes.decode())))
            if raw is None:
                # Stale index entry — skip.
                continue
            try:
                jobs.append(_json_to_job(raw))
            except Exception as exc:
                _logger.warning(
                    "RedisJobStore.list_by_status: failed to deserialize job %s: %s",
                    jid_bytes,
                    exc,
                )

        _logger.debug(
            "RedisJobStore.list_by_status: status=%s returned %d jobs",
            status,
            len(jobs),
        )
        return jobs

    async def delete(self, job_id: UUID) -> None:
        """
        Remove a ``Job`` from the store and all status indexes.

        Silent no-op if ``job_id`` is not in Redis.

        Args:
            job_id: The unique job identifier.

        Async safety: ✅ GET + DEL + ZREM across all status keys.
        """
        job_key = self._job_key(job_id)
        raw = await self._client.get(job_key)

        # Remove from whichever status index the job currently belongs to.
        if raw is not None:
            try:
                job = _json_to_job(raw)
                await self._client.zrem(self._status_key(job.status), str(job_id))
            except Exception:
                # Corrupted value — remove from all status indexes defensively.
                for s in JobStatus:
                    await self._client.zrem(self._status_key(s), str(job_id))

        await self._client.delete(job_key)
        _logger.debug("RedisJobStore.delete: job_id=%s", job_id)

    async def delete_where(
        self,
        *,
        status: JobStatus | Sequence[JobStatus] | None = None,
        completed_before: datetime | None = None,
        expires_before: datetime | None = None,
        limit: int | None = None,
    ) -> int:
        """
        Native bulk delete (Plan 005 Phase 6, U-18) — walks the per-status
        Sorted Set index(es) instead of the ABC's portable
        ``list_by_status`` + ``delete`` default, saving the extra
        deserialize-then-re-filter-by-status round trip the default does
        (status filtering here is index-native via which Sorted Set is
        scanned; only ``completed_before``/``expires_before`` require a GET
        + deserialize per candidate).

        Args:
            status: A single ``JobStatus`` or sequence of them.  ``None``
                (default) scans every status's Sorted Set.
            completed_before: Only match jobs whose ``completed_at`` is set
                AND strictly before this timestamp.
            expires_before: Only match jobs whose ``expires_at`` is set AND
                strictly before this timestamp.
            limit: Maximum rows to delete.  ``None`` deletes every match.

        Returns:
            The number of jobs actually deleted.

        Raises:
            ValueError: No predicate at all was supplied.

        Edge cases:
            - Same eventual-consistency caveat as the rest of this store:
              the Sorted Set index and the job JSON value can diverge on a
              crash mid-write; stale index entries are skipped (no job JSON
              found at that key).

        Async safety: ✅ All I/O is awaited; each job's ZREM+DELETE pair is
            independent — no cross-job atomicity needed for a delete sweep.
        """
        if status is None and completed_before is None and expires_before is None:
            raise ValueError(
                "delete_where() requires at least one predicate (status, "
                "completed_before, or expires_before) — refusing to delete "
                "every row in the store. Pass an explicit predicate, e.g. "
                "delete_where(status=JobStatus.COMPLETED)."
            )

        if status is None:
            statuses: tuple[JobStatus, ...] = tuple(JobStatus)
        elif isinstance(status, JobStatus):
            statuses = (status,)
        else:
            statuses = tuple(status)

        deleted = 0
        for st in statuses:
            status_key = self._status_key(st)
            # Fetch the whole index for this status — the Sorted Set already
            # narrows the candidate set to the right status, so there is no
            # need for the ABC default's generous per-status over-fetch.
            job_id_strs: list[bytes] = await self._client.zrange(status_key, 0, -1)
            for jid_bytes in job_id_strs:
                if limit is not None and deleted >= limit:
                    return deleted
                job_id = UUID(jid_bytes.decode())
                raw = await self._client.get(self._job_key(job_id))
                if raw is None:
                    # Stale index entry — clean it up and move on.
                    await self._client.zrem(status_key, str(job_id))
                    continue
                try:
                    job = _json_to_job(raw)
                except Exception:
                    continue
                if completed_before is not None and (
                    job.completed_at is None or job.completed_at >= completed_before
                ):
                    continue
                if expires_before is not None and (
                    job.expires_at is None or job.expires_at >= expires_before
                ):
                    continue
                await self.delete(job_id)
                deleted += 1
        return deleted

    async def try_claim(
        self,
        job_id: UUID,
        *,
        owner_id: str | None = None,
        lease_ttl: float | None = None,
    ) -> Job | None:
        """
        Atomically claim a PENDING job and transition it to RUNNING.

        Uses a claim guard key with ``SET NX EX`` to ensure that only one
        caller succeeds even under concurrent invocations.

        Steps:
        1. ``SET claim_key "1" NX EX {claim_ttl}`` — atomic claim acquisition.
        2. Read the job JSON (``GET job_key``).
        3. If the job is PENDING and ``run_at`` has passed, update it to
           RUNNING (and, when ``lease_ttl`` is given, set ``owner_id`` /
           ``lease_expires_at`` / incremented ``lease_epoch``) and save.
        4. Return the running ``Job``; release claim key on failure.

        Plan 005 Phase 4 (U-17 §1, U-11): the claim predicate now also
        honours ``run_at IS NULL OR run_at <= now``.

        DESIGN: claim key (SET NX EX) over WATCH/MULTI/EXEC
            ✅ Simpler than a Redis transaction — one extra key, one extra command.
            ✅ EX TTL auto-expires if the runner crashes after claiming but
               before updating the job — prevents indefinite lock-out.
            ✅ No Lua script required for single-process correctness — a real
               multi-replica deployment should extend this with a Lua claim
               script (see module docstring); the decisive N-concurrent-
               claimers test is `@pytest.mark.integration` against real Redis.
            ❌ The claim key and the job JSON are two separate keys — not atomically
               linked.  A crash after NX-SET but before job JSON update leaves the
               job PENDING until the claim TTL expires.  After expiry, another runner
               can re-claim.

        Args:
            job_id: The UUID of the PENDING job to claim.
            owner_id: Identifies the lease holder. ``None`` (default) takes
                no lease — today's behaviour exactly.
            lease_ttl: Lease duration in seconds from now. ``None``
                (default) takes no lease.

        Returns:
            The claimed ``Job`` in RUNNING state, or ``None`` if:
            - The job does not exist.
            - The job is not in PENDING state.
            - ``run_at`` is in the future.
            - The claim key was already held by another caller.

        Async safety: ✅ SET NX is atomic on the Redis server.
        """
        claim_key = self._claim_key(job_id)

        # Attempt to acquire the claim guard atomically.
        # SET NX returns True if set (acquired), None/False if already exists.
        acquired = await self._client.set(claim_key, "1", nx=True, ex=self._claim_ttl)
        if not acquired:
            # Another runner already holds the claim for this job.
            _logger.debug(
                "RedisJobStore.try_claim: claim key already held for job_id=%s",
                job_id,
            )
            return None

        try:
            raw = await self._client.get(self._job_key(job_id))
            if raw is None:
                # Job was deleted between claim and read — release and bail.
                return None

            job = _json_to_job(raw)
            if job.status != JobStatus.PENDING:
                # Already running or terminal — another path got here first.
                return None

            if job.run_at is not None and job.run_at > datetime.now(UTC):
                # Scheduled for the future — not yet eligible. Release the
                # claim key so another (later) attempt can succeed.
                await self._client.delete(claim_key)
                return None

            # Transition PENDING → RUNNING and persist.
            running_job = job.as_running()
            if lease_ttl is not None:
                running_job = dataclasses.replace(
                    running_job,
                    owner_id=owner_id,
                    lease_expires_at=datetime.now(UTC)
                    + timedelta(seconds=lease_ttl),
                    lease_epoch=job.lease_epoch + 1,
                )
            await self.save(running_job)

            _logger.debug(
                "RedisJobStore.try_claim: claimed job_id=%s → RUNNING", job_id
            )
            return running_job

        except Exception:
            # On any error, release the claim key so another runner can try.
            await self._client.delete(claim_key)
            raise

    async def claim_next(
        self,
        *,
        owner_id: str | None = None,
        lease_ttl: float | None = None,
        now: datetime | None = None,
    ) -> Job | None:
        """
        Claim the oldest eligible PENDING job.

        Uses the PENDING status index (Sorted Set, oldest-first) and delegates
        the actual claim to ``try_claim`` so lease-write logic lives in one
        place. For a store with many future-scheduled jobs interleaved with
        eligible ones, this may scan past several ineligible entries — see
        module docstring for the sorted-by-run_at index this could be
        upgraded to for very large backlogs.

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
        Heartbeat an in-progress lease.

        Args:
            job_id: The job whose lease is being renewed.
            owner_id: Must match the job's current ``owner_id``.
            epoch: Must match the job's current ``lease_epoch``.
            lease_ttl: New lease duration in seconds, from now.

        Returns:
            The renewed ``Job`` with an extended ``lease_expires_at``, or
            ``None`` if the job/owner/epoch does not match (fenced out).

        Async safety: ✅ GET + conditional SET — not atomic across the two
            (same tradeoff documented for ``save()``); acceptable because a
            heartbeat racing a reap is expected to occasionally lose.
        """
        raw = await self._client.get(self._job_key(job_id))
        if raw is None:
            return None
        job = _json_to_job(raw)
        if job.owner_id != owner_id or job.lease_epoch != epoch:
            return None

        renewed = dataclasses.replace(
            job,
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=lease_ttl),
        )
        await self.save(renewed)
        return renewed

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

        Async safety: ✅ All I/O is awaited; each reaped job's save is
            independent (no cross-job atomicity needed).
        """
        current = now if now is not None else datetime.now(UTC)
        running = await self.list_by_status(JobStatus.RUNNING, limit=limit)
        reaped: list[Job] = []
        for job in running:
            if job.lease_expires_at is None or job.lease_expires_at > current:
                continue
            new_job = dataclasses.replace(
                job,
                status=JobStatus.PENDING,
                lease_epoch=job.lease_epoch + 1,
                owner_id=None,
                lease_expires_at=None,
            )
            await self.save(new_job)
            reaped.append(new_job)
        _logger.debug("RedisJobStore.reap_expired_leases: reaped %d jobs", len(reaped))
        return reaped

    def __repr__(self) -> str:
        return f"RedisJobStore(prefix={self._prefix!r}, claim_ttl={self._claim_ttl})"


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    "RedisJobStore",
]
