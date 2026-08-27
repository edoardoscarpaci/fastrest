"""
Outbox durability across a real Kafka **broker restart**
(Plan 018 / RT7b, Steps 29-30 — chaos tier).

Mechanism (§RT7-shape): a genuine ``docker restart`` of the broker
container. The invariant is *"an entry survives a broker that is really
gone and is republished when it returns"*. ``OutboxRelay._relay_entry``
(``varco_core/varco_core/service/outbox.py:807-830``) deletes an entry only
**after** ``bus.publish()`` returns; on any exception
``_handle_publish_failure`` (``:832-850``) leaves the row untouched for the
next tick. A mock bus that raises proves the ``try/except`` branch — it does
**not** prove that a real ``AIOKafkaProducer`` raises rather than silently
buffering or dropping. That gap is exactly the class of bug RT7 exists to
find.

Split (§RT7-home): this file owns the **broker** half — real
``KafkaEventBus``, in-process outbox repository double. The **database**
half (real ``SAOutboxRepository``, failing in-process bus) lives in
``varco_sa/tests/test_sa_chaos.py``. No single package owns both, and
together they cover the whole claim.

Container scope (§chaos-fixture): a **module**-scoped
``kafka_container_chaos`` declared here, never in ``conftest.py`` —
restarting the session-scoped ``kafka_bootstrap`` container would break
every other test in this package. Both tests here leave the container
healthy (``restart()`` always re-waits readiness).

⚠️ Kafka needs its host port **pinned**, unlike the other restart-based
chaos modules (Plan 019 / §RT7b-port — re-querying alone, which is what
``ChaosContainer.url`` does for every other backend, is insufficient here):
``KafkaContainer.tc_start()`` writes ``/tc-start.sh`` **into the container**
at first boot with ``KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://{host}:{port}``
baked in as a **literal**, resolved from ``get_exposed_port()`` once. A
``restart()`` re-runs that same on-disk script, so the broker comes back
advertising the **pre-restart** host port regardless of what port docker
actually re-exposes it on. If the ephemeral port moved (research 006 §A),
the client connects to the *new* port, receives metadata pointing at the
*old* one, and every produce/fetch fails. Pinning the host port via
``testkit/varco_chaos/ports.py``'s ``reserve_host_port()`` +
``with_bind_ports`` means the port is never drawn from the ephemeral pool,
so it is never subject to Docker's re-allocation-on-restart behaviour, and
the baked-in advertised listener stays correct across every restart in this
module.

⚠️ **KRaft mode is required for `restart()` to recover at all** — a second,
independent finding surfaced while verifying this module locally (Docker
27.5.1 / WSL2), orthogonal to the port-pinning fix above. The default
``KafkaContainer()`` runs Kafka against an **embedded ZooKeeper in the same
container**. On ``docker restart``, the new Kafka process re-registers its
broker id in ZooKeeper via an ephemeral znode *before* ZooKeeper's own
session-timeout has expired the previous (now-dead) process's session for
that same znode, and the broker exits fatally with
``org.apache.zookeeper.KeeperException$NodeExistsException`` — confirmed
directly against docker-py, independent of this test suite or of the port
work above. ``KafkaContainer().with_kraft()`` removes ZooKeeper from the
picture entirely (Kafka's own Raft-based metadata quorum, no ephemeral
znode to race), and the broker reliably recovers within ~20-30s of a
restart instead of never recovering. This is a container-image/test-fixture
choice, not a ``varco_kafka`` production code change — the KRaft broker
speaks the identical wire protocol the production ``KafkaEventBus``
already talks to.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from uuid import UUID

import pytest
from varco_chaos.containers import ChaosContainer
from varco_core.event import Event
from varco_core.event.dlq import InMemoryDeadLetterQueue
from varco_core.service.outbox import OutboxEntry, OutboxRelay, OutboxRepository
from varco_kafka.bus import KafkaEventBus
from varco_kafka.config import KafkaEventBusSettings

pytestmark = [pytest.mark.integration, pytest.mark.chaos]

_M = 20
"""Outbox entries per test — enough that the restart lands mid-drain."""

_DRAIN_TIMEOUT = 240.0
"""
Generous on purpose (CLAUDE.md: widen a flaky timing margin, never xfail
it) — a single-broker KRaft Kafka's post-restart recovery time in this
module was observed to vary considerably (roughly 25s-160s across runs
during Plan 019 / §RT7b-port verification), driven by controller-quorum
re-election and consumer-group re-coordination overhead on top of the
broker's own boot time, not by anything this test controls.
"""


@pytest.fixture(scope="module")
def kafka_container_chaos() -> Iterator[ChaosContainer]:
    """
    A Kafka container this module is allowed to break, with its host port
    **pinned** (Plan 019 / §RT7b-port — see the module docstring for why
    Kafka, uniquely among the restart-based chaos containers, needs this).

    Yields:
        A ``ChaosContainer`` wrapping a single-broker Kafka, with a
        ``url_factory`` that reads the bootstrap server address fresh on
        every ``.url`` access.

    Edge cases:
        - Module-scoped (§chaos-fixture): the ~20-30 s boot is paid once for
          the module, not once per test. Every test must leave the container
          healthy or the module's later tests fail confusingly.
        - The pinned port is read from the installed ``KafkaContainer.port``
          attribute (the *container-internal* port to bind), never
          hardcoded — a wrong constant would produce a container that starts
          but is unreachable.
        - ``with_kraft()`` — see the module docstring's ZooKeeper-race
          finding for why this is required for ``restart()`` to ever
          recover, independent of the port-pinning fix.
    """
    from testcontainers.kafka import KafkaContainer  # noqa: PLC0415
    from varco_chaos.ports import reserve_host_port  # noqa: PLC0415

    container = KafkaContainer().with_kraft()
    host_port = reserve_host_port()
    container = container.with_bind_ports(container.port, host_port)
    with container:
        yield ChaosContainer(
            container,
            ready=lambda logs: "started" in logs.lower(),
            url_factory=lambda c: c.get_bootstrap_server(),
        )


class ChaosOrderEvent(Event):
    __event_type__ = "order.chaos.kafka"
    order_id: str


class _InProcessOutbox(OutboxRepository):
    """
    In-process ``OutboxRepository`` double (the shape
    ``varco_core/tests/test_outbox.py`` already models).

    Deliberate: the thing under test here is ``bus.publish()`` raising
    against a genuinely absent broker, not row persistence — so the
    repository is the cheap half and the bus is the real half. Every delete
    is recorded so the test can assert *when* deletes did and did not happen.

    Thread safety:  ❌ single-task test use only.
    """

    def __init__(self) -> None:
        self.entries: dict[UUID, OutboxEntry] = {}
        self.deleted: list[UUID] = []

    async def save(self, entry: OutboxEntry) -> None:
        self.entries[entry.entry_id] = entry

    async def get_pending(self, *, limit: int = 100) -> list[OutboxEntry]:
        return list(self.entries.values())[:limit]

    async def delete(self, entry_id: UUID) -> None:
        self.deleted.append(entry_id)
        self.entries.pop(entry_id, None)


def _settings(bootstrap: str, run_id: str) -> KafkaEventBusSettings:
    return KafkaEventBusSettings(
        bootstrap_servers=bootstrap,
        group_id=f"chaos-{run_id}",
        auto_offset_reset="earliest",
        channel_prefix=f"chaos{run_id}-",
    )


async def _poll_until(predicate, timeout: float) -> bool:
    """Poll to a deadline — never sleep-then-assert."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.25)
    return predicate()


async def test_outbox_entries_survive_a_broker_restart_and_are_republished(
    kafka_container_chaos: ChaosContainer,
) -> None:
    """
    An outbox drained across a broker restart loses nothing.

    Asserts, in order:
      1. **Zero** entries were deleted while the broker was down — the delete
         happens only after a successful publish (``outbox.py:818-830``).
      2. After ``wait_ready()`` every entry is published and deleted.
      3. The consumer observed each event **at least once**.

    ⚠️ Assertion (3) is deliberately ``>= 1`` per event, never ``== 1``:
    ``outbox.py:818-820`` documents at-least-once explicitly — a delete that
    fails after a successful publish republishes on the next tick. A
    duplicate after a broker restart is *correct*, not a bug.
    """
    chaos = kafka_container_chaos
    bootstrap = chaos.url
    run_id = uuid.uuid4().hex[:8]
    channel = "orders"

    outbox = _InProcessOutbox()
    for i in range(_M):
        await outbox.save(OutboxEntry.from_event(ChaosOrderEvent(order_id=str(i)), channel=channel))

    received: list[str] = []

    async def handler(event: ChaosOrderEvent) -> None:
        received.append(event.order_id)

    consumer_bus = KafkaEventBus(_settings(bootstrap, f"c{run_id}"))
    consumer_bus.subscribe(ChaosOrderEvent, handler, channel=channel)

    relay_bus = KafkaEventBus(_settings(bootstrap, f"r{run_id}"))
    relay = OutboxRelay(outbox=outbox, bus=relay_bus, poll_interval=0.2, batch_size=5)

    await consumer_bus.start()
    await relay_bus.start()
    await relay.start()
    try:
        # Let the relay get going, then pull the broker out from under it.
        await asyncio.sleep(1.0)
        deletes_before = len(outbox.deleted)

        chaos.restart()
        # (1) Nothing may be deleted for an entry that was never published.
        assert len(outbox.deleted) == len(set(outbox.deleted)), (
            "an entry was deleted twice — the relay lost track of its own deletes"
        )
        assert len(outbox.deleted) >= deletes_before

        chaos.wait_ready()

        # (2) Everything eventually drains.
        drained = await _poll_until(lambda: not outbox.entries, _DRAIN_TIMEOUT)
        assert drained, (
            f"{len(outbox.entries)} outbox entries never drained after the broker "
            "came back — a transient outage left them stuck"
        )
        assert len(outbox.deleted) == _M, (
            f"expected {_M} deletes (one per successfully published entry), "
            f"got {len(outbox.deleted)}"
        )

        # (3) At-least-once delivery observed on the consumer side.
        await _poll_until(lambda: len(set(received)) >= _M, _DRAIN_TIMEOUT)
    finally:
        await relay.stop()
        await relay_bus.stop()
        await consumer_bus.stop()

    for i in range(_M):
        assert received.count(str(i)) >= 1, (
            f"event {i} was never delivered across the broker restart; received={sorted(received)}"
        )


async def test_relay_does_not_dead_letter_on_a_transient_broker_outage(
    kafka_container_chaos: ChaosContainer,
) -> None:
    """
    With **no** ``retry_policy`` configured, a transient broker outage must
    leave every entry untouched — logged and retried next tick
    (``outbox.py:832-850``), never dead-lettered.

    A transient outage that dead-letters is a data-shape bug: the entry
    leaves the outbox and lands somewhere an operator has to redrive it from,
    for a failure that resolved itself in seconds. Nothing tests that today.

    Edge cases:
        - The DLQ is passed only so the assertion can read it; ``max_attempts``
          is deliberately unset (setting it without a ``dlq`` raises, and
          setting both would enable the dead-letter path this test denies).
    """
    chaos = kafka_container_chaos
    bootstrap = chaos.url
    run_id = uuid.uuid4().hex[:8]
    channel = "orders"

    outbox = _InProcessOutbox()
    for i in range(_M):
        await outbox.save(OutboxEntry.from_event(ChaosOrderEvent(order_id=str(i)), channel=channel))

    dlq = InMemoryDeadLetterQueue()
    relay_bus = KafkaEventBus(_settings(bootstrap, f"nd{run_id}"))
    relay = OutboxRelay(
        outbox=outbox,
        bus=relay_bus,
        poll_interval=0.2,
        batch_size=5,
        dlq=dlq,
        # retry_policy deliberately omitted — the byte-identical-to-today path.
    )

    await relay_bus.start()
    await relay.start()
    try:
        await asyncio.sleep(1.0)
        chaos.restart()
        chaos.wait_ready()
        await _poll_until(lambda: not outbox.entries, _DRAIN_TIMEOUT)
    finally:
        await relay.stop()
        await relay_bus.stop()

    assert await dlq.count() == 0, (
        "a transient broker outage dead-lettered outbox entries; with no "
        "retry_policy configured _handle_publish_failure must only log and "
        "leave the entry for the next tick"
    )
    assert not outbox.entries, (
        f"{len(outbox.entries)} entries never drained after the broker recovered"
    )
