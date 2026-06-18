"""
router.py
=========
HTTP, WebSocket, and SSE endpoints for the ``18-realtime-ws-sse`` live
scoreboard example.

Routes
------
``POST /v1/scores``
    Publish a ``ScoreUpdatedEvent`` to the in-memory bus.  The WebSocket and
    SSE adapters fan it out to all connected clients.

``GET /ws``
    WebSocket endpoint — bidirectional; clients receive ``ScoreUpdatedEvent``
    messages as JSON objects.

``GET /events``
    SSE endpoint — server-to-client only; clients receive
    ``ScoreUpdatedEvent`` messages as ``text/event-stream``.

``GET /health``
    Always returns ``200 OK``.

DESIGN: plain FastAPI APIRouter + plain bus reference over DI container
    ✅ Zero DI boilerplate — keeps the example focused on the WS/SSE wiring.
    ✅ ``AbstractEventBus`` is passed directly; services/handlers never hold it
       (only the adapters do — acceptable infrastructure exception).
    ✅ Readers see the exact fan-out topology without container indirection.
    ❌ Not idiomatic for large apps — use DI + scan for production wiring.

Thread safety:  ✅ Handler functions are stateless; shared state lives in the
                   bus and adapters (both designed for single-event-loop use).
Async safety:   ✅ All handlers are ``async def``.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from varco_core.event import AbstractEventBus
from varco_ws.sse import SSEEventBus
from varco_ws.websocket import WebSocketEventBus

from events import ScoreUpdatedEvent


# ── Request / response models ─────────────────────────────────────────────────


class ScoreRequest(BaseModel):
    """
    Body for ``POST /v1/scores``.

    Attributes:
        team:  Name of the team whose score changed.
        score: New absolute score for the team (non-negative).
    """

    team: str = Field(..., description="Team name", min_length=1)
    score: int = Field(..., ge=0, description="New score (must be ≥ 0)")


class ScoreResponse(BaseModel):
    """
    HTTP 200 response for ``POST /v1/scores``.

    Attributes:
        team:      Name of the team.
        score:     Published score.
        published: Always ``True`` — confirms the event was enqueued.
    """

    team: str
    score: int
    published: bool = True


# ── Router factory ────────────────────────────────────────────────────────────


def build_router(
    bus: AbstractEventBus,
    ws_bus: WebSocketEventBus,
    sse_bus: SSEEventBus,
) -> APIRouter:
    """
    Build and return the FastAPI APIRouter with all endpoints.

    All three infrastructure objects are passed in at construction time so
    the router is fully testable without a real HTTP server — just pass in
    ``InMemoryEventBus``, ``WebSocketEventBus``, and ``SSEEventBus`` directly.

    Args:
        bus:     The underlying ``AbstractEventBus`` used to publish events.
        ws_bus:  The ``WebSocketEventBus`` adapter (must be started before use).
        sse_bus: The ``SSEEventBus`` adapter (must be started before use).

    Returns:
        A configured ``APIRouter`` ready to be mounted on a FastAPI app.

    Edge cases:
        - ``ws_bus`` and ``sse_bus`` must have ``start()`` called before serving
          clients; the lifespan handler in ``app.py`` is responsible for this.
        - Events published before any client connects are delivered to nobody —
          both adapters discard events when their connection sets are empty.

    Thread safety:  ✅ ``APIRouter`` is immutable after construction.
    Async safety:   ✅ No I/O during construction.
    """
    router = APIRouter()

    # ── GET /health ───────────────────────────────────────────────────────────

    @router.get("/health")
    async def health() -> dict:
        """
        Liveness probe.

        Returns:
            ``{"status": "ok"}`` — always 200.
        """
        return {"status": "ok"}

    # ── POST /v1/scores ───────────────────────────────────────────────────────

    @router.post("/v1/scores", response_model=ScoreResponse)
    async def publish_score(body: ScoreRequest) -> ScoreResponse:
        """
        Publish a ``ScoreUpdatedEvent`` to the in-memory bus.

        The WebSocket and SSE adapters pick it up from the bus and fan it
        out to all connected clients.

        Args:
            body: Score update payload (team + score).

        Returns:
            ``ScoreResponse`` echoing the published values.

        Edge cases:
            - If no clients are connected, the event is silently discarded
              by the adapters.
            - The publish is fire-and-forget; this endpoint does not wait
              for delivery confirmation.
        """
        event = ScoreUpdatedEvent(team=body.team, score=body.score)
        # publish() enqueues the event for all subscribers.
        # The adapters (ws_bus, sse_bus) have already subscribed to the bus.
        await bus.publish(event)
        return ScoreResponse(team=body.team, score=body.score)

    # ── GET /ws ───────────────────────────────────────────────────────────────

    @router.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        """
        WebSocket endpoint — connects the client to the ``WebSocketEventBus``.

        Accepts the WebSocket handshake, registers the client with the
        adapter, and holds the connection open until the client disconnects.
        The adapter's drain task delivers events asynchronously.

        Args:
            websocket: FastAPI-injected WebSocket connection.

        Edge cases:
            - ``WebSocketDisconnect`` is caught so the connection context
              exits cleanly — ``ws_bus.connect`` handles drain-task cleanup.
            - Messages are sent as JSON strings: ``{"event_type": "...", ...}``
        """
        await websocket.accept()
        # ws_bus.connect() registers the client, starts the drain task,
        # and removes the client on context exit.
        async with ws_bus.connect(websocket):
            try:
                # Keep the connection alive by waiting for client messages.
                # We don't use client messages — this is server-push only.
                # An infinite wait on receive is the idiomatic keep-alive pattern.
                while True:
                    # Wait indefinitely — disconnect raises WebSocketDisconnect.
                    await asyncio.sleep(30)
            except WebSocketDisconnect:
                # Client closed the connection — context manager handles cleanup.
                pass

    # ── GET /events ───────────────────────────────────────────────────────────

    @router.get("/events")
    async def sse_endpoint() -> StreamingResponse:
        """
        SSE endpoint — streams ``ScoreUpdatedEvent`` to the client.

        Creates a new SSE subscription, builds an async generator that yields
        SSE-formatted messages, and wraps it in a ``StreamingResponse``.

        Returns:
            A ``StreamingResponse`` with ``Content-Type: text/event-stream``.

        DESIGN: async generator + StreamingResponse over a raw ASGI response
            ✅ FastAPI handles chunked encoding and connection cleanup.
            ✅ The ``subscribe()`` context manager removes the connection from
               the adapter when the generator is garbage-collected / closed.
            ❌ The generator can only detect client disconnect via the
               ``StreamingResponse`` lifecycle — not an explicit disconnect hook.
               Clients that disappear without a TCP RST leave stale connections
               until the next event delivery attempt raises.

        Edge cases:
            - An ``asyncio.CancelledError`` inside the generator propagates
              through ``StreamingResponse`` — the connection is cleaned up by
              the ``subscribe()`` context manager on generator close.
        """

        async def _generate():
            # subscribe() registers a new SSEConnection and removes it on exit.
            async with sse_bus.subscribe() as conn:
                async for message in conn.stream():
                    yield message

        return StreamingResponse(
            _generate(),
            media_type="text/event-stream",
            # Disable buffering so messages are flushed immediately.
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # Tells nginx to disable proxy buffering.
            },
        )

    return router


__all__ = ["build_router", "ScoreRequest", "ScoreResponse"]
