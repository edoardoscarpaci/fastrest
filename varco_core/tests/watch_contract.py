"""
Shared behavioural contract for ``AbstractPathWatcher`` implementations (Plan 025 / T1, Step 4).

Same naming discipline as ``testkit/varco_conformance``: this base class is deliberately
NOT ``Test*``-prefixed, so pytest never collects it standalone — a subclass that fails to
override the ``watcher`` fixture fails loudly with ``NotImplementedError`` instead of
silently passing.

RED phase: ``varco_core.watch`` does not exist yet — this module fails to import.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from varco_core.watch.base import AbstractPathWatcher, WatchEvent, WatchKind


async def _until(predicate, *, timeout: float = 2.0, interval: float = 0.02) -> None:
    """Bounded poll loop — never a bare asyncio.sleep (CLAUDE.md timing-flakiness rule)."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condition not met within timeout")


class PathWatcherContract:
    """Abstract base — subclass and override ``watcher`` to run this suite against a backend."""

    @pytest.fixture
    async def watcher(self, tmp_path: Path) -> AbstractPathWatcher:
        raise NotImplementedError("subclasses must override the `watcher` fixture")

    async def test_single_added_file_produces_one_event(
        self, watcher: AbstractPathWatcher, tmp_path: Path
    ) -> None:
        events: list[WatchEvent] = []
        watcher.subscribe(events.append)
        await watcher.start()
        try:
            (tmp_path / "new.pem").write_text("x")
            await _until(lambda: len(events) >= 1)
            assert events[0].kind == WatchKind.ADDED
        finally:
            await watcher.stop()

    async def test_three_rapid_writes_coalesce_into_one_notification(
        self, watcher: AbstractPathWatcher, tmp_path: Path
    ) -> None:
        # §D-T1-debounce: a rotation rewriting several files fires ONE callback, not several.
        batches: list[tuple[WatchEvent, ...]] = []
        watcher.subscribe(lambda evs=None: None)  # noop subscriber to prove multi-subscriber safety
        watcher.subscribe(
            lambda evs: batches.append(tuple(evs)) if isinstance(evs, (list, tuple)) else None
        )
        await watcher.start()
        try:
            (tmp_path / "a.pem").write_text("1")
            (tmp_path / "b.pem").write_text("2")
            (tmp_path / "c.pem").write_text("3")
            await _until(lambda: len(batches) >= 1, timeout=3.0)
            await asyncio.sleep(0.3)  # ensure no further, later batch trickles in
            assert len(batches) == 1
            assert len(batches[0]) == 3
        finally:
            await watcher.stop()

    async def test_kubelet_symlink_swap_produces_exactly_one_modified(
        self, watcher: AbstractPathWatcher, tmp_path: Path
    ) -> None:
        gen1 = tmp_path / "..2026_01_01_00_00_00.000000000"
        gen1.mkdir()
        (gen1 / "ca.pem").write_text("gen1")
        (tmp_path / "..data").symlink_to(gen1.name)
        (tmp_path / "ca.pem").symlink_to("..data/ca.pem")

        events: list[WatchEvent] = []
        watcher.subscribe(events.append)
        await watcher.start()
        try:
            gen2 = tmp_path / "..2026_01_02_00_00_00.000000000"
            gen2.mkdir()
            (gen2 / "ca.pem").write_text("gen2")
            tmp_link = tmp_path / "..data_tmp"
            tmp_link.symlink_to(gen2.name)
            import os

            os.replace(tmp_link, tmp_path / "..data")

            await _until(lambda: len(events) >= 1)
            modified = [e for e in events if e.kind == WatchKind.MODIFIED]
            assert len(modified) == 1
        finally:
            await watcher.stop()

    async def test_removed_on_unlink(self, watcher: AbstractPathWatcher, tmp_path: Path) -> None:
        path = tmp_path / "gone.pem"
        path.write_text("x")
        events: list[WatchEvent] = []
        watcher.subscribe(events.append)
        await watcher.start()
        try:
            path.unlink()
            await _until(lambda: len(events) >= 1)
            assert events[0].kind == WatchKind.REMOVED
        finally:
            await watcher.stop()

    async def test_raising_subscriber_does_not_block_other_subscribers(
        self, watcher: AbstractPathWatcher, tmp_path: Path
    ) -> None:
        # §D-T1-errors: one bad consumer never stops notification for the others.
        good_events: list[WatchEvent] = []

        def bad_cb(_events: object) -> None:
            raise RuntimeError("boom")

        watcher.subscribe(bad_cb)
        watcher.subscribe(good_events.append)
        await watcher.start()
        try:
            (tmp_path / "x.pem").write_text("x")
            await _until(lambda: len(good_events) >= 1)
        finally:
            await watcher.stop()

    async def test_stop_is_idempotent(self, watcher: AbstractPathWatcher) -> None:
        await watcher.start()
        await watcher.stop()
        await watcher.stop()  # must not raise

    async def test_start_twice_is_a_noop(self, watcher: AbstractPathWatcher) -> None:
        await watcher.start()
        try:
            await watcher.start()  # must not raise or spawn a second task
        finally:
            await watcher.stop()
