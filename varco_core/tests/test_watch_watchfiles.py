"""
Tests for ``varco_core.watch.wfiles.WatchfilesWatcher`` (Plan 025 / T1b, Step 9).

RED phase: ``varco_core.watch.wfiles`` does not exist yet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("watchfiles")

from varco_core.watch.base import MissingWatchDependencyError, WatchTarget
from varco_core.watch.wfiles import WatchfilesWatcher

from tests.watch_contract import PathWatcherContract


class TestWatchfilesWatcher(PathWatcherContract):
    @pytest.fixture
    async def watcher(self, tmp_path: Path) -> WatchfilesWatcher:
        target = WatchTarget(root=tmp_path, patterns=("*",), recursive=True)
        return WatchfilesWatcher([target], quiet_period=0.05)


async def test_missing_dependency_raises_at_construction_time(monkeypatch) -> None:
    # The extra's install hint must be construction-time, not import-time (§D-T1-watchfiles).
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "watchfiles":
            raise ImportError("no module named watchfiles")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    target = WatchTarget(root=Path("/tmp"), patterns=("*",), recursive=True)
    with pytest.raises(MissingWatchDependencyError) as exc:
        WatchfilesWatcher([target])

    assert 'pip install "varco-core[watch]"' in str(exc.value)
