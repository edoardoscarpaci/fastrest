"""
Real-socket backpressure coverage for ``WebSocketEventBus``
(Plan 018 / RT4, Steps 17-18).

All four ``BackpressurePolicy`` branches are already unit-tested
deterministically against a fake websocket
(``varco_ws/tests/test_ws_bus.py:230,255,279,297,321``), so policy
*semantics* are not the gap. The gap §RT4-backpressure identifies is
whether the module's own central DESIGN claim
(``varco_ws/varco_ws/websocket.py:19-26``) — *"Each client drains at its
own rate — a slow client never blocks others"* — holds **over a real
socket**, with real TCP and real uvicorn write buffers in the path.

Both tests therefore assert about the **fast** client, never about a drop
count on the slow one: the number of messages that fit in the kernel send
buffer + uvicorn's write buffer before ``send_text`` stalls depends on the
OS, the runner and the payload size, and asserting on it is how a test
becomes a flake (§RT4-backpressure, "Rejected — asserting exact drop
counts").

The deterministic knob is the *client*-side ``max_queue=1`` on the slow
client (research 004 §2): once exceeded, the websockets client stops
reading from the network, which propagates backpressure up through
uvicorn's write buffer into ``send_text`` and fills varco's per-client
``asyncio.Queue``.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

pytestmark = pytest.mark.integration

_N = 6000
"""Messages published per test. Must be ≫ the server-side queue depth AND
large enough (with the payload below) to overflow the kernel + uvicorn
buffers. Empirically calibrated (Plan 018 / Step 19): 2000 x 16 KiB and
3000 x 64 KiB both fail assertion (1) on this machine because uvicorn's
websockets_impl buffers writes without an applied ``write_limit``, so the
per-client asyncio.Queue never fills; 6000 x 64 KiB engages backpressure
reliably. If it ever fails again, raise these again -- never relax the
assertion — otherwise assertion (1) fails and the test is telling you N is
too small, not that varco is broken (§Edge cases)."""

_PAYLOAD = "x" * 65536
"""Padding so the total bytes published comfortably exceed the buffers a
stalled client leaves unread."""


async def _publish(base_url: str, payload: str) -> None:
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        resp = await client.post("/publish", json={"payload": payload})
        resp.raise_for_status()


async def _connected_count(base_url: str) -> int:
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        resp = await client.get("/ws/connected-count")
        return int(resp.json()["count"])


async def _poll_connected_count(base_url: str, expected: int, timeout: float = 15.0) -> int:
    """
    Poll ``GET /ws/connected-count`` to a deadline.

    Deterministic wait, never ``asyncio.sleep(n)``-then-assert (Step 18):
    a fixed sleep either makes the test slow or makes it flaky, and on a
    shared CI runner it does both.

    Returns:
        The last observed count (== ``expected`` if it was reached).
    """
    deadline = asyncio.get_event_loop().time() + timeout
    last = await _connected_count(base_url)
    while last != expected and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
        last = await _connected_count(base_url)
    return last


def _seq(raw: str) -> int:
    """Extract the sequence number a test encoded into an event payload."""
    return int(json.loads(raw)["data"]["payload"].split(":", 1)[0])


async def _drain_n(ws, n: int, timeout: float = 30.0) -> list[int]:
    """Receive exactly ``n`` frames from ``ws``, returning their sequence numbers."""
    received: list[int] = []
    deadline = asyncio.get_event_loop().time() + timeout
    while len(received) < n:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        received.append(_seq(await asyncio.wait_for(ws.recv(), timeout=remaining)))
    return received


async def _count_available(ws, budget: float = 2.0) -> int:
    """
    Drain whatever a client has already buffered, then stop.

    Used only on the *slow* client, and only to establish that it received
    strictly fewer than N — never to assert an exact drop count.
    """
    count = 0
    while True:
        try:
            await asyncio.wait_for(ws.recv(), timeout=budget)
        except TimeoutError:
            return count
        except Exception:  # noqa: BLE001 — a closed socket ends the drain
            return count
        count += 1


async def test_slow_client_does_not_starve_a_fast_client(running_server) -> None:
    """
    A client that never reads must not cost a prompt client a single message.

    Assertion order is load-bearing (§RT4-backpressure):
      1. The slow client received strictly fewer than N — proving backpressure
         actually engaged. Without this the test passes vacuously when N or
         the payload is too small.
      2. The fast client received all N.
      3. The fast client's sequence numbers are exactly ``list(range(N))`` —
         a legitimate assertion, not a hopeful one, because RFC 6455
         guarantees in-order delivery on a single connection (research 004
         §3).

    Edge cases:
        - If (1) fails, raise ``_N`` / ``_PAYLOAD``; do NOT relax it.
        - No assertion is made about *which* policy fired on the slow client
          — that stays unit-level, deliberately.
    """
    import websockets

    uri = f"{running_server.ws_url}/bp?policy=drop_newest&queue=2"

    # max_queue=1: the websockets client stops reading from the network once
    # its own buffer is full, which is what propagates TCP backpressure into
    # the server's send_text (research 004 §2).
    async with (
        websockets.connect(uri, max_queue=1) as slow,
        websockets.connect(uri, max_queue=None) as fast,
    ):
        assert await _poll_connected_count(running_server.base_url, 2) == 2

        for i in range(_N):
            await _publish(running_server.base_url, f"{i}:{_PAYLOAD}")

        fast_received = await _drain_n(fast, _N)
        slow_received = await _count_available(slow)

    # (1) Backpressure genuinely engaged on the stalled client.
    assert slow_received < _N, (
        f"the slow client received all {_N} messages — backpressure never "
        f"engaged, so this test would pass vacuously. Raise _N and/or _PAYLOAD; "
        f"do not relax this assertion."
    )
    # (2) The DESIGN claim: a slow client never starves a fast one.
    assert len(fast_received) == _N, (
        f"the fast client received {len(fast_received)}/{_N} messages while a "
        f"slow client was stalled — 'each client drains at its own rate' does "
        f"not hold over a real socket"
    )
    # (3) RFC 6455 in-order delivery on one connection.
    assert fast_received == list(range(_N)), (
        "the fast client's messages arrived out of order or with gaps"
    )


async def test_disconnect_policy_ejects_a_stalled_client(running_server) -> None:
    """
    Under ``DISCONNECT`` a stalled client is ejected from the bus while a
    prompt client keeps receiving everything.

    ⚠️ ``DISCONNECT`` does **not** close the WebSocket. ``_handle_event``
    (``varco_ws/varco_ws/websocket.py:600-609``) only discards the connection
    from ``self._connections`` and calls ``_stop_drain()`` on it — the socket
    stays TCP-connected and simply stops receiving. So the observable is
    ``WebSocketEventBus.connected_count`` (exposed by the test conftest as
    ``GET /ws/connected-count``), **not** a client-side close frame. A test
    asserting on a close frame would be asserting behaviour varco does not
    implement.

    Edge cases:
        - ``connected_count`` is polled to a deadline, never slept-then-read.
    """
    import websockets

    uri = f"{running_server.ws_url}/bp?policy=disconnect&queue=1"

    async with (
        websockets.connect(uri, max_queue=1) as slow,
        websockets.connect(uri, max_queue=None) as fast,
    ):
        assert await _poll_connected_count(running_server.base_url, 2) == 2

        for i in range(_N):
            await _publish(running_server.base_url, f"{i}:{_PAYLOAD}")

        remaining = await _poll_connected_count(running_server.base_url, 1)
        fast_received = await _drain_n(fast, _N)
        slow_received = await _count_available(slow)

    assert slow_received < _N, (
        f"the slow client received all {_N} messages — its queue never filled, "
        f"so DISCONNECT could not have fired. Raise _N and/or _PAYLOAD."
    )
    assert remaining == 1, (
        f"connected_count never dropped from 2 to 1 — the DISCONNECT policy "
        f"did not eject the stalled client (last seen: {remaining})"
    )
    assert len(fast_received) == _N, (
        f"the fast client received {len(fast_received)}/{_N} messages while a "
        f"stalled client was being ejected"
    )
    assert fast_received == list(range(_N)), (
        "the fast client's messages arrived out of order or with gaps"
    )
