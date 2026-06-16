"""
events.py
=========
Domain events for the NATS JetStream notification-hub example.

``OrderPlacedEvent`` is published via the HTTP endpoint and consumed
by ``OrderConsumer`` — demonstrating the full ``AbstractEventProducer``
→ ``NatsEventBus`` → ``EventConsumer`` pipeline.

DESIGN: single shared event module
    ✅ Both producer (router) and consumer import from one place — no
       circular dependency, no event type mismatch at deserialization.
    ✅ ``__event_type__`` is explicit and unique, ensuring the
       ``JsonEventSerializer`` can round-trip the class correctly.
    ❌ For a larger app, events would live in a dedicated domain package.
       One module is fine for a self-contained example.
"""

from __future__ import annotations

from varco_core.event import Event


class OrderPlacedEvent(Event):
    """
    Emitted when a new order is placed.

    Carried over NATS JetStream to any subscribed consumer.  JetStream
    provides at-least-once delivery — handlers may receive duplicates on
    redelivery after a crash; idempotent handling is the caller's responsibility.

    Attributes:
        order_id: Unique order identifier (free-form string for the example).
        amount:   Order total in USD.

    Edge cases:
        - ``amount`` must be non-negative; no domain validation is enforced
          here — kept simple to focus on the event bus mechanics.
        - NATS JetStream deduplication (``EXACTLY_ONCE`` semantics) uses
          ``event_id`` (inherited from ``Event``) as the message ID.
    """

    # Explicit event type string — used by JsonEventSerializer for round-trip
    # deserialization.  Must be unique across all Event subclasses in the app.
    __event_type__ = "order.placed"

    order_id: str
    amount: float


__all__ = ["OrderPlacedEvent"]
