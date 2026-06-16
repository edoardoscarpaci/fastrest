# 18 — Real-time Scoreboard (WebSocket + SSE)

Demonstrates `varco_ws` — `WebSocketEventBus` and `SSEEventBus` fan-out to
browser clients. All in-process; no broker, no Docker required.

## What you'll learn

| Feature | File |
|---|---|
| `WebSocketEventBus` — bidirectional push to `/ws` clients | `router.py`, `app.py` |
| `SSEEventBus` — server-to-client stream at `/events` | `router.py`, `app.py` |
| Publishing `DomainEvent` via `AbstractEventBus.publish()` | `router.py` |
| Lifespan wiring — `start()` / `stop()` for both adapters | `app.py` |
| In-process testing without a real broker | `tests/test_smoke.py` |

## Architecture

```
POST /v1/scores
    → InMemoryEventBus.publish(ScoreUpdatedEvent)
          → WebSocketEventBus._handle_event()
                → all connected /ws clients receive JSON
          → SSEEventBus._handle_event()
                → all connected /events clients receive SSE
```

`WebSocketEventBus` and `SSEEventBus` are **adapters**, not bus implementations.
They subscribe to an existing `AbstractEventBus` and push events to browser
clients. Application code always publishes via the underlying bus.

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/scores` | Publish `ScoreUpdatedEvent` `{team, score}` |
| `GET` | `/ws` | WebSocket — receives all score events as JSON |
| `GET` | `/events` | SSE stream — receives all score events as `text/event-stream` |
| `GET` | `/health` | Liveness probe |

## Run locally

```bash
cd examples/18-realtime-ws-sse
uv run uvicorn app:app --reload
```

Connect a WebSocket client:

```bash
# wscat (npm install -g wscat)
wscat -c ws://localhost:8000/ws
```

Subscribe to SSE:

```bash
curl -N http://localhost:8000/events
```

Publish a score update:

```bash
curl -X POST http://localhost:8000/v1/scores \
     -H "Content-Type: application/json" \
     -d '{"team": "Red", "score": 3}'
```

## Wire format

### WebSocket message

```json
{
  "event_type": "scoreboard.score_updated",
  "event_id": "<uuid>",
  "data": { "team": "Red", "score": 3 }
}
```

### SSE message

```
data: {"event_type": "scoreboard.score_updated", "event_id": "<uuid>", "data": {"team": "Red", "score": 3}}

```

## Key design decisions

**Adapters, not buses** — `WebSocketEventBus` and `SSEEventBus` wrap an
`AbstractEventBus`; they are not implementations of it. Application code
always publishes to the underlying bus.

**Per-client `asyncio.Queue`** — each WebSocket client gets its own drain
task and queue. A slow client never blocks other clients (backpressure
policy: `DROP_OLDEST` by default).

**Manual wiring in the example** — the app instantiates `InMemoryEventBus`,
`WebSocketEventBus`, and `SSEEventBus` directly to keep the dependency
graph visible. Production apps should use `container.scan("varco_ws")`.

## Running the tests

```bash
uv run pytest examples/18-realtime-ws-sse/tests/ -v
```
