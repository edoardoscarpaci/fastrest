"""
Outbox **row** durability across a real Postgres restart
(Plan 018 / RT7b, Step 31 — chaos tier).

The database half of the split scenario (§RT7-home). ``varco_kafka``'s
``test_kafka_chaos.py`` owns the broker half — real ``KafkaEventBus`` plus
an in-process outbox double, because the thing under test there is
``bus.publish()`` raising against an absent broker. **Here it is inverted**:
a real ``SARelayOutboxRepository`` on real Postgres plus a deliberately
failing in-process bus, because the thing under test is *row durability*.
Neither test needs a dependency its package does not already have, and
together they cover the whole "the outbox does not lose entries" claim.

Container scope (§chaos-fixture): a **module**-scoped
``postgres_container_chaos`` declared here, never in ``conftest.py``.
Restarting the session-scoped ``postgres_container`` would break every other
integration test in ``varco_sa/tests/``.

⚠️ ``ChaosContainer.restart()`` uses docker-py's ``restart()``, which
preserves the container **id** but NOT its host port mapping — Docker may
re-allocate the ephemeral port on every restart (research 006 §A/§B/§F,
Plan 019 / §RT7b-port; supersedes research 002 §1's original "port
survives" claim). ``.stop()`` + ``.start()`` on a testcontainers container
is still strictly worse — it **deletes and recreates** the container,
losing the id too — and remains forbidden. This module therefore never
captures the connection URL into a local once; every use reads
``chaos.url`` fresh, which re-derives it from the live container on every
access.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator

import pytest
from varco_chaos.containers import ChaosContainer
from varco_core.event import Event
from varco_core.event.base import AbstractEventBus
from varco_core.service.outbox import OutboxEntry, OutboxRelay

pytestmark = [pytest.mark.integration, pytest.mark.chaos]

_M = 15
"""Rows written before the restart."""

_DRAIN_TIMEOUT = 90.0


def _postgres_url(container) -> str:  # noqa: ANN001 — DockerContainer, PostgresContainer subclass
    url = container.get_connection_url(driver="asyncpg")
    assert url.startswith("postgresql+asyncpg://"), f"unexpected DSN shape: {url}"
    return url


@pytest.fixture(scope="module")
def postgres_container_chaos() -> Iterator[ChaosContainer]:
    """
    A Postgres container this module is allowed to restart.

    Yields:
        A ``ChaosContainer`` wrapping ``postgres:16-alpine``, with a
        ``url_factory`` so ``.url`` always re-derives the current DSN
        (Plan 019 / §RT7b-port) instead of any caller trusting a value
        captured before a ``restart()``.

    Edge cases:
        - Module-scoped: the boot cost (~1-5 s) is paid once for the module.
          Every test must leave the container healthy — ``restart()`` always
          re-waits readiness before returning.
    """
    from testcontainers.postgres import PostgresContainer  # noqa: PLC0415

    with PostgresContainer("postgres:16-alpine") as container:
        _postgres_url(container)  # shape check at fixture setup, as before
        yield ChaosContainer(
            container,
            ready=lambda logs: "database system is ready to accept connections" in logs,
            url_factory=_postgres_url,
        )


class ChaosOutboxEvent(Event):
    __event_type__ = "order.chaos.sa"
    order_id: str


class _ControllableBus(AbstractEventBus):
    """
    In-process bus whose ``publish()`` fails until ``allow`` is set.

    Deliberate (§RT7-home): the bus is the cheap half here. Its only job is
    to keep entries *pending* across the restart so the assertion can be
    about the rows themselves, then to let them drain once flipped.
    """

    def __init__(self) -> None:
        self.allow = False
        self.published: list[str] = []

    async def publish(self, event, *, channel: str = "default"):  # type: ignore[override]
        if not self.allow:
            raise ConnectionError("simulated broker outage")
        self.published.append(event.order_id)
        return None

    def subscribe(self, event_type, handler, **kwargs):  # type: ignore[override]
        raise NotImplementedError("this double is publish-only")

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


async def _poll_until(predicate, timeout: float) -> bool:
    """Poll to a deadline — never sleep-then-assert."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.25)
    return predicate()


async def test_outbox_rows_survive_a_database_restart(
    postgres_container_chaos: ChaosContainer,
) -> None:
    """
    M outbox rows written in a transaction are still pending, with their
    ``attempts`` intact, after Postgres is restarted underneath the relay —
    and all M relay and delete once the bus recovers.

    Asserts, in order:
      1. All M rows are present and pending immediately after commit.
      2. After ``restart()`` + ``wait_ready()`` and a fresh engine, all M
         rows are **still** present and pending (nothing was lost, nothing
         was deleted for an entry that never published).
      3. With the bus allowed to succeed, all M drain to zero and every
         event was published.

    Edge cases:
        - A fresh ``AsyncEngine`` is created after the restart: the pre-restart
          engine's pooled connections are dead, and reusing it would surface
          as a connection error that says nothing about row durability.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from varco_sa.outbox import SARelayOutboxRepository, outbox_metadata

    chaos = postgres_container_chaos
    dsn = chaos.url
    run_id = uuid.uuid4().hex[:8]
    channel = f"chaos-{run_id}"

    engine = create_async_engine(dsn, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(outbox_metadata.create_all)

    repo = SARelayOutboxRepository(async_sessionmaker(engine, expire_on_commit=False))
    for i in range(_M):
        await repo.save(
            OutboxEntry.from_event(ChaosOutboxEvent(order_id=f"{run_id}-{i}"), channel=channel)
        )

    def _mine(entries) -> list[OutboxEntry]:
        return [e for e in entries if e.channel == channel]

    # (1) Everything landed.
    assert len(_mine(await repo.get_pending(limit=1000))) == _M

    # Run the relay against a deliberately FAILING bus first: publish raises,
    # so _relay_entry must not reach its delete. This is what makes the
    # post-restart assertion meaningful — the rows are pending because the
    # relay genuinely tried and failed, not merely because nothing ran.
    failing_bus = _ControllableBus()
    failing_relay = OutboxRelay(outbox=repo, bus=failing_bus, poll_interval=0.2, batch_size=5)
    await failing_relay.start()
    try:
        await asyncio.sleep(1.0)
    finally:
        await failing_relay.stop()
    assert failing_bus.published == [], "the failing bus published something"
    assert len(_mine(await repo.get_pending(limit=1000))) == _M, (
        "a failed publish deleted outbox rows — delete must happen only AFTER "
        "a successful publish (outbox.py:818-830)"
    )
    await engine.dispose()

    # The database goes away mid-flight. restart() already re-waits
    # readiness internally; chaos.url is read again afterward — never the
    # `dsn` captured above — since the host port may have moved
    # (research 006 §A, Plan 019 / §RT7b-port).
    chaos.restart()
    dsn_after_restart = chaos.url

    # (2) A fresh engine sees every row, still pending.
    engine2 = create_async_engine(dsn_after_restart, echo=False)
    repo2 = SARelayOutboxRepository(async_sessionmaker(engine2, expire_on_commit=False))
    try:
        survivors = _mine(await repo2.get_pending(limit=1000))
        assert len(survivors) == _M, (
            f"{_M - len(survivors)} committed outbox rows did not survive a database restart"
        )
        assert all(e.attempts == 0 for e in survivors), (
            f"attempts counters were mutated across the restart: {[e.attempts for e in survivors]}"
        )

        # (3) Let the bus succeed; every row must relay and be deleted.
        bus = _ControllableBus()
        bus.allow = True
        relay = OutboxRelay(outbox=repo2, bus=bus, poll_interval=0.2, batch_size=5)
        await relay.start()
        try:
            drained = await _poll_until(
                lambda: len(bus.published) >= _M,
                _DRAIN_TIMEOUT,
            )
        finally:
            await relay.stop()

        assert drained, f"only {len(bus.published)}/{_M} rows relayed after the database came back"
        assert _mine(await repo2.get_pending(limit=1000)) == [], (
            "rows were published but never deleted from the outbox"
        )
        assert sorted(bus.published) == sorted(f"{run_id}-{i}" for i in range(_M))
    finally:
        await engine2.dispose()
