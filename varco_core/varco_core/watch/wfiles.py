"""
varco_core.watch.wfiles
========================

``WatchfilesWatcher`` — opt-in ``AbstractPathWatcher`` implementation backed by the
``watchfiles`` package (``varco-core[watch]`` extra, §D-T1-watchfiles).

DESIGN: construction-time, not import-time, dependency check
    ✅ ``varco_core.watch`` must remain importable without ``watchfiles`` installed — its
       ``__init__`` exports ``WatchfilesWatcher`` unconditionally so ``isinstance`` checks and
       type hints work everywhere, and only *constructing* one without the extra fails.
    ✅ Same precedent as ``varco_fastapi.connection.HttpConnectionSettings.to_trust_store()``:
       a function-body import (``# noqa: PLC0415``) deferred to the call site that actually
       needs the optional dependency.

DESIGN: still re-stats — never trusts watchfiles' own ``Change`` enum
    ⚠️ On every batch of raw filesystem events, this implementation recomputes the same
       ``_DirSnapshot`` fingerprint used by ``StatPollWatcher`` and emits ``WatchEvent``s from
       the *diff*, never from watchfiles' own ``Change.added``/``modified``/``deleted``. This is
       brief 001 §1's pitfall stated as code: the raw notification only says "something about
       this directory changed" (and on a kubelet ``..data`` swap, may even report the *wrong*
       kind, since the underlying inotify event is on the temporary symlink dance, not the
       resolved cert file) — the snapshot is what says *what actually changed*, using the exact
       same stable-key/resolved-fingerprint rule as the poll backend. It also means both
       implementations emit byte-identical event streams for identical filesystem changes, which
       is what makes one shared ``PathWatcherContract`` suite meaningful across both.

Async safety: ✅ ``awatch()`` is watchfiles' own async generator; iteration is cooperative and
    stops cleanly via its ``stop_event=`` kwarg, which ``AbstractPathWatcher.stop()`` sets.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path

from varco_core.watch.base import AbstractPathWatcher, MissingWatchDependencyError, WatchTarget
from varco_core.watch.snapshot import _DirSnapshot

_Fingerprint = tuple[int, int, int]

logger = logging.getLogger(__name__)

_INSTALL_HINT = 'pip install "varco-core[watch]"'


class WatchfilesWatcher(AbstractPathWatcher):
    """
    ``AbstractPathWatcher`` backed by the Rust-``notify``-powered ``watchfiles`` library.

    Args:
        targets: The roots to watch.
        quiet_period: Seconds a target must be stable before a settled batch is notified —
            same contract as ``StatPollWatcher`` (§D-T1-debounce).

    Raises:
        MissingWatchDependencyError: At construction time (not import time) if ``watchfiles``
            is not installed. Install with ``pip install "varco-core[watch]"``.
    """

    def __init__(self, targets: Sequence[WatchTarget], *, quiet_period: float = 0.25) -> None:
        try:
            import watchfiles  # noqa: PLC0415 — deferred: the extra is opt-in, see module docstring
        except ImportError as exc:
            raise MissingWatchDependencyError(
                f'varco_core.watch.WatchfilesWatcher requires the "watch" extra. '
                f"Install it with: {_INSTALL_HINT}"
            ) from exc

        super().__init__(targets, quiet_period=quiet_period)
        self._watchfiles = watchfiles
        self._last_snapshot: _DirSnapshot | None = None

    async def start(self) -> None:
        """
        Start watching.

        Baseline snapshot is taken here — see ``StatPollWatcher.start()`` for why the baseline
        must be captured before ``start()`` returns, not as the first line of the background
        task (identical race, identical fix).

        DESIGN: one extra ``asyncio.sleep(0)`` after creating the background task
            ✅ ``watchfiles.awatch()`` is an async generator whose synchronous prefix — the
               construction of the Rust-side ``RustNotify`` watch, i.e. the point the OS-level
               watch actually becomes armed — only runs on the *first* ``__anext__()``, which
               only happens once the event loop dispatches our new task for its first step.
               ``asyncio.create_task()`` merely schedules that; it does not run it. Without
               this yield, a caller doing ``await watcher.start(); write_file()`` with no
               intervening ``await`` races the OS-level watch arming and reliably misses the
               write — confirmed against raw ``watchfiles.awatch()`` with no wrapper at all.
            ✅ One ``sleep(0)`` is enough: Python runs a task's synchronous prefix in a single
               scheduler step, and that prefix reaches (and completes) ``RustNotify()``
               construction before the coroutine's first real suspension point (the
               thread-pool bridge that actually waits for a change).
        """
        if self._task is not None and not self._task.done():
            return
        self._last_snapshot = await self._merged_snapshot()
        await super().start()
        await asyncio.sleep(0)  # let the background task run up to RustNotify() arming

    async def _run(self) -> None:
        assert self._stop_event is not None  # set by AbstractPathWatcher.start()
        stop_event = self._stop_event
        roots = [str(target.root) for target in self.targets if target.root.exists()]

        if not roots:
            # Nothing to watch yet (e.g. a cert volume not mounted at start()) — fall back to
            # waiting on stop_event only; a real root appearing is a StatPollWatcher-only
            # guarantee for now (documented Edge case, same as the poll backend's own).
            await stop_event.wait()
            return

        # watchfiles' own `debounce`/`step` are about *event batching*, not "the writer has
        # finished" (§D-T1-debounce) — but its 1600ms default debounce would still add up to
        # 1.6s of latency before we even see the first raw notification, on top of our own
        # settle loop below. Derive it from quiet_period so the two debounce layers agree.
        debounce_ms = max(1, int(self._quiet_period * 1000))
        step_ms = max(1, min(50, debounce_ms))
        async for _changes in self._watchfiles.awatch(
            *roots, stop_event=stop_event, debounce=debounce_ms, step=step_ms
        ):
            if stop_event.is_set():
                break
            settled = await self._settle(stop_event)
            if settled is None:
                return
            assert self._last_snapshot is not None
            events = self._last_snapshot.diff(settled)
            self._last_snapshot = settled
            self._notify(events)

    async def _settle(self, stop_event: asyncio.Event) -> _DirSnapshot | None:
        """Re-snapshot every ``quiet_period`` until two consecutive reads agree (or stop())."""
        stable = await self._merged_snapshot()
        while True:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._quiet_period)
                return None
            except TimeoutError:
                pass
            probe = await self._merged_snapshot()
            if not stable.diff(probe):
                return probe
            stable = probe

    async def _merged_snapshot(self) -> _DirSnapshot:
        snapshots = [await _DirSnapshot.take(target) for target in self.targets]
        if len(snapshots) == 1:
            return snapshots[0]
        entries: dict[Path, _Fingerprint] = {}
        for snap in snapshots:
            entries.update(snap.entries)
        return _DirSnapshot(target=snapshots[0].target, entries=entries)
