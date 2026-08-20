"""
Shared fixtures for varco_ws integration tests (Plan 012 / RT4, Step 18).

``running_server`` is a small FastAPI app, defined here in the test tree
(never in ``varco_ws/varco_ws/``), exposing:

  - ``GET /ws``  — accepts a real Starlette ``WebSocket``, registers it with
    a module-level ``WebSocketEventBus`` wrapping an ``InMemoryEventBus``.
  - ``GET /sse`` — returns the ``SSEEventBus`` stream over the same bus.
  - ``POST /publish`` — publishes a JSON-decoded event onto the shared
    ``InMemoryEventBus`` so tests can trigger a push from an HTTP client
    without importing the bus module directly (keeps the server fixture
    self-contained and usable from a separate `websockets`/`httpx` client).

Modeled on the working precedent at
``examples/00-full-stack-post-api/example/tests/conftest.py`` — including
its session-scoped-event-loop requirement (that file documents exactly the
failure this fixture would otherwise hit: "uvicorn's background task runs
in one loop while httpx.AsyncClient runs in another — requests never
complete"). ``varco_ws/pyproject.toml`` sets
``asyncio_default_fixture_loop_scope = "session"`` /
``asyncio_default_test_loop_scope = "session"`` to match.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from collections.abc import AsyncIterator

import pytest
import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from varco_core.event.base import Event
from varco_core.event.memory import InMemoryEventBus
from varco_ws.sse import SSEEventBus
from varco_ws.websocket import WebSocketEventBus


class GenericTestEvent(Event):
    """A minimal, schema-stable event used only by this test server."""

    __event_type__ = "varco_ws.test.event"
    payload: str = ""


class PublishRequest(BaseModel):
    payload: str = ""


def _free_port() -> int:
    """Find a free TCP port on the loopback interface (see the example app's
    identical helper for the rationale)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_app() -> tuple[FastAPI, InMemoryEventBus, WebSocketEventBus, SSEEventBus]:
    """Build the FastAPI app + the buses it wraps, so tests can reach both
    the HTTP surface and the underlying `InMemoryEventBus` directly."""
    bus = InMemoryEventBus()
    ws_bus = WebSocketEventBus(bus, event_type=GenericTestEvent, channel="test")
    sse_bus = SSEEventBus(bus, event_type=GenericTestEvent, channel="test")

    app = FastAPI()

    @app.on_event("startup")
    async def _startup() -> None:
        # InMemoryEventBus needs no start()/stop() lifecycle — it holds no
        # external connection, unlike the push adapters wrapping it.
        await ws_bus.start()
        await sse_bus.start()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await ws_bus.disconnect_all()
        await ws_bus.stop()
        await sse_bus.stop()

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        from starlette.websockets import WebSocketDisconnect  # noqa: PLC0415

        await websocket.accept()
        async with ws_bus.connect(websocket):
            try:
                while True:
                    # Must actively call receive() to observe the ASGI
                    # "websocket.disconnect" message — a bare asyncio.sleep()
                    # never sees a client disconnect and leaves the handler
                    # task (and therefore uvicorn's graceful shutdown) stuck
                    # forever, since the client never sends anything either.
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
            except WebSocketDisconnect:
                # connect()'s context manager cleans up on exit.
                return

    @app.get("/sse")
    async def sse_endpoint() -> StreamingResponse:
        async def _gen() -> AsyncIterator[str]:
            async with sse_bus.subscribe() as conn:
                async for message in conn.stream():
                    yield message

        return StreamingResponse(_gen(), media_type="text/event-stream")

    @app.post("/publish")
    async def publish(req: PublishRequest) -> dict:
        await bus.publish(GenericTestEvent(payload=req.payload), channel="test")
        return {"ok": True}

    @app.get("/ws/connected-count")
    async def ws_connected_count() -> dict:
        return {"count": ws_bus.connected_count}

    @app.get("/sse/subscriber-count")
    async def sse_subscriber_count() -> dict:
        return {"count": sse_bus.subscriber_count}

    return app, bus, ws_bus, sse_bus


class RunningServer:
    """Handle returned by the ``running_server`` fixture."""

    def __init__(self, base_url: str, ws_url: str, bus: InMemoryEventBus) -> None:
        self.base_url = base_url
        self.ws_url = ws_url
        self.bus = bus


@pytest.fixture(scope="session")
async def running_server() -> AsyncIterator[RunningServer]:
    """
    Start the test FastAPI app under real uvicorn on an ephemeral port.

    Yields:
        A ``RunningServer`` exposing ``base_url`` (http), ``ws_url``
        (ws), and the underlying ``InMemoryEventBus`` for tests that want
        to publish directly rather than via ``POST /publish``.

    Raises:
        TimeoutError: uvicorn did not start within 15 seconds.
    """
    if not os.environ.get("VARCO_RUN_INTEGRATION"):
        pytest.skip(
            "Integration tests disabled — set VARCO_RUN_INTEGRATION=1 or use -m integration"
        )

    app, bus, _ws_bus, _sse_bus = _build_app()

    port = _free_port()
    config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=port,
        workers=1,
        access_log=False,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    deadline = asyncio.get_event_loop().time() + 15.0
    while not server.started:
        if asyncio.get_event_loop().time() > deadline:
            server.should_exit = True
            await server_task
            raise TimeoutError(
                f"uvicorn did not start within 15 seconds on port {port}"
            )
        await asyncio.sleep(0.05)

    base_url = f"http://127.0.0.1:{port}"
    ws_url = f"ws://127.0.0.1:{port}/ws"

    try:
        yield RunningServer(base_url=base_url, ws_url=ws_url, bus=bus)
    finally:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await server_task
