"""
Unit tests for ``varco_core.watch.snapshot._DirSnapshot`` (Plan 025 / T1, Step 3).

RED phase: ``varco_core.watch.snapshot`` does not exist yet. These tests must fail
with ImportError until the module is implemented.
"""

from __future__ import annotations

import os
from pathlib import Path

from varco_core.watch.base import WatchKind, WatchTarget

# This import is expected to fail (ImportError) until Plan 025 Phase 0 lands.
from varco_core.watch.snapshot import _DirSnapshot


def _target(root: Path, *, recursive: bool = True) -> WatchTarget:
    return WatchTarget(root=root, patterns=("*",), recursive=recursive)


async def test_diff_reports_added_file(tmp_path: Path) -> None:
    # A brand-new file must surface as a single ADDED event.
    target = _target(tmp_path)
    before = await _DirSnapshot.take(target)
    (tmp_path / "new.pem").write_text("hello")
    after = await _DirSnapshot.take(target)

    events = before.diff(after)

    assert len(events) == 1
    assert events[0].kind == WatchKind.ADDED
    assert events[0].path == (tmp_path / "new.pem").resolve()


async def test_diff_reports_removed_file(tmp_path: Path) -> None:
    # Deleting a previously-seen file must surface as REMOVED.
    (tmp_path / "old.pem").write_text("bye")
    target = _target(tmp_path)
    before = await _DirSnapshot.take(target)
    (tmp_path / "old.pem").unlink()
    after = await _DirSnapshot.take(target)

    events = before.diff(after)

    assert len(events) == 1
    assert events[0].kind == WatchKind.REMOVED


async def test_diff_reports_rewritten_file_as_modified(tmp_path: Path) -> None:
    # A rewrite that changes size must be detected even at the same mtime granularity.
    path = tmp_path / "cert.pem"
    path.write_text("a")
    target = _target(tmp_path)
    before = await _DirSnapshot.take(target)
    path.write_text("a much longer replacement body")
    after = await _DirSnapshot.take(target)

    events = before.diff(after)

    assert len(events) == 1
    assert events[0].kind == WatchKind.MODIFIED


async def test_diff_detects_same_size_rewrite_with_bumped_mtime(tmp_path: Path) -> None:
    # Same-size content edit is only caught because mtime_ns differs (fingerprint = 3 fields).
    path = tmp_path / "cert.pem"
    path.write_text("aaaa")
    target = _target(tmp_path)
    before = await _DirSnapshot.take(target)

    # Force a distinguishable mtime while keeping identical size.
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    path.write_text("bbbb")
    after = await _DirSnapshot.take(target)

    events = before.diff(after)

    assert len(events) == 1
    assert events[0].kind == WatchKind.MODIFIED


async def test_diff_detects_kubelet_data_symlink_swap(tmp_path: Path) -> None:
    # This is the core §D-T1-fingerprint claim: a k8s ..data symlink swap must be
    # visible via st_ino even though the visible file name never changes.
    gen1 = tmp_path / "..2026_01_01_00_00_00.000000000"
    gen1.mkdir()
    (gen1 / "ca.pem").write_text("gen1-content")

    data_link = tmp_path / "..data"
    data_link.symlink_to(gen1.name)

    ca_link = tmp_path / "ca.pem"
    ca_link.symlink_to("..data/ca.pem")

    target = _target(tmp_path)
    before = await _DirSnapshot.take(target)

    gen2 = tmp_path / "..2026_01_02_00_00_00.000000000"
    gen2.mkdir()
    (gen2 / "ca.pem").write_text("gen2-content")

    tmp_link = tmp_path / "..data_tmp"
    tmp_link.symlink_to(gen2.name)
    os.replace(tmp_link, data_link)

    after = await _DirSnapshot.take(target)

    events = before.diff(after)

    kinds = {e.kind for e in events}
    assert WatchKind.MODIFIED in kinds
    # The visible resolved name is ca.pem, and only one MODIFIED event fires for it.
    modified = [e for e in events if e.kind == WatchKind.MODIFIED]
    assert len(modified) == 1


async def test_take_skips_dotdot_prefixed_names(tmp_path: Path) -> None:
    # Enumeration must skip kubelet's own ..data / ..timestamp bookkeeping entries
    # as *named* entries (they are only followed via resolution of the real files).
    (tmp_path / "..hidden_bookkeeping").mkdir()
    target = _target(tmp_path)
    snap = await _DirSnapshot.take(target)

    assert not any("..hidden_bookkeeping" in str(p) for p in snap.paths())


async def test_dangling_symlink_is_treated_as_removed_not_raised(tmp_path: Path) -> None:
    # A dangling symlink must never raise; it disappears from the snapshot cleanly.
    real = tmp_path / "real.pem"
    real.write_text("x")
    link = tmp_path / "link.pem"
    link.symlink_to(real)

    target = _target(tmp_path)
    before = await _DirSnapshot.take(target)

    real.unlink()
    after = await _DirSnapshot.take(target)  # must not raise

    events = before.diff(after)
    assert any(e.kind == WatchKind.REMOVED for e in events)


async def test_non_recursive_enumeration_ignores_subdirectories(tmp_path: Path) -> None:
    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "deep.pem").write_text("x")
    (tmp_path / "top.pem").write_text("y")

    target = _target(tmp_path, recursive=False)
    snap = await _DirSnapshot.take(target)

    names = {p.name for p in snap.paths()}
    assert "top.pem" in names
    assert "deep.pem" not in names


async def test_recursive_enumeration_includes_subdirectories(tmp_path: Path) -> None:
    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "deep.pem").write_text("x")
    (tmp_path / "top.pem").write_text("y")

    target = _target(tmp_path, recursive=True)
    snap = await _DirSnapshot.take(target)

    names = {p.name for p in snap.paths()}
    assert "top.pem" in names
    assert "deep.pem" in names
