"""
Regression guard: ``bind_cloudevents_serializer()`` must reach the Redis buses
and the Redis DLQ.

Why this file exists
--------------------
Plan 030 shipped ``CloudEventsJsonSerializer`` and documented, in five places,
that "every backend already resolves ``Serializer[Event]`` through DI".  That was
true of Kafka and NATS — both bind their bus as a scanned ``@Singleton``, so
providify injects every constructor parameter — and **false of Redis**, whose two
implementations are produced by ``RedisEventBusSelectorConfiguration.bus()``.
providify only injects what a ``@Provider`` *method* declares, and that method
declared ``settings`` alone.  So an app that opted into CloudEvents silently kept
publishing plain varco JSON on Redis, and Plan 030's entire ``ce``-stream-field
mechanism never engaged outside a hand-constructed bus.

The same shape bit the dead-letter queues: all five backends constructed
``JsonEventSerializer()`` as a literal in ``__init__``, so a CloudEvents app wrote
envelopes onto the bus but plain JSON into the DLQ — two wire formats for one
event, and a redrive that republished the wrong one.

These tests assert the *wiring*, not the envelope's contents (that is
``varco_core/tests/test_cloudevents_serializer.py``'s job).  No Redis server is
required: nothing connects, only construction and DI resolution run.
"""

from __future__ import annotations

import pytest
from providify import DIContainer
from varco_core.event import AbstractEventBus
from varco_core.event.cloudevents import (
    CloudEventsJsonSerializer,
    CloudEventsSettings,
    bind_cloudevents_serializer,
)
from varco_core.event.serializer import JsonEventSerializer
from varco_redis.bus import RedisEventBus
from varco_redis.config import RedisEventBusSettings
from varco_redis.dlq import RedisDLQ
from varco_redis.streams import RedisStreamEventBus


def _container(monkeypatch: pytest.MonkeyPatch, *, use_streams: bool) -> DIContainer:
    """
    Build a bootstrapped container whose Redis bus selector will pick one shape.

    ``use_streams`` is steered through the environment rather than by providing a
    second ``RedisEventBusSettings`` binding: the package's own settings binding
    is DEPENDENT-scoped, and a hand-registered replacement trips providify's
    singleton→dependent scope-leak detector on unrelated bindings.

    Args:
        monkeypatch: pytest's env patcher, scoped to the calling test.
        use_streams: ``True`` selects ``RedisStreamEventBus``, ``False`` selects
                     the Pub/Sub ``RedisEventBus``.

    Returns:
        A container with ``varco_redis`` scanned and the CloudEvents serializer
        bound at default priority.
    """
    from varco_redis.di import bootstrap

    monkeypatch.setenv("VARCO_REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("VARCO_REDIS_USE_STREAMS", str(use_streams).lower())

    container = bootstrap(DIContainer())
    assert container is not None, "providify must be installed for this suite"

    bind_cloudevents_serializer(container, CloudEventsSettings(source="/varco/tests"))
    return container


class TestSerializerReachesTheRedisBus:
    @pytest.mark.parametrize(
        ("use_streams", "expected_cls"),
        [(True, RedisStreamEventBus), (False, RedisEventBus)],
        ids=["streams", "pubsub"],
    )
    def test_regression_bound_serializer_reaches_both_bus_shapes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        use_streams: bool,
        expected_cls: type,
    ) -> None:
        """
        User-visible symptom this pins: an app calls
        ``bind_cloudevents_serializer()``, sees CloudEvents envelopes on Kafka,
        and sees plain varco JSON on Redis — with no error anywhere.
        """
        container = _container(monkeypatch, use_streams=use_streams)
        bus = container.get(AbstractEventBus)

        assert isinstance(bus, expected_cls)
        # Reaching into _serializer is deliberate: the injected serializer has no
        # public accessor, and this is exactly the private wiring that regressed.
        assert isinstance(bus._serializer, CloudEventsJsonSerializer)

    def test_streams_bus_writes_the_ce_field_once_a_serializer_is_bound(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        The ``ce`` stream-field convention (§D-CE4) is downstream of the binding
        actually arriving — before the fix this asserted ``payload`` forever.
        """
        container = _container(monkeypatch, use_streams=True)
        bus = container.get(AbstractEventBus)
        assert isinstance(bus, RedisStreamEventBus)
        assert bus._stream_field() == "ce"

    def test_no_binding_leaves_the_json_default_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Edge case: nothing bound → the optional parameter is ``None`` and each bus
        constructs its own ``JsonEventSerializer()``.  Byte-identical to the
        behaviour before the ``serializer`` parameter existed.
        """
        from varco_redis.di import bootstrap

        monkeypatch.setenv("VARCO_REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.delenv("VARCO_REDIS_USE_STREAMS", raising=False)

        container = bootstrap(DIContainer())
        assert container is not None
        bus = container.get(AbstractEventBus)

        assert isinstance(bus._serializer, JsonEventSerializer)
        assert getattr(bus, "_stream_field", lambda: "payload")() == "payload"


class TestSerializerReachesTheRedisDlq:
    def test_regression_dlq_uses_the_bound_serializer(self) -> None:
        """
        A dead letter stores the event as bytes; whoever redrives it must be able
        to read them.  Before the fix the DLQ hard-coded ``JsonEventSerializer()``,
        so a CloudEvents app's dead letters were in a different format from its
        bus traffic.
        """
        serializer = CloudEventsJsonSerializer(CloudEventsSettings(source="/varco/tests"))
        dlq = RedisDLQ(RedisEventBusSettings(url="redis://localhost:6379/0"), serializer=serializer)

        assert dlq._serializer is serializer

    def test_dlq_default_is_still_json(self) -> None:
        """Edge case: no serializer passed → unchanged ``JsonEventSerializer``."""
        dlq = RedisDLQ(RedisEventBusSettings(url="redis://localhost:6379/0"))
        assert isinstance(dlq._serializer, JsonEventSerializer)
