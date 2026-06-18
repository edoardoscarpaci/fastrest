"""
events.py
=========
Domain events for the ``17-transactional-outbox`` example.

Events are persisted to the outbox table by ``OrderService.create()`` within
the same DB transaction as the ``Order`` row.  The ``OutboxRelay`` background
task reads pending entries and publishes them to the event bus.

DESIGN: frozen dataclass events
    ✅ Immutable — safe to share across threads and tasks.
    ✅ ``@dataclass`` interop with ``JsonEventSerializer`` — field names match
       the JSON payload keys automatically.
    ❌ Adding fields in future requires a migration of the serialized ``payload``
       bytes in the outbox table if old entries exist.

Thread safety:  ✅ Frozen dataclass; no mutable state.
Async safety:   ✅ Pure value object; no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from varco_core.event import DomainEvent


@dataclass(frozen=True)
class OrderCreatedEvent(DomainEvent):
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
        event_id: Auto-generated UUID per event instance for idempotency.

    Edge cases:
        - Two ``OrderCreatedEvent`` instances with different ``event_id`` values
          may carry the same ``order_id`` if ``OrderService.create()`` is
          retried after a partial failure.  Consumers should use
          ``SADeduplicator`` with ``event_id`` as the idempotency key.
        - ``amount`` is informational in this event — downstream consumers should
          re-fetch the order from the DB if they need authoritative data.

    Thread safety:  ✅ Frozen dataclass; immutable after construction.
    Async safety:   ✅ Pure value object; no I/O.
    """

    # Unique event identifier — used by SADeduplicator as the idempotency key.
    event_id: UUID = field(default_factory=uuid4)

    # Order fields mirrored from the domain entity for consumer convenience.
    order_id: str = ""
    amount: float = 0.0


__all__ = ["OrderCreatedEvent"]
