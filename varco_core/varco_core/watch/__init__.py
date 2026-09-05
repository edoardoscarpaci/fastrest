"""
varco_core.watch
=================

Backend-agnostic filesystem watching: an ``AbstractPathWatcher`` ABC over a set of watch
roots, notifying subscribers of changes via callbacks. Two implementations:

- ``StatPollWatcher`` (default, stdlib-only) — correct on NFS and Docker bind mounts, and
  on Kubernetes ``..data`` symlink-swap cert rotation.
- ``WatchfilesWatcher`` (opt-in, ``pip install "varco-core[watch]"``) — lower-latency local
  dev-loop watching backed by the Rust ``notify`` crate.

Both are correct under the same fingerprint/debounce rules (§D-T1-fingerprint,
§D-T1-debounce) and pass the identical ``varco_core/tests/watch_contract.py`` suite.

Nothing in this module imports ``ssl`` or touches TLS — that is Plan 026's
``varco_core.tls``, deliberately not this plan (see plans/025's Non-goals).

Usage::

    from varco_core.watch import WatchTarget, default_watcher

    watcher = default_watcher([WatchTarget(root=Path("/etc/certs"))])
    watcher.subscribe(lambda event: print("changed:", event))
    await watcher.start()
    ...
    await watcher.stop()
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from varco_core.watch.base import (
    AbstractPathWatcher,
    MissingWatchDependencyError,
    WatchEvent,
    WatchKind,
    WatchTarget,
)
from varco_core.watch.poll import StatPollWatcher
from varco_core.watch.wfiles import WatchfilesWatcher


def default_watcher(targets: Sequence[WatchTarget], **kwargs: Any) -> StatPollWatcher:
    """
    Return the recommended default watcher — always a ``StatPollWatcher``.

    Args:
        targets: The roots to watch.
        kwargs: Forwarded to ``StatPollWatcher.__init__`` (``interval``, ``quiet_period``,
            ``digest``).

    Returns:
        A new ``StatPollWatcher``. Documented as the stable way to get "the default" —
        prefer this over constructing ``StatPollWatcher`` directly if you only care that you
        get *a* correct watcher, not which implementation.
    """
    return StatPollWatcher(targets, **kwargs)


__all__ = [
    "AbstractPathWatcher",
    "MissingWatchDependencyError",
    "StatPollWatcher",
    "WatchEvent",
    "WatchKind",
    "WatchTarget",
    "WatchfilesWatcher",
    "default_watcher",
]
