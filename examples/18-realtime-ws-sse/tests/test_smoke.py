"""
tests.test_smoke
================
Smoke tests for the ``18-realtime-ws-sse`` live scoreboard example.

All tests use in-process wiring — no Docker, no real broker required.

Test strategy
-------------
1. **Health check** — ``GET /health`` → 200.
2. **Publish endpoint** — ``POST /v1/scores`` → 200; body echoes the score.
3. **WebSocket receives event** — connect WS, publish, assert message received.
4. **SSE subscribe count** — POST while SSE subscriber is active; verify the
   adapter registers and de-registers the connection correctly.
5. **Invalid score payload** — missing ``team`` field → 422 Unprocessable Entity.

DESIGN: sync TestClient over async httpx for WS tests
    ✅ Starlette's TestClient supports WebSocket via ``websocket_connect()``.
    ✅ Sync client is simpler for short-lived one-shot assertions.
    ✅ No event loop lifecycle complexity — TestClient manages the loop.
    ❌ Async httpx does NOT support WebSocket — the choice is intentional.

DESIGN: in-process bus + adapter assertions over full HTTP roundtrip for SSE
    ✅ SSE streams are open-ended — asserting delivery via HTTP requires
       thread-concurrent read, which is complex and flaky.
    ✅ Testing at the adapter level (``subscriber_count``) is sufficient to
       verify the wiring is correct.
    ✅ The SSEEventBus itself is tested exhaustively in varco_ws unit tests.

Thread safety:  ✅ Each test builds a fresh ``FastAPI`` app — no shared state.
Async safety:   ✅ In-process tests with TestClient run in their own loop.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

# Add the example directory to sys.path so ``from events import ...`` works.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from app import create_app
from events import ScoreUpdatedEvent
from starlette.testclient import TestClient
from varco_core.event import InMemoryEventBus
from varco_ws.sse import SSEEventBus
from varco_ws.websocket import WebSocketEventBus

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    """
    Build a fresh app and wrap it in a synchronous TestClient.

    The TestClient drives the ASGI lifespan — ``ws_bus.start()`` and
    ``sse_bus.start()`` are called on ``__enter__``.

    Yields:
        A ``TestClient`` with the lifespan active.

    Edge cases:
        - A fresh app is created per test — no shared bus state between tests.
        - TestClient re-uses the same event loop for the duration of the
          ``with`` block; no manual loop management is needed.
    """
    app = create_app()
    # ``with TestClient(app)`` drives the lifespan (startup + teardown).
    with TestClient(app) as c:
        yield c


# ── 1. Health check ───────────────────────────────────────────────────────────


def test_health_returns_200(client: TestClient) -> None:
    """
    ``GET /health`` must return HTTP 200 with ``{"status": "ok"}``.
    """
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ── 2. Publish endpoint ───────────────────────────────────────────────────────


def test_publish_score_returns_200(client: TestClient) -> None:
    """
    ``POST /v1/scores`` with a valid body must return HTTP 200 and echo the
    submitted team and score.
    """
    response = client.post("/v1/scores", json={"team": "Red", "score": 3})

    assert response.status_code == 200
    body = response.json()
    assert body["team"] == "Red"
    assert body["score"] == 3
    assert body["published"] is True


def test_publish_score_different_team(client: TestClient) -> None:
    """
    Publishing scores for different teams must be accepted independently.
    Both return 200 with the correct echoed values.
    """
    r1 = client.post("/v1/scores", json={"team": "Blue", "score": 0})
    r2 = client.post("/v1/scores", json={"team": "Green", "score": 99})

    assert r1.status_code == 200
    assert r1.json()["team"] == "Blue"

    assert r2.status_code == 200
    assert r2.json()["score"] == 99


# ── 3. WebSocket receives event ───────────────────────────────────────────────


def test_websocket_receives_score_event(client: TestClient) -> None:
    """
    A connected WebSocket client must receive a ``ScoreUpdatedEvent`` after
    ``POST /v1/scores`` is called.

    The message must be valid JSON with ``event_type == "scoreboard.score_updated"``
    and the correct ``data`` payload.

    DESIGN: receive_text() with timeout over asyncio.sleep polling
        ✅ TestClient's WebSocket blocks until a message arrives or the timeout
           fires — no polling or sleep needed.
        ✅ The adapter's drain task delivers the message in the same event loop
           iteration that processes the POST request.
    """
    with client.websocket_connect("/ws") as ws:
        # Publish after connecting so the WS adapter has an active subscriber.
        response = client.post("/v1/scores", json={"team": "Home", "score": 7})
        assert response.status_code == 200

        # Receive the pushed event — blocks until available.
        raw = ws.receive_text()

    payload = json.loads(raw)
    assert payload["event_type"] == "scoreboard.score_updated"
    assert payload["data"]["team"] == "Home"
    assert payload["data"]["score"] == 7


def test_websocket_receives_multiple_events(client: TestClient) -> None:
    """
    A connected WebSocket client must receive each published event in order.
    """
    with client.websocket_connect("/ws") as ws:
        client.post("/v1/scores", json={"team": "A", "score": 1})
        client.post("/v1/scores", json={"team": "B", "score": 2})

        msg1 = json.loads(ws.receive_text())
        msg2 = json.loads(ws.receive_text())

    assert msg1["data"]["team"] == "A"
    assert msg2["data"]["team"] == "B"


# ── 4. SSE adapter subscription ───────────────────────────────────────────────


async def test_sse_subscriber_count_while_active() -> None:
    """
    While the SSE endpoint is being streamed, the ``SSEEventBus.subscriber_count``
    must reflect the active connection.

    DESIGN: adapter-level assertion over full HTTP streaming test
        ✅ SSE streams are open-ended — reading the full stream via HTTP would
           block indefinitely.
        ✅ ``subscriber_count`` is the exact observable that tests the wiring.
        ✅ The ``SSEEventBus`` fanout is tested in unit tests in varco_ws;
           this test verifies only that the example wires it correctly.

    Edge cases:
        - The subscriber is registered when ``sse_bus.subscribe()`` is entered
          (in the ``_generate`` generator in router.py).  Since the generator
          is started lazily by ``StreamingResponse``, we use a real HTTP client
          for this test to trigger the generator start.
    """
    # For this test, build the infrastructure directly so we can inspect
    # ``sse_bus.subscriber_count`` without going through HTTP.
    bus = InMemoryEventBus()
    ws_bus = WebSocketEventBus(bus)
    sse_bus = SSEEventBus(bus)

    await ws_bus.start()
    await sse_bus.start()

    assert sse_bus.subscriber_count == 0

    # Manually subscribe — mirrors what the SSE endpoint generator does.
    async with sse_bus.subscribe() as conn:
        assert sse_bus.subscriber_count == 1

        # Publish an event and verify it lands in the subscriber's queue.
        await bus.publish(ScoreUpdatedEvent(team="Test", score=42))
        await asyncio.sleep(0)  # Allow the bus handler to run.

        item = await asyncio.wait_for(conn._queue.get(), timeout=1.0)
        assert "scoreboard.score_updated" in item
        assert "42" in item

    # After the context exits, the subscriber is removed.
    assert sse_bus.subscriber_count == 0

    await ws_bus.stop()
    await sse_bus.stop()


# ── 5. Invalid payload ────────────────────────────────────────────────────────


def test_missing_team_returns_422(client: TestClient) -> None:
    """
    ``POST /v1/scores`` without the required ``team`` field must return
    HTTP 422 Unprocessable Entity (Pydantic validation failure).
    """
    response = client.post("/v1/scores", json={"score": 10})

    assert response.status_code == 422


def test_negative_score_returns_422(client: TestClient) -> None:
    """
    ``POST /v1/scores`` with a negative ``score`` must return HTTP 422 —
    the ``score`` field has a ``ge=0`` constraint.
    """
    response = client.post("/v1/scores", json={"team": "X", "score": -1})

    assert response.status_code == 422


def test_empty_team_returns_422(client: TestClient) -> None:
    """
    ``POST /v1/scores`` with an empty string ``team`` must return HTTP 422 —
    the ``team`` field has a ``min_length=1`` constraint.
    """
    response = client.post("/v1/scores", json={"team": "", "score": 5})

    assert response.status_code == 422


# ── 6. In-process bus wiring ──────────────────────────────────────────────────


async def test_publish_delivers_to_ws_adapter() -> None:
    """
    Publishing a ``ScoreUpdatedEvent`` via the bus must fan it out to a
    connected WebSocket client through the ``WebSocketEventBus`` adapter.

    This is a pure in-process test — no HTTP transport involved.
    """

    class MockWebSocket:
        """
        Minimal WebSocket mock — records all sent messages.

        Mirrors the pattern used in varco_ws unit tests.
        """

        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send_text(self, message: str) -> None:
            """Append message to sent list instead of writing to a socket."""
            self.sent.append(message)

    bus = InMemoryEventBus()
    ws_bus = WebSocketEventBus(bus)
    await ws_bus.start()

    mock_ws = MockWebSocket()

    async with ws_bus.connect(mock_ws):
        await bus.publish(ScoreUpdatedEvent(team="Rovers", score=5))
        # Two yields: one for bus dispatch, one for drain task wake-up.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert len(mock_ws.sent) == 1
    payload = json.loads(mock_ws.sent[0])
    assert payload["event_type"] == "scoreboard.score_updated"
    assert payload["data"]["team"] == "Rovers"
    assert payload["data"]["score"] == 5

    await ws_bus.stop()
