"""
app.py
======
Application factory for the ``18-realtime-ws-sse`` live scoreboard example.

Demonstrates ``varco_ws`` — ``WebSocketEventBus`` and ``SSEEventBus`` wired
to an ``InMemoryEventBus`` so that ``POST /v1/scores`` pushes events to all
connected WebSocket and SSE clients in real-time.

Architecture::

    POST /v1/scores
        → bus.publish(ScoreUpdatedEvent)
              → WebSocketEventBus._handle_event()
                    → all connected /ws clients receive JSON
              → SSEEventBus._handle_event()
                    → all connected /events clients receive SSE message

No broker, no Docker, no database — all in-process.

Run locally::

    cd examples/18-realtime-ws-sse
    uv run uvicorn app:app --reload

DESIGN: plain FastAPI lifespan + manual wiring over DI container + scan
    ✅ Zero DI ceremony — readers see every bus and adapter instantiation.
    ✅ Lifespan is explicit — ``start()`` / ``stop()`` calls are visible.
    ✅ ``InMemoryEventBus`` is the glue — no external broker needed.
    ❌ Not the idiomatic production pattern; for production use
       ``container.scan("varco_ws")`` + a real bus (Kafka, Redis).

Thread safety:  ✅ Called once at startup; no concurrent access to the factory.
Async safety:   ✅ Synchronous factory; lifespan is an async context manager.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from router import build_router
from varco_core.event import InMemoryEventBus
from varco_ws.sse import SSEEventBus
from varco_ws.websocket import WebSocketEventBus


def create_app() -> FastAPI:
    """
    Build and return the configured FastAPI application.

    Wires together:
    - ``InMemoryEventBus`` — the shared event backbone.
    - ``WebSocketEventBus`` — subscribes to the bus and pushes to WS clients.
    - ``SSEEventBus`` — subscribes to the bus and pushes to SSE clients.
    - Router with all endpoints.

    Returns:
        A configured ``FastAPI`` instance ready for an ASGI server.

    Edge cases:
        - ``ws_bus.start()`` and ``sse_bus.start()`` must be called before
          serving clients; the lifespan context manager handles this.
        - Adapters subscribe to ``Event`` (all events) on channel ``"*"``
          (all channels) by default — fine for a single-event demo.

    Thread safety:  ✅ Called once per process.
    Async safety:   ✅ Synchronous factory; no event loop required at call time.
    """
    # ── Shared infrastructure ─────────────────────────────────────────────────
    # InMemoryEventBus is the backbone — all publish/subscribe flows through it.
    # Both adapters subscribe to it and push incoming events to their clients.
    bus = InMemoryEventBus()

    # WebSocketEventBus and SSEEventBus are adapters — they subscribe to the bus
    # and fan out events to browser clients.  They are NOT AbstractEventBus
    # implementations; they wrap one.
    ws_bus = WebSocketEventBus(bus)
    sse_bus = SSEEventBus(bus)

    # ── Lifespan: start adapters before serving, stop after ───────────────────
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        """
        Subscribe both adapters to the bus before the server accepts connections.

        DESIGN: explicit lifespan over auto-start in constructor
            ✅ Constructors have no event-loop dependency — safe to call
               at module level (e.g. when running tests synchronously).
            ✅ ``start()`` / ``stop()`` calls are clearly visible — no hidden
               subscriptions created during DI scanning.
            ✅ ``stop()`` cancels the subscription and signals SSE connections
               to terminate — graceful shutdown.
        """
        await ws_bus.start()
        await sse_bus.start()
        try:
            yield
        finally:
            await ws_bus.stop()
            await sse_bus.stop()

    # ── FastAPI app ───────────────────────────────────────────────────────────
    app = FastAPI(
        title="Real-time Scoreboard (WS + SSE)",
        version="0.1.0",
        description=(
            "Live scoreboard demo using ``varco_ws``.\n\n"
            "- ``POST /v1/scores`` — publish a score update\n"
            "- ``GET /ws`` — WebSocket; clients receive all score events as JSON\n"
            "- ``GET /events`` — SSE stream; clients receive score events as text/event-stream\n"
            "- ``GET /health`` — liveness probe\n\n"
            "No broker, no Docker, no database — all in-process."
        ),
        lifespan=lifespan,
    )

    # ── Routes ────────────────────────────────────────────────────────────────
    app.include_router(build_router(bus=bus, ws_bus=ws_bus, sse_bus=sse_bus))

    return app


# Module-level app — lets uvicorn use ``uvicorn app:app`` without ``--factory``.
app = create_app()

__all__ = ["app", "create_app"]
