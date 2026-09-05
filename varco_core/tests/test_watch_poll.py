"""
Tests for ``varco_core.watch.poll.StatPollWatcher`` (Plan 025 / T1, Step 6).

RED phase: ``varco_core.watch.poll`` does not exist yet.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from varco_core.watch.base import WatchTarget
from varco_core.watch.poll import StatPollWatcher

from tests.watch_contract import PathWatcherContract, _until


class TestStatPollWatcher(PathWatcherContract):
    @pytest.fixture
    async def watcher(self, tmp_path: Path) -> StatPollWatcher:
        target = WatchTarget(root=tmp_path, patterns=("*",), recursive=True)
        return StatPollWatcher([target], interval=0.02, quiet_period=0.05)


async def test_root_directory_deleted_and_recreated_mid_run(tmp_path: Path) -> None:
    # §D-T1-errors: OSError while stat-ing must be logged and swallowed, watcher keeps polling.
    root = tmp_path / "certs"
    root.mkdir()
    (root / "a.pem").write_text("x")
    target = WatchTarget(root=root, patterns=("*",), recursive=True)
    watcher = StatPollWatcher([target], interval=0.02, quiet_period=0.05)

    events = []
    watcher.subscribe(events.append)
    await watcher.start()
    try:
        shutil.rmtree(root)
        await asyncio.sleep(0.3)  # watcher must not crash
        root.mkdir()
        (root / "b.pem").write_text("y")
        await _until(lambda: len(events) >= 1, timeout=3.0)
    finally:
        await watcher.stop()


async def test_digest_true_detects_same_stat_content_rewrite(tmp_path: Path) -> None:
    # Documents the §D-T1-fingerprint limitation: digest=False MISSES a same-mtime_ns/
    # same-size/same-inode content edit; digest=True catches it.
    path = tmp_path / "cert.pem"
    path.write_text("AAAA")
    target = WatchTarget(root=tmp_path, patterns=("*",), recursive=True)

    watcher_plain = StatPollWatcher([target], interval=0.02, quiet_period=0.05, digest=False)
    watcher_digest = StatPollWatcher([target], interval=0.02, quiet_period=0.05, digest=True)

    plain_events = []
    digest_events = []
    watcher_plain.subscribe(plain_events.append)
    watcher_digest.subscribe(digest_events.append)
    await watcher_plain.start()
    await watcher_digest.start()
    try:
        st = path.stat()
        # Overwrite same size, same mtime_ns, same inode (in-place write, no truncate/rename).
        with path.open("r+b") as f:
            f.write(b"BBBB")
        import os

        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))

        await asyncio.sleep(0.5)
        assert plain_events == []  # documents the miss
        await _until(lambda: len(digest_events) >= 1, timeout=3.0)
    finally:
        await watcher_plain.stop()
        await watcher_digest.stop()
