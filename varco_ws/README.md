# varco-ws

WebSocket and Server-Sent Events (SSE) push adapters for the
[varco](https://github.com/edoardoscarpaci/varco) event system.

`WebSocketEventBus` and `SSEEventBus` are **not** standalone `AbstractEventBus`
implementations — they are push *adapters* that subscribe to an existing
`AbstractEventBus` (Kafka, Redis, NATS, in-memory, …) and forward matching
events to connected browser clients. The underlying bus still owns routing,
retries, and DLQ; `varco_ws` only handles the last hop to the browser.

| Adapter | Protocol | Direction | Best for |
|---|---|---|---|
| `WebSocketEventBus` | WebSocket | bidirectional (push side only used here) | real-time UIs needing full-duplex |
| `SSEEventBus` | Server-Sent Events | server → client only | simple, proxy/CDN-friendly live feeds |

---

## Installation

```bash
uv add varco-ws
# or: pip install varco-ws
```

`varco_ws` depends only on `varco_core` — no third-party broker client. It is
framework-agnostic; the examples below use FastAPI/Starlette because that is
the common pairing with the rest of varco.

---

## Quick start — WebSocket

```python
from varco_ws import WebSocketEventBus
from varco_core.event import Event

class OrderPlacedEvent(Event):
    __event_type__ = "order.placed"
    order_id: str

# bus is any AbstractEventBus (KafkaEventBus, RedisEventBus, InMemoryEventBus, ...)
ws_bus = WebSocketEventBus(bus, event_type=OrderPlacedEvent, channel="orders")

@app.on_event("startup")
async def startup() -> None:
    await ws_bus.start()          # subscribes to the underlying bus

@app.websocket("/ws/orders")
async def orders_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    async with ws_bus.connect(websocket):
        await asyncio.sleep(3600)  # keep the connection open until the client disconnects

@app.on_event("shutdown")
async def shutdown() -> None:
    await ws_bus.stop()
```

Each event is serialized to JSON and pushed to every connected client:

```json
{"event_type": "order.placed", "event_id": "...", "data": {"order_id": "abc"}}
```

## Quick start — Server-Sent Events

```python
from fastapi.responses import StreamingResponse
from varco_ws import SSEEventBus

sse_bus = SSEEventBus(bus, event_type=OrderPlacedEvent, channel="orders")
await sse_bus.start()

@app.get("/events/orders")
async def orders_sse(request: Request):
    async def generate():
        async with sse_bus.subscribe() as stream:
            async for message in stream:
                if await request.is_disconnected():
                    break
                yield message

    return StreamingResponse(generate(), media_type="text/event-stream")
```

---

## Constructor reference

Both adapters share the same shape:

```python
WebSocketEventBus(
    bus: AbstractEventBus,        # required — the underlying event bus
    *,
    event_type: type[Event] = Event,      # default: subscribe to all event types
    channel: str = "*",                    # default: subscribe to all channels
    max_queue_size: int = 100,             # per-client outbound queue depth (0 = unbounded)
    backpressure_policy: BackpressurePolicy = BackpressurePolicy.DROP_OLDEST,
)

SSEEventBus(
    bus: AbstractEventBus,
    *,
    event_type: type[Event] = Event,
    channel: str = "*",
    max_queue_size: int = 100,
    # SSEEventBus has no backpressure_policy — a full subscriber queue blocks
    # the put() call (natural backpressure) rather than dropping/disconnecting.
)
```

`WebSocketEventBus.connect(websocket, *, connection_id=None, max_queue_size=None,
backpressure_policy=None)` and `SSEEventBus.subscribe()` are both async context
managers that register/unregister the client automatically, even on exception.

### Backpressure policy (WebSocket only)

`BackpressurePolicy` governs what happens when a slow client's outbound queue fills up:

| Policy | Effect |
|---|---|
| `DROP_OLDEST` (default) | discard the oldest buffered message to make room for the new one — best for live feeds where freshness matters more than completeness |
| `DROP_NEWEST` | discard the incoming message; queue contents are preserved — best when clients must receive events in order from the start |
| `BLOCK` | `await` until space is available — guarantees delivery but can stall other clients if many are slow |
| `DISCONNECT` | eject the client immediately once its queue is full |

Each client drains from its own `asyncio.Queue` via a dedicated background task —
a slow client never blocks delivery to other clients or the bus handler itself.

---

## Lifecycle

Both adapters must be explicitly started and stopped — they are **not**
started automatically, even when DI-managed:

```python
await ws_bus.start()   # subscribes to the underlying AbstractEventBus
await ws_bus.stop()    # cancels the subscription and disconnects all clients

async with ws_bus:     # WebSocketEventBus also supports async context-manager use
    ...
```

---

## DI integration

`WebSocketEventBus` and `SSEEventBus` are `@Singleton`-decorated and inject
`AbstractEventBus` — they self-register when `container.scan("varco_ws",
recursive=True)` is called. An `AbstractEventBus` implementation must already
be registered in the container before scanning `varco_ws`.

```python
from varco_redis.di import bootstrap as redis_bootstrap
from varco_ws.di import bootstrap as ws_bootstrap

redis_bootstrap()               # registers AbstractEventBus
ws_bootstrap()                  # scans varco_ws, finds both adapters

ws_bus = container.get(WebSocketEventBus)
sse_bus = container.get(SSEEventBus)

# Start/stop in the FastAPI lifespan handler — the container never calls
# start()/stop() itself.
@asynccontextmanager
async def lifespan(app):
    await ws_bus.start()
    await sse_bus.start()
    yield
    await ws_bus.stop()
    await sse_bus.stop()
```

The scan-discovered singletons subscribe to **all** events on **all**
channels (`event_type=Event, channel="*"`). For a per-channel adapter, use
`bind_websocket_adapter()` / `bind_sse_adapter()` instead:

```python
from varco_ws.di import bootstrap, bind_websocket_adapter, bind_sse_adapter
from myapp.events import OrderEvent

bootstrap(container)
bind_websocket_adapter(container, event_type=OrderEvent, channel="orders")
bind_sse_adapter(container, event_type=OrderEvent, channel="orders")

orders_ws = container.get(WebSocketEventBus)    # per-channel singleton
orders_sse = container.get(SSEEventBus)          # per-channel singleton
```

---

## Running tests

```bash
uv run pytest varco_ws/tests/
```

No external broker is required — `varco_ws` tests exercise the adapters
against `InMemoryEventBus`.

---

## Caveats

- Not thread-safe — use each adapter instance from a single event loop.
- `WebSocketEventBus`/`SSEEventBus` are push sidecars, not `AbstractEventBus`
  implementations — they cannot be passed anywhere an `AbstractEventBus` is
  expected. Use the underlying bus for service-to-service messaging.
- Memory grows with connected clients × `max_queue_size`; size accordingly for
  high fan-out.
