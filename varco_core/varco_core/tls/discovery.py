"""
varco_core.tls.discovery
=========================

``iter_cert_files`` — the one cert-glob helper for every "what is a certificate file in
this folder?" call site in the repo (Plan 026 / T7, §D-T7).

Before this module, four call sites each answered that question differently and silently:
``SSLConfig.build_ssl_context`` and ``varco_fastapi.auth.TrustStore.build_ssl_context`` glob
``*.pem`` + ``*.crt``; ``PemFolderSource._has_changes``/``_scan`` glob ``*.pem`` only. A
``.cer`` file dropped into a CA folder was silently ignored (``BACKLOG.md:69``) — the named
defect is the *silence*, not the narrow pattern set, so this helper does not widen any
existing site's default patterns. It only makes the mismatch loud.

DESIGN: warn loudly, do not widen (§D-T7)
    ✅ The BACKLOG's own complaint is that a ``.cer`` file is ignored "with no error" — the
       defect named is the silence, and the warning fixes exactly that.
    ✅ Widening any of the four *existing* call sites from ``{pem,crt}``/``{pem}`` to the
       wider known set would make a previously inert file become trusted (or, for
       ``PemFolderSource``, become a bogus JWT signing key) on upgrade to 3.1 — exactly the
       "Cert search" locked-decision failure mode (``BACKLOG.md:30``) applied to patterns
       instead of recursion.
    ❌ The three globs still differ after this helper. Accepted: they differ *by design* now,
       from one shared implementation, with one constant naming the wider set — instead of by
       accident, from four hand-written ``glob()`` calls.
    ❌ One WARNING per skipped file could be noisy in a folder of mixed content. Mitigated:
       the warning is emitted at most once per ``(root, pattern-set)`` per process, listing the
       skipped names together.

``CERT_FILE_PATTERNS`` is the "wider known set" and is the default for the *new*
``varco_core.tls.TrustStore`` only (which has no existing deployments to widen). Opting an
existing site in is a one-line, explicit ``patterns=CERT_FILE_PATTERNS``.

Reuses Plan 025's ``varco_core.watch`` enumeration (``WatchTarget`` + the private
``_iter_paths`` walker in ``varco_core.watch.snapshot``) for the ``..``-skip rule (kubelet's
``..data`` / ``..2026_...`` ConfigMap/Secret rotation bookkeeping) rather than reimplementing
it — a second, divergent ``os.walk`` here would be exactly the kind of accidental disagreement
this module exists to end.

Thread safety:  ⚠️ The per-``(root, patterns)`` "already warned" cache is a module-level
                   ``set`` mutated without a lock. A benign race (two threads/tasks both
                   deciding to warn) can emit the warning twice under concurrent first calls
                   for the same ``(root, patterns)`` — logging is not a correctness path, so
                   this is accepted rather than adding synchronisation to a synchronous helper.
Async safety:   ✅ Synchronous and filesystem-bound — callers already run it via
                   ``asyncio.to_thread()`` (e.g. ``TrustStore.build_ssl_context()``,
                   ``PemFolderSource._scan()``), same precedent as ``varco_core.watch``.
"""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Iterator
from pathlib import Path

from varco_core.watch.base import WatchTarget
from varco_core.watch.snapshot import _iter_paths

logger = logging.getLogger(__name__)

# The wider known cert-file pattern set — default for varco_core.tls.TrustStore only.
# Existing call sites (SSLConfig, varco_fastapi.auth.TrustStore, PemFolderSource) keep their
# own narrower default and opt in explicitly (§D-T7).
CERT_FILE_PATTERNS: tuple[str, ...] = ("*.pem", "*.crt", "*.cer")

# (resolved root, patterns) pairs already warned about — process-lifetime, never cleared.
# Keeps the "at most once per (root, patterns) per process" promise (§D-T7).
_WARNED: set[tuple[str, tuple[str, ...]]] = set()


def iter_cert_files(
    root: Path,
    *,
    patterns: tuple[str, ...],
    recursive: bool = False,
) -> Iterator[Path]:
    """
    Enumerate certificate files under ``root`` matching ``patterns``, deterministically.

    Any file under ``root`` that matches ``CERT_FILE_PATTERNS`` (the wider known set) but
    not this call's own ``patterns`` is skipped and logged once at WARNING, naming the
    skipped file(s) — the fix for BACKLOG's "a ``.cer`` file is ignored with no error".

    Args:
        root: Directory to scan. A non-existent root yields nothing — never raises.
        patterns: ``fnmatch`` patterns a file's name must match at least one of to be
            *returned*. Files matching ``CERT_FILE_PATTERNS`` but not this set are warned
            about, not returned.
        recursive: Whether to descend into subdirectories. Entries whose name begins with
            ``..`` are always skipped (Kubernetes ``..data`` symlink-swap rotation
            bookkeeping — reused from ``varco_core.watch``, never re-implemented here).

    Returns:
        An iterator of matching file paths, sorted deterministically (callers rely on
        stable load order — e.g. ``TrustStore``/``SSLConfig`` loading CAs in the same order
        across runs).

    Edge cases:
        - ``root`` does not exist → empty iterator, no exception (mirrors ``os.walk``'s own
          behaviour for a missing top).
        - A file matches the wider ``CERT_FILE_PATTERNS`` set but not ``patterns`` → skipped,
          not returned, and produces exactly one WARNING per ``(root, patterns)`` per
          process — never once per file, never once per call.
        - Duplicate resolved paths (e.g. a c_rehash-style directory of symlinks pointing at
          the same underlying file) are de-duplicated by resolved path — a hash-symlink never
          double-loads the same certificate.
        - A dangling symlink whose target cannot be resolved is skipped, not raised.
    """
    root = Path(root)
    if not root.exists():
        return

    # Enumerate everything that looks cert-like (the wider set) so we can tell "not a cert
    # file at all" (e.g. "notes.txt", silently irrelevant) apart from "a cert file this site
    # doesn't glob" (e.g. a ".cer" when patterns=("*.pem","*.crt"), the case that must warn).
    scan_target = WatchTarget(root=root, patterns=CERT_FILE_PATTERNS, recursive=recursive)

    seen_resolved: set[Path] = set()
    matched: list[Path] = []
    skipped_names: list[str] = []

    for real_path, _key_path in _iter_paths(scan_target):
        try:
            resolved = real_path.resolve()
        except OSError:
            continue  # dangling symlink — treated as absent, not a crash
        if resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)

        if _matches_any(real_path.name, patterns):
            matched.append(real_path)
        else:
            skipped_names.append(real_path.name)

    if skipped_names:
        _warn_once(root, patterns, skipped_names)

    yield from sorted(matched, key=str)


def _matches_any(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _warn_once(root: Path, patterns: tuple[str, ...], skipped_names: list[str]) -> None:
    key = (str(Path(root).resolve()) if root.exists() else str(root), patterns)
    if key in _WARNED:
        return
    _WARNED.add(key)
    logger.warning(
        "iter_cert_files: %s contains file(s) matching a wider known cert-file pattern "
        "(%s) but not this site's patterns %s — skipped, not loaded: %s. "
        "This is a silent-misconfiguration guard (BACKLOG.md:69) — no trust/keyset is "
        "widened by this warning; opt in explicitly with patterns=CERT_FILE_PATTERNS if the "
        "file should actually be loaded.",
        root,
        CERT_FILE_PATTERNS,
        patterns,
        ", ".join(sorted(skipped_names)),
    )


__all__ = ["CERT_FILE_PATTERNS", "iter_cert_files"]
