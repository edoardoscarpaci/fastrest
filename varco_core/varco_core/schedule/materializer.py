"""
varco_core.schedule.materializer
===================================
``ScheduleMaterializer`` — turns a ``Schedule`` + "now" into zero or more
``Job`` rows (Plan 032 / D6, §D-D6-cron).

This is the design the zoned ``Job`` fields (``run_at_wall``/``run_at_tz``/
``run_at_fold``, ``varco_core/varco_core/job/base.py:258-273``) were added
for: the materializer computes the next wall-clock occurrence from the
cron expression in the schedule's timezone, resolves it to a UTC instant
via ``resolve_zoned()`` (DST-safe by construction), and stores both the
materialization (``run_at``) and the intent (the three zoned fields).

DESIGN: in-process per-schedule lock, not a job-store-held lease, for
in-process double-materialization safety
    The plan's design narrative floated reusing the job store's fenced-lease
    primitives (``try_claim``/``save(expected_epoch=)``) as the mutual-
    exclusion mechanism. That does not fit here: a lease needs a persisted
    row to hold it, and any row saved through ``AbstractJobStore.save()``
    is indistinguishable from a real job to every caller of
    ``list_by_status()``/``delete_where()`` — a synthetic "schedule lock"
    row would corrupt every job-count invariant downstream code relies on.
    Instead:
    ✅ Occurrence ``Job.job_id`` is **deterministic** — ``uuid5`` of
       ``(schedule_id, wall)``. Two materializers computing the same
       occurrence converge on the same physical row (an idempotent upsert
       via ``AbstractJobStore.save()``'s own documented semantics), not two
       rows — this is the real cross-process backstop, reinforced by the
       ``UNIQUE(schedule_id, run_at)`` index on the SA/Beanie repositories
       (Step 10).
    ✅ A lazily-created, per-schedule ``asyncio.Lock`` (module-level
       registry, keyed by ``schedule_id`` — never a ``Lock()`` constructed
       at import time or in ``__init__``, per CLAUDE.md's rule) serializes
       the whole "compute occurrences → check-existing → save" section
       **within one process**, closing the check-then-create race a bare
       idempotent id alone would still leave open for two materializers
       racing inside the same event loop.
    ❌ The lock only coordinates one process — a genuinely distributed
       double-run still relies on the deterministic id + DB unique index,
       not this lock. Accepted: that is exactly what the unique index is
       for, and duplicating its job in Python would be the "second locking
       model" the plan explicitly warns against.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from varco_core.job.base import AbstractJobStore, Job
from varco_core.schedule.cron import parse_cron
from varco_core.schedule.entity import CatchUpPolicy, Schedule
from varco_core.tz.schedule import resolve_zoned

__all__ = ["ScheduleMaterializer"]

# Module-level registry of per-schedule locks. The dict itself is a plain
# mutable container (fine at module scope); each asyncio.Lock inside it is
# still created lazily, inside a running event loop, the first time that
# schedule_id is materialized — see _lock_for().
_SCHEDULE_LOCKS: dict[str, asyncio.Lock] = {}


def _lock_for(schedule_id: UUID) -> asyncio.Lock:
    """Return the shared, lazily-created lock for one schedule_id."""
    key = str(schedule_id)
    lock = _SCHEDULE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _SCHEDULE_LOCKS[key] = lock
    return lock


def _occurrence_job_id(schedule_id: UUID, wall: datetime) -> UUID:
    """Deterministic job id for one (schedule, occurrence) pair."""
    return uuid5(NAMESPACE_URL, f"varco:schedule:{schedule_id}:{wall.isoformat()}")


class ScheduleMaterializer:
    """
    Materializes a ``Schedule``'s due occurrence(s) into ``Job`` rows.

    Args:
        job_store: The same ``AbstractJobStore`` the ``AbstractJobRunner``
            claims jobs from — materialized jobs are ordinary ``PENDING``
            jobs, run exactly like any other (§Non-goals).

    Thread safety:  ✅ Safe to share one instance across schedules — the
                       only mutable state is the module-level lock registry,
                       itself lazily populated and keyed per schedule.
    Async safety:   ✅ All I/O is awaited; ``asyncio.Lock`` created lazily.
    """

    def __init__(self, *, job_store: AbstractJobStore) -> None:
        self._job_store = job_store

    async def materialize(self, schedule: Schedule, *, now: datetime | None = None) -> list[Job]:
        """
        Materialize ``schedule``'s due occurrence(s) as of ``now``.

        Args:
            schedule: The schedule to evaluate. Never mutated.
            now: The "current" instant, timezone-aware. Defaults to
                ``datetime.now(UTC)``. Exposed for deterministic testing.

        Returns:
            The list of newly created ``Job`` rows (empty if the schedule is
            disabled, not yet due, or every due occurrence was already
            materialized by a concurrent caller).

        Edge cases:
            - A disabled schedule always returns ``[]`` without even parsing
              ``cron_expr``.
            - Concurrent calls for the *same* ``schedule.schedule_id``
              produce exactly one job per occurrence — see the module
              DESIGN note.
        """
        if not schedule.enabled:
            return []

        current = now if now is not None else datetime.now(UTC)

        async with _lock_for(schedule.schedule_id):
            occurrences = self._compute_occurrences(schedule, current)
            jobs: list[Job] = []
            for wall in occurrences:
                job = self._build_job(schedule, wall)
                if await self._job_store.get(job.job_id) is not None:
                    # Already materialized by a prior/concurrent call whose
                    # lock section overlapped this one under a different
                    # process — deterministic id makes this a no-op, never
                    # a duplicate.
                    continue
                await self._job_store.save(job)
                jobs.append(job)
            return jobs

    def _compute_occurrences(self, schedule: Schedule, now: datetime) -> list[datetime]:
        """
        Return the due occurrence(s) (naive wall-clock, in ``schedule``'s
        timezone) per ``schedule.catchup_policy``.

        Args:
            schedule: The schedule being evaluated.
            now: Aware "current" instant.

        Returns:
            Zero or more naive wall-clock datetimes, earliest first, all
            ``<= now`` (converted into ``schedule``'s timezone).
        """
        cron = parse_cron(schedule.cron_expr)
        zone = ZoneInfo(schedule.timezone)
        now_wall = now.astimezone(zone).replace(tzinfo=None)
        lower_bound = self._as_naive_wall(schedule.last_materialized_at, zone)

        if schedule.catchup_policy == CatchUpPolicy.SKIP:
            latest = cron.at_or_before(now_wall)
            if latest is None:
                return []
            if lower_bound is not None and latest <= lower_bound:
                return []
            return [latest]

        if schedule.catchup_policy == CatchUpPolicy.FIRE_ONCE:
            candidate: datetime | None
            if lower_bound is not None:
                candidate = cron.next_after(lower_bound)
            else:
                candidate = cron.at_or_before(now_wall)
            if candidate is not None and candidate <= now_wall:
                return [candidate]
            return []

        # BACKFILL_ALL — every missed occurrence, oldest first, bounded.
        occurrences: list[datetime] = []
        cursor = lower_bound
        if cursor is None:
            first = cron.at_or_before(now_wall)
            if first is None:
                return []
            occurrences.append(first)
            cursor = first
        max_total = max(schedule.max_backfill, 0)
        while len(occurrences) < max_total:
            nxt = cron.next_after(cursor)
            if nxt > now_wall:
                break
            occurrences.append(nxt)
            cursor = nxt
        return occurrences[:max_total]

    @staticmethod
    def _as_naive_wall(value: datetime | None, zone: ZoneInfo) -> datetime | None:
        """Normalize a stored timestamp (aware or naive) into ``zone``'s
        naive wall-clock representation, or ``None`` if unset."""
        if value is None:
            return None
        if value.tzinfo is not None:
            return value.astimezone(zone).replace(tzinfo=None)
        return value

    @staticmethod
    def _build_job(schedule: Schedule, wall: datetime) -> Job:
        """
        Build the ``Job`` for one due occurrence.

        Args:
            schedule: The owning schedule.
            wall: The occurrence's naive wall-clock time, already resolved
                by ``_compute_occurrences`` — NOT yet DST-resolved.

        Returns:
            A ``PENDING`` ``Job`` with ``run_at`` materialized via
            ``resolve_zoned()`` and ``run_at_wall``/``run_at_tz``/
            ``run_at_fold`` carrying the original intent.
        """
        zone = ZoneInfo(schedule.timezone)
        resolved = resolve_zoned(
            wall,
            zone,
            gap=schedule.gap_policy,
            overlap=schedule.overlap_policy,
        )
        return Job(
            job_id=_occurrence_job_id(schedule.schedule_id, wall),
            run_at=resolved.astimezone(UTC),
            run_at_wall=wall,
            run_at_tz=schedule.timezone,
            run_at_fold=resolved.fold,
            callback_url=schedule.callback_url,
            metadata={"schedule_id": str(schedule.schedule_id), **schedule.payload},
        )
