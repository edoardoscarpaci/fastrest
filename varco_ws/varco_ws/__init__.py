"""
varco_ws
========
WebSocket and SSE event bus backends for varco.

Provides ``WebSocketEventBus`` and ``SSEEventBus`` — two ``AbstractEventBus``
implementations that push events to browser clients without polling.

    WebSocketEventBus — bidirectional, full-duplex; suitable for real-time UIs.
    SSEEventBus       — server-to-client only; simpler, HTTP/1.1 compatible.

Quick start::

    # WebSocket (FastAPI example) — bus is the underlying AbstractEventBus
    # (Kafka/Redis/NATS/in-memory); the adapter is a push sidecar, not a
    # standalone bus.
    from varco_ws import WebSocketEventBus
    ws_bus = WebSocketEventBus(bus, event_type=OrderEvent, channel="orders")
    await ws_bus.start()

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket):
        await websocket.accept()
        async with ws_bus.connect(websocket):
            await asyncio.sleep(3600)   # keep connection alive until disconnect

    # SSE (FastAPI example)
    from fastapi.responses import StreamingResponse
    from varco_ws import SSEEventBus
    sse_bus = SSEEventBus(bus, event_type=OrderEvent, channel="orders")
    await sse_bus.start()

    @app.get("/events")
    async def sse_endpoint(request: Request):
        async def generate():
            async with sse_bus.subscribe() as stream:
                async for message in stream:
                    if await request.is_disconnected():
                        break
                    yield message

        return StreamingResponse(generate(), media_type="text/event-stream")
"""

from varco_ws.di import bind_sse_adapter, bind_websocket_adapter
from varco_ws.sse import SSEEventBus, SSEConnection
from varco_ws.websocket import (
    BackpressurePolicy,
    WebSocketEventBus,
    WebSocketConnection,
)

__all__ = [
    "BackpressurePolicy",
    "WebSocketEventBus",
    "WebSocketConnection",
    "SSEEventBus",
    "SSEConnection",
    "bind_websocket_adapter",
    "bind_sse_adapter",
]
