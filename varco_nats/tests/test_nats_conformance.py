"""
Real-NATS conformance opt-in (Plan 012 / RT6, Step 27).

Consumes the session-scoped ``nats_url`` fixture that Phase 1/2 (Steps 7,
12) adds to ``varco_nats/tests/conftest.py`` (backed by first-party
``testcontainers.nats.NatsContainer`` with JetStream enabled). Until that
fixture exists, every test class below errors at fixture-resolution time
with ``fixture 'nats_url' not found``.

Also depends on ``pythonpath = ["../testkit"]`` in
``varco_nats/pyproject.toml`` — until then every import below fails with
``ModuleNotFoundError: No module named 'varco_conformance'``.
"""

from __future__ import annotations

import pytest
from varco_conformance.dlq import DeadLetterQueueConformance
from varco_conformance.event_bus import EventBusConformance
from varco_nats.bus import NatsEventBus
from varco_nats.config import NatsEventBusSettings
from varco_nats.dlq import NatsDLQ

pytestmark = pytest.mark.integration


class TestNatsEventBusConformance(EventBusConformance):
    @pytest.fixture
    async def bus(self, nats_url: str):
        async with NatsEventBus(NatsEventBusSettings(servers=nats_url)) as bus:
            yield bus


class TestNatsDLQConformance(DeadLetterQueueConformance):
    @pytest.fixture
    async def dlq(self, nats_url: str):
        # Each test needs its own DLQ stream/subject — the default settings
        # use a fixed stream name, and a second NatsDLQ trying to create the
        # SAME stream on the session-shared NATS container collides with
        # "subjects overlap with an existing stream" (per-test namespacing
        # rule, tests/conftest.py's module docstring).
        import uuid  # noqa: PLC0415

        run_id = uuid.uuid4().hex[:8]
        async with NatsDLQ(
            NatsEventBusSettings(
                servers=nats_url,
                stream_name=f"dlq-conformance-{run_id}",
                subject_prefix=f"dlqconf{run_id}",
            )
        ) as dlq:
            yield dlq

    async def test_delete_where_no_predicate_raises(self, dlq) -> None:  # type: ignore[override]
        # Regression test for KI-7: NatsDLQ.delete_where() previously raised
        # NotImplementedError unconditionally, never reaching the ABC's
        # "no predicate at all -> ValueError" check. Fixed in
        # varco_nats/varco_nats/dlq.py — the no-predicate check now runs
        # before the backend-support NotImplementedError.
        await super().test_delete_where_no_predicate_raises(dlq)
