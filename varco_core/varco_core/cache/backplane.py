"""
varco_core.cache.backplane
=============================
``CacheBackplane`` — the ABC for a cross-node L1 invalidation channel
(Plan 010 / C1). Concrete backend implementations live in the matching
backend package (``varco_redis.backplane.RedisPubSubBackplane``) — the same
layer split as ``AbstractEventBus``/``RedisEventBus`` and
``AbstractDeadLetterQueue``/``RedisDLQ``.

Mechanism: **Redis Pub/Sub, not RESP3 CLIENT TRACKING** (Decision D-1 —
settled by research brief 002; ``redis.asyncio`` has no client-side-caching
support, issue redis/redis-py#3916 is open with no ETA, and the sync-client
workaround defeats the point of an async framework).

``InMemoryBackplane`` is a process-local fan-out registry keyed by
``bus_name`` — two ``LayeredCache`` instances in one process (and therefore
one test) can exchange invalidations without Docker, making multi-pod L1
coherence unit-testable for the first time (the scout noted such tests were
entirely absent).

Five design rules (each closes a hazard named in research brief 002):

1. ``publish()`` **must never raise** — identical contract to
   ``AbstractDeadLetterQueue.push()``. By the time a backplane publish
   happens, the authoritative L2 write has already succeeded and cannot be
   unwound.
2. Publish happens **strictly after** the authoritative-layer write — see
   ``LayeredCache``'s ordered-write path, entered only when a backplane is
   wired.
3. A **received** message evicts local layers only — **never** the last
   (authoritative) layer. Propagating a received invalidation back to L2
   would nuke shared state and amplify one write into a fleet-wide storm.
4. **Echo suppression** — a node skips messages whose ``origin`` equals its
   own ``origin`` (brief 002 §3's "self-invalidation echo").
5. **Bounded staleness enforced at construction** — ``LayeredCache(...,
   backplane=X)`` with ``promote_ttl=None`` raises ``ValueError``. Pub/Sub is
   fire-and-forget: a subscriber disconnected at publish time never receives
   the message, with no queue and no replay (brief 002 §3). A short,
   mandatory L1 TTL bounds the damage — the industry answer (Redisson,
   FusionCache).

Thread safety:  ❌ Not thread-safe — one event loop per backplane instance.
Async safety:   ✅ All public methods are ``async def`` (except ``origin``,
                   a trivial property, and ``subscribe()``, synchronous
                   registration).
"""

from __future__ import annotations

import abc
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import ClassVar, Literal

_logger = logging.getLogger(__name__)

InvalidationKind = Literal["key", "prefix", "clear"]


@dataclass(frozen=True)
class InvalidationMessage:
    """
    One backplane invalidation message.

    Attributes:
        kind:    ``"key"`` (evict one key), ``"prefix"`` (evict all keys
                 starting with ``payload``), or ``"clear"`` (evict
                 everything).
        payload: The key name, the key prefix, or ``""`` for ``"clear"``.
        origin:  The publishing node's ``CacheBackplane.origin`` — used for
                 echo suppression (design rule 4).
        ts:      UNIX timestamp the message was published at (diagnostic —
                 not used for ordering, Pub/Sub gives none across
                 publishers).
    """

    kind: InvalidationKind
    payload: str
    origin: str
    ts: float


class CacheBackplane(abc.ABC):
    """
    Abstract cross-node invalidation channel for ``LayeredCache``.

    Lifecycle-owned only — ``start()``/``stop()`` are driven exclusively by
    the hosting ``LayeredCache.start()``/``stop()``, never constructed-and-
    started by application code ad hoc (CLAUDE.md's "instantiate
    InvalidationStrategy outside its lifecycle" pitfall applies identically
    here).
    """

    @property
    @abc.abstractmethod
    def origin(self) -> str:
        """This node's unique publisher identity — used for echo suppression."""

    @abc.abstractmethod
    async def start(self) -> None:
        """Begin listening for invalidation messages. Called by ``LayeredCache.start()``."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Stop listening and release resources. Called by ``LayeredCache.stop()``. Idempotent."""

    @abc.abstractmethod
    async def publish(self, message: InvalidationMessage) -> None:
        """
        Broadcast ``message`` to every other subscribed node.

        Contract: **must never raise**. Implementations catch every
        exception, log it, and record ``varco.cache.backplane.dropped``
        (``reason="publish_failed"``) — mirroring
        ``AbstractDeadLetterQueue.push()``'s "never raise" contract, for the
        identical reason: by the time this is called the caller's write to
        the authoritative layer has already succeeded and cannot be undone.
        """

    @abc.abstractmethod
    def subscribe(self, handler: Callable[[InvalidationMessage], Awaitable[None]]) -> None:
        """
        Register the local receive handler.

        Exactly one handler per backplane instance — ``LayeredCache``
        registers its own local-eviction handler here from ``start()``.
        Implementations apply echo suppression (design rule 4) before
        invoking ``handler``.
        """


# ── InMemoryBackplane ────────────────────────────────────────────────────────


class InMemoryBackplane(CacheBackplane):
    """
    Process-local fan-out ``CacheBackplane`` — the standard backplane for
    unit tests (mirrors ``InMemoryEventBus`` / ``InMemoryDeadLetterQueue``).

    Two (or more) instances sharing the same ``bus_name`` exchange
    invalidations synchronously on ``publish()`` — no Docker, no real
    network, and therefore no message-loss window to reason about in tests
    (the real-world hazard this ABC's design rules mitigate is exercised
    against ``RedisPubSubBackplane`` instead).

    Args:
        bus_name: Registry key. Instances sharing a ``bus_name`` see each
                  other's publishes; instances with a different ``bus_name``
                  are fully isolated (mirrors ``InMemoryEventBus``'s
                  ``bus_name`` parameter for the same reason: multiple
                  independent "clusters" in one test process).

    Thread safety:  ❌ Not thread-safe — one event loop only.
    Async safety:   ✅ ``publish()`` synchronously calls every OTHER
                       registered subscriber's handler (never its own —
                       design rule 4), each of which is awaited in turn.
    """

    #: Registry of live buses, keyed by bus_name — mirrors InMemoryEventBus.
    #: Two (or more) DISTINCT ``InMemoryBackplane`` instances sharing a
    #: ``bus_name`` see each other's publishes.  A single instance may ALSO
    #: be shared directly by several callers (e.g. several ``LayeredCache``
    #: "nodes" in one test process) — ``subscribe()`` supports multiple
    #: handlers per instance for exactly that reason.
    _buses: ClassVar[dict[str, list[InMemoryBackplane]]] = {}

    def __init__(self, *, bus_name: str = "default") -> None:
        self._bus_name = bus_name
        self._origin = uuid.uuid4().hex
        self._handlers: list[Callable[[InvalidationMessage], Awaitable[None]]] = []
        self._started = False

    @property
    def origin(self) -> str:
        return self._origin

    async def start(self) -> None:
        if self._started:
            return
        members = self._buses.setdefault(self._bus_name, [])
        if self not in members:
            members.append(self)
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        members = self._buses.get(self._bus_name, [])
        if self in members:
            members.remove(self)
        self._started = False

    async def publish(self, message: InvalidationMessage) -> None:
        # publish() must never raise — see the CacheBackplane docstring.
        #
        # DESIGN: no origin filtering here — unlike a real Pub/Sub transport
        #     (which echoes a publisher's own message back over the wire,
        #     forcing RedisPubSubBackplane to filter internally by
        #     `self.origin`), this in-process double delivers by directly
        #     calling registered handler callables — there is no "wire" to
        #     echo over. Echo suppression (design rule 4) is therefore the
        #     CALLER's (``LayeredCache``'s) responsibility here, keyed off
        #     its own generated node id rather than ``InvalidationMessage.
        #     origin`` — which is what lets multiple independent
        #     ``LayeredCache`` "nodes" share ONE ``InMemoryBackplane``
        #     instance directly (every handler on that instance still gets
        #     called; each owning node decides for itself whether the
        #     message is its own echo).
        try:
            for member in list(self._buses.get(self._bus_name, [])):
                for handler in list(member._handlers):
                    await handler(message)
        except Exception as exc:  # noqa: BLE001 - publish() must never raise
            _logger.debug("InMemoryBackplane: publish failed: %s", exc)

    def subscribe(self, handler: Callable[[InvalidationMessage], Awaitable[None]]) -> None:
        self._handlers.append(handler)


__all__ = ["CacheBackplane", "InMemoryBackplane", "InvalidationMessage"]
