"""
router.py
=========
FastAPI router for the NATS JetStream notification-hub example.

Endpoints:
    POST /v1/orders           — publish an ``OrderPlacedEvent``
    GET  /v1/notifications    — return all events received by the consumer
    GET  /health              — health check (always 200)

The router is a plain FastAPI ``APIRouter`` rather than a ``VarcoRouter``
because it doesn't need DI-managed service objects — the producer and
consumer are passed in via a closure factory pattern.

DESIGN: closure-based dependency injection over global state
    ✅ ``build_router(producer, consumer)`` is a pure factory — no
       module-level singletons, no ``app.state`` lookups in handlers.
    ✅ Tests can pass stubs directly without touching DI machinery.
    ❌ Handler signatures don't show the dependencies — they're captured
       in the closure.  Acceptable for a self-contained example.
"""

from __future__ import annotations

from consumer import OrderConsumer
from events import OrderPlacedEvent
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from varco_core.event import AbstractEventProducer

# ── Request / response models ─────────────────────────────────────────────────


class PlaceOrderRequest(BaseModel):
    """
    HTTP request body for ``POST /v1/orders``.

    Attributes:
        order_id: Caller-supplied unique order identifier.
        amount:   Order total in USD.  Must be >= 0.

    Edge cases:
        - ``amount`` is validated by Pydantic as a float; negative values
          are accepted by the schema (business validation is out of scope).
    """

    order_id: str
    amount: float


class NotificationItem(BaseModel):
    """
    Single notification entry returned by ``GET /v1/notifications``.

    Attributes:
        order_id: Order identifier from the received event.
        amount:   Order total from the received event.
    """

    order_id: str
    amount: float


# ── Router factory ────────────────────────────────────────────────────────────


def build_router(
    producer: AbstractEventProducer,
    consumer: OrderConsumer,
) -> APIRouter:
    """
    Build and return the FastAPI router for the NATS JetStream notification-hub.

    All handlers close over ``producer`` and ``consumer`` — no global state
    or request-scoped DI resolution required.

    Args:
        producer: The event producer used to publish ``OrderPlacedEvent``.
                  Must NOT be the bus itself — layer boundary is respected.
        consumer: The ``OrderConsumer`` whose ``received`` list is served
                  by the notifications endpoint.

    Returns:
        A configured ``APIRouter`` with the three example endpoints.

    Edge cases:
        - ``producer._produce()`` propagates NATS errors directly to the
          caller — the endpoint returns 500 on broker failures.
        - ``consumer.received`` grows without bound — no pagination applied.
        - NATS JetStream is at-least-once; the ``POST /v1/orders`` response
          (202 Accepted) confirms the publish succeeded, not that the handler
          has run.  ``GET /v1/notifications`` reflects delivery after JetStream
          dispatches the message to the consumer.
    """
    router = APIRouter()

    @router.post("/v1/orders", status_code=202)
    async def place_order(body: PlaceOrderRequest) -> dict:
        """
        Publish an ``OrderPlacedEvent`` to the ``"orders"`` channel.

        Returns HTTP 202 Accepted — the event is handed off to NATS JetStream
        asynchronously; delivery to handlers may lag slightly due to JetStream
        ack round-trips.

        Args:
            body: JSON body with ``order_id`` and ``amount``.

        Returns:
            ``{"status": "accepted", "order_id": <id>}``

        Raises:
            HTTPException 500: If the NATS publish call fails.
        """
        event = OrderPlacedEvent(order_id=body.order_id, amount=body.amount)
        try:
            # Use _produce() — the producer hides the bus from this handler.
            # Services must NEVER call bus.publish() directly.
            await producer._produce(event, channel="orders")
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to publish event: {exc}",
            ) from exc
        return {"status": "accepted", "order_id": body.order_id}

    @router.get("/v1/notifications")
    async def list_notifications() -> list[NotificationItem]:
        """
        Return all ``OrderPlacedEvent`` notifications received so far.

        The list is ordered by arrival time (oldest first) and grows without
        bound until the server restarts.

        Returns:
            List of ``NotificationItem`` dicts.

        Edge cases:
            - Returns ``[]`` if no events have been received yet.
            - JetStream at-least-once may produce duplicates; they appear here
              as duplicate entries (de-duplication is out of scope).
        """
        return [
            NotificationItem(order_id=ev.order_id, amount=ev.amount)
            for ev in consumer.received
        ]

    @router.get("/health")
    async def health() -> dict:
        """
        Liveness probe.

        Returns:
            ``{"status": "ok"}`` — always HTTP 200.
        """
        return {"status": "ok"}

    return router


__all__ = ["build_router", "PlaceOrderRequest", "NotificationItem"]
