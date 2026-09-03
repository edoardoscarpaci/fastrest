"""
varco_core.watch.base
======================

``AbstractPathWatcher`` — the shared ABC both ``StatPollWatcher`` (poll.py) and
``WatchfilesWatcher`` (wfiles.py) implement, plus the value types they exchange
with subscribers (``WatchEvent``, ``WatchKind``, ``WatchTarget``).

DESIGN: one ABC, parent-directory watching, callback subscribers  (§D-T1-shape)
    A file watcher that watches the file itself is *wrong on Kubernetes* — kubelet swaps the
    ``..data`` symlink, so the watched inode is deleted and never modified (brief 001 §1: watchers
    see only ``IN_DELETE_SELF`` on the old symlink, not a content change). Watching the parent and
    filtering by name is the only shape that survives it, so there is no reason for two ABCs.
    ✅ Callbacks compose with ``ReloadableResource`` (§D-T2) without either side owning an event
       loop queue, and they let one watcher feed N resources.
    ✅ ``start()``/``stop()`` **structurally satisfy** ``varco_fastapi.lifespan.AbstractLifecycle``
       — it is a ``runtime_checkable`` Protocol checked with ``isinstance`` at registration. So a
       ``varco_core`` object registers into ``VarcoLifespan`` with **zero import from
       ``varco_core`` to ``varco_fastapi``**, honouring the layer rule.
    ❌ An ``async for event in watcher.watch()`` iterator API is more Pythonic and is what
       ``watchfiles`` itself exposes. Rejected: it forces every consumer to own a task, and a
       shared watcher feeding two resources then needs a fan-out layer anyway. A callback
       registry *is* that fan-out layer.
    ❌ Callbacks make error handling the watcher's problem — addressed explicitly below
       (§D-T1-errors, ``_notify``).

Thread safety:  N/A — single-event-loop usage only, like the rest of ``varco_core``.
Async safety:   ✅ ``start()``/``stop()`` are idempotent and the background task is always
                   cancelled and awaited by ``stop()``, never left dangling.
"""

from __future__ import annotations

import abc
import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Value types ─────────────────────────────────────────────────────────────


class WatchKind(Enum):
    """What happened to a watched path, from one snapshot to the next."""

    ADDED = "added"
    MODIFIED = "modified"
    REMOVED = "removed"


@dataclass(frozen=True)
class WatchEvent:
    """
    One detected change to a watched path.

    Args:
        path: The resolved-directory-relative path that changed (see
            ``varco_core.watch.snapshot._DirSnapshot`` for the exact resolution rule — the
            *ancestor directories* are resolved once, but the leaf component is never
            resolved through a symlink, so a kubelet ``..data`` swap reports the same
            ``path`` across generations).
        kind: ``ADDED`` / ``MODIFIED`` / ``REMOVED``.
        detected_at: ``time.monotonic()`` at diff time — never wall-clock, so it is safe
            under clock adjustments.
    """

    path: Path
    kind: WatchKind
    detected_at: float


@dataclass(frozen=True)
class WatchTarget:
    """
    One root a watcher observes.

    Args:
        root: Directory to watch. If ``root`` exists today and is a **file**, it is
            normalised in ``__post_init__`` to ``root.parent`` filtered to ``root.name`` —
            "watching a file" is really "watching its parent, filtered to one name" (see
            module docstring). A root that does not exist yet at construction time is left
            as-is (it may become a directory or a file later; nothing to normalise against).
        patterns: ``fnmatch`` glob patterns a file's name must match at least one of.
            Defaults to ``("*",)`` — everything.
        recursive: Whether to descend into subdirectories. Entries (files or directories)
            whose name begins with ``..`` are always skipped, recursive or not — that is
            kubelet's ``..data`` / ``..2026_01_01_...`` bookkeeping (§D-T1-fingerprint).

    Edge cases:
        - Root is a file, not a directory → normalised to (parent, {name}) as above.
    """

    root: Path
    patterns: tuple[str, ...] = ("*",)
    recursive: bool = True

    def __post_init__(self) -> None:
        # Frozen dataclass: mutate via object.__setattr__, same pattern used across the repo
        # (e.g. varco_core.query AST nodes) for post-construction normalisation.
        if self.root.exists() and self.root.is_file():
            object.__setattr__(self, "patterns", (self.root.name,))
            object.__setattr__(self, "root", self.root.parent)


class MissingWatchDependencyError(ImportError):
    """Raised at construction time when an opt-in watcher backend's dependency is absent."""


# ── AbstractPathWatcher ──────────────────────────────────────────────────────


class AbstractPathWatcher(abc.ABC):
    """
    Watches a set of ``WatchTarget`` roots and notifies subscribers of changes.

    Subclasses implement ``_run()`` — the background loop — and call ``self._notify(events)``
    with a debounced, coalesced batch (§D-T1-debounce) once per settled change. ``start()``/
    ``stop()`` are implemented here and are final: idempotent, and always own a single
    background ``asyncio.Task``.

    Args:
        targets: The roots to watch.
        quiet_period: Seconds a target must be stable before a batch is notified.

    Async safety: ✅ ``start()``/``stop()`` are idempotent; ``stop()`` always cancels and
        awaits the background task, swallowing the resulting ``CancelledError`` (§D-T1-errors).
    """

    def __init__(self, targets: Sequence[WatchTarget], *, quiet_period: float = 0.25) -> None:
        self._targets: tuple[WatchTarget, ...] = tuple(targets)
        self._quiet_period = quiet_period
        self._subscribers: list[Callable[[Any], None]] = []
        self._task: asyncio.Task[None] | None = None
        # Lazy: an asyncio.Event/Lock must be constructed inside a running event loop
        # (CLAUDE.md's lazy-lock rule) — created in start(), never here or at module scope.
        self._stop_event: asyncio.Event | None = None

    @property
    def targets(self) -> tuple[WatchTarget, ...]:
        """The watch roots this watcher observes."""
        return self._targets

    def subscribe(self, callback: Callable[[Any], None]) -> Callable[[], None]:
        """
        Register a callback invoked on every settled change batch.

        The callback receives a single ``WatchEvent`` when exactly one event settled in the
        batch, or a ``tuple[WatchEvent, ...]`` when more than one did — see ``_notify``'s
        docstring for why.

        Args:
            callback: Called with the batch payload. Must not raise; if it does, the
                exception is logged and the remaining subscribers still run (§D-T1-errors).

        Returns:
            An ``unsubscribe`` callable — call it to remove this subscriber.
        """
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass  # already unsubscribed — idempotent, same rule as stop()

        return _unsubscribe

    async def start(self) -> None:
        """Start the background watch task. Idempotent — a second call is a no-op."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop the background watch task. Idempotent — safe before ``start()`` too."""
        if self._stop_event is not None:
            self._stop_event.set()
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # expected — stop() owns cancellation, never propagates it to the caller

    def _notify(self, events: Sequence[WatchEvent]) -> None:
        """
        Deliver one settled, debounced batch to every subscriber (§D-T1-errors).

        DESIGN: unwrap a singleton batch to the bare ``WatchEvent``
            ✅ The overwhelmingly common case is "one file changed" — handlers written as
               ``watcher.subscribe(handle_one_event)`` read naturally without unpacking a
               1-tuple every time.
            ✅ A coalesced batch (§D-T1-debounce — e.g. a directory rotation rewriting six
               files) is still delivered as one call, carrying the full ``tuple`` of events,
               so a subscriber that cares about the whole rotation can see it atomically.
            ❌ Two shapes on one callback parameter is less uniform than always passing a
               tuple. Accepted: ``ReloadableResource`` and every test in this plan only ever
               care about "did something change", not the batch shape, so the ergonomic win
               for the common single-event case outweighs the uniformity loss.

        A subscriber that raises is logged (with its ``__qualname__``) and does not prevent
        the remaining subscribers from running.
        """
        if not events:
            return
        payload: WatchEvent | tuple[WatchEvent, ...] = (
            events[0] if len(events) == 1 else tuple(events)
        )
        for callback in list(self._subscribers):
            try:
                callback(payload)
            except Exception:  # noqa: BLE001 — a bad subscriber must never break the others
                name = getattr(callback, "__qualname__", repr(callback))
                logger.exception("watch subscriber %s raised while handling a batch", name)

    @abc.abstractmethod
    async def _run(self) -> None:
        """Background loop: detect changes, debounce/settle, then call ``self._notify()``."""
        raise NotImplementedError
