# Example 15 — NATS JetStream Events

Demonstrates **varco_nats** eventing: `NatsEventBus` (JetStream, at-least-once delivery)
wired with `EventConsumer + @listen` and `AbstractEventProducer` as the publish
interface for HTTP handlers.

## What it shows

| Concept | Where |
|---|---|
| `NatsEventBus` as the event backend | `app.py` |
| `BusEventProducer` publishing via HTTP POST | `router.py` |
| `OrderConsumer(EventConsumer)` with `@listen` | `consumer.py` |
| In-memory notification list via GET | `router.py` |
| Integration test with real NATS JetStream | `tests/test_smoke.py` |

## Run locally

```bash
# Start NATS with JetStream
docker run --rm -p 4222:4222 nats:2.10-alpine -js

# From the workspace root
uv run uvicorn examples.15-nats-jetstream-events.app:app --reload
```

Then place an order:

```bash
curl -X POST http://localhost:8000/v1/orders \
  -H 'Content-Type: application/json' \
  -d '{"order_id": "ord-1", "amount": 49.99}'

curl http://localhost:8000/v1/notifications
```

## Run integration tests

Requires Docker daemon running (testcontainers spins up NATS automatically):

```bash
uv run pytest -m integration examples/15-nats-jetstream-events/tests/
```

## Key design decisions

**NATS JetStream vs Redis Pub/Sub**: JetStream is at-least-once (messages are
persisted to disk and redelivered on ack timeout), whereas Redis Pub/Sub is
at-most-once (fire-and-forget). JetStream is a better fit when event loss is
unacceptable and services need to catch up after downtime.

**Pre-started bus for tests**: `httpx.ASGITransport` does not trigger the
FastAPI lifespan, so `create_app` accepts an optional pre-started `bus`
argument. Tests manage the lifecycle; production just passes the URL.

**Unique durable names per test**: Each test fixture invocation gets a UUID
suffix on `durable_name` to avoid JetStream "consumer already exists with
different config" errors when running multiple tests against the same stream.

## Delivery semantics

`NatsEventBusSettings.delivery_semantics` controls the guarantee level:

| Value | Description |
|---|---|
| `at_least_once` | Default. Ack after dispatch — redelivery on crash. |
| `at_most_once` | Ack before dispatch — no duplicates, possible loss. |
| `exactly_once` | At-least-once + producer dedup via `Nats-Msg-Id` header. |
