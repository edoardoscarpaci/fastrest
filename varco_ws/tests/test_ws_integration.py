"""
Real-server WebSocket integration tests (Plan 012 / RT4, Step 19).

Exercises ``WebSocketEventBus`` end-to-end against a real ``uvicorn``
server (the ``running_server`` fixture in ``tests/conftest.py``) using a
real wire connection via the ``websockets`` client library — never
``MockWebSocket`` (see ``tests/test_ws_bus.py:44`` for the mocked
counterpart these tests complement, not replace).

This is also where the ``WebSocketEventBus`` conformance coverage flagged
in ``test_ws_conformance.py``'s module docstring lives — Design decision
(a) from that docstring: real-server WS/SSE coverage belongs in these
bespoke integration files, not in a reused ``EventBusConformance`` subclass
(a push adapter is not an ``AbstractEventBus``).
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

pytestmark = pytest.mark.integration


async def _publish(base_url: str, payload: str) -> None:
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        resp = await client.post("/publish", json={"payload": payload})
        resp.raise_for_status()


async def _wait_for_connected_count(
    base_url: str, expected: int, timeout: float = 5.0
) -> None:
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            resp = await client.get("/ws/connected-count")
            if resp.json()["count"] == expected:
                return
            if asyncio.get_event_loop().time() > deadline:
                raise AssertionError(
                    f"connected-count never reached {expected}, "
                    f"last seen {resp.json()['count']}"
                )
            await asyncio.sleep(0.05)


async def test_publish_is_delivered_over_real_websocket(running_server) -> None:
    """Connect a real WS client, publish, and receive the frame."""
    import websockets

    async with websockets.connect(running_server.ws_url) as ws:
        await _wait_for_connected_count(running_server.base_url, 1)
        await _publish(running_server.base_url, "hello")

        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
        message = json.loads(raw)
        assert message["event_type"] == "varco_ws.test.event"
        assert message["data"]["payload"] == "hello"


async def test_connection_pooling_multiple_clients_each_receive_broadcast(
    running_server,
) -> None:
    """N concurrent clients each receive the same broadcast; connected_count
    tracks connect/disconnect accurately."""
    import websockets

    n = 3
    async with (
        websockets.connect(running_server.ws_url) as ws1,
        websockets.connect(running_server.ws_url) as ws2,
        websockets.connect(running_server.ws_url) as ws3,
    ):
        await _wait_for_connected_count(running_server.base_url, n)

        await _publish(running_server.base_url, "broadcast")

        for ws in (ws1, ws2, ws3):
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            message = json.loads(raw)
            assert message["data"]["payload"] == "broadcast"

    # All three disconnected on context exit — the server evicts them.
    await _wait_for_connected_count(running_server.base_url, 0)


async def test_message_ordering_single_connection(running_server) -> None:
    """100 sequenced events arrive in publish order on a single connection."""
    import websockets

    async with websockets.connect(running_server.ws_url) as ws:
        await _wait_for_connected_count(running_server.base_url, 1)

        n = 100
        for i in range(n):
            await _publish(running_server.base_url, str(i))

        received: list[str] = []
        for _ in range(n):
            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            received.append(json.loads(raw)["data"]["payload"])

        assert received == [str(i) for i in range(n)]

    await _wait_for_connected_count(running_server.base_url, 0)


async def test_reconnect_after_drop_resumes_receiving(running_server) -> None:
    """A client drops mid-stream, the server evicts it (connected_count
    returns to 0, no leaked drain task), and a reconnect resumes delivery."""
    import websockets

    async with websockets.connect(running_server.ws_url):
        await _wait_for_connected_count(running_server.base_url, 1)
    # Context exit closes the socket — the server must notice and evict.
    await _wait_for_connected_count(running_server.base_url, 0)

    async with websockets.connect(running_server.ws_url) as ws:
        await _wait_for_connected_count(running_server.base_url, 1)
        await _publish(running_server.base_url, "resumed")
        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
        assert json.loads(raw)["data"]["payload"] == "resumed"

    await _wait_for_connected_count(running_server.base_url, 0)
