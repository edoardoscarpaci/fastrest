"""
events.py
=========
Domain events for the ``17-transactional-outbox`` example.

Events are persisted to the outbox table by ``OrderService.create()`` within
the same DB transaction as the ``Order`` row.  The ``OutboxRelay`` background
task reads pending entries and publishes them to the event bus.

DESIGN: Pydantic Event subclass
    ✅ Immutable — ``Event`` is ``BaseModel(frozen=True)``, safe to share.
    ✅ ``JsonEventSerializer`` serializes via ``model_dump()`` — field names
       match JSON payload keys automatically.
    ✅ ``event_id`` is inherited from ``Event`` — unique per instance, used
       by ``SADeduplicator`` as the idempotency key.
    ❌ Adding fields in future requires a migration of the serialized ``payload``
       bytes in the outbox table if old entries exist.

Thread safety:  ✅ Frozen Pydantic model; no mutable state.
Async safety:   ✅ Pure value object; no I/O.
"""

from __future__ import annotations

from varco_core.event import Event


class OrderCreatedEvent(Event):
    """
    Emitted when a new ``Order`` is persisted to the database.

    Stored in the outbox table within the same DB transaction as the ``Order``
    row.  The ``OutboxRelay`` background task publishes this event to the bus
    after commit, guaranteeing at-least-once delivery even if the broker is
    temporarily unavailable.

    Attributes:
        order_id: UUID of the newly created order (as string for JSON-safe
                  serialization).
        amount:   Order total — same value as ``Order.amount``.
        event_id: Auto-generated UUID per event instance (inherited from
                  ``Event``); used by ``SADeduplicator`` as the idempotency key.

    Edge cases:
        - Two ``OrderCreatedEvent`` instances with different ``event_id`` values
          may carry the same ``order_id`` if ``OrderService.create()`` is
          retried after a partial failure.  Consumers should use
          ``SADeduplicator`` with ``event_id`` as the idempotency key.
        - ``amount`` is informational in this event — downstream consumers should
          re-fetch the order from the DB if they need authoritative data.

    Thread safety:  ✅ Frozen Pydantic model; immutable after construction.
    Async safety:   ✅ Pure value object; no I/O.
    """

    __event_type__ = "order.created"

    # Order fields mirrored from the domain entity for consumer convenience.
    order_id: str = ""
    amount: float = 0.0


__all__ = ["OrderCreatedEvent"]
