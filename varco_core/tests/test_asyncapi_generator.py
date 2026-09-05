"""
Unit tests for the AsyncAPI 3.1.0 document generator (Plan 030 / Phase 1, N3a).
===============================================================================

RED-MODE TDD: written *before* ``varco_core/asyncapi/`` exists.  Encodes plan 030
Steps 8-12 and its prior design (``plans/022-api-freeze-and-standards-alignment.md``
§D-AA1-§D-AA4, ``design/api-freeze-and-standards/reserved-seams.md`` RS-3).

Contract under test:

    from varco_core.asyncapi import generate_asyncapi

    doc: dict = generate_asyncapi(
        consumers,                      # LIVE, already-registered instances
        title="Orders", version="1.0.0",
        protocol="kafka",               # source bus protocol, or None
        group_id="orders-workers",      # Kafka operation binding
        queue_group=None,               # NATS operation binding (only when set)
        servers=None,                   # no `servers` block by default (§D-AA2)
    )

Generation is **runtime**, never a static import walk: ``@listen``'s channel may be
``Callable[[Any], str]`` resolved at ``register_to()`` time against a bound ``self``
(``event/consumer.py:180``), which a static scan gets silently wrong.
"""

from __future__ import annotations

from typing import Any

import pytest
from varco_core.event import Event, EventConsumer, InMemoryEventBus, listen

# ── Test event types ───────────────────────────────────────────────────────────


class AaPlacedEvent(Event):
    __event_type__ = "aa.order.placed"
    order_id: str
    total: float = 0.0


class AaShippedEvent(Event):
    __event_type__ = "aa.order.shipped"
    order_id: str


# ── Test consumers ─────────────────────────────────────────────────────────────


class StaticChannelConsumer(EventConsumer):
    @listen(AaPlacedEvent, channel="orders")
    async def on_placed(self, event: AaPlacedEvent) -> None: ...


class CallableChannelConsumer(EventConsumer):
    """Channel resolved from instance state at ``register_to()`` time."""

    def __init__(self, channel: str) -> None:
        self._channel = channel

    @listen(AaShippedEvent, channel=lambda self: self._channel)
    async def on_shipped(self, event: AaShippedEvent) -> None: ...


class SecondOrdersConsumer(EventConsumer):
    @listen(AaShippedEvent, channel="orders")
    async def on_shipped_too(self, event: AaShippedEvent) -> None: ...


# ── Helpers ────────────────────────────────────────────────────────────────────


def generate(consumers: list[EventConsumer], **kwargs: Any) -> dict[str, Any]:
    from varco_core.asyncapi import generate_asyncapi  # noqa: PLC0415

    kwargs.setdefault("title", "Orders API")
    kwargs.setdefault("version", "1.0.0")
    return generate_asyncapi(consumers, **kwargs)


def channel_addresses(doc: dict[str, Any]) -> set[str]:
    return {channel["address"] for channel in doc["channels"].values()}


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


# ── Document shape ─────────────────────────────────────────────────────────────


class TestDocumentShape:
    async def test_asyncapi_version_is_3_1_0(self, bus: InMemoryEventBus) -> None:
        consumer = StaticChannelConsumer()
        consumer.register_to(bus)

        doc = generate([consumer])

        assert doc["asyncapi"] == "3.1.0"

    async def test_info_carries_title_and_version(self, bus: InMemoryEventBus) -> None:
        consumer = StaticChannelConsumer()
        consumer.register_to(bus)

        doc = generate([consumer], title="Orders API", version="2.3.4")

        assert doc["info"]["title"] == "Orders API"
        assert doc["info"]["version"] == "2.3.4"

    async def test_info_description_explains_the_binding_coverage_choices(
        self, bus: InMemoryEventBus
    ) -> None:
        # §D-AA3: a reader wondering why Redis channels have no bindings must
        # find the answer in the artifact itself, not in a plan file.
        consumer = StaticChannelConsumer()
        consumer.register_to(bus)

        doc = generate([consumer])

        description = doc["info"]["description"].lower()
        assert "kafka" in description
        assert "nats" in description
        assert "redis" in description

    async def test_operation_action_is_receive(self, bus: InMemoryEventBus) -> None:
        # varco consumers only ever receive; `reply` patterns are unused.
        consumer = StaticChannelConsumer()
        consumer.register_to(bus)

        doc = generate([consumer])

        assert {op["action"] for op in doc["operations"].values()} == {"receive"}

    async def test_message_payload_comes_from_model_json_schema(
        self, bus: InMemoryEventBus
    ) -> None:
        consumer = StaticChannelConsumer()
        consumer.register_to(bus)

        doc = generate([consumer])

        messages = [m for ch in doc["channels"].values() for m in ch["messages"].values()]
        assert any(m["payload"] == AaPlacedEvent.model_json_schema() for m in messages)


# ── Runtime resolution (§D-AA1) ────────────────────────────────────────────────


class TestRuntimeResolution:
    async def test_callable_channel_is_resolved_against_the_bound_instance(
        self, bus: InMemoryEventBus
    ) -> None:
        # The whole reason generation is runtime rather than a static walk.
        consumer = CallableChannelConsumer(channel="shipments-eu")
        consumer.register_to(bus)

        doc = generate([consumer])

        assert "shipments-eu" in channel_addresses(doc)

    async def test_two_instances_of_one_class_yield_their_own_channels(
        self, bus: InMemoryEventBus
    ) -> None:
        # A static scan would report one channel (or the lambda itself) for both.
        eu = CallableChannelConsumer(channel="shipments-eu")
        us = CallableChannelConsumer(channel="shipments-us")
        eu.register_to(bus)
        us.register_to(bus)

        doc = generate([eu, us])

        assert {"shipments-eu", "shipments-us"} <= channel_addresses(doc)

    async def test_two_consumers_on_one_channel_share_the_channel_entry(
        self, bus: InMemoryEventBus
    ) -> None:
        first = StaticChannelConsumer()
        second = SecondOrdersConsumer()
        first.register_to(bus)
        second.register_to(bus)

        doc = generate([first, second])

        orders = [ch for ch in doc["channels"].values() if ch["address"] == "orders"]
        assert len(orders) == 1
        assert len(doc["operations"]) == 2

    async def test_two_consumers_on_one_channel_contribute_both_messages(
        self, bus: InMemoryEventBus
    ) -> None:
        first = StaticChannelConsumer()
        second = SecondOrdersConsumer()
        first.register_to(bus)
        second.register_to(bus)

        doc = generate([first, second])

        (orders,) = [ch for ch in doc["channels"].values() if ch["address"] == "orders"]
        assert len(orders["messages"]) == 2

    async def test_unregistered_consumer_is_absent_from_the_document(
        self, bus: InMemoryEventBus
    ) -> None:
        # Correct behaviour, asserted: a consumer never wired is never documented.
        registered = StaticChannelConsumer()
        registered.register_to(bus)
        never_registered = CallableChannelConsumer(channel="shipments-eu")

        doc = generate([registered, never_registered])

        assert "shipments-eu" not in channel_addresses(doc)
        assert len(doc["operations"]) == 1

    async def test_generator_accepts_a_container_as_the_consumer_source(
        self, bus: InMemoryEventBus
    ) -> None:
        # §D-AA1: "consumer instances **or a container**" — never a static walk.
        from providify import DIContainer  # noqa: PLC0415
        from varco_core.asyncapi import generate_asyncapi  # noqa: PLC0415

        consumer = StaticChannelConsumer()
        consumer.register_to(bus)
        container = DIContainer()
        container.provide(consumer, returns=StaticChannelConsumer)

        doc = generate_asyncapi(container, title="Orders API", version="1.0.0")

        assert "orders" in channel_addresses(doc)


# ── Bindings (§D-AA3) ──────────────────────────────────────────────────────────


class TestBindings:
    async def test_kafka_channel_binding_carries_the_topic(self, bus: InMemoryEventBus) -> None:
        consumer = StaticChannelConsumer()
        consumer.register_to(bus)

        doc = generate([consumer], protocol="kafka", group_id="orders-workers")

        (channel,) = [ch for ch in doc["channels"].values() if ch["address"] == "orders"]
        assert channel["bindings"]["kafka"]["topic"] == "orders"
        assert channel["bindings"]["kafka"]["bindingVersion"] == "0.5.0"

    async def test_kafka_operation_binding_carries_the_group_id(
        self, bus: InMemoryEventBus
    ) -> None:
        consumer = StaticChannelConsumer()
        consumer.register_to(bus)

        doc = generate([consumer], protocol="kafka", group_id="orders-workers")

        (operation,) = list(doc["operations"].values())
        assert operation["bindings"]["kafka"]["groupId"] == "orders-workers"
        assert operation["bindings"]["kafka"]["bindingVersion"] == "0.5.0"

    async def test_redis_source_emits_no_binding_block_at_all(self, bus: InMemoryEventBus) -> None:
        # Redis binding 0.1.0 has zero properties; an empty stanza communicates
        # nothing, so none is emitted (§D-AA3).
        consumer = StaticChannelConsumer()
        consumer.register_to(bus)

        doc = generate([consumer], protocol="redis")

        (channel,) = [ch for ch in doc["channels"].values() if ch["address"] == "orders"]
        (operation,) = list(doc["operations"].values())
        assert "redis" not in channel.get("bindings", {})
        assert "redis" not in operation.get("bindings", {})

    async def test_nats_operation_binding_present_only_with_a_queue_group(
        self, bus: InMemoryEventBus
    ) -> None:
        consumer = StaticChannelConsumer()
        consumer.register_to(bus)

        doc = generate([consumer], protocol="nats", queue_group="orders-q")

        (operation,) = list(doc["operations"].values())
        assert operation["bindings"]["nats"]["queue"] == "orders-q"
        assert operation["bindings"]["nats"]["bindingVersion"] == "0.1.0"

    async def test_nats_binding_absent_without_a_queue_group(self, bus: InMemoryEventBus) -> None:
        # A bindings stanza carrying only bindingVersion is noise.
        consumer = StaticChannelConsumer()
        consumer.register_to(bus)

        doc = generate([consumer], protocol="nats")

        (operation,) = list(doc["operations"].values())
        assert "nats" not in operation.get("bindings", {})

    async def test_no_protocol_emits_no_bindings(self, bus: InMemoryEventBus) -> None:
        consumer = StaticChannelConsumer()
        consumer.register_to(bus)

        doc = generate([consumer])

        (channel,) = [ch for ch in doc["channels"].values() if ch["address"] == "orders"]
        assert not channel.get("bindings")


# ── Servers (§D-AA2) ───────────────────────────────────────────────────────────


class TestServers:
    async def test_no_servers_block_by_default(self, bus: InMemoryEventBus) -> None:
        # A broker URL is deployment config, not source truth — baking a dev URL
        # into a committed snapshot is the rot the gate exists to prevent.
        consumer = StaticChannelConsumer()
        consumer.register_to(bus)

        doc = generate([consumer])

        assert "servers" not in doc

    async def test_explicit_server_is_emitted_with_host_and_protocol(
        self, bus: InMemoryEventBus
    ) -> None:
        consumer = StaticChannelConsumer()
        consumer.register_to(bus)

        doc = generate([consumer], servers={"prod": "kafka://broker.example.com:9092"})

        assert doc["servers"]["prod"]["host"] == "broker.example.com:9092"
        assert doc["servers"]["prod"]["protocol"] == "kafka"
