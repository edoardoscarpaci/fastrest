"""
varco_core.tls.reload
======================

``ReloadingTrustStore`` — a live, hot-reloading ``ssl.SSLContext`` built from a
``TrustStore`` spec (Plan 026 / T3b, §D-T3-reload). Composes Plan 025's
``ReloadableResource[ssl.SSLContext]`` (keep-last-good swap semantics) and
``AbstractPathWatcher`` (filesystem change detection) — it does not inherit from either.

DESIGN: mutate vs. swap, chosen per event (§D-T3-reload)
    ``ReloadStrategy.AUTO`` (the default) mutates the *live* ``ssl.SSLContext`` in place when
    a settled watch batch is **additions only**, and rebuilds + swaps the context reference
    when anything was removed or replaced.

    ✅ Grounded in ``ssl.SSLContext``'s own documented behaviour: ``load_verify_locations``/
       ``load_cert_chain`` **can** be called on a live context, but there is no unload API —
       "already-established TLS connections see no change; only NEW handshakes use the
       updated cert" (brief 001 §2). So mutation adds trust for free and can never remove it —
       the 6-day-cert-renewal common path (a new CA/leaf appearing) takes the cheap branch,
       and a revoked/replaced CA (something disappearing or changing under the same name)
       correctly forces a full rebuild instead of silently keeping the old, wrong trust.
    ✅ ``WatchEvent.kind`` already carries ADDED/MODIFIED/REMOVED per file (Plan 025 / T1), so
       the branch is a one-line predicate over the settled batch — no new machinery.
    ✅ ``SWAP`` publishes a new context object and bumps ``generation``, so pooled clients can
       be told to rebuild via ``subscribe()``. ``MUTATE`` leaves the object identity alone, so
       every client already holding a reference to it picks the rotation up with zero
       coordination.
    ❌ ``SWAP`` cannot revoke trust for connections already established on the *old* context
       object (brief 001 §2 again) — varco cannot fix that from a library. Established
       connections keep whatever context they negotiated with until they reconnect.
    ❌ ``AUTO`` is a heuristic: a CA replaced by a file *rename* is seen by the diff as
       ADDED+REMOVED in one batch, which lands on the SWAP branch — correct, but more
       expensive than a pure addition would have been. Errs toward the safe, expensive branch.

``generation`` counts only real SWAPs — never the initial ``start()`` load, never a MUTATE.
It is derived from the underlying ``ReloadableResource.generation`` (which increments on
*every* successful load, including the first) by subtracting the always-present initial load,
so ``generation == 0`` immediately after ``start()`` and only increments thereafter when the
SWAP branch actually runs.

Thread safety:  N/A — single-event-loop usage only, like the rest of ``varco_core``.
Async safety:   ✅ ``start()``/``stop()`` are idempotent. A MUTATE reload mutates
                   ``self.context`` off the event loop thread (``asyncio.to_thread``) — a
                   ``load_verify_locations`` failure on a malformed file is caught and logged
                   at ERROR, never propagated, matching keep-last-good (the previously loaded
                   trust in that same context object is untouched by a failed *additional*
                   load). A SWAP reload goes through ``ReloadableResource.reload()``, which
                   already guards the swap with a lazily-created ``asyncio.Lock``
                   (CLAUDE.md's lazy-lock rule) — this module introduces no additional lock.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, cast

from varco_core.reload import ReloadableResource, ReloadOutcome
from varco_core.watch import WatchTarget, default_watcher
from varco_core.watch.base import WatchKind

if TYPE_CHECKING:
    from varco_core.tls.store import TrustStore
    from varco_core.watch.base import AbstractPathWatcher, WatchEvent

logger = logging.getLogger(__name__)


class ReloadStrategy(Enum):
    """How a settled watch batch is applied to the live ``ssl.SSLContext``."""

    AUTO = "auto"
    """Mutate for additions-only batches, swap for anything removed/replaced (default)."""

    MUTATE = "mutate"
    """Always mutate the live context in place — never swap. See the module DESIGN block for
    why an explicit MUTATE can only ever *add* trust, never revoke it."""

    SWAP = "swap"
    """Always rebuild from scratch and swap the context reference, even for additions-only
    batches — the safe, expensive branch, forced unconditionally."""


class ReloadingTrustStore:
    """
    A live, hot-reloading ``ssl.SSLContext`` built from a ``TrustStore`` spec.

    Args:
        spec: The frozen ``TrustStore`` configuration to build from. Never mutated.
        watcher: An ``AbstractPathWatcher`` to drive reloads. Defaults to a
            ``varco_core.watch.default_watcher()`` over ``spec.ca_folders`` (plus the parent
            directories of ``ca_cert``/``client_cert``/``client_key``, filtered to those
            filenames) when ``None``. Injectable so tests can drive a fast poll interval.
        strategy: The ``ReloadStrategy`` applied to every settled watch batch. Defaults to
            ``AUTO``.

    Edge cases:
        - A mid-rotation unreadable/invalid file: keep-last-good, inherited from
          ``ReloadableResource`` (Plan 025 / T2) for the SWAP branch, and from the try/except
          around the mutate call for the MUTATE branch. Either way, ``self.context`` is left
          untouched and the failure is logged at ERROR.
        - ``.context`` before ``start()`` raises ``ResourceNotLoadedError`` (Plan 025 / T2) —
          never silently serves an unloaded/empty context.
        - ``stop()`` is idempotent, safe even if ``start()`` was never called.
    """

    def __init__(
        self,
        spec: TrustStore,
        *,
        watcher: AbstractPathWatcher | None = None,
        strategy: ReloadStrategy = ReloadStrategy.AUTO,
    ) -> None:
        self.spec = spec
        self._strategy = strategy
        self._watcher: AbstractPathWatcher = (
            watcher if watcher is not None else default_watcher(_default_watch_targets(spec))
        )
        self._resource: ReloadableResource[ssl.SSLContext] = ReloadableResource(
            spec.build_ssl_context, name="ReloadingTrustStore"
        )
        self._unsubscribe_from_watcher: Callable[[], None] | None = None

    @property
    def context(self) -> ssl.SSLContext:
        """The current ``ssl.SSLContext``. Raises ``ResourceNotLoadedError`` before ``start()``."""
        return self._resource.current

    @property
    def generation(self) -> int:
        """Bumps by exactly one on every SWAP — never on the initial load, never on MUTATE."""
        return self._resource.generation - 1

    async def start(self) -> None:
        """
        Perform the first (full, synchronous) load, then start watching for changes.

        Raises:
            Exception: Whatever ``spec.build_ssl_context()`` raises on the first load — no
                last-good value exists yet to fall back to (Plan 025 / T2's fail-fast rule).
        """
        await self._resource.start()
        self._unsubscribe_from_watcher = self._watcher.subscribe(self._on_watch_event)
        await self._watcher.start()

    async def stop(self) -> None:
        """Stop the watcher and unsubscribe. Idempotent."""
        await self._watcher.stop()
        if self._unsubscribe_from_watcher is not None:
            self._unsubscribe_from_watcher()
            self._unsubscribe_from_watcher = None

    async def reload(self) -> ReloadOutcome:
        """Force a full rebuild + swap by hand — bypasses the mutate/swap decision entirely."""
        return await self._resource.reload()

    def subscribe(self, callback: Callable[[ssl.SSLContext], None]) -> Callable[[], None]:
        """
        Register a callback invoked with the new context after every successful SWAP.

        Never called for a MUTATE reload — the context object identity did not change, so
        there is nothing for a pooled-client-rebuild subscriber to react to.

        Returns:
            An ``unsubscribe`` callable.
        """
        return self._resource.subscribe(callback)

    async def __aenter__(self) -> ReloadingTrustStore:
        await self.start()
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.stop()

    # ── HTTP-client adapters (Plan 027 / T4b, §D-T4-adapters) ─────────────────
    #
    # Same thin-delegation shape as TrustStore's own four methods (varco_core.tls.store) —
    # each reads self.context **at call time**, so the object handed to the client library is
    # always the current one. This is what makes a MUTATE rotation reach an already-built
    # client with zero action (see clients.py's "Reload interaction" docstring section); a
    # SWAP rotation still requires the caller to rebuild via subscribe() — reading fresh here
    # cannot retroactively fix a client that already captured the old context object.

    def to_httpx_verify(self) -> ssl.SSLContext:
        """See ``varco_core.tls.clients.to_httpx_verify``."""
        from varco_core.tls.clients import to_httpx_verify  # noqa: PLC0415

        return to_httpx_verify(self)

    async def to_aiohttp_connector(self, **kwargs: object) -> object:
        """See ``varco_core.tls.clients.to_aiohttp_connector``."""
        from varco_core.tls.clients import to_aiohttp_connector  # noqa: PLC0415

        return await to_aiohttp_connector(self, **kwargs)

    def to_urllib3_poolmanager(self, **kwargs: object) -> object:
        """See ``varco_core.tls.clients.to_urllib3_poolmanager``."""
        from varco_core.tls.clients import to_urllib3_poolmanager  # noqa: PLC0415

        return to_urllib3_poolmanager(self, **kwargs)

    def to_requests_adapter(self) -> object:
        """See ``varco_core.tls.clients.to_requests_adapter``."""
        from varco_core.tls.clients import to_requests_adapter  # noqa: PLC0415

        return to_requests_adapter(self)

    # ── Watcher glue ──────────────────────────────────────────────────────────

    def _on_watch_event(self, payload: WatchEvent | tuple[WatchEvent, ...]) -> None:
        events = payload if isinstance(payload, tuple) else (payload,)
        # Fire-and-forget on the running event loop — same pattern and rationale as
        # varco_core.reload.ReloadableResource._on_watch_event: the watcher's own callback
        # contract (AbstractPathWatcher._notify) is synchronous, so reacting to a batch with
        # an async operation must be scheduled, not awaited inline.
        asyncio.create_task(self._apply(events))  # noqa: RUF006 — fire-and-forget by design

    async def _apply(self, events: tuple[WatchEvent, ...]) -> None:
        strategy = self._decide(events)
        if strategy is ReloadStrategy.MUTATE:
            await self._mutate(events)
        else:
            outcome = await self._resource.reload()
            if outcome.error is not None:
                logger.error(
                    "ReloadingTrustStore: SWAP reload failed, keeping last-good context: %s",
                    outcome.error,
                )

    def _decide(self, events: tuple[WatchEvent, ...]) -> ReloadStrategy:
        if self._strategy is not ReloadStrategy.AUTO:
            return self._strategy
        if all(event.kind is WatchKind.ADDED for event in events):
            return ReloadStrategy.MUTATE
        return ReloadStrategy.SWAP

    async def _mutate(self, events: tuple[WatchEvent, ...]) -> None:
        """
        Add newly-appeared certificate files to the *live* context, in place.

        Never removes or replaces anything — only ``load_verify_locations`` calls, which are
        purely additive (see the module DESIGN block). A failure loading one of the new files
        (e.g. caught mid-write) is logged at ERROR; whatever was already trusted on this
        context object remains trusted (keep-last-good, for free, since we never discard it).
        """
        ctx = self._resource.current
        try:
            await asyncio.to_thread(self._mutate_sync, ctx, events)
        except Exception as exc:  # noqa: BLE001 — keep-last-good: never propagate to the watcher
            logger.error(
                "ReloadingTrustStore: MUTATE failed for %s, previously-trusted certs on this "
                "context are unaffected: %s",
                [str(e.path) for e in events],
                exc,
            )

    @staticmethod
    def _mutate_sync(ctx: ssl.SSLContext, events: tuple[WatchEvent, ...]) -> None:
        for event in events:
            ctx.load_verify_locations(cafile=str(event.path))


def _default_watch_targets(spec: TrustStore) -> list[WatchTarget]:
    """
    Build the default watch targets for a ``TrustStore`` spec: every ``ca_folders`` entry,
    plus the individual ``ca_cert``/``client_cert``/``client_key`` files if configured as
    ``Path``s (``WatchTarget`` normalises a file root to "its parent, filtered to its name" —
    see ``varco_core.watch.base.WatchTarget.__post_init__``).
    """
    targets: list[WatchTarget] = []
    # TrustStore.__post_init__ always normalises ca_folders to tuple[Path, ...] | None —
    # mypy only sees the wider constructor-accepted type (see varco_core.tls.store's own
    # cast() note for the same, tested invariant).
    folders = cast("tuple[Path, ...] | None", spec.ca_folders)
    for folder in folders or ():
        targets.append(
            WatchTarget(root=folder, patterns=spec.cert_patterns, recursive=spec.recursive)
        )
    for single_file in (spec.ca_cert, spec.client_cert, spec.client_key):
        if isinstance(single_file, Path):
            targets.append(WatchTarget(root=single_file))
    return targets


__all__ = ["ReloadStrategy", "ReloadingTrustStore"]
