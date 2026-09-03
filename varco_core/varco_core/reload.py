"""
varco_core.reload
==================

``ReloadableResource[T]`` — load a value, swap it under a lock, notify subscribers, with
keep-last-good on any post-startup load failure (Plan 025 / T2).

DESIGN: owns a loader, current value, generation counter, subscribers; an *optional* watcher
    (§D-T2-shape)
    ✅ Usable from a watcher, from a SIGHUP handler, from an admin endpoint or from a test,
       with no code change — ``reload()`` is always public and callable by hand.
    ✅ Composes with ``varco_core.watch.AbstractPathWatcher`` (Plan 025 / T1) with no coupling
       beyond "the watcher's callback calls ``reload()``" — ``ReloadableResource`` never
       imports ``varco_core.watch`` itself, so it stays usable for any ``T`` triggered by any
       source, watcher or not.
    ❌ Optional coupling means the watcher/resource pairing is entirely the caller's
       responsibility (Plan 026 wires this for ``TrustStore``). Accepted: it is exactly the
       "no separate FileWatcher/DirWatcher types" trade-off §D-T1-shape already made.

DESIGN: keep-last-good, but fail-fast on the very first load
    ✅ Locked in the BACKLOG row itself: "a truncated or half-written file must never take
       down a live service, and a cert folder mid-rotation is exactly that". Every
       post-startup load failure logs ERROR, leaves ``current``/``generation`` untouched, and
       returns ``ReloadOutcome(changed=False, error=exc)``.
    ✅ The **first** load is different: there is no last-good to keep, and a service that
       starts with no CA bundle and discovers it on the first outbound call is strictly worse
       than one that refuses to start. ``start()`` therefore propagates. This mirrors
       ``JwksUrlSource.refresh()`` (``varco_core/varco_core/authority/sources/jwks_url.py``),
       which returns the stale keyset on failure but re-raises when ``self._keyset is None`` —
       the repo already made this exact call once.
    ❌ A resource that is *permanently* broken after startup serves a stale value
       indefinitely. The mitigation is observability, not failure: every failed reload logs
       ERROR and the outcome carries the exception, so a caller may escalate. varco does not
       decide to take a process down.

Async safety: ✅ The swap is guarded by an ``asyncio.Lock`` created **lazily** in
    ``_get_lock()`` on first use — never in ``__init__``, never at module scope (CLAUDE.md's
    lock rule). ``current`` is a plain attribute read of an immutable reference, so readers
    never take the lock and never see a torn value. Subscriber notification happens **outside**
    the lock, after the swap, so a subscriber that itself calls ``reload()`` cannot deadlock.
    Subscriber exceptions are logged and swallowed, same rule as
    ``varco_core.watch.base.AbstractPathWatcher._notify``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from varco_core.watch.base import AbstractPathWatcher, WatchEvent

logger = logging.getLogger(__name__)

T = TypeVar("T")

_Loader = Callable[[], "T | Awaitable[T]"]  # Callable[[], T] | Callable[[], Awaitable[T]]


class ResourceNotLoadedError(RuntimeError):
    """Raised by ``ReloadableResource.current`` before ``start()`` has completed successfully."""


@dataclass(frozen=True)
class ReloadOutcome:
    """
    The result of one ``reload()`` call.

    Args:
        changed: ``True`` iff a reload ran and the loader succeeded — a swap happened.
            **Not** "the bytes differ": a loader returning an equal-but-not-identical value
            still counts as a swap (see the module's Edge cases in the plan; equality is
            deliberately never checked — ``T`` is not required to be comparable, and equality
            on e.g. ``ssl.SSLContext`` is identity anyway).
        generation: The resource's generation *after* this call (unchanged if ``changed`` is
            ``False``).
        error: The exception raised by the loader, if any. Only set when ``changed`` is
            ``False`` and this was not the first load (the first load's failure propagates out
            of ``start()`` instead of being captured here).
    """

    changed: bool
    generation: int
    error: Exception | None = None


class ReloadableResource(Generic[T]):
    """
    Load → swap under a lock → notify subscribers, with keep-last-good semantics.

    Args:
        loader: A zero-argument callable returning ``T`` (sync — run via
            ``asyncio.to_thread()``, same reasoning and precedent as
            ``PemFolderSource._scan()``) or ``Awaitable[T]`` (awaited directly).
        watcher: An optional ``AbstractPathWatcher`` (or any object exposing the same
            ``start()``/``stop()``/``subscribe()`` surface). When given, ``start()``/``stop()``
            also start/stop the watcher, and every settled watch batch triggers ``reload()``.
            Purely additive — ``reload()`` is always independently callable, e.g. from a
            ``SIGHUP`` handler wired by the application in three lines.
        name: Optional label used only in log messages.
    """

    def __init__(
        self,
        loader: _Loader[T],
        *,
        watcher: AbstractPathWatcher | None = None,
        name: str = "",
    ) -> None:
        self._loader = loader
        self._watcher = watcher
        self._name = name or getattr(loader, "__qualname__", repr(loader))
        self._current: T | None = None
        self._loaded = False
        self._generation = 0
        self._subscribers: list[Callable[[T], None]] = []
        # Lazy: an asyncio.Lock must be constructed inside a running event loop
        # (CLAUDE.md's lazy-lock rule) — created in _get_lock(), never here.
        self._lock: asyncio.Lock | None = None
        self._unsubscribe_from_watcher: Callable[[], None] | None = None

    def _get_lock(self) -> asyncio.Lock:
        """Lazily construct the swap lock — see the class/module docstring's Async safety."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def current(self) -> T:
        """
        The most recently, successfully loaded value.

        Raises:
            ResourceNotLoadedError: Before ``start()`` has completed at least one successful
                load.
        """
        if not self._loaded:
            raise ResourceNotLoadedError(
                f"ReloadableResource({self._name!r}) has no loaded value — call start() first."
            )
        return self._current  # type: ignore[return-value]  # `_loaded` guarantees non-None-ness of T

    @property
    def generation(self) -> int:
        """Increments by exactly one on every successful swap (``start()`` counts as one)."""
        return self._generation

    async def start(self) -> None:
        """
        Perform the first load and start the watcher, if any.

        Raises:
            Exception: Whatever the loader raises on the *first* load — unlike every
                subsequent ``reload()``, there is no last-good value to keep (§D-T2-shape's
                fail-fast-on-first-load design).
        """
        await self._load_and_swap(first_load=True)
        if self._watcher is not None:
            self._unsubscribe_from_watcher = self._watcher.subscribe(self._on_watch_event)
            await self._watcher.start()

    async def stop(self) -> None:
        """Stop the watcher, if any. Idempotent — safe even if ``start()`` was never called."""
        if self._watcher is not None:
            await self._watcher.stop()
        if self._unsubscribe_from_watcher is not None:
            self._unsubscribe_from_watcher()
            self._unsubscribe_from_watcher = None

    async def reload(self) -> ReloadOutcome:
        """
        Reload the value by hand.

        Keep-last-good (§D-T2-shape): a loader failure here is caught, logged at ERROR, and
        leaves ``current``/``generation`` untouched.

        Returns:
            A ``ReloadOutcome`` describing whether the swap happened.
        """
        return await self._load_and_swap(first_load=False)

    def subscribe(self, callback: Callable[[T], None]) -> Callable[[], None]:
        """
        Register a callback invoked with the new value after every successful swap.

        Args:
            callback: Called with ``self.current`` right after it changes. A raising
                callback is logged and does not prevent the remaining subscribers from
                running (same rule as ``AbstractPathWatcher._notify``).

        Returns:
            An ``unsubscribe`` callable.
        """
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass  # already unsubscribed — idempotent

        return _unsubscribe

    def _on_watch_event(self, _event: WatchEvent | tuple[WatchEvent, ...]) -> None:
        """Watcher callback — fire-and-forget a ``reload()`` on the running event loop."""
        asyncio.create_task(self._reload_and_log())  # noqa: RUF006 — fire-and-forget by design

    async def _reload_and_log(self) -> None:
        outcome = await self.reload()
        if outcome.error is not None:
            logger.error(
                "ReloadableResource(%r): watch-triggered reload failed: %s",
                self._name,
                outcome.error,
            )

    async def _load_and_swap(self, *, first_load: bool) -> ReloadOutcome:
        try:
            value = await self._invoke_loader()
        except Exception as exc:  # noqa: BLE001 — keep-last-good: caller decides what to do
            if first_load:
                raise  # no last-good value to keep — propagate (§D-T2-shape)
            logger.error(
                "ReloadableResource(%r): reload failed, keeping last-good value",
                self._name,
                exc_info=True,
            )
            return ReloadOutcome(changed=False, generation=self._generation, error=exc)

        async with self._get_lock():
            self._current = value
            self._loaded = True
            self._generation += 1
            generation = self._generation

        # Outside the lock: a re-entrant reload() from a subscriber cannot deadlock.
        self._notify_subscribers(value)
        return ReloadOutcome(changed=True, generation=generation, error=None)

    async def _invoke_loader(self) -> T:
        """
        Run ``self._loader()``, dispatching by *callable shape*, not by calling it first.

        An async-def loader is awaited directly; anything else is assumed synchronous and run
        via ``asyncio.to_thread()`` — the same pattern and rationale as
        ``PemFolderSource._scan()`` ("run in thread to avoid blocking the loop on slow
        filesystems (NFS, etc.)"). Deciding by shape (``inspect.iscoroutinefunction``) rather
        than by calling the loader and inspecting the result means a slow *sync* loader is
        never accidentally invoked directly on the event loop thread.
        """
        if inspect.iscoroutinefunction(self._loader):
            return await self._loader()  # type: ignore[no-any-return]
        return await asyncio.to_thread(self._loader)  # type: ignore[arg-type]

    def _notify_subscribers(self, value: T) -> None:
        for callback in list(self._subscribers):
            try:
                callback(value)
            except Exception:  # noqa: BLE001 — one bad subscriber must never break the others
                name = getattr(callback, "__qualname__", repr(callback))
                logger.exception("ReloadableResource(%r): subscriber %s raised", self._name, name)


__all__ = ["ReloadOutcome", "ReloadableResource", "ResourceNotLoadedError"]
