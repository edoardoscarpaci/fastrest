# varco-nats
[![PyPI version](https://img.shields.io/pypi/v/varco-nats.svg)](https://pypi.org/project/varco-nats/)

NATS **JetStream** event bus backend for [varco](https://github.com/edoardoscarpaci/varco) —
`NatsEventBus` built on [nats-py](https://github.com/nats-io/nats.py).

`varco_nats` implements `varco_core`'s `AbstractEventBus`, `ChannelManager`,
`AbstractDeadLetterQueue` and `HealthCheck` contracts on top of NATS JetStream —
the persistent, at-least-once layer of NATS (its analogue of Apache Kafka).

> **JetStream only.** Core NATS at-most-once pub/sub is intentionally not
> exposed. If you need fire-and-forget delivery, use `varco_redis`'s Pub/Sub bus.

---

## Installation

```bash
uv add varco-nats          # or: pip install varco-nats
```

Requires a running NATS server with **JetStream enabled** (`nats-server -js`).

---

## Quick start

```python
from varco_nats import NatsEventBus, NatsEventBusSettings
from varco_core.event import BusEventProducer, EventConsumer, listen


class OrderPlacedEvent(Event):
    __event_type__ = "order.placed"
    order_id: str


config = NatsEventBusSettings(
    servers="nats://localhost:4222",
    durable_name="order-service",  # the JetStream analogue of a Kafka group_id
)

async with NatsEventBus(config) as bus:

    class OrderConsumer(EventConsumer):
        @listen(OrderPlacedEvent, channel="orders")
        async def on_placed(self, event: OrderPlacedEvent) -> None:
            print(f"Order placed: {event.order_id}")

    OrderConsumer().register_to(bus)

    producer = BusEventProducer(bus)
    await producer._produce(OrderPlacedEvent(order_id="abc"), channel="orders")
```

---

## How channels map to NATS

Unlike Kafka — where each channel is a topic — NATS channels are **subjects
under a single JetStream stream's wildcard**:

| varco concept | NATS concept |
|---------------|--------------|
| stream (`stream_name`) | one JetStream stream capturing `{subject_prefix}.>` |
| channel `"orders"` | subject `{subject_prefix}.{channel_prefix}orders` |
| `durable_name` | base name for durable consumers (≈ Kafka consumer group) |
| `CHANNEL_ALL` | local-only filter — opens **no** consumer |

The bus creates the backing stream automatically on `start()` when
`auto_create_stream=True` (the default).

---

## Delivery semantics

`NatsEventBus` mirrors `KafkaEventBus`: JetStream redelivery is the
**broker-level** safety net, while **handler-level** retries are the job of
varco's `@listen(retry_policy=..., dlq=...)` machinery.

| `delivery_semantics` | Behaviour |
|----------------------|-----------|
| `at_most_once` | Message acked **before** dispatch. Crash → message lost. No duplicates. |
| `at_least_once` *(default)* | Message acked **after** dispatch. Crash before ack → JetStream redelivers. **A handler that merely raises (no crash) also redelivers** (Plan 019 / RT2-B): the message is `nak()`ed for immediate redelivery, bounded by `max_deliver`. |
| `exactly_once` | As `at_least_once` + every publish carries `Nats-Msg-Id = event.event_id`, so JetStream drops producer-retry duplicates within `duplicate_window`. |

```python
from varco_nats import NatsDeliverySemantics

config = NatsEventBusSettings(
    servers="nats://localhost:4222",
    delivery_semantics=NatsDeliverySemantics.EXACTLY_ONCE,
    max_deliver=5,  # redelivery budget — JetStream's own default is unlimited
)
```

⚠️ **BREAKING (behaviour), Plan 019 / RT2-B**: prior to this, a handler that
raised under `AT_LEAST_ONCE`/`EXACTLY_ONCE` was silently acked and never
redelivered — only a process crash triggered redelivery. Now a raising
handler `nak()`s the message for redelivery, up to `max_deliver` attempts
(default 5, env `VARCO_NATS_MAX_DELIVER`), after which it is `term()`ed
(never redelivered again) with a WARNING log. **A non-idempotent handler
with no `@listen(retry_policy=...)` may now see repeat side-effects** where
it previously saw exactly one delivery. A deserialization failure is always
`term()`ed regardless of delivery count — a poison payload can never
succeed on retry.

⚠️ **`ErrorPolicy.FIRE_FORGET` opts OUT of redelivery.** `FIRE_FORGET`
swallows a handler exception inside `_dispatch` before `_on_message` ever
sees it — the bus observes a "successful" dispatch and acks. Use the
default `COLLECT_ALL` or `FAIL_FAST` if you want `AT_LEAST_ONCE` redelivery
on handler failure.

`ack_wait_seconds` (previously dead configuration — see the env-var table
below) now actually reaches the JetStream consumer.

---

## Configuration

All settings are read from environment variables with the `VARCO_NATS_` prefix:

```bash
VARCO_NATS_SERVERS=nats://nats.internal:4222
VARCO_NATS_STREAM_NAME=orders-events
VARCO_NATS_SUBJECT_PREFIX=orders
VARCO_NATS_DURABLE_NAME=order-service
VARCO_NATS_DELIVERY_SEMANTICS=at_least_once
VARCO_NATS_CHANNEL_PREFIX=prod.
VARCO_NATS_ACK_WAIT_SECONDS=30.0    # now live — reaches the JetStream consumer (Plan 019 / RT2-B)
VARCO_NATS_MAX_DELIVER=5            # redelivery budget before term() (Plan 019 / RT2-B)
```

```python
config = NatsEventBusSettings.from_env()
```

For structured connection/security config (TLS, user/password, token), use
`NatsConnectionSettings` with the `NATS_` prefix:

```bash
NATS_SERVERS=nats://nats1:4222,nats://nats2:4222
NATS_SSL__CA_CERT=/etc/ssl/nats-ca.pem
NATS_AUTH__TYPE=basic
NATS_AUTH__USERNAME=alice
NATS_AUTH__PASSWORD=secret
```

```python
from varco_nats import NatsConnectionSettings

conn = NatsConnectionSettings.from_env()
config = NatsEventBusSettings(connect_kwargs=conn.to_nats_kwargs())
```

---

## Stream management

`NatsStreamManager` administers the backing JetStream stream. `channel_exists()`
implements the `ChannelManager` ABC's **declared-or-present** contract (Plan
019 / RT2-C): `declare_channel(c)` implies `channel_exists(c)` is `True`
until `delete_channel(c)`, even with zero messages published — via a
process-local declaration registry layered on top of broker evidence
("does the subject currently carry a message?", still available separately
as `channel_has_messages()`):

```python
from varco_nats import NatsStreamManager, NatsChannelManagerSettings

settings = NatsChannelManagerSettings(servers="nats://localhost:4222")
async with NatsStreamManager(settings) as manager:
    await manager.declare_channel("orders")  # ensures the stream + registers "orders"
    exists = await manager.channel_exists("orders")  # True — declared, even with 0 messages
    has_data = await manager.channel_has_messages("orders")  # NATS-only: old "carries a message?" predicate
    channels = await manager.list_channels()  # declared channels ∪ channels carrying messages
    await manager.delete_channel("orders")  # purge messages + discard from the registry
```

⚠️ **BREAKING (behaviour), Plan 019 / RT2-C**: `channel_exists()`/
`list_channels()` previously answered "does this channel currently carry a
message?" — a freshly declared, empty channel reported as not existing,
which violated the `ChannelManager` ABC's own documented contract. They now
answer "was this channel declared (by this manager instance) or does it
carry a message?", matching Kafka and Redis. The registry is
**process-local** — a fresh manager in another process reports `False` for
a channel declared elsewhere that has never carried a message (identical to
Redis's long-standing, documented limitation).

---

## Dead letter queue

`NatsDLQ` stores exhausted events in a dedicated **WorkQueue-retention**
JetStream stream — so `count()` returns the *exact* pending-entry count:

```python
from varco_nats import NatsDLQ

async with NatsDLQ(settings=NatsEventBusSettings()) as dlq:
    # Usually wired automatically via @listen(dlq=dlq):
    class OrderConsumer(EventConsumer):
        @listen(
            OrderPlacedEvent,
            channel="orders",
            retry_policy=RetryPolicy(max_attempts=3),
            dlq=dlq,
        )
        async def on_order(self, event: OrderPlacedEvent) -> None: ...

    # Relay:
    entries = await dlq.pop_batch(limit=10)
    for entry in entries:
        await alert_ops(entry)
        await dlq.ack(entry.entry_id)
```

---

## Dependency injection (Providify)

```python
from varco_nats.di import bootstrap
from varco_core.event import AbstractEventBus

container = bootstrap()  # scans varco_nats
bus = await container.aget(AbstractEventBus)  # NatsEventBus singleton
# ...
await container.ashutdown()  # stops the bus via @PreDestroy
```

Install the DLQ explicitly when needed:

```python
from varco_nats.dlq import NatsDLQConfiguration
from varco_core.event.dlq import AbstractDeadLetterQueue

await container.ainstall(NatsDLQConfiguration)
dlq = await container.aget(AbstractDeadLetterQueue)
```

---

## Running tests

```bash
# Unit tests — no broker required (nats-py is faked)
uv run pytest varco_nats/tests/

# Integration tests — require Docker (a real NATS server is started)
uv run pytest varco_nats/tests/ -m integration
# or
VARCO_RUN_INTEGRATION=1 uv run pytest varco_nats/tests/
```

---

## Migration 1.x → 2.0

The no-op `@Configuration` aliases (`NatsEventBusConfiguration`,
`NatsChannelManagerConfiguration`) were removed. Register the bus via scan:

```python
# Before (1.x)
await container.ainstall(NatsEventBusConfiguration)

# After (2.0)
from varco_nats.di import bootstrap

bootstrap(container)  # or: container.scan("varco_nats", recursive=True)
```

The opt-in `NatsDLQConfiguration` is unchanged — still `await container.ainstall(...)`.

---

## License

Apache-2.0
