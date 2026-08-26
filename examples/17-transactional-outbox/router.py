"""
router.py
=========
HTTP endpoints for the ``17-transactional-outbox`` example.

Two endpoint groups:

``POST /v1/orders``
    Creates a new order and returns 202 Accepted.  The ``OrderService``
    persists both the ``Order`` row and an ``OutboxEntry`` in the same DB
    transaction.  The entry is relayed to the bus asynchronously by the
    background ``OutboxRelay``.

``GET /v1/events``
    Returns the list of ``OrderCreatedEvent`` payloads received by the
    ``OrderConsumer`` so far.  This endpoint lets integration tests verify
    that the relay published the event successfully.

``GET /health``
    Liveness probe.

DESIGN: plain FastAPI APIRouter over CRUDRouter
    ``CRUDRouter`` is designed for standard CRUD.  The ``create`` endpoint
    here returns ``202 Accepted`` (not ``201 Created``) because event
    delivery is asynchronous — the caller should not assume the event has
    been published by the time the response arrives.

    ✅ Fine-grained status code control (202 vs 201).
    ✅ ``GET /v1/events`` has no CRUDRouter equivalent.
    ❌ More boilerplate than CRUDRouter for standard CRUD.

Thread safety:  ✅ Stateless route functions; consumer list is GIL-safe.
Async safety:   ✅ All route handlers are ``async def``.
"""

from __future__ import annotations

from consumer import OrderConsumer
from dtos import OrderCreate, OrderRead
from fastapi import APIRouter
from service import OrderService


def build_router(service: OrderService, consumer: OrderConsumer) -> APIRouter:
    """
    Build and return the FastAPI ``APIRouter`` for order management.

    Captures ``service`` and ``consumer`` in a closure so route handlers have
    access to them without global state.

    Args:
        service:  The ``OrderService`` singleton for CRUD operations.
        consumer: The ``OrderConsumer`` singleton; exposes ``received`` list.

    Returns:
        A configured ``APIRouter`` with ``/v1/orders``, ``/v1/events``, and
        ``/health`` routes.
    """
    router = APIRouter()

    @router.post("/v1/orders", status_code=202)
    async def create_order(dto: OrderCreate) -> OrderRead:
        """
        Create a new order and persist an outbox entry atomically.

        Returns ``202 Accepted`` — the ``OrderCreatedEvent`` will be published
        to the bus by the background ``OutboxRelay``, not within this request.

        Args:
            dto: ``OrderCreate`` JSON body with ``amount``.

        Returns:
            ``OrderRead`` DTO of the newly created order.

        Edge cases:
            - The event is NOT delivered by the time this response is sent —
              callers must poll ``GET /v1/events`` to confirm delivery.
            - If the relay is stopped, the outbox entry persists in the DB and
              will be delivered when the relay restarts.
        """
        return await service.create(dto)

    @router.get("/v1/events")
    async def list_events() -> list[dict]:
        """
        Return a JSON-serializable snapshot of all received ``OrderCreatedEvent``
        instances.

        This endpoint exists only for test verification — it exposes the
        consumer's in-memory list so integration tests can assert that the
        relay delivered the event.

        Returns:
            List of dicts with ``order_id`` and ``amount`` for each received event.
        """
        return [
            {"order_id": e.order_id, "amount": e.amount, "event_id": str(e.event_id)}
            for e in consumer.received
        ]

    @router.get("/health")
    async def health() -> dict:
        """
        Liveness probe.

        Returns:
            ``{"status": "ok"}`` always.
        """
        return {"status": "ok"}

    return router


__all__ = ["build_router"]
