"""
varco_core.job.base
===================
Background job domain types and abstract runner/store contracts.

Problem
-------
Some HTTP operations are too slow to complete within a request/response
cycle (bulk imports, PDF rendering, ML inference, etc.).  The async job
pattern provides a standard way to:

1. Accept a slow request immediately (202 Accepted + job_id).
2. Execute the work in a background asyncio task.
3. Poll for status / result at GET /jobs/{job_id}.
4. Optionally call back a webhook URL on completion.

Components
----------
``JobStatus``
    StrEnum of lifecycle states: PENDING → RUNNING → COMPLETED / FAILED / CANCELLED.

``Job``
    Frozen dataclass representing one background job.  All state transitions
    return a NEW ``Job`` instance via ``dataclasses.replace()``.

``AbstractJobStore``
    Persistence ABC for ``Job`` objects.  Implemented in varco_fastapi
    (InMemoryJobStore) or backend packages (Redis, SQL).

``AbstractJobRunner``
    Execution ABC that submits, tracks, and cancels asyncio coroutines.
    Implemented in varco_fastapi (JobRunner) which manages asyncio.Tasks.

``auth_context_to_snapshot`` / ``auth_context_from_snapshot``
    Serialization helpers for ``AuthContext``.  Background workers must
    execute with the same identity/grants as the originating HTTP request,
    but ``AuthContext`` contains ``frozenset`` values that are not directly
    JSON-serializable — these functions bridge that gap.

DESIGN: Job is immutable (frozen dataclass) with transition methods
    ✅ Safe to pass between asyncio Tasks without locking
    ✅ State transitions are explicit and traceable (new instance per change)
    ✅ Hashable — can be stored in sets for deduplication
    ❌ More verbose than mutating a dict — mitigated by transition helpers

DESIGN: auth_snapshot is dict[str, Any] rather than AuthContext
    ✅ JSON-serializable — can be persisted to any backend without custom codecs
    ✅ Decoupled from AuthContext schema changes (snapshot is a stable format)
    ✅ Can be passed as a POST body to webhook callbacks for re-authentication
    ❌ Type safety is weaker — mitigated by auth_context_from_snapshot helper

Thread safety:  ⚠️  ``AbstractJobRunner.start()`` must be called from within
                    the running event loop.  ``stop()`` cancels all tasks.
Async safety:   ✅  All methods are ``async def``.

📚 Docs
- 🐍 https://docs.python.org/3/library/asyncio-task.html
  asyncio.create_task — background task creation pattern
- 📐 https://restfulapi.net/rest-api-design-tutorial-with-example/
  202 Accepted pattern — async job acceptance
"""

from __future__ import annotations

import dataclasses
import hashlib
from abc import ABC, abstractmethod
from collections.abc import Coroutine, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID, uuid4

from varco_core.tz.schedule import GapPolicy, OverlapPolicy, resolve_zoned

if TYPE_CHECKING:
    from varco_core.auth.base import AuthContext
    from varco_core.job.task import TaskPayload, TaskRegistry, VarcoTask


class StaleLeaseError(Exception):
    """
    Raised when a write with ``expected_epoch=`` is refused because the
    stored ``lease_epoch`` has moved on (Plan 005 Phase 4, U-11 §3).

    This is the Kleppmann fencing case: a claimant that stalls past its
    lease window, gets reaped, and then resumes and tries to write must be
    rejected **at the point of write**, not merely detected after the fact.
    A stalled worker catching this must abort — it no longer owns the job.
    """


# ── JobStatus ──────────────────────────────────────────────────────────────────


class JobStatus(StrEnum):
    """
    Lifecycle states for a background job.

    State machine::

        PENDING ──(run_at future)──► PENDING (unclaimed until run_at <= now)
            ↓ (runner claims it — try_claim / claim_next)
        RUNNING
            ↓ (coroutine completes)   ↓ (coroutine raises,       ↓ (client
            │                          attempt+1 < max_attempts)   cancels)
            │                          ↓
            │                        PENDING (as_retry — run_at scheduled)
            │                          ↓ (attempts exhausted)
        COMPLETED                    FAILED / DEAD              CANCELLED

    Terminal states: COMPLETED, FAILED, CANCELLED, DEAD (Plan 005 Phase 4,
    U-17 §3) — no further transitions. ``DEAD`` specifically means "handed to
    a DLQ" (``as_dead()``) — a job that exhausted ``max_attempts`` with no
    DLQ wired lands in ``FAILED`` instead, exactly as it does today.

    Thread safety:  ✅ StrEnum members are immutable singletons.
    Async safety:   ✅ Pure value; no I/O.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD = "dead"
    """Attempts exhausted AND handed to a DLQ (Plan 005 Phase 4). Distinct
    from FAILED: FAILED means "gave up, no DLQ"; DEAD means "gave up, and a
    DLQ has the poison record" (``source=DeadLetterSource.JOB``)."""

    @property
    def is_terminal(self) -> bool:
        """Return True if this status cannot transition to another state."""
        return self in (
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.DEAD,
        )


# ── Job ────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Job:
    """
    Immutable value object representing one background job.

    State transitions return a NEW ``Job`` instance via ``dataclasses.replace()``.
    Never mutate ``Job`` fields directly — use the ``as_*`` helper methods.

    The ``auth_snapshot`` and ``request_token`` fields capture the original
    HTTP request's identity so background workers execute with the same
    authorization grants as the originating request.

    Attributes:
        job_id:          Unique identifier for this job.
        status:          Current lifecycle state (default: PENDING).
        created_at:      UTC timestamp when the job was created.
        started_at:      UTC timestamp when execution began (None if PENDING).
        completed_at:    UTC timestamp when execution ended (None if PENDING/RUNNING).
        result:          Serialized result payload (None until COMPLETED).
                         Format is opaque bytes — callers decide serialization.
        error:           Error message string (None unless FAILED).
        callback_url:    Optional webhook URL to POST completion notification to.
        auth_snapshot:   Serialized ``AuthContext`` at request time.
                         JSON-safe dict — see ``auth_context_to_snapshot()``.
        request_token:   Raw Bearer JWT for audit trail and callback authentication.
                         **Discouraged** (Plan 005 Phase 6, U-19) — no
                         ``DeprecationWarning``, no removal scheduled (matches
                         how ``JwtUtil.SYSTEM_ISSUER`` was handled). A JWT is
                         base64-encoded, not encrypted, so any PII in its
                         claims is readable at rest by anyone with read
                         access to the jobs table/collection (OWASP/NIST
                         finding). Prefer ``request_issuer`` /
                         ``request_subject`` / ``request_token_hash`` — pass
                         ``store_raw_token=False`` to have this dataclass
                         populate them for you and leave this field ``None``.
        metadata:        Arbitrary extra data (excluded from equality and hashing).

    Thread safety:  ✅ frozen=True — immutable after construction.
    Async safety:   ✅ Pure value object; safe to share across tasks.

    Edge cases:
        - ``created_at`` defaults to ``datetime.now(timezone.utc)`` — always UTC.
        - ``result`` is opaque bytes.  The caller that submitted the job is
          responsible for knowing how to deserialize it.
        - Two ``Job`` instances with different ``metadata`` but identical other
          fields compare as equal (metadata is ``compare=False``).

    Example::

        job = Job(job_id=uuid4(), created_at=datetime.now(timezone.utc))
        job = job.as_running()
        # ... work happens ...
        job = job.as_completed(result=b'{"ok": true}')
    """

    # Required: unique job identifier
    job_id: UUID = field(default_factory=uuid4)

    # Current lifecycle state
    status: JobStatus = JobStatus.PENDING

    # UTC creation timestamp
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # UTC timestamp when execution began (None if not yet started)
    started_at: datetime | None = None

    # UTC timestamp when execution ended (None if not yet finished)
    completed_at: datetime | None = None

    # Serialized result payload — set on COMPLETED, None otherwise
    result: bytes | None = None

    # Error message — set on FAILED, None otherwise
    error: str | None = None

    # Optional webhook URL for completion notification
    callback_url: str | None = None

    # Serialized AuthContext at request time — plain dict for JSON-safety.
    # frozenset fields in AuthContext (roles, scopes) are serialized as sorted lists.
    auth_snapshot: dict[str, Any] | None = None

    # Raw Bearer JWT from the originating request — for audit trail + callback auth.
    # DISCOURAGED (Plan 005 Phase 6, U-19): a JWT is base64-encoded, not
    # encrypted — any PII in its claims is readable at rest. Prefer
    # request_issuer/request_subject/request_token_hash below; pass
    # store_raw_token=False to populate them and leave this field None.
    # No DeprecationWarning, no removal scheduled — see the Attributes
    # docstring above for the full rationale.
    request_token: str | None = None

    # Arbitrary extra data — excluded from equality and hashing
    metadata: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    # Serialized task invocation for named-task recovery.
    # When set, the job runner can re-invoke the task after restart by looking
    # up the function name in the TaskRegistry and calling it with stored args.
    # None for jobs submitted via the legacy coroutine-only path (non-recoverable).
    task_payload: TaskPayload | None = field(default=None, compare=False, hash=False)

    # ── Phase 4 (Plan 005): time dimension, lease, fencing ─────────────────────
    # All defaulted so an unchanged caller gets today's behaviour exactly:
    # run_at=None claims immediately, lease_ttl=None takes no lease,
    # max_attempts=1 fails terminally on first failure.

    run_at: datetime | None = None
    """U-17 §1 — earliest time this job is eligible to be claimed. ``None``
    (default) claims immediately, exactly as today. The predicate uses the
    **database's** ``now()``, not the worker's clock — see ``claim_next``."""

    # ── Plan 011 (T2): DST-safe scheduling — three additive columns ─────────
    # D-7: `run_at` is MATERIALIZED, not replaced. These three fields are the
    # *intent*; `run_at` above remains the *materialization* of that intent
    # under the tzdata available when resolve_zoned() computed it. A row with
    # run_at_tz IS NULL is byte-identical to today in every respect — the
    # claim predicate never reads these fields.
    run_at_wall: datetime | None = None
    """Naive local wall-clock time, no tzinfo. ``None`` (default) — this job
    has no zoned schedule; `run_at` (if set) is a plain UTC instant, exactly
    as before this plan."""

    run_at_tz: str | None = None
    """IANA zone name, e.g. ``"America/New_York"``. ``None`` (default) means
    unzoned. A store must declare ``supports_zoned_schedules = True`` before
    a caller may enqueue a job with this set (RD-5)."""

    run_at_fold: int = 0
    """PEP 495 fold — disambiguates a materialization that landed on an
    ambiguous (fall-back overlap) wall-clock time. ``0`` by default."""

    attempt: int = 0
    """U-17 §3 — number of times this job has been attempted so far
    (incremented by ``as_retry``). ``0`` until the first failed attempt."""

    max_attempts: int = 1
    """U-17 §3 — ``1`` (default) means terminal-on-first-failure, exactly
    today's behaviour. A retry-capable job sets this higher and pairs it
    with a ``retry_policy`` on the runner."""

    owner_id: str | None = None
    """U-11 — identifier of the worker instance currently holding this
    job's lease. ``None`` when unleased."""

    lease_expires_at: datetime | None = None
    """U-11 — when the current lease expires. ``None`` means no lease is
    held — a store can run leased and unleased jobs side by side."""

    lease_epoch: int = 0
    """U-11 — fencing token, incremented on every claim/renew and on every
    lease-expiry reap. Used with ``expected_epoch=`` on writes to reject a
    stalled worker that resumes after being fenced out (``StaleLeaseError``)
    — the Kleppmann point: fencing happens at the point of *write*, not
    merely at claim time."""

    expires_at: datetime | None = None
    """U-18 — column added in Phase 4; retention API lands in Phase 6
    (``delete_where(expires_before=...)``). ``None`` = never expires."""

    request_issuer: str | None = None
    """U-19 — the originating request token's ``iss`` claim, as a reference
    field. Column added in Phase 4; populated by ``store_raw_token=False``
    machinery — see ``request_token_hash`` below."""

    request_subject: str | None = None
    """U-19 — the originating request token's ``sub`` claim, as a reference
    field. ``None`` unless explicitly supplied by the caller (this dataclass
    does not decode JWTs itself)."""

    request_token_hash: str | None = None
    """U-19 — sha256 hex digest of ``request_token``, populated automatically
    by ``__post_init__`` when ``store_raw_token=False`` — see below. Prefer
    this + ``request_issuer``/``request_subject`` over the raw
    ``request_token`` for anything written to a jobs table: a JWT is
    base64-encoded, not encrypted, so any PII in its claims is readable at
    rest (OWASP/NIST finding)."""

    store_raw_token: bool = True
    """U-19 — when ``False``, ``__post_init__`` computes
    ``request_token_hash`` from ``request_token`` and then clears
    ``request_token`` to ``None``. **Default stays ``True``** — Source
    correction 4: ``JobRunner`` forwards ``job.request_token`` as the
    completion callback's ``Authorization: Bearer`` header, so flipping the
    default would silently break callback auth. Setting this ``False``
    requires the callback to authenticate with a service credential instead
    of replaying the caller's token — which also removes a token-replay
    surface. Discouraged-not-deprecated: no ``DeprecationWarning``, no
    removal scheduled for ``request_token`` itself (matches how
    ``JwtUtil.SYSTEM_ISSUER`` was handled)."""

    def __post_init__(self) -> None:
        """
        Apply the ``store_raw_token=False`` reference-fields transform
        (Plan 005 Phase 6, U-19).

        Frozen dataclass — uses ``object.__setattr__``, the established
        escape hatch for post-construction field synthesis in this codebase
        (see ``EncryptionKeyEntry.__post_init__``).

        Edge cases:
            - ``store_raw_token=True`` (default) — no-op, ``request_token``
              is left exactly as passed.
            - ``store_raw_token=False`` with ``request_token=None`` — no-op,
              nothing to hash.
            - The hash never contains the raw token as a substring — it is a
              sha256 hex digest, a one-way function.
        """
        if not self.store_raw_token and self.request_token is not None:
            digest = hashlib.sha256(self.request_token.encode("utf-8")).hexdigest()
            object.__setattr__(self, "request_token_hash", digest)
            object.__setattr__(self, "request_token", None)

    # ── State transitions (return new Job via dataclasses.replace) ─────────────

    def as_running(self) -> Job:
        """
        Transition this job to RUNNING state.

        Returns:
            New ``Job`` with ``status=RUNNING`` and ``started_at`` set to
            current UTC time.

        Raises:
            ValueError: If the current status is not PENDING.

        Edge cases:
            - Calling on a job already in RUNNING is a programming error —
              raises ``ValueError`` to catch double-start bugs early.
        """
        if self.status != JobStatus.PENDING:
            raise ValueError(
                f"Cannot transition job {self.job_id} to RUNNING from {self.status!r}. "
                "Only PENDING jobs can be started."
            )
        return dataclasses.replace(
            self,
            status=JobStatus.RUNNING,
            started_at=datetime.now(UTC),
        )

    def as_completed(self, result: bytes | None) -> Job:
        """
        Transition this job to COMPLETED state.

        Args:
            result: Serialized result payload.  Format is caller-defined.

        Returns:
            New ``Job`` with ``status=COMPLETED``, ``result`` set, and
            ``completed_at`` set to current UTC time.

        Raises:
            ValueError: If the current status is not RUNNING.

        Edge cases:
            - ``result`` may be empty bytes (``b""``) for void operations.
            - The previous ``error`` field is not cleared — it should already
              be ``None`` for a RUNNING → COMPLETED transition.
        """
        if self.status != JobStatus.RUNNING:
            raise ValueError(
                f"Cannot transition job {self.job_id} to COMPLETED from {self.status!r}. "
                "Only RUNNING jobs can complete."
            )
        return dataclasses.replace(
            self,
            status=JobStatus.COMPLETED,
            result=result,
            completed_at=datetime.now(UTC),
        )

    def as_failed(self, error: str) -> Job:
        """
        Transition this job to FAILED state.

        Args:
            error: Human-readable error message describing the failure.

        Returns:
            New ``Job`` with ``status=FAILED``, ``error`` set, and
            ``completed_at`` set to current UTC time.

        Raises:
            ValueError: If the current status is not RUNNING.

        Edge cases:
            - ``error`` should be sanitized (no stack traces) before being
              stored — the raw exception message may contain sensitive data.
        """
        if self.status != JobStatus.RUNNING:
            raise ValueError(
                f"Cannot transition job {self.job_id} to FAILED from {self.status!r}. "
                "Only RUNNING jobs can fail."
            )
        return dataclasses.replace(
            self,
            status=JobStatus.FAILED,
            error=error,
            completed_at=datetime.now(UTC),
        )

    def as_cancelled(self) -> Job:
        """
        Transition this job to CANCELLED state.

        Returns:
            New ``Job`` with ``status=CANCELLED`` and ``completed_at`` set
            to current UTC time.

        Raises:
            ValueError: If the job is already in a terminal state.

        Edge cases:
            - Cancellation is allowed from both PENDING and RUNNING states.
            - Cancelling a COMPLETED/FAILED job is an error — use this check
              to prevent accidental cancellation of finished work.
        """
        if self.status.is_terminal:
            raise ValueError(
                f"Cannot cancel job {self.job_id}: already in terminal state {self.status!r}."
            )
        return dataclasses.replace(
            self,
            status=JobStatus.CANCELLED,
            completed_at=datetime.now(UTC),
        )

    def as_retry(self, next_run_at: datetime) -> Job:
        """
        Transition this job back to PENDING for a scheduled retry
        (Plan 005 Phase 4, U-17 §3 — the retry binding).

        Args:
            next_run_at: When the job becomes eligible to be reclaimed —
                         typically ``now + retry_policy.compute_delay(attempt)``.

        Returns:
            New ``Job`` with ``status=PENDING``, ``run_at=next_run_at``, and
            ``attempt`` incremented by one. ``started_at``/``completed_at``
            are left as-is (the previous RUNNING attempt's timestamps) —
            the next claim overwrites ``started_at`` again.

        Raises:
            ValueError: If the current status is not RUNNING — only a
                        job that was actually running can be retried.

        Edge cases:
            - Callers are responsible for checking
              ``attempt + 1 < max_attempts`` before calling this — ``as_retry``
              itself does not enforce the ceiling (that decision belongs to
              the runner, which chooses between ``as_retry`` and
              ``as_failed``/``as_dead``).
        """
        if self.status != JobStatus.RUNNING:
            raise ValueError(
                f"Cannot transition job {self.job_id} to a retry from "
                f"{self.status!r}. Only RUNNING jobs can be retried."
            )
        return dataclasses.replace(
            self,
            status=JobStatus.PENDING,
            run_at=next_run_at,
            attempt=self.attempt + 1,
        )

    def as_dead(self, error: str) -> Job:
        """
        Transition this job to DEAD state — attempts exhausted **and** the
        job was handed to a DLQ (Plan 005 Phase 4, U-17 §3).

        Distinct from ``as_failed()``: ``FAILED`` means "gave up, no DLQ
        wired"; ``DEAD`` means "gave up, and a DLQ has the poison record"
        (``source=DeadLetterSource.JOB``). Callers push to the DLQ
        themselves (this method only records the terminal state) — mirrors
        ``OutboxRelay``'s "dead-letter first, then delete/terminalize" order.

        Args:
            error: Human-readable terminal error message.

        Returns:
            New ``Job`` with ``status=DEAD``, ``error`` set, and
            ``completed_at`` set to current UTC time.

        Raises:
            ValueError: If the current status is not RUNNING.
        """
        if self.status != JobStatus.RUNNING:
            raise ValueError(
                f"Cannot transition job {self.job_id} to DEAD from "
                f"{self.status!r}. Only RUNNING jobs can be dead-lettered."
            )
        return dataclasses.replace(
            self,
            status=JobStatus.DEAD,
            error=error,
            completed_at=datetime.now(UTC),
        )


# ── Auth snapshot serialization ────────────────────────────────────────────────


def auth_context_to_snapshot(ctx: AuthContext) -> dict[str, Any]:
    """
    Serialize an ``AuthContext`` to a JSON-safe dict for job persistence.

    Background workers must run with the same identity/grants as the HTTP
    request that submitted them.  ``AuthContext`` contains ``frozenset``
    values which are not JSON-serializable — this function normalizes them.

    Serialization rules:
    - ``frozenset[str]`` (roles, scopes) → ``sorted(list(...))``
    - ``ResourceGrant`` tuple → list of ``{"resource": ..., "actions": [...]}`` dicts
    - ``metadata`` → included as-is (must already be JSON-safe; callers responsible)

    Args:
        ctx: The ``AuthContext`` to serialize.

    Returns:
        A ``dict[str, Any]`` that is safe to pass to ``json.dumps()`` and
        to store in any backend (in-memory dict, Redis, SQL JSONB column).

    Edge cases:
        - ``user_id`` is ``None`` for anonymous contexts — stored as ``None``.
        - ``metadata`` is included verbatim — the caller must ensure its values
          are JSON-serializable (str, int, float, bool, list, dict, None).
        - ``ResourceGrant.actions`` (frozenset[Action]) → sorted list of str.

    Thread safety:  ✅ Pure function; no shared state.
    Async safety:   ✅ Pure function; no I/O.

    Example::

        snapshot = auth_context_to_snapshot(ctx)
        # snapshot == {
        #     "user_id": "usr_123",
        #     "roles": ["admin", "editor"],
        #     "scopes": ["write:posts"],
        #     "grants": [{"resource": "posts", "actions": ["create", "read"]}],
        #     "metadata": {"tenant_id": "t1"},
        # }
    """
    return {
        "user_id": ctx.user_id,
        "roles": sorted(ctx.roles),
        "scopes": sorted(ctx.scopes),
        "grants": [
            {
                "resource": g.resource,
                "actions": sorted(str(a) for a in g.actions),
            }
            for g in ctx.grants
        ],
        "metadata": ctx.metadata,
    }


def auth_context_from_snapshot(snapshot: dict[str, Any]) -> AuthContext:
    """
    Reconstruct an ``AuthContext`` from a stored snapshot dict.

    Inverse of ``auth_context_to_snapshot()``.  Used by ``JobRunner`` to
    restore the original request's auth context before executing the job
    coroutine, so it runs with the correct identity and grants.

    Args:
        snapshot: A dict produced by ``auth_context_to_snapshot()``.

    Returns:
        An ``AuthContext`` with all fields restored from the snapshot.

    Raises:
        KeyError: If expected keys are missing from the snapshot.
        TypeError: If values have unexpected types.

    Edge cases:
        - Missing ``"metadata"`` key defaults to an empty dict — tolerates
          snapshots written before metadata was added.
        - Unknown keys in ``snapshot`` are ignored — forward-compatible.
        - Actions in ``grants`` are stored as raw strings; they compare
          equal to ``Action`` enum members (``Action`` is a ``StrEnum``).

    Thread safety:  ✅ Pure function; no shared state.
    Async safety:   ✅ Pure function; no I/O.
    """
    # Import here to avoid making job.base depend on auth at module level.
    # TYPE_CHECKING guard at the top covers type hints only.
    from varco_core.auth.base import Action, AuthContext, ResourceGrant

    grants = tuple(
        ResourceGrant(
            resource=g["resource"],
            actions=frozenset(Action(a) for a in g["actions"]),
        )
        for g in snapshot.get("grants", [])
    )

    return AuthContext(
        user_id=snapshot.get("user_id"),
        roles=frozenset(snapshot.get("roles", [])),
        scopes=frozenset(snapshot.get("scopes", [])),
        grants=grants,
        metadata=snapshot.get("metadata", {}),
    )


# ── AbstractJobStore ───────────────────────────────────────────────────────────


class AbstractJobStore(ABC):
    """
    Abstract persistence contract for ``Job`` objects.

    Implement this to provide job persistence:
    - ``InMemoryJobStore`` (varco_fastapi): dict-backed, no durability.
    - Redis store (future): TTL-based, suitable for distributed deployments.
    - SQL store (future): durable, queryable, backed by varco_sa.

    The HTTP layer reads/writes jobs via this interface.  Backend implementations
    must ensure that ``save()`` on an existing ``job_id`` replaces the stored value
    (upsert semantics).

    Thread safety:  ⚠️ Implementations must document their own thread safety.
                       ``InMemoryJobStore`` uses a lazy ``asyncio.Lock`` for safety.
    Async safety:   ✅ All methods are ``async def``.
    """

    #: RD-5 (Plan 011 T2) — a store must DECLARE zoned-schedule support
    #: before ``AbstractJobRunner.enqueue(tz=...)`` may target it. ``False``
    #: on the ABC means every out-of-tree store is unaffected until its
    #: author opts in — failing closed at enqueue turns a silent
    #: degradation (columns dropped, DST safety quietly absent) into a
    #: named ``ValueError`` at enqueue time instead.
    supports_zoned_schedules: ClassVar[bool] = False

    @abstractmethod
    async def save(self, job: Job, *, expected_epoch: int | None = None) -> None:
        """
        Persist or update a ``Job``.  Upsert semantics — if a job with the
        same ``job_id`` exists, it is replaced.

        Args:
            job: The job to save.
            expected_epoch: Fencing token (Plan 005 Phase 4, U-11 §3).
                ``None`` (default) — no fencing check, today's behaviour
                exactly. When supplied, implementations MUST refuse the
                write with ``StaleLeaseError`` if the row's stored
                ``lease_epoch`` no longer equals ``expected_epoch`` — this
                is the Kleppmann case: a claimant that stalls past its
                lease window and resumes must be rejected **at the point of
                write**, not merely detected after the fact.

        Raises:
            StaleLeaseError: ``expected_epoch`` is supplied and no longer
                matches the stored ``lease_epoch``.
            Exception: Any backend-specific persistence error propagates
                to the caller unchanged.

        Edge cases:
            - Saving a terminal job (COMPLETED, FAILED, CANCELLED) is valid —
              implementations should not reject terminal-state saves.
            - Concurrent saves for the same ``job_id`` are implementation-defined;
              last-write-wins is acceptable for the RUNNING → COMPLETED transition
              since only one task should ever hold a job.
            - Implementations that do not support leases may treat
              ``expected_epoch`` as a no-op (document this explicitly) rather
              than raising ``NotImplementedError`` — unlike ``renew``/
              ``reap_expired_leases``, a store with zero lease usage never
              has a stale epoch to detect.
        """

    @abstractmethod
    async def get(self, job_id: UUID) -> Job | None:
        """
        Retrieve a ``Job`` by its ``job_id``.

        Args:
            job_id: The unique job identifier.

        Returns:
            The ``Job`` if found, or ``None`` if not found.

        Edge cases:
            - Returns ``None`` for unknown job IDs — callers must check for None.
        """

    @abstractmethod
    async def list_by_status(
        self,
        status: JobStatus,
        *,
        limit: int = 100,
    ) -> list[Job]:
        """
        Return up to ``limit`` jobs matching ``status``, ordered by ``created_at``.

        Used by ``JobPoller`` to recover stale RUNNING jobs after restart.

        Args:
            status: Filter by this lifecycle state.
            limit:  Maximum number of results to return.

        Returns:
            List of matching ``Job`` objects, oldest first.

        Edge cases:
            - Returns an empty list if no matching jobs exist.
        """

    @abstractmethod
    async def delete(self, job_id: UUID) -> None:
        """
        Remove a ``Job`` from the store.

        Called by cleanup tasks to purge old completed jobs.

        Args:
            job_id: The unique job identifier.

        Edge cases:
            - Deleting an unknown ``job_id`` should be a silent no-op.
        """

    @abstractmethod
    async def try_claim(
        self,
        job_id: UUID,
        *,
        owner_id: str | None = None,
        lease_ttl: float | None = None,
    ) -> Job | None:
        """
        Atomically transition a PENDING job to RUNNING state.

        ⚠️ **This is an addition, not the activation of a dormant
        parameter** (Plan 005, Source correction 1) — pre-Phase-4
        ``try_claim(job_id)`` took no lease-related arguments at all.
        External ``AbstractJobStore`` subclasses must add ``owner_id``/
        ``lease_ttl`` to their own override before enabling leases; callers
        that never pass them are completely unaffected.

        This is the distributed-safety primitive for job recovery.  When multiple
        runner instances start concurrently (e.g. after a rolling restart or in a
        multi-replica deployment), each calls ``try_claim()`` on every PENDING job
        they discover.  Only one runner succeeds — the others get ``None`` and skip.

        Implementations MUST guarantee atomicity:
        - In-memory: use an ``asyncio.Lock`` that wraps the read + write together.
        - Redis: use SET NX (set-if-not-exists) on a claim key, or WATCH + MULTI.
        - SQL: use ``SELECT ... FOR UPDATE SKIP LOCKED`` (PostgreSQL/MySQL).

        DESIGN: try_claim() over optimistic locking (check-then-act)
            ✅ Single round-trip on the "success" path for Redis/SQL
            ✅ Prevents double-execution with zero coordination between runners
            ✅ No distributed lock manager needed — store is the source of truth
            ❌ Requires store implementations to support atomic CAS (Redis, SQL, etc.)
               Simple dict-based stores must use a Lock, which only guards a single process.

        Args:
            job_id: The UUID of the job to claim.

        Returns:
            The claimed ``Job`` in RUNNING state if the claim succeeded.
            ``None`` if the job does not exist, is not in PENDING state, or was
            already claimed by another runner.

        Thread safety:  ✅ Implementations must ensure atomicity of the PENDING → RUNNING
                           check-and-set operation under concurrent callers.
        Async safety:   ✅ Must be ``async def``.

        Edge cases:
            - Unknown ``job_id`` → returns ``None`` (not an error).
            - Job in a non-PENDING state → returns ``None`` (already running or terminal).
            - Concurrent calls on the same ``job_id`` → exactly one returns the Job,
              all others return ``None``.
        """

    # ── Phase 4 (Plan 005): concrete default implementations ───────────────────
    # These are concrete (not @abstractmethod) so existing external
    # AbstractJobStore subclasses keep importing/instantiating unchanged —
    # see "Compatibility posture" in plan 005: ABCs get default
    # implementations rather than new abstract methods.

    async def claim_next(
        self,
        *,
        owner_id: str | None = None,
        lease_ttl: float | None = None,
        now: datetime | None = None,
    ) -> Job | None:
        """
        Claim the oldest eligible PENDING job, honouring the schedule
        predicate ``run_at IS NULL OR run_at <= now`` (Plan 005 Phase 4).

        Default implementation: ``list_by_status(PENDING)`` filtered
        client-side by the schedule predicate, then a ``try_claim`` loop —
        correct but not lock-free/atomic across the *selection* step
        (only the individual ``try_claim`` call is atomic). Backends with a
        native atomic claim query (``SAJobStore``, ``RedisJobStore``,
        ``BeanieJobStore``) override this with a single round-trip.

        Args:
            owner_id: Forwarded to ``try_claim`` — identifies the lease
                holder. ``None`` takes no lease.
            lease_ttl: Forwarded to ``try_claim`` — lease duration in
                seconds. ``None`` takes no lease.
            now: The "current time" to evaluate ``run_at`` against.
                Defaults to ``datetime.now(timezone.utc)``. Exposed as a
                parameter for deterministic testing.

        Returns:
            The claimed ``Job`` in RUNNING state, or ``None`` if no eligible
            PENDING job exists (all future-scheduled, or the list was empty,
            or every candidate was already claimed by a concurrent caller).

        Edge cases:
            - Jobs with ``run_at`` in the future are skipped even if PENDING.
            - Under this default impl, two concurrent callers each list
              PENDING independently; only one wins any given job's
              ``try_claim`` — no double-claim, but callers may briefly
              contend on the same candidate.

        Async safety: ✅ All I/O is awaited.
        """
        candidates = await self.list_by_status(JobStatus.PENDING, limit=100)
        current = now if now is not None else datetime.now(UTC)
        for candidate in candidates:
            if candidate.run_at is not None and candidate.run_at > current:
                continue
            claimed = await self.try_claim(candidate.job_id, owner_id=owner_id, lease_ttl=lease_ttl)
            if claimed is not None:
                return claimed
        return None

    async def delete_where(
        self,
        *,
        status: JobStatus | Sequence[JobStatus] | None = None,
        completed_before: datetime | None = None,
        expires_before: datetime | None = None,
        limit: int | None = None,
    ) -> int:
        """
        Bulk-delete jobs matching one or more predicates (Plan 005 Phase 6,
        U-18 — job retention).

        Concrete on the ABC (not ``@abstractmethod``) with a portable default
        implementation over ``list_by_status`` + ``delete`` — correct for any
        existing external ``AbstractJobStore`` subclass, just not as fast as
        a native single-statement ``DELETE``. Backends with a native bulk
        delete (``SAJobStore``, ``RedisJobStore``, ``BeanieJobStore``,
        ``InMemoryJobStore``) override this method.

        **Chunked-sweep recipe** — the recommended way to retire old jobs
        without starving a pooled connection::

            deleted = -1
            while deleted != 0:
                deleted = await store.delete_where(
                    status=JobStatus.COMPLETED,
                    completed_before=cutoff,
                    limit=1000,
                )

        ``limit`` exists specifically for this: enumerating and deleting rows
        one id at a time under a transaction-mode connection pooler pins a
        server connection for the ENTIRE sweep (every round-trip must land on
        the same physical connection the transaction started on). Looping
        bounded ``delete_where(..., limit=N)`` calls, each its own short
        transaction, releases the connection back to the pool between
        batches instead.

        Args:
            status: A single ``JobStatus`` or a sequence of them to match.
                ``None`` (default) does not filter by status — matches every
                status (subject to the other predicates below).
            completed_before: Only match jobs whose ``completed_at`` is set
                AND strictly before this timestamp. Jobs with
                ``completed_at=None`` (never completed) never match this
                predicate.
            expires_before: Only match jobs whose ``expires_at`` is set AND
                strictly before this timestamp. Jobs with ``expires_at=None``
                (never expires) never match this predicate.
            limit: Maximum number of rows to delete in this call. ``None``
                (default) deletes every matching row in one call — see the
                chunked-sweep recipe above for large backlogs.

        Returns:
            The number of jobs actually deleted — callers loop on this value
            (``0`` means the sweep is done).

        Raises:
            ValueError: No predicate at all was supplied (``status``,
                ``completed_before``, and ``expires_before`` are all
                ``None``) — refusing to silently delete every row in the
                store. Pass at least one explicit predicate.

        Edge cases:
            - ``status`` as a bare ``JobStatus`` and as a one-element
              ``Sequence[JobStatus]`` are equivalent.
            - Combining ``status`` with ``completed_before``/``expires_before``
              is an AND — a job must match every supplied predicate.
            - The portable default fetches up to ``max(limit, 1000)`` (or a
              flat ``10_000`` when ``limit`` is ``None``) candidates per
              status before filtering — a backlog larger than that requires
              multiple chunked calls even for `limit=None`, exactly like the
              chunked-sweep recipe.

        Async safety: ✅ All I/O is awaited.
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

        # Fetch generously so predicate filtering below has enough candidates
        # to work with — see the chunked-sweep recipe above for backlogs
        # larger than this per-status fetch size.
        per_status_fetch = max(limit, 1000) if limit is not None else 10_000

        deleted = 0
        for st in statuses:
            candidates = await self.list_by_status(st, limit=per_status_fetch)
            for job in candidates:
                if limit is not None and deleted >= limit:
                    return deleted
                if completed_before is not None and (
                    job.completed_at is None or job.completed_at >= completed_before
                ):
                    continue
                if expires_before is not None and (
                    job.expires_at is None or job.expires_at >= expires_before
                ):
                    continue
                await self.delete(job.job_id)
                deleted += 1
        return deleted

    async def list_pending_zoned(
        self,
        before: datetime,
        *,
        limit: int = 100,
    ) -> list[Job]:
        """
        Return PENDING jobs with a zoned schedule (``run_at_tz IS NOT
        NULL``) whose ``run_at`` is before ``before`` (Plan 011 T2 —
        ``ScheduleRematerializer``'s query).

        Portable default over ``list_by_status(PENDING)`` + an in-Python
        filter — a correct (if unindexed) fallback genuinely exists here,
        unlike ``renew()``/``reap_expired_leases()`` below, so this is
        concrete rather than raising. ``SAJobStore`` overrides with a real
        ``WHERE run_at_tz IS NOT NULL AND run_at < :before LIMIT :limit``.

        Args:
            before: Only jobs whose ``run_at`` is strictly before this are
                returned — bounds the sweep to jobs "about to fire" rather
                than re-materializing years-out schedules on every pass.
            limit: Maximum number of jobs to return.

        Returns:
            Matching jobs, unordered.
        """
        candidates = await self.list_by_status(JobStatus.PENDING, limit=max(limit, 1000))
        return [
            job
            for job in candidates
            if job.run_at_tz is not None and job.run_at is not None and job.run_at < before
        ][:limit]

    async def renew(
        self,
        job_id: UUID,
        *,
        owner_id: str,
        epoch: int,
        lease_ttl: float,
    ) -> Job | None:
        """
        Heartbeat an in-progress lease, extending ``lease_expires_at``
        (Plan 005 Phase 4, U-11).

        Default implementation deliberately raises — there is no correct
        fallback for a lease renewal (a silent no-op heartbeat would be
        worse than an error, masking a lease that is about to expire).

        Args:
            job_id: The job whose lease is being renewed.
            owner_id: Must match the job's current ``owner_id``.
            epoch: Must match the job's current ``lease_epoch`` — a stale
                epoch means this caller was fenced out (e.g. reaped after a
                stall) and must NOT succeed in renewing.
            lease_ttl: New lease duration in seconds, from ``now``.

        Returns:
            The renewed ``Job`` with an extended ``lease_expires_at``, or
            ``None`` when the epoch is stale (fenced out) — never raises for
            that case, only for "this store does not support leases at all".

        Raises:
            NotImplementedError: This store does not support leases.

        Async safety: ✅ (raises synchronously from an async function).
        """
        raise NotImplementedError(f"{type(self).__name__} does not support leases")

    async def reap_expired_leases(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> list[Job]:
        """
        Return RUNNING jobs whose lease has expired back to PENDING,
        incrementing ``lease_epoch`` to fence out the stalled owner
        (Plan 005 Phase 4, U-11).

        Default implementation deliberately raises — same rationale as
        ``renew()``: there is no correct fallback for lease expiry
        detection, and a silent no-op would leave dead workers' jobs stuck
        RUNNING forever.

        Args:
            now: The "current time" to compare ``lease_expires_at`` against.
                Defaults to ``datetime.now(timezone.utc)``.
            limit: Maximum number of jobs to reap in one call.

        Returns:
            The list of jobs that were moved back to PENDING (already
            reflecting the new state — same shape as ``try_claim``'s
            return value pattern).

        Raises:
            NotImplementedError: This store does not support leases.

        Async safety: ✅ (raises synchronously from an async function).
        """
        raise NotImplementedError(f"{type(self).__name__} does not support leases")


# ── AbstractJobRunner ──────────────────────────────────────────────────────────


class AbstractJobRunner(ABC):
    """
    Abstract execution contract for background job coroutines.

    The runner wraps each submitted coroutine in an ``asyncio.Task``,
    tracks it by ``job_id``, and handles cancellation.

    The concrete ``JobRunner`` in varco_fastapi:
    - Creates one ``asyncio.Task`` per submitted coroutine.
    - Updates ``Job`` status in ``AbstractJobStore`` on start/complete/fail.
    - Emits ``JobProgressEvent`` events for SSE streaming.
    - Forwards auth context so the coroutine runs with the correct identity.

    DESIGN: AbstractJobRunner as ABC in varco_core (not varco_fastapi)
        ✅ Services in varco_core can declare ``Inject[AbstractJobRunner]``
           without depending on the HTTP layer.
        ✅ Testable with a stub implementation — no asyncio.Task needed in unit tests.
        ❌ Concrete implementations (asyncio tasks, Celery, etc.) still live
           in backend packages — this ABC adds a small indirection layer.

    DESIGN: enqueue() as the primary submission API (not submit())
        ✅ Saves the job to the store before scheduling the task — crash-safe.
           If the process dies between save and task creation, the JobPoller
           finds the PENDING job and can mark it FAILED or re-queue it.
        ✅ Single call site — callers cannot forget to persist before submitting.
        ❌ The save and task creation are not truly atomic across store + event loop.
           A crash *between* store.save() and asyncio.create_task() leaves a
           PENDING zombie — acceptable because the JobPoller recovers it.

    Thread safety:  ⚠️  ``start()`` must be called from within the running event loop.
    Async safety:   ✅  All methods are ``async def``.
    """

    @abstractmethod
    async def enqueue(
        self,
        job: Job,
        coro: Coroutine[Any, Any, Any],
        *,
        run_at: datetime | None = None,
        delay: timedelta | None = None,
        run_at_wall: datetime | None = None,
        tz: str | None = None,
        fold: int = 0,
        gap: GapPolicy = GapPolicy.NEXT_VALID,
        overlap: OverlapPolicy = OverlapPolicy.FIRST,
    ) -> None:
        """
        Persist ``job`` to the store as PENDING, then schedule ``coro`` for
        background execution.

        This is the **only correct way** to submit a job.  Callers must not
        call ``submit()`` directly — use ``enqueue()`` so the job is durably
        persisted before the asyncio.Task is created.  This ensures that a
        process crash between submission and execution leaves a recoverable
        PENDING record in the store rather than a silent loss.

        Steps performed by implementations:
        1. ``store.save(job)`` — persists PENDING record.
        2. ``submit(job.job_id, coro)`` — schedules the asyncio.Task.

        Args:
            job:  A ``Job`` instance in PENDING state.
            coro: The coroutine to execute in the background.
            run_at: Plan 005 Phase 4, U-17 §2 — earliest time this job is
                eligible to be claimed, pass-through to ``Job.run_at``.
                Mutually exclusive with ``delay``. ``None`` (default) claims
                immediately, exactly as today.
            delay: Convenience alternative to ``run_at`` — resolved to
                ``now + delay`` by implementations. Mutually exclusive with
                ``run_at``.

        Raises:
            ValueError: Both ``run_at`` and ``delay`` were supplied.
            Exception: Any store-specific error from ``store.save()`` propagates
                to the caller unchanged.

        Edge cases:
            - If ``store.save()`` raises, ``coro`` is closed immediately (no leak).
            - If the process crashes after ``save()`` but before the task starts,
              the ``JobPoller`` will find the PENDING job on restart and mark it
              FAILED (since it never transitions to RUNNING).

        Async safety:   ✅ Returns after scheduling; does not wait for completion.
        """

    @staticmethod
    def _prepare_zoned_job(
        job: Job,
        store: AbstractJobStore,
        *,
        run_at: datetime | None = None,
        run_at_wall: datetime | None = None,
        tz: str | None = None,
        fold: int = 0,
        gap: GapPolicy = GapPolicy.NEXT_VALID,
        overlap: OverlapPolicy = OverlapPolicy.FIRST,
    ) -> Job:
        """
        Concrete, reusable RD-5 guard + T2 materialization step every
        concrete ``enqueue()`` implementation calls before ``store.save()``.

        Plan 011 / RD-5: refuses (``ValueError`` naming the store class)
        when ``tz`` is supplied but ``store.supports_zoned_schedules`` is
        ``False`` — turns a silent degradation (an explicit-column store
        would otherwise silently drop ``run_at_wall``/``run_at_tz``) into a
        named, startup/enqueue-time error.

        Args:
            job: The job to prepare (its ``run_at``/``run_at_wall``/
                ``run_at_tz``/``run_at_fold`` fields are NOT read — only
                ``run_at``/``run_at_wall``/``tz``/``fold``/``gap``/
                ``overlap`` kwargs below drive materialization).
            store: The target ``AbstractJobStore`` — its
                ``supports_zoned_schedules`` is the RD-5 gate.
            run_at: A plain (already-UTC) claim time. Mutually exclusive
                with ``run_at_wall``/``tz``.
            run_at_wall: Naive local wall-clock time — the T2 zoned path.
            tz: IANA zone name for ``run_at_wall``.
            fold: PEP 495 fold, forwarded to ``resolve_zoned``.
            gap: ``GapPolicy`` forwarded to ``resolve_zoned``.
            overlap: ``OverlapPolicy`` forwarded to ``resolve_zoned``.

        Returns:
            ``job`` with ``run_at``/``run_at_wall``/``run_at_tz``/
            ``run_at_fold`` populated (via ``dataclasses.replace`` — ``Job``
            is frozen).

        Raises:
            ValueError: Both ``run_at`` and ``run_at_wall``/``tz`` were
                supplied; or ``tz`` was supplied but
                ``store.supports_zoned_schedules`` is ``False``.
        """
        if run_at is not None and (run_at_wall is not None or tz is not None):
            raise ValueError(
                "enqueue() received both run_at= and run_at_wall=/tz= — "
                "these are mutually exclusive (D-7: run_at is the "
                "materialization, run_at_wall/tz is the intent)."
            )

        if tz is None:
            if run_at is not None:
                return dataclasses.replace(job, run_at=run_at)
            return job

        if not store.supports_zoned_schedules:
            raise ValueError(
                f"{type(store).__name__} does not declare "
                "supports_zoned_schedules = True — refusing to enqueue a "
                "zoned schedule (RD-5). Persist run_at_wall/run_at_tz/"
                "run_at_fold in your store, then set "
                "supports_zoned_schedules = True."
            )

        # BUG (surfaced by RL-6's mypy gate, plans/017): `tz` alone does not
        # imply `run_at_wall` is set — without this guard, a caller passing
        # `tz=` but not `run_at_wall=` would fall through to
        # `resolve_zoned(None, ...)` and crash inside it with an opaque
        # AttributeError instead of the descriptive ValueError every other
        # misuse in this function raises.
        if run_at_wall is None:
            raise ValueError(
                "enqueue() received tz= without run_at_wall= — a zoned schedule requires both."
            )

        from zoneinfo import ZoneInfo

        zone = ZoneInfo(tz)
        materialized = resolve_zoned(
            run_at_wall, zone, fold=fold, gap=gap, overlap=overlap
        ).astimezone(UTC)
        return dataclasses.replace(
            job,
            run_at=materialized,
            run_at_wall=run_at_wall,
            run_at_tz=tz,
            run_at_fold=fold,
        )

    @abstractmethod
    async def submit(
        self,
        job_id: UUID,
        coro: Coroutine[Any, Any, Any],
    ) -> None:
        """
        Schedule a coroutine for background execution under a pre-existing ``job_id``.

        **Prefer ``enqueue()`` over calling this directly.**  This method assumes
        the job already exists in the store as PENDING.  Calling it without a
        prior ``store.save()`` will cause ``_run_job`` to log an error and
        silently drop the coroutine.

        The runner:
        1. Loads the job from the store (must already exist in PENDING state).
        2. Transitions it to RUNNING and saves.
        3. Executes the coroutine inside an asyncio.Task.
        4. On completion → ``job.as_completed(result)`` saved to store.
        5. On failure → ``job.as_failed(str(exc))`` saved to store.

        Args:
            job_id: The ID of the pre-existing PENDING job.
            coro:   The coroutine to execute.

        Edge cases:
            - ``job_id`` not in store → task logs an error and closes ``coro``.
            - Job not in PENDING state → ``as_running()`` raises ValueError inside task.
            - Runner stopped before task finishes → task cancelled → FAILED in store.

        Async safety:   ✅ Returns immediately after scheduling the task.
        """

    @abstractmethod
    async def cancel(self, job_id: UUID) -> bool:
        """
        Cancel the running task for ``job_id``.

        Args:
            job_id: The job to cancel.

        Returns:
            ``True`` if the task was found and cancellation was requested.
            ``False`` if no active task exists for this job (already completed
            or never submitted to this runner instance).

        Edge cases:
            - Cancellation is cooperative — the coroutine must check for
              ``asyncio.CancelledError`` (which is raised automatically at
              the next ``await`` point).
            - ``cancel()`` returns immediately; the task may not have finished
              by the time this returns.  Poll the job store for final status.
        """

    @abstractmethod
    async def start(self) -> None:
        """
        Start the job runner.  Idempotent.

        Must be called from within a running event loop (i.e. inside an
        ``async`` function or at application startup).

        Edge cases:
            - Calling ``start()`` twice is safe — the second call is a no-op.
        """

    @abstractmethod
    async def enqueue_task(
        self,
        task: VarcoTask,
        *args: Any,
        callback_url: str | None = None,
        auth_snapshot: dict[str, Any] | None = None,
        request_token: str | None = None,
        **kwargs: Any,
    ) -> UUID:
        """
        Submit a named task for background execution and return its job ID.

        Unlike ``enqueue(job, coro)`` which takes a bare coroutine, this method
        accepts a ``VarcoTask`` and its call arguments.  The runner serializes
        the invocation as a ``TaskPayload`` and stores it in the ``Job`` record,
        making the job recoverable after a process restart via ``recover()``.

        Steps performed by implementations:
        1. Build a ``TaskPayload`` from ``task.payload(*args, **kwargs)``.
        2. Create a ``Job`` in PENDING state with ``task_payload`` set.
        3. ``store.save(job)`` — persist the PENDING record with payload.
        4. ``submit(job.job_id, task(*args, **kwargs))`` — schedule the task.
        5. Return ``job.job_id``.

        Args:
            task:          The ``VarcoTask`` to execute.
            *args:         Positional arguments forwarded to the task function.
            callback_url:  Optional webhook URL to call on completion.
            auth_snapshot: Serialized ``AuthContext`` from the originating request.
            request_token: Raw Bearer JWT for audit trail and callback auth.
            **kwargs:      Keyword arguments forwarded to the task function.

        Returns:
            The ``UUID`` of the newly created job.

        Raises:
            Exception: Any store-specific error from ``store.save()`` propagates
                to the caller unchanged.

        Edge cases:
            - All values in ``args`` and ``kwargs`` must be JSON-serializable.
              If they are not, the job will fail at recovery time (not submission time
              unless the implementation calls ``payload.validate_serializable()``).
            - Process crash after ``store.save()`` but before the task runs →
              job stays PENDING; ``recover()`` will re-invoke on next startup.

        Async safety:   ✅ Returns after scheduling; does not wait for task completion.
        """

    @abstractmethod
    async def recover(self, registry: TaskRegistry) -> int:
        """
        Re-submit all PENDING jobs that have a ``task_payload`` using ``try_claim()``.

        Called at application startup to resume jobs that were in-flight when the
        process died.  For each PENDING job with a ``task_payload``:

        1. ``store.try_claim(job_id)`` — atomically claim PENDING → RUNNING.
        2. If claim succeeds, look up the task in ``registry``.
        3. Re-invoke the task with the stored args — scheduling a new asyncio.Task.
        4. If the task name is not in the registry, log a warning and leave the
           job in RUNNING state (it will not progress until manually resolved).

        **Distributed safety**: ``try_claim()`` ensures that even when multiple
        runner instances start concurrently (rolling restart, multi-replica), each
        PENDING job is claimed by exactly one runner.

        Args:
            registry: The ``TaskRegistry`` containing all recoverable tasks.
                      Must be populated (via ``VarcoCRUDRouter.build_router()``)
                      before ``recover()`` is called.

        Returns:
            The number of jobs successfully re-submitted.

        Edge cases:
            - No PENDING jobs with ``task_payload`` → returns 0, no-op.
            - ``try_claim()`` returns ``None`` → job was claimed by another instance; skip.
            - Task name not in registry → logs a warning; returns 0 for that job.
            - ``task_payload`` is ``None`` → job was submitted via legacy path; skip
              (no recovery possible without a serialized payload).

        Async safety:   ✅ All I/O is awaited.  Safe to call from ``startup`` lifespan.
        """

    @abstractmethod
    async def stop(self, *, timeout: float = 30.0) -> None:
        """
        Stop the runner, cancelling all in-flight tasks.  Idempotent.

        Args:
            timeout: Seconds to wait for in-flight tasks to finish after
                     cancellation.  Tasks that don't finish within timeout
                     are abandoned.

        Edge cases:
            - Calling ``stop()`` before ``start()`` is a silent no-op.
            - In-flight jobs are transitioned to FAILED with a cancellation
              message in the store so callers can observe the final state.
        """


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    "AbstractJobRunner",
    "AbstractJobStore",
    "Job",
    "JobStatus",
    "auth_context_from_snapshot",
    "auth_context_to_snapshot",
]

# VarcoTask / TaskRegistry are defined in task.py (same package) to avoid a
# circular import.  AbstractJobRunner references them via TYPE_CHECKING only.
# At runtime the method signatures use string literals ("VarcoTask", "TaskRegistry").
