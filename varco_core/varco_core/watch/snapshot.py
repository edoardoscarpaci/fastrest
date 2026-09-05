"""
varco_core.watch.snapshot
==========================

``_DirSnapshot`` — the stat-fingerprint snapshot both ``StatPollWatcher`` and
``WatchfilesWatcher`` diff against each other to produce ``WatchEvent``s. Internal:
not exported from ``varco_core.watch.__init__`` (§D-T1-shape's public surface table).

DESIGN: three-field stat fingerprint over bare mtime  (§D-T1-fingerprint)
    This generalises ``PemFolderSource._has_changes()``
    (``varco_core/varco_core/authority/sources/pem_folder.py``), which snapshots
    ``{p: p.stat().st_mtime for p in self._path.glob("*.pem")}`` and compares dicts. That code
    is the proof the repo already needed this; it is also exactly the code that would *miss* a
    K8s rotation.
    ✅ ``st_ino`` is what makes the ``..data`` symlink swap visible: the name is unchanged and
       the mtime of the *symlink* may be unchanged, but the resolved target is a different
       inode (brief 001 §1).
    ✅ ``st_size`` closes the "atomic rename within the same mtime granularity" window that a
       mtime-only snapshot leaves open. ``st_mtime_ns`` (not ``st_mtime``) removes float
       rounding.
    ✅ Cheap — ``os.stat`` only, no reads. Runs in ``asyncio.to_thread()``, the pattern already
       used at ``pem_folder.py`` with the comment "run in thread to avoid blocking the loop on
       slow filesystems (NFS, etc.)".
    ❌ A content edit that preserves all three fields is not detected. Documented as an Edge
       case; the mitigation is an opt-in ``digest=True`` mode (``hashlib.blake2b`` of each
       file) for paranoid callers, off by default because it reads every file on every poll.
    ❌ ``st_ino`` is meaningless on some filesystems. Linux-only cycle — no claim is made for
       others.

DESIGN: key by the *unresolved* path, fingerprint the *resolved* target
    A dict key of ``root.resolve() / relative_name`` stays identical across a kubelet ``..data``
    swap (the visible name never changes), while the fingerprint — taken via
    ``Path.stat()``, which follows symlinks — picks up the new inode. Keying by the fully
    *resolved* path instead (following the leaf symlink too) would make gen1's ``ca.pem`` and
    gen2's ``ca.pem`` two different dict keys — one REMOVED, one ADDED — which is exactly the
    "watcher sees only ``IN_DELETE_SELF``" failure mode this whole subsystem exists to avoid.

Entries whose **name** begins with ``..`` are skipped when enumerating (that is kubelet's
``..data`` / ``..2026_01_02_..."`` bookkeeping); enumeration also never descends into a
``..``-prefixed directory. A dangling symlink's ``stat()`` raises ``OSError`` — caught and
treated as "not present", never propagated (a dangling symlink is not a crash, it is a
mid-rotation transient).

Async safety: ✅ ``take()`` runs the blocking walk/stat work in ``asyncio.to_thread()``.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import os
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from varco_core.watch.base import WatchEvent, WatchKind, WatchTarget

# (mtime_ns, size, ino)
_Fingerprint = tuple[int, int, int]


@dataclass(frozen=True)
class _DirSnapshot:
    """A point-in-time fingerprint of every matching file under a ``WatchTarget``."""

    target: WatchTarget
    entries: Mapping[Path, _Fingerprint] = field(default_factory=dict)
    digests: Mapping[Path, bytes] | None = None

    @classmethod
    async def take(cls, target: WatchTarget, *, digest: bool = False) -> _DirSnapshot:
        """
        Build a snapshot of ``target`` off the event loop.

        Args:
            target: The root/patterns/recursive spec to snapshot.
            digest: When ``True``, also blake2b-hashes each file's bytes — catches a
                same-mtime_ns/same-size/same-inode in-place rewrite that the plain
                fingerprint misses. Off by default: it reads every file on every call.

        Returns:
            A new, immutable ``_DirSnapshot``.

        Edge cases:
            - ``target.root`` does not exist → empty snapshot, no exception (mirrors
              ``os.walk``'s own default behaviour of silently yielding nothing for a
              missing top).
            - A dangling symlink → excluded from the snapshot entirely (not an exception).
        """
        return await asyncio.to_thread(cls._take_sync, target, digest)

    @classmethod
    def _take_sync(cls, target: WatchTarget, digest: bool) -> _DirSnapshot:
        entries: dict[Path, _Fingerprint] = {}
        digests: dict[Path, bytes] | None = {} if digest else None
        root_resolved = target.root.resolve() if target.root.exists() else target.root

        for real_path, key_path in _iter_paths(target):
            try:
                st = real_path.stat()  # follows symlinks — this is what sees the new inode
            except OSError:
                continue  # dangling symlink / raced-away file — treated as absent, not a crash
            key = root_resolved / key_path
            entries[key] = (st.st_mtime_ns, st.st_size, st.st_ino)
            if digests is not None:
                try:
                    digests[key] = hashlib.blake2b(real_path.read_bytes()).digest()
                except OSError:
                    pass  # same race as above — best-effort, never fatal

        return cls(target=target, entries=entries, digests=digests)

    def paths(self) -> Iterator[Path]:
        """Every path currently in this snapshot."""
        return iter(self.entries)

    def diff(self, other: _DirSnapshot) -> tuple[WatchEvent, ...]:
        """
        Compute the ``WatchEvent``s that transform ``self`` into ``other``.

        Returns:
            A tuple of events, sorted by path for deterministic ordering. Empty if nothing
            changed.
        """
        now = time.monotonic()
        events: list[WatchEvent] = []

        self_keys = set(self.entries)
        other_keys = set(other.entries)

        for path in sorted(other_keys - self_keys, key=str):
            events.append(WatchEvent(path=path, kind=WatchKind.ADDED, detected_at=now))
        for path in sorted(self_keys - other_keys, key=str):
            events.append(WatchEvent(path=path, kind=WatchKind.REMOVED, detected_at=now))
        for path in sorted(self_keys & other_keys, key=str):
            if self.entries[path] != other.entries[path] or self._digest_differs(other, path):
                events.append(WatchEvent(path=path, kind=WatchKind.MODIFIED, detected_at=now))

        return tuple(events)

    def _digest_differs(self, other: _DirSnapshot, path: Path) -> bool:
        if self.digests is None or other.digests is None:
            return False
        return self.digests.get(path) != other.digests.get(path)


def _iter_paths(target: WatchTarget) -> Iterator[tuple[Path, Path]]:
    """
    Yield ``(real_path, relative_key_path)`` pairs for every matching file under ``target``.

    ``real_path`` is the path to actually ``stat()``/read (relative to the unresolved root, so
    ``os.walk`` can find it on disk). ``relative_key_path`` is joined onto the *resolved* root
    by the caller to build the stable dict key described in the module docstring.

    Directories (and files) whose name starts with ``..`` are skipped entirely and never
    descended into — kubelet's own bookkeeping entries.
    """
    root = target.root
    if not root.exists():
        return

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith("..")]
        current = Path(dirpath)
        is_root = current == root

        for fname in filenames:
            if fname.startswith(".."):
                continue
            if not _matches(fname, target.patterns):
                continue
            real_path = current / fname
            yield real_path, real_path.relative_to(root)

        if not target.recursive and is_root:
            dirnames[:] = []  # stop os.walk from descending any further


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)
