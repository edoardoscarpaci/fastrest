"""
consumer.py
===========
Order consumer for the notification-hub example.

``OrderConsumer`` subscribes to ``OrderPlacedEvent`` messages on the
``"orders"`` channel via the ``@listen`` decorator.  Received events are
appended to an in-memory list that the HTTP handler exposes as notifications.

Pattern
-------
The ``@listen`` decorator is **declarative** — it stores metadata on the
method at class-definition time.  No subscription is created until
``self.register_to(bus)`` is called (in the ``@PostConstruct`` hook).  This
separation keeps the consumer bus-agnostic and easy to test.

DESIGN: in-memory notification list over a database
    ✅ Keeps the example laser-focused on the event bus mechanics.
    ✅ No persistence dependency — tests run without a DB.
    ❌ State is lost on restart and invisible across multiple processes.
       For production, persist notifications to a database.

Thread safety:  ❌  Not thread-safe — single asyncio event loop assumed.
Async safety:   ✅  ``on_order`` is ``async def``; list.append() is GIL-safe.
"""

from __future__ import annotations

from events import OrderPlacedEvent
from providify import PostConstruct
from varco_core.event import AbstractEventBus, EventConsumer, listen


class OrderConsumer(EventConsumer):
    """
    Listens for ``OrderPlacedEvent`` messages on the ``"orders"`` channel.

    Published events are appended to ``received`` — an ordered list of all
    events seen since the consumer was created.  The HTTP layer reads this
    list to serve the ``GET /v1/notifications`` endpoint.

    Lifecycle:
        ``_setup()`` (``@PostConstruct``) registers all ``@listen`` methods
        with the bus.  This must be called before any events arrive — the
        ``create_app`` factory calls ``_setup()`` explicitly when not using
        a DI container.

    Args:
        bus: The event bus to subscribe to.  Injected or passed directly.

    Attributes:
        received: Ordered list of ``OrderPlacedEvent`` objects seen so far.

    Thread safety:  ❌  Single event loop; ``received`` is not thread-safe.
    Async safety:   ✅  Handler is ``async def``; list.append() is atomic.

    Edge cases:
        - Events published before ``_setup()`` is called are silently lost
          (no subscription exists yet).
        - There is no maximum list size — in production, add eviction or
          pagination to avoid unbounded memory growth.
    """

    def __init__(self, bus: AbstractEventBus) -> None:
        """
        Args:
            bus: The event bus this consumer will subscribe to.
        """
        # Store bus reference for use in @PostConstruct — NOT for publishing.
        # Only EventConsumer.register_to() may hold/call the bus directly.
        self._bus = bus

        # In-memory store — accumulates every OrderPlacedEvent received.
        self.received: list[OrderPlacedEvent] = []

    @PostConstruct
    def _setup(self) -> None:
        """
        Wire all ``@listen``-decorated methods to the bus.

        Called once after construction (either by the DI container via
        ``@PostConstruct`` or explicitly by ``create_app``).  After this
        call, the bus will dispatch matching events to ``on_order``.

        Edge cases:
            - Calling twice is safe — ``register_to()`` is idempotent for
              the same bus reference.
        """
        # register_to() is the only call that creates real subscriptions.
        # @listen stores metadata only; no subscription exists until here.
        self.register_to(self._bus)

    @listen(OrderPlacedEvent, channel="orders")
    async def on_order(self, event: OrderPlacedEvent) -> None:
        """
        Handle an incoming ``OrderPlacedEvent``.

        Appends the event to ``self.received`` so the HTTP layer can expose it.

        Args:
            event: The deserialized ``OrderPlacedEvent`` from Redis.

        Edge cases:
            - If the bus delivers duplicate events (e.g., Redis Streams
              at-least-once redelivery), duplicates appear in ``received``.
              De-duplication is out of scope for this example.
        """
        self.received.append(event)


__all__ = ["OrderConsumer"]
