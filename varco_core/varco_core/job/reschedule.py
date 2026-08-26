"""
varco_core.job.reschedule
============================
``ScheduleRematerializer`` — Plan 011 T2's opt-in recompute-on-read
component (brief 004 §A3).

``run_at`` is written **once**, at enqueue, under whichever pod's tzdata
was current then. This component sweeps pending zoned jobs inside a
bounded horizon, recomputes ``run_at`` from ``(run_at_wall, run_at_tz,
run_at_fold)`` under **current** tzdata, and writes back only when the
value actually changed, fenced with ``save(expected_epoch=...)``.

Default ``interval=0.0`` = **not started** — byte-identical to not using
this feature (RD-1).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone, UTC
from typing import TYPE_CHECKING

from varco_core.job.base import StaleLeaseError
from varco_core.tz.schedule import resolve_zoned

if TYPE_CHECKING:
    from varco_core.job.base import AbstractJobStore, Job

logger = logging.getLogger(__name__)

__all__ = ["ScheduleRematerializer"]


class ScheduleRematerializer:
    """
    Args:
        store: The ``AbstractJobStore`` to sweep.
        interval: Seconds between sweeps. ``0.0`` (default) — ``start()``
            spawns no background task at all.
        horizon: Only jobs whose ``run_at`` falls within this window from
            now are swept — re-materializing a job scheduled years out on
            every pass is pointless; the interesting window is "jobs about
            to fire".

    Async safety: ✅ ``asyncio.Lock``/task are created lazily inside
        ``start()``, never at ``__init__``/module scope.
    """

    def __init__(
        self,
        store: AbstractJobStore,
        *,
        interval: float = 0.0,
        horizon: timedelta = timedelta(hours=48),
    ) -> None:
        self._store = store
        self._interval = interval
        self._horizon = horizon
        self._task: asyncio.Task | None = None
        self._stopped = False

    async def start(self) -> None:
        """Start the periodic sweep. No-op (no task created) when
        ``interval <= 0.0`` — RD-1's byte-identical-by-default guarantee."""
        if self._interval <= 0.0:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        """Cancel the periodic sweep, if one was started."""
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_forever(self) -> None:
        while not self._stopped:
            try:
                await self.sweep_once()
            except Exception:
                logger.exception("ScheduleRematerializer sweep failed")
            await asyncio.sleep(self._interval)

    async def sweep_once(self) -> int:
        """
        Run one sweep. Returns the number of jobs actually re-materialized
        (writes only happen on an actual change).
        """
        now = datetime.now(UTC)
        before = now + self._horizon
        candidates = await self._store.list_pending_zoned(before)

        changed = 0
        for job in candidates:
            changed += await self._reconcile_one(job)
        return changed

    async def _reconcile_one(self, job: Job) -> int:
        if job.run_at_tz is None or job.run_at_wall is None:
            return 0

        from zoneinfo import ZoneInfo

        zone = ZoneInfo(job.run_at_tz)
        new_run_at = resolve_zoned(
            job.run_at_wall, zone, fold=job.run_at_fold
        ).astimezone(UTC)

        if job.run_at is not None and new_run_at == job.run_at:
            return 0

        import dataclasses

        updated = dataclasses.replace(job, run_at=new_run_at)
        try:
            await self._store.save(updated, expected_epoch=job.lease_epoch)
        except StaleLeaseError:
            # The job was claimed between our read and this write — it is
            # executing right now and must not have its schedule rewritten
            # underneath the worker. Skip, don't raise.
            logger.debug(
                "ScheduleRematerializer: job %s was claimed mid-sweep; skipping",
                job.job_id,
            )
            return 0

        logger.info(
            "ScheduleRematerializer: re-materialized job %s run_at %s -> %s",
            job.job_id,
            job.run_at,
            new_run_at,
        )
        return 1
