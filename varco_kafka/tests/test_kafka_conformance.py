"""
Real-Kafka conformance opt-in (Plan 012 / RT6, Step 27).

Consumes the session-scoped ``kafka_bootstrap`` fixture that Phase 1
(Step 7) adds to ``varco_kafka/tests/conftest.py``. Until that fixture
exists, every test class below errors at fixture-resolution time with
``fixture 'kafka_bootstrap' not found``.

Also depends on ``pythonpath = ["../testkit"]`` in
``varco_kafka/pyproject.toml`` — until then every import below fails with
``ModuleNotFoundError: No module named 'varco_conformance'``.
"""

from __future__ import annotations

import pytest
from varco_conformance.dlq import DeadLetterQueueConformance
from varco_conformance.event_bus import EventBusConformance
from varco_kafka.bus import KafkaEventBus
from varco_kafka.config import KafkaEventBusSettings
from varco_kafka.dlq import KafkaDLQ

pytestmark = pytest.mark.integration


class TestKafkaEventBusConformance(EventBusConformance):
    @pytest.fixture
    async def bus(self, kafka_bootstrap: str):
        # auto_offset_reset="earliest": the shared conformance suite
        # subscribes then publishes to a brand-new topic in quick succession
        # — with the production default ("latest"), a consumer that joins
        # the group *after* the topic was auto-created by the producer's
        # publish() starts reading from *after* that first message and
        # permanently misses it. "earliest" is the correct choice for this
        # test-only fixture; it is not a statement that "latest" (the
        # production default) is wrong for real deployments.
        #
        # A unique group_id per test avoids a back-to-back test reusing a
        # group_id whose previous member has not yet fully left the group
        # (session-timeout-bound), which was observed to add several more
        # seconds of rebalance latency on top of the topic-creation race.
        #
        # metadata_max_age_ms: the real root cause of the topic-creation
        # race — aiokafka's default metadata refresh interval is 5 minutes
        # (300000ms). subscribe() is called BEFORE the topic exists (the
        # conformance suite's channel is brand new); the consumer's first
        # metadata fetch caches "topic not found" and, with the default
        # interval, would not retry for up to 5 minutes — far longer than
        # any reasonable test timeout. A short interval here makes the
        # consumer notice the topic (auto-created moments later by
        # publish()) promptly.
        import uuid  # noqa: PLC0415

        async with KafkaEventBus(
            KafkaEventBusSettings(
                bootstrap_servers=kafka_bootstrap,
                auto_offset_reset="earliest",
                group_id=f"conformance-{uuid.uuid4().hex[:8]}",
                consumer_kwargs={"metadata_max_age_ms": 250},
            )
        ) as bus:
            yield bus


class TestKafkaDLQConformance(DeadLetterQueueConformance):
    @pytest.fixture
    async def dlq(self, kafka_bootstrap: str):
        async with KafkaDLQ(KafkaEventBusSettings(bootstrap_servers=kafka_bootstrap)) as dlq:
            yield dlq

    # test_regression_kafka_dlq_delete_where_no_predicate: KafkaDLQ.delete_where()
    # (varco_kafka/varco_kafka/dlq.py) used to always raise NotImplementedError,
    # even with NO predicate given — it never reached the "no predicate at all
    # -> ValueError" check the ABC documents (varco_core/varco_core/event/
    # dlq.py, "Raises: ValueError: no predicate at all was given"). Fixed by
    # checking for the no-predicate case first, mirroring SA/Redis. The base
    # class's test_delete_where_no_predicate_raises now runs unmodified
    # (inherited, no override needed) and passes.

    # NOTE for the implementer: KafkaDLQ.count() is documented
    # (varco_kafka/varco_kafka/dlq.py) to always return -1 (no AdminClient
    # wired) rather than tracking a real pending-entry count. The shared
    # suite's test_count_reflects_pushed_entries currently only asserts
    # `after >= before`, which trivially holds even at a constant -1, so
    # it is NOT expected to fail here — left unmarked deliberately rather
    # than guessing at an xfail with no verified failure to pin down
    # (Non-goals: "do not guess blindly"). If a stricter assertion is
    # added to the shared suite later, this is the first place to check.
