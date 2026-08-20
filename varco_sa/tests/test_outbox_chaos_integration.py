"""
Outbox durability chaos tests (Plan 012 / RT7, Steps 28-29), on real
Postgres + real Redis.

Step 28 — broker-outage survival: write N entries through
``SARelayOutboxRepository`` (the outbox-side counterpart of
``SAOutboxRepository`` meant for the relay's own polling connection —
see ``varco_sa/varco_sa/outbox.py``'s docstring), start ``OutboxRelay``,
**pause the broker container mid-drain**, assert the relay neither loses
nor deletes undelivered entries and does not crash, unpause the broker,
assert every entry is ultimately published exactly once and removed.

DEVIATION from the plan's literal ASSUMPTION A-2 recipe
(``container.get_wrapped_container().stop()/start()``): this environment
was observed (Plan 012's earlier chaos test,
``varco_redis/tests/test_breaker_chaos_integration.py``) to reassign a NEW
host port on container restart, which would require hot-swapping the
relay's live Redis connection mid-poll-loop to a new port — needless
complexity for what this test needs to prove. ``pause()``/``unpause()``
(the same docker-py handle from ``get_wrapped_container()``, per A-2) is
used instead: it freezes the container's process without touching its
network configuration, producing an equally real "broker unreachable"
outage (verified: connections and publishes to a paused Redis genuinely
time out) while keeping the host:port stable across the outage.

Step 29 — poison-entry containment: an entry with a payload that can never
be deserialized, with ``OutboxRelay(retry_policy=..., max_attempts=...,
dlq=...)``, must end up in the DLQ and be deleted so entries queued behind
it drain. Also asserts ``OutboxRelay(max_attempts=...)`` **without** ``dlq=``
raises ``ValueError`` at construction (a pure unit-level check — no I/O
needed to prove a constructor guard).
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from varco_core.event.base import Event
from varco_core.event.dlq import DeadLetterEntry
from varco_core.resilience import RetryPolicy
from varco_core.service.outbox import OutboxEntry, OutboxRelay
from varco_redis.bus import RedisEventBus
from varco_redis.config import RedisEventBusSettings
from varco_redis.streams import RedisStreamEventBus
from varco_sa.dlq import SADeadLetterQueue
from varco_sa.outbox import SARelayOutboxRepository, outbox_metadata

pytestmark = pytest.mark.integration


class ChaosOrderEvent(Event):
    __event_type__ = "order.chaos.outbox"
    order_id: str


@pytest.fixture
def redis_container_fresh():
    """
    Function-scoped, pristine Redis container — local to this test module.

    varco_sa has no shared Redis fixture of its own (Phase 1's session-scoped
    ``redis_url`` lives in varco_redis); this file needs a real, independently
    stoppable/pausable broker, so a dedicated container is started per test
    (mirroring the identically-named fixture in
    ``varco_redis/tests/conftest.py``).
    """
    if not os.environ.get("VARCO_RUN_INTEGRATION"):
        pytest.skip(
            "Integration tests disabled — set VARCO_RUN_INTEGRATION=1 or use -m integration"
        )

    from testcontainers.redis import RedisContainer  # noqa: PLC0415

    with RedisContainer() as container:
        yield container


@pytest.fixture
async def outbox_engine(postgres_url: str):
    """Function-scoped engine + table for outbox chaos tests — each test
    gets its own fresh ``varco_outbox`` schema via a private engine
    (the outbox_metadata Base is separate from the app's DeclarativeBase,
    same as ``tests/test_sa_outbox.py``'s precedent)."""
    engine = create_async_engine(postgres_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(outbox_metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(outbox_metadata.drop_all)
        await engine.dispose()


def _outbox_repo(engine) -> SARelayOutboxRepository:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return SARelayOutboxRepository(factory)


async def test_outbox_survives_broker_outage_no_loss_no_premature_delete(
    outbox_engine, redis_container_fresh
) -> None:
    """
    DESIGN: ``RedisStreamEventBus``, not ``RedisEventBus`` (plain Pub/Sub).

    Plain Redis Pub/Sub is fire-and-forget by design (CLAUDE.md: "Redis
    Pub/Sub is ephemeral — messages published before any subscriber
    connects are lost") — ``PUBLISH`` never errors just because zero
    clients are currently listening, so ``OutboxRelay`` would delete an
    entry it "successfully" published even if no subscriber actually
    received it during a reconnect race, which is a real, EXPECTED
    Pub/Sub characteristic, not a durability bug to prove or disprove
    here. ``RedisStreamEventBus`` (``XREADGROUP`` / consumer groups,
    proven durable by ``TestRedisStreamEventBusConformance``) is the
    correct transport for a test whose entire point is "no message is
    lost across a broker outage".
    """
    container = redis_container_fresh
    host = container.get_container_host_ip()
    port = int(container.get_exposed_port(6379))
    run_id = uuid.uuid4().hex[:8]
    channel = f"chaos-outbox-{run_id}"

    repo = _outbox_repo(outbox_engine)

    n = 5
    entries = [
        OutboxEntry.from_event(ChaosOrderEvent(order_id=str(i)), channel=channel)
        for i in range(n)
    ]
    for entry in entries:
        await repo.save(entry)
    assert len(await repo.get_pending(limit=100)) == n

    bus = RedisStreamEventBus(
        RedisEventBusSettings(url=f"redis://{host}:{port}/0"),
        consumer_group=f"chaos-outbox-group-{run_id}",
        middleware=[],
    )
    await bus.start()

    received: list[str] = []

    async def handler(event: ChaosOrderEvent) -> None:
        received.append(event.order_id)

    bus.subscribe(ChaosOrderEvent, handler, channel=channel)

    relay = OutboxRelay(outbox=repo, bus=bus, poll_interval=0.2, batch_size=10)
    await relay.start()

    try:
        # Kill the broker mid-drain — a real network outage, not a raised
        # mock (see the module docstring for why pause()/unpause() is used
        # instead of stop()/start()).
        wrapped = container.get_wrapped_container()
        wrapped.pause()

        # Give the relay several poll cycles to hit the real failure —
        # generous margin per CLAUDE.md's timing-flake convention.
        await asyncio.sleep(2.0)

        # The relay must not have crashed (still an alive asyncio task)...
        assert relay._task is not None and not relay._task.done()  # noqa: SLF001
        # ...and must not have lost or prematurely deleted any entry —
        # every entry is still pending because it was never actually
        # published while the broker was unreachable.
        still_pending = await repo.get_pending(limit=100)
        assert len(still_pending) == n
        assert received == []

        # Restore the broker.
        wrapped.unpause()

        # Wait for the relay to catch up and drain everything, generous
        # margin (poll_interval=0.2s, but reconnection + redelivery takes
        # a few real cycles).
        deadline = asyncio.get_event_loop().time() + 30.0
        while (
            len(received) < n or await repo.get_pending(limit=100)
        ) and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.3)

        assert sorted(received) == [
            str(i) for i in range(n)
        ], f"expected every entry delivered exactly once, got {received}"
        assert await repo.get_pending(limit=100) == []
    finally:
        await relay.stop()
        await bus.stop()
        try:
            container.get_wrapped_container().unpause()
        except Exception:  # noqa: BLE001 — already unpaused is fine
            pass


async def test_poison_entry_routes_to_dlq_and_unblocks_stream(
    outbox_engine, redis_container_fresh
) -> None:
    container = redis_container_fresh
    host = container.get_container_host_ip()
    port = int(container.get_exposed_port(6379))
    run_id = uuid.uuid4().hex[:8]
    channel = f"chaos-poison-{run_id}"

    repo = _outbox_repo(outbox_engine)

    # A poison entry: payload bytes that JsonEventSerializer can never
    # deserialize into an Event. Saved FIRST so any entries "behind" it
    # would be starved if the relay's stream got stuck on it.
    poison = OutboxEntry(
        entry_id=uuid.uuid4(),
        event_type="unparseable",
        channel=channel,
        payload=b"this is not valid event json at all",
    )
    await repo.save(poison)

    good_entries = [
        OutboxEntry.from_event(ChaosOrderEvent(order_id=str(i)), channel=channel)
        for i in range(3)
    ]
    for entry in good_entries:
        await repo.save(entry)

    dlq = SADeadLetterQueue(outbox_engine)
    await dlq.ensure_table()

    bus = RedisEventBus(
        RedisEventBusSettings(url=f"redis://{host}:{port}/0"), middleware=[]
    )
    await bus.start()

    received: list[str] = []

    async def handler(event: ChaosOrderEvent) -> None:
        received.append(event.order_id)

    bus.subscribe(ChaosOrderEvent, handler, channel=channel)

    relay = OutboxRelay(
        outbox=repo,
        bus=bus,
        poll_interval=0.1,
        batch_size=10,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0.01),
        max_attempts=2,
        dlq=dlq,
    )
    await relay.start()

    try:
        deadline = asyncio.get_event_loop().time() + 15.0
        while (
            len(received) < len(good_entries)
        ) and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.2)

        # Every good entry drained despite the poison entry queued ahead of
        # them — the stream was not starved.
        assert sorted(received) == [str(i) for i in range(len(good_entries))]

        # The poison entry is gone from the outbox (deleted after DLQ push)...
        remaining = await repo.get_pending(limit=100)
        assert all(e.entry_id != poison.entry_id for e in remaining)

        # ...and landed in the DLQ.
        dlq_entries = await dlq.pop_batch(limit=10)
        assert any(
            isinstance(e, DeadLetterEntry) and e.channel == channel for e in dlq_entries
        )
    finally:
        await relay.stop()
        await bus.stop()


def test_max_attempts_without_dlq_raises_at_construction() -> None:
    """Pure unit-level guard — mirrors OutboxRelay's own refusal to
    configure silent data loss. No I/O needed."""
    from unittest.mock import MagicMock

    with pytest.raises(ValueError):
        OutboxRelay(
            outbox=MagicMock(),
            bus=MagicMock(),
            max_attempts=3,
            dlq=None,
        )
