"""
varco_fastapi.job.poller
=========================
``JobPoller`` — background recovery for stale RUNNING jobs.

After a process restart, jobs that were in the RUNNING state never transition
to COMPLETED or FAILED — their asyncio.Tasks were lost but the store still
shows them as RUNNING.  ``JobPoller`` detects these and marks them FAILED.

This is only meaningful with persistent stores (``SaJobStore``,
``RedisJobStore``).  With ``InMemoryJobStore`` the store is empty after restart
so there are no stale jobs to recover.

Mirrors the ``OutboxRelay`` polling pattern from ``varco_core``:
    - ``start()`` spawns a background asyncio.Task
    - ``stop()`` cancels it and waits for shutdown
    - Poll interval and stale threshold are configurable

DESIGN: poller over locking / distributed coordination
    ✅ Simple — just a poll + mark-as-failed loop
    ✅ Stateless between polls — no coordination between instances needed
       (multiple pods may mark the same job failed, but that's idempotent)
    ✅ Configurable thresholds — tune for your expected job duration
    ❌ Not real-time — stale jobs persist until the next poll
    ❌ False positives if a job legitimately takes longer than stale_threshold
       → set stale_threshold conservatively (5-10x your p99 job duration)

Thread safety:  ✅ Uses asyncio.Task — runs in the event loop, no threads.
Async safety:   ✅ Background task cooperatively yields at each poll interval.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from varco_core.job.base import AbstractJobStore, JobStatus
from providify import Singleton, Inject

logger = logging.getLogger(__name__)


@Singleton
class JobPoller:
    """
    Background loop that marks stale RUNNING jobs as FAILED.

    Args:
        store:            ``AbstractJobStore`` to poll.
        stale_threshold:  Jobs in RUNNING status older than this are considered
                          stale (default: 5 minutes). Used as the fallback
                          check when ``lease_aware`` is ``True`` but the store
                          raises ``NotImplementedError`` from
                          ``reap_expired_leases`` (no lease support), and
                          always when ``lease_aware=False``.
        poll_interval:    Seconds between polls (default: 60.0).
        batch_size:       Maximum stale jobs to process per poll cycle (default: 100).
        lease_aware:      Plan 005 Phase 4 (U-11 §2). When ``True`` (default),
                          detect death via ``store.reap_expired_leases()`` —
                          a RUNNING job **holding an expired lease** is
                          returned to PENDING with a fenced ``lease_epoch``
                          instead of being marked FAILED by wall-clock age,
                          and a job holding a *live* lease is never touched
                          no matter how old ``started_at`` is.
                          The two signals are disjoint and both always run:
                          a RUNNING job that holds **no lease at all**
                          (``lease_expires_at is None``) is invisible to
                          lease reaping, so the wall-clock age threshold
                          still governs it exactly as it does today. That is
                          what keeps leases opt-in — enabling this flag
                          cannot reclassify unleased in-flight jobs.
                          Falls back to the age check alone when the store
                          raises ``NotImplementedError`` (no lease support).
        retention_sweep:  Plan 005 Phase 6 (U-18). When ``True``, each poll
                          tick also calls
                          ``store.delete_where(expires_before=now, limit=retention_batch_size)``
                          to retire jobs past their ``expires_at``. **Default
                          ``False``** — no existing deployment starts
                          deleting rows on upgrade; opt in explicitly once
                          you are setting ``expires_at`` on jobs. See
                          ``technical_docs/features/job-scheduling-and-leases.md``
                          for the chunked-sweep rationale (``retention_batch_size``
                          exists specifically to avoid pinning a pooled
                          connection for an unbounded sweep).
        retention_batch_size: ``limit`` passed to ``delete_where`` on each
                          retention sweep tick. Defaults to ``batch_size``.
                          Only relevant when ``retention_sweep=True``.

    Lifecycle::

        poller = JobPoller(store=store, stale_threshold=timedelta(minutes=10))
        await poller.start()
        # ... runs in background ...
        await poller.stop()

    Thread safety:  ✅ asyncio.Task — runs in event loop, not threads.
    Async safety:   ✅ Background task cooperatively sleeps between polls.

    Edge cases:
        - No stale jobs → poll completes immediately, sleeps until next cycle
        - ``stop()`` called before ``start()`` → no-op
        - Store raises during poll → error logged, loop continues on next cycle
    """

    def __init__(
        self,
        store: Inject[AbstractJobStore],
        *,
        stale_threshold: timedelta | None = None,
        poll_interval: float = 60.0,
        batch_size: int = 100,
        lease_aware: bool = True,
        retention_sweep: bool = False,
        retention_batch_size: int | None = None,
    ) -> None:
        self._store = store
        # 5-minute default is conservative — adjust to 10x your p99 job duration
        self._stale_threshold = stale_threshold or timedelta(minutes=5)
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._lease_aware = lease_aware
        # Plan 005 Phase 6 (U-18) — opt-in retention sweep. Default False:
        # no deployment starts deleting rows on upgrade.
        self._retention_sweep = retention_sweep
        self._retention_batch_size = retention_batch_size or batch_size
        self._task: asyncio.Task | None = None

    def __repr__(self) -> str:
        return (
            f"JobPoller("
            f"stale_threshold={self._stale_threshold}, "
            f"poll_interval={self._poll_interval}s)"
        )

    async def start(self) -> None:
        """
        Start the background polling task, running an immediate recovery pass first.

        On startup any job left in RUNNING state from a previous process
        (before a crash) is stuck — its asyncio.Task is gone but the store
        still shows it as RUNNING.  Calling ``_recover_stale_jobs()`` before the
        loop starts marks those jobs FAILED immediately rather than waiting up to
        ``poll_interval`` seconds for the first scheduled pass.

        DESIGN: eager recovery pass on start() over waiting for the first loop tick
            ✅ Stale jobs are visible as FAILED within milliseconds of startup,
               not up to ``poll_interval`` (default 60 s) later.
            ✅ Callers that check job status right after app startup get correct state.
            ❌ Adds one store query to the startup path — acceptable overhead.

        Calling ``start()`` multiple times is idempotent — the existing task is
        reused if it is still running.

        Thread safety:  ✅ asyncio.create_task is event-loop thread-safe.
        Async safety:   ✅ Safe to await from any async context.

        Edge cases:
            - With ``InMemoryJobStore`` the store is empty on restart so the recovery
              pass is a no-op (one cheap empty-list query).
            - If the recovery pass itself raises, the error is logged and the poller
              loop still starts — a failed recovery is better than no poller at all.
        """
        if self._task is not None and not self._task.done():
            # Already running — idempotent, don't create a second task
            return

        # Eagerly recover stale RUNNING jobs before the polling loop starts.
        # With persistent stores (SAJobStore, RedisJobStore) this is critical —
        # jobs stuck in RUNNING from a previous crash would otherwise appear
        # indefinitely stuck until the first scheduled poll cycle fires.
        try:
            await self._recover_stale_jobs()
        except Exception:
            # Recovery failure must never prevent the poller from starting
            logger.exception(
                "JobPoller: startup recovery pass failed; continuing anyway"
            )

        self._task = asyncio.create_task(
            self._poll_loop(),
            name="varco-job-poller",
        )
        logger.info(
            "JobPoller started (interval=%.1fs, stale_threshold=%s)",
            self._poll_interval,
            self._stale_threshold,
        )

    async def stop(self) -> None:
        """
        Stop the background polling task.

        Cancels the task and waits for it to finish.  Idempotent — safe to call
        if the poller was never started or has already stopped.

        Async safety:   ✅ Awaitable — waits for clean task shutdown.
        """
        if self._task is None or self._task.done():
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
        logger.info("JobPoller stopped")

    async def _poll_loop(self) -> None:
        """
        Main polling loop — runs until cancelled.

        Each iteration:
        1. Fetch RUNNING jobs older than ``stale_threshold``
        2. Mark each as FAILED with message ``"stale_job_timeout"``
        3. Sleep for ``poll_interval`` seconds

        Edge cases:
            - Store raises → log exception, continue on next cycle (don't crash)
            - CancelledError → propagate (clean shutdown)
        """
        while True:
            try:
                await self._recover_stale_jobs()
            except asyncio.CancelledError:
                # Clean shutdown — let the loop exit
                raise
            except Exception:
                logger.exception("JobPoller: unexpected error during poll; will retry")
            await asyncio.sleep(self._poll_interval)

    async def _recover_stale_jobs(self) -> None:
        """
        Identify and recover stale RUNNING jobs.

        Plan 005 Phase 4 (U-11 §2): when ``lease_aware`` is ``True``
        (default), both death signals run every tick over disjoint sets:

        - ``store.reap_expired_leases()`` — a RUNNING job **holding a lease
          that expired** (``lease_expires_at <= now``) is atomically
          returned to PENDING with a fenced ``lease_epoch``, so a stalled
          owner that resumes is rejected at its next write
          (``StaleLeaseError``). A job with a *live* lease is never touched
          here no matter how old ``started_at`` is.
        - ``_recover_stale_jobs_by_age()`` — a RUNNING job holding **no
          lease at all** (``lease_expires_at is None``) is invisible to
          lease reaping, so it is still governed by the wall-clock age
          threshold, exactly as it was pre-Phase-4. A ``NULL`` lease is not
          an expired lease, it is the absence of the signal
          ``reap_expired_leases`` reads — this is what keeps leases opt-in:
          enabling ``lease_aware`` cannot reclassify unleased in-flight jobs.

        Falls back to the wall-clock age check alone (today's exact
        pre-Phase-4 behaviour) when ``lease_aware=False``, or when the store
        raises ``NotImplementedError`` from ``reap_expired_leases`` (no
        lease support — an external ``AbstractJobStore`` that predates
        leases).

        Edge cases:
            - ``started_at=None`` for a RUNNING job (shouldn't happen) → skipped
              (age-threshold path only).
            - Store save/reap fails for a specific job → logged, remaining
              jobs processed.
        """
        if self._lease_aware:
            try:
                reaped = await self._store.reap_expired_leases(limit=self._batch_size)
            except NotImplementedError:
                logger.debug(
                    "JobPoller: store %r does not support leases — falling back "
                    "to wall-clock age threshold.",
                    self._store,
                )
            else:
                if reaped:
                    logger.info(
                        "JobPoller: reaped %d expired-lease RUNNING jobs to PENDING",
                        len(reaped),
                    )

        # Unleased RUNNING jobs are invisible to lease reaping, so the age
        # threshold still owns them — see the DESIGN block above.
        await self._recover_stale_jobs_by_age()

        if self._retention_sweep:
            await self._run_retention_sweep()

    async def _run_retention_sweep(self) -> None:
        """
        Retire jobs past their ``expires_at`` (Plan 005 Phase 6, U-18).

        Calls ``store.delete_where(expires_before=now, limit=retention_batch_size)``
        exactly once per poll tick — deliberately NOT looped to drain the
        entire backlog in one tick, so a large backlog is worked off
        gradually across ticks rather than blocking a single tick (or
        pinning a pooled connection) for an unbounded sweep. See
        ``AbstractJobStore.delete_where``'s docstring for the full
        chunked-sweep recipe if you need a faster one-shot drain (e.g. a
        maintenance script looping this call directly with a tight
        interval).

        Edge cases:
            - The store raising ``NotImplementedError``/any other exception
              is caught by the caller (``_recover_stale_jobs`` is itself
              called from within a broad ``try/except`` in ``_poll_loop``,
              and from an explicit ``try/except`` in ``start()``) — a
              sweep failure never crashes the poller.
        """
        now = datetime.now(UTC)
        deleted = await self._store.delete_where(
            expires_before=now,
            limit=self._retention_batch_size,
        )
        if deleted:
            logger.info("JobPoller: retention sweep deleted %d expired jobs", deleted)

    async def _recover_stale_jobs_by_age(self) -> None:
        """
        Mark RUNNING jobs older than ``stale_threshold`` as FAILED.

        This is today's exact pre-Phase-4 behaviour, used as the
        ``lease_aware=False`` path and as the automatic fallback when the
        store does not support leases.

        Edge cases:
            - ``started_at=None`` for a RUNNING job (shouldn't happen) → skipped
            - Store save fails for a specific job → logged, remaining jobs processed
        """
        now = datetime.now(UTC)
        stale_cutoff = now - self._stale_threshold

        running_jobs = await self._store.list_by_status(
            JobStatus.RUNNING,
            limit=self._batch_size,
        )

        stale_count = 0
        for job in running_jobs:
            # Skip jobs without started_at (defensive — should always be set)
            if job.started_at is None:
                continue
            # A job holding a lease is governed by its lease, never by age —
            # this is the U-11 §2 fix: a legitimately long-running job that
            # keeps renewing must never be killed for being old.
            if job.lease_expires_at is not None:
                continue
            # Skip recently started jobs — they may still be healthy
            if job.started_at >= stale_cutoff:
                continue

            # Mark as failed with a clear message for operators
            failed_job = job.as_failed(
                f"stale_job_timeout: job was RUNNING for more than {self._stale_threshold}"
            )
            try:
                await self._store.save(failed_job)
                stale_count += 1
            except Exception:
                logger.exception(
                    "JobPoller: failed to mark job %s as stale", job.job_id
                )

        if stale_count:
            logger.info("JobPoller: recovered %d stale RUNNING jobs", stale_count)


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    "JobPoller",
]
