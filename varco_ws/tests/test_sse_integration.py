"""
Real-server SSE integration tests (Plan 012 / RT4, Step 20).

Exercises ``SSEEventBus`` end-to-end over real HTTP, using
``httpx.AsyncClient(...).stream()`` against the ``running_server`` fixture
— the wire-correct counterpart to ``tests/test_ws_bus.py:491-527``'s
in-process stand-in. Also where the ``SSEEventBus`` half of
``test_ws_conformance.py``'s flagged design decision (a) lives — see that
file's module docstring.
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


async def _wait_for_subscriber_count(base_url: str, expected: int, timeout: float = 5.0) -> None:
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            resp = await client.get("/sse/subscriber-count")
            if resp.json()["count"] == expected:
                return
            if asyncio.get_event_loop().time() > deadline:
                raise AssertionError(
                    f"subscriber-count never reached {expected}, last seen {resp.json()['count']}"
                )
            await asyncio.sleep(0.05)


def _parse_data_line(chunk: str) -> dict:
    """Extract the JSON payload from an SSE 'data: {...}\\n\\n' frame."""
    for line in chunk.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise AssertionError(f"no data: line found in SSE chunk: {chunk!r}")


async def test_publish_is_delivered_over_real_sse_stream(running_server) -> None:
    """Real HTTP SSE stream, wire-correct ``data:`` framing."""
    async with (
        httpx.AsyncClient(base_url=running_server.base_url, timeout=10.0) as client,
        client.stream("GET", "/sse") as response,
    ):
        agen = response.aiter_text()

        # Give the server a moment to register the subscriber before
        # publishing, so the event is not discarded (no active subscriber
        # is a documented no-op per SSEEventBus's edge cases).
        await _wait_for_subscriber_count(running_server.base_url, 1)
        await _publish(running_server.base_url, "sse-hello")

        chunk = await asyncio.wait_for(agen.__anext__(), timeout=5.0)
        message = _parse_data_line(chunk)
        assert message["event_type"] == "varco_ws.test.event"
        assert message["data"]["payload"] == "sse-hello"


async def test_multiple_subscribers_each_receive_every_event(running_server) -> None:
    """Two concurrent SSE subscribers both receive the same published event."""
    async with (
        httpx.AsyncClient(base_url=running_server.base_url, timeout=10.0) as c1,
        httpx.AsyncClient(base_url=running_server.base_url, timeout=10.0) as c2,
    ):
        async with c1.stream("GET", "/sse") as r1, c2.stream("GET", "/sse") as r2:
            a1 = r1.aiter_text()
            a2 = r2.aiter_text()

            await _wait_for_subscriber_count(running_server.base_url, 2)
            await _publish(running_server.base_url, "fanout")

            m1 = _parse_data_line(await asyncio.wait_for(a1.__anext__(), timeout=5.0))
            m2 = _parse_data_line(await asyncio.wait_for(a2.__anext__(), timeout=5.0))
            assert m1["data"]["payload"] == "fanout"
            assert m2["data"]["payload"] == "fanout"
