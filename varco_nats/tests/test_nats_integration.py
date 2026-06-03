"""
Integration tests for varco_nats
=================================
These tests spin up a real NATS server (with JetStream) via testcontainers and
verify end-to-end publish/subscribe and dead-letter behaviour.

DISABLED BY DEFAULT — requires Docker.  Run with::

    pytest -m integration varco_nats/tests/test_nats_integration.py

Or set the ``VARCO_RUN_INTEGRATION`` env var::

    VARCO_RUN_INTEGRATION=1 pytest varco_nats/tests/test_nats_integration.py

Prerequisites:
    - Docker daemon running
    - testcontainers installed (see pyproject.toml dev dependencies)
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator

import pytest

from varco_core.event import Event
from varco_core.event.dlq import DeadLetterEntry

pytestmark = pytest.mark.integration

# Skip the entire module unless integration tests are explicitly requested.
if not os.environ.get("VARCO_RUN_INTEGRATION"):
    pytest.skip(
        "Integration tests disabled — set VARCO_RUN_INTEGRATION=1 or use -m integration",
        allow_module_level=True,
    )


# ── Test event type ───────────────────────────────────────────────────────────


class IntegrationOrderEvent(Event):
    __event_type__ = "order.integration.nats"
    order_id: str
    amount: float = 0.0


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def nats_server() -> Iterator[str]:
    """
    Start a NATS server with JetStream enabled and yield its connection URL.

    Uses the generic ``DockerContainer`` (no testcontainers extra) running the
    official ``nats`` image with the ``-js`` flag to enable JetStream.
    """
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.waiting_utils import wait_for_logs

    container = (
        DockerContainer("nats:2.10-alpine").with_command("-js").with_exposed_ports(4222)
    )
    container.start()
    try:
        # The NATS server logs this line once it is accepting connections.
        wait_for_logs(container, "Server is ready", timeout=30)
        host = container.get_container_host_ip()
        port = container.get_exposed_port(4222)
        yield f"nats://{host}:{port}"
    finally:
        container.stop()


# ── Event bus end-to-end ──────────────────────────────────────────────────────


class TestNatsEventBusIntegration:
    async def test_publish_subscribe_round_trip(self, nats_server: str) -> None:
        from varco_nats import NatsEventBus, NatsEventBusSettings

        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        # Unique stream/subject prefix per run so reruns never collide.
        run_id = uuid.uuid4().hex[:8]
        config = NatsEventBusSettings(
            servers=nats_server,
            stream_name=f"it-events-{run_id}",
            subject_prefix=f"it{run_id}",
            durable_name=f"it-durable-{run_id}",
        )

        bus = NatsEventBus(config)
        bus.subscribe(IntegrationOrderEvent, handler, channel="orders")
        async with bus:
            await bus.publish(
                IntegrationOrderEvent(order_id="o-1", amount=99.0),
                channel="orders",
            )
            # JetStream delivery is asynchronous — poll briefly for the message.
            for _ in range(50):
                if received:
                    break
                await asyncio.sleep(0.1)

        assert len(received) == 1
        assert isinstance(received[0], IntegrationOrderEvent)
        assert received[0].order_id == "o-1"


# ── Dead letter queue end-to-end ──────────────────────────────────────────────


class TestNatsDLQIntegration:
    async def test_push_pop_ack_round_trip(self, nats_server: str) -> None:
        from varco_nats import NatsDLQ, NatsEventBusSettings

        run_id = uuid.uuid4().hex[:8]
        config = NatsEventBusSettings(
            servers=nats_server,
            stream_name=f"it-events-{run_id}",
            subject_prefix=f"it{run_id}",
        )

        async with NatsDLQ(config) as dlq:
            entry = DeadLetterEntry.from_failure(
                event=IntegrationOrderEvent(order_id="o-dead"),
                channel="orders",
                handler_name="on_order",
                last_exc=RuntimeError("boom"),
                attempts=3,
                first_failed_at=__import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ),
            )
            await dlq.push(entry)

            assert await dlq.count() == 1

            entries = await dlq.pop_batch(limit=10)
            assert len(entries) == 1
            assert entries[0].handler_name == "on_order"
            assert isinstance(entries[0].event, IntegrationOrderEvent)

            await dlq.ack(entries[0].entry_id)
            # WorkQueue retention → ack deletes the entry.
            assert await dlq.count() == 0
