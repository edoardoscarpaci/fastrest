"""
Integration test: ``ReloadableResource`` wired to a ``StatPollWatcher`` (Plan 025 / T2, Step 13).

RED phase: neither ``varco_core.watch`` nor ``varco_core.reload`` exist yet.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from varco_core.reload import ReloadableResource
from varco_core.watch.base import WatchTarget
from varco_core.watch.poll import StatPollWatcher


async def _until(predicate, *, timeout: float = 3.0, interval: float = 0.02) -> None:
    """Bounded poll loop — never a bare asyncio.sleep (CLAUDE.md timing-flakiness rule)."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condition not met within timeout")


async def test_value_updates_within_bounded_wait_after_kubelet_symlink_swap(
    tmp_path: Path,
) -> None:
    gen1 = tmp_path / "..2026_01_01_00_00_00.000000000"
    gen1.mkdir()
    (gen1 / "ca.pem").write_text("gen1-content")
    (tmp_path / "..data").symlink_to(gen1.name)
    ca_link = tmp_path / "ca.pem"
    ca_link.symlink_to("..data/ca.pem")

    def load() -> str:
        return ca_link.read_text()

    target = WatchTarget(root=tmp_path, patterns=("*",), recursive=True)
    watcher = StatPollWatcher([target], interval=0.02, quiet_period=0.05)
    resource = ReloadableResource(loader=load, watcher=watcher, name="ca-bundle")

    await resource.start()
    try:
        assert resource.current == "gen1-content"

        gen2 = tmp_path / "..2026_01_02_00_00_00.000000000"
        gen2.mkdir()
        (gen2 / "ca.pem").write_text("gen2-content")
        tmp_link = tmp_path / "..data_tmp"
        tmp_link.symlink_to(gen2.name)
        os.replace(tmp_link, tmp_path / "..data")

        await _until(lambda: resource.current == "gen2-content")
    finally:
        await resource.stop()


async def test_mid_write_truncated_file_leaves_last_good_value_in_place(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cert.pem"
    path.write_text("good-cert-content")

    call_count = {"n": 0}

    def load() -> str:
        call_count["n"] += 1
        content = path.read_text()
        if content == "":
            raise ValueError("truncated file mid-write")
        return content

    target = WatchTarget(root=tmp_path, patterns=("*",), recursive=True)
    watcher = StatPollWatcher([target], interval=0.02, quiet_period=0.05)
    resource = ReloadableResource(loader=load, watcher=watcher, name="cert")

    await resource.start()
    try:
        assert resource.current == "good-cert-content"

        # Simulate a truncate-then-rewrite that briefly leaves the file empty.
        path.write_text("")
        await asyncio.sleep(0.3)

        assert resource.current == "good-cert-content"
    finally:
        await resource.stop()
