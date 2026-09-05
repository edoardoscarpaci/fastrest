"""
varco_core.watch.poll
======================

``StatPollWatcher`` — the default, stdlib-only ``AbstractPathWatcher`` implementation.

DESIGN: polling first  (§D-T1-poll)
    ✅ Locked decision (``BACKLOG.md:34``). The evidence: inotify does **not** fire on
       NFS-mounted volumes (brief 001 §1, kernel-level limitation) and Kubernetes cert
       delivery is a symlink swap that an inotify consumer sees as ``IN_DELETE_SELF`` on a
       path it no longer watches. Certs arrive via exactly those two channels.
    ✅ A 5 s poll of a directory of ~10 certs is ~10 ``stat`` calls per 5 s — unmeasurable.
    ❌ Brief 001's Librarian's Note recommends *against* polling as a primary strategy ("slow
       and racy"). That recommendation is written for editor-driven dev-server reload, where
       latency is the product; it is explicitly qualified two sentences later by the
       Kubernetes exception. For a certificate whose renewal window is measured in days, a 5 s
       detection latency is not slow, and the "racy" objection is answered by §D-T1-debounce.
       This divergence from the brief is deliberate and is recorded here so it is not silently
       re-decided.

DESIGN: debounce/settle loop
    After the periodic poll first detects *any* diff against the last known-good snapshot, the
    watcher switches to a tighter settle loop: it re-snapshots every ``quiet_period`` seconds
    until two consecutive snapshots are identical, then notifies **once** with the accumulated
    diff between the pre-change baseline and the final, settled snapshot (§D-T1-debounce). A
    rotation that rewrites six files this way fires one callback, not six.

Async safety: ✅ Every snapshot is taken via ``_DirSnapshot.take()``, which itself runs off the
    event loop. ``OSError`` while snapshotting is caught, logged once per transition, and the
    watcher keeps polling with its last good snapshot (§D-T1-errors).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path

from varco_core.watch.base import AbstractPathWatcher, WatchTarget
from varco_core.watch.snapshot import _DirSnapshot

_Fingerprint = tuple[int, int, int]

logger = logging.getLogger(__name__)


class StatPollWatcher(AbstractPathWatcher):
    """
    Polls a set of ``WatchTarget`` roots on a fixed interval and diffs stat snapshots.

    Args:
        targets: The roots to watch.
        interval: Seconds between polls when idle (no in-flight change).
        quiet_period: Seconds a target must be stable before a settled batch is notified
            (also the interval used while settling — see module docstring).
        digest: Passed through to every ``_DirSnapshot.take()`` call — opt-in content
            hashing for the same-stat rewrite edge case (§D-T1-fingerprint).
    """

    def __init__(
        self,
        targets: Sequence[WatchTarget],
        *,
        interval: float = 5.0,
        quiet_period: float = 0.25,
        digest: bool = False,
    ) -> None:
        super().__init__(targets, quiet_period=quiet_period)
        self._interval = interval
        self._digest = digest
        self._last_snapshot: _DirSnapshot | None = None
        self._in_error = False

    async def start(self) -> None:
        """
        Start polling.

        DESIGN: baseline snapshot is taken *here*, synchronously, before the background task
        is scheduled — not as the first line of ``_run()``.
            ✅ ``asyncio.create_task()`` does not run the task body until the caller's coroutine
               next awaits. A caller that does ``await watcher.start(); write_file();
               await watcher.stop()`` with no intervening ``await`` would otherwise let the
               background task's baseline snapshot observe a file that was "added" before the
               watcher ever considered itself started — losing the very first event. Taking the
               baseline inside the awaited ``start()`` call closes that race.
        """
        if self._task is not None and not self._task.done():
            return
        self._last_snapshot = await self._safe_take()
        await super().start()

    async def _run(self) -> None:
        assert self._stop_event is not None  # set by AbstractPathWatcher.start()
        stop_event = self._stop_event

        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval)
                break  # stop() was called during the sleep
            except TimeoutError:
                pass  # normal poll tick

            if stop_event.is_set():
                break

            current = await self._safe_take()
            if current is None:
                continue  # OSError this tick — already logged, keep last snapshot

            assert self._last_snapshot is not None
            if not self._last_snapshot.diff(current):
                self._last_snapshot = current
                continue

            settled = await self._settle(current, stop_event)
            if settled is None:
                return  # stop() fired mid-settle
            events = self._last_snapshot.diff(settled)
            self._last_snapshot = settled
            self._notify(events)

    async def _settle(
        self, current: _DirSnapshot, stop_event: asyncio.Event
    ) -> _DirSnapshot | None:
        """Re-snapshot every ``quiet_period`` until two consecutive reads agree (or stop())."""
        stable = current
        while True:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._quiet_period)
                return None
            except TimeoutError:
                pass

            probe = await self._safe_take()
            if probe is None:
                continue
            if not stable.diff(probe):
                return probe
            stable = probe

    async def _safe_take(self) -> _DirSnapshot | None:
        """Wrap ``_DirSnapshot.take`` per §D-T1-errors: log once per error transition."""
        try:
            snapshots = [
                await _DirSnapshot.take(target, digest=self._digest) for target in self.targets
            ]
        except OSError:
            if not self._in_error:
                logger.warning(
                    "StatPollWatcher: error while stat-ing a watch target", exc_info=True
                )
                self._in_error = True
            return None

        self._in_error = False
        return _merge(snapshots)


def _merge(snapshots: Sequence[_DirSnapshot]) -> _DirSnapshot:
    """Combine per-target snapshots into one, for watchers observing multiple roots."""
    if len(snapshots) == 1:
        return snapshots[0]

    entries: dict[Path, _Fingerprint] = {}
    digests: dict[Path, bytes] = {}
    any_digest = False
    for snap in snapshots:
        entries.update(snap.entries)
        if snap.digests is not None:
            any_digest = True
            digests.update(snap.digests)
    return _DirSnapshot(
        target=snapshots[0].target,
        entries=entries,
        digests=digests if any_digest else None,
    )
