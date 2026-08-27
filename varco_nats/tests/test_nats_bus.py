"""
Unit tests for varco_nats.NatsEventBus
=======================================
All tests fake ``nats-py`` — no real NATS broker required.

Integration tests that spin up a real NATS server via Docker live in
``test_nats_integration.py`` and are disabled by default::

    pytest -m integration

Test doubles
------------
``tests.fakes`` provides ``FakeNatsClient`` / ``FakeJetStream`` which model
JetStream stream storage, push-subscription callback delivery, and
acknowledgement.  ``varco_nats.bus.connect`` is patched to return the fake.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from varco_core.event import Event, Subscription
from varco_core.event.serializer import JsonEventSerializer

from tests.fakes import FakeJetStream, FakeMsg, FakeNatsClient, OrderPlacedEvent
from varco_nats import NatsDeliverySemantics, NatsEventBus, NatsEventBusSettings

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_js() -> FakeJetStream:
    return FakeJetStream()


@pytest.fixture
def fake_nc(fake_js: FakeJetStream) -> FakeNatsClient:
    return FakeNatsClient(fake_js)


@asynccontextmanager
async def _started_bus(
    nc: FakeNatsClient,
    config: NatsEventBusSettings,
    *,
    pre_subscribe: list[tuple[type[Event], object, str]] | None = None,
) -> AsyncIterator[NatsEventBus]:
    """
    Build a ``NatsEventBus`` wired to ``nc`` and start it.

    ``pre_subscribe`` registers handlers BEFORE ``start()`` so the JetStream
    consumers are opened deterministically by ``start()`` — avoiding the
    fire-and-forget task that a post-start ``subscribe()`` schedules.
    """

    async def _fake_connect(**_: object) -> FakeNatsClient:
        return nc

    with patch("varco_nats.bus.connect", new=_fake_connect):
        bus = NatsEventBus(config)
        for event_type, handler, channel in pre_subscribe or []:
            bus.subscribe(event_type, handler, channel=channel)  # type: ignore[arg-type]
        async with bus:
            yield bus


# ── Lifecycle ─────────────────────────────────────────────────────────────────


class TestLifecycle:
    async def test_publish_before_start_raises(self) -> None:
        bus = NatsEventBus(NatsEventBusSettings())
        with pytest.raises(RuntimeError, match="start()"):
            await bus.publish(OrderPlacedEvent(order_id="1"))

    async def test_start_idempotent(self, fake_nc: FakeNatsClient) -> None:
        async def _fake_connect(**_: object) -> FakeNatsClient:
            return fake_nc

        with patch("varco_nats.bus.connect", new=_fake_connect):
            bus = NatsEventBus(NatsEventBusSettings())
            await bus.start()
            await bus.start()  # second call is a no-op
            assert bus._started
            await bus.stop()

    async def test_stop_before_start_is_noop(self) -> None:
        bus = NatsEventBus(NatsEventBusSettings())
        await bus.stop()  # must not raise

    async def test_context_manager_starts_and_stops(self, fake_nc: FakeNatsClient) -> None:
        async with _started_bus(fake_nc, NatsEventBusSettings()) as bus:
            assert bus._started
        assert not bus._started

    async def test_start_creates_backing_stream(
        self, fake_nc: FakeNatsClient, fake_js: FakeJetStream
    ) -> None:
        async with _started_bus(fake_nc, NatsEventBusSettings()):
            # auto_create_stream=True → start() must have created the stream.
            assert "varco-events" in fake_js.streams
            assert fake_js.streams["varco-events"].subjects == ["varco.>"]

    async def test_start_skips_stream_creation_when_disabled(
        self, fake_nc: FakeNatsClient, fake_js: FakeJetStream
    ) -> None:
        config = NatsEventBusSettings(auto_create_stream=False)
        async with _started_bus(fake_nc, config):
            # auto_create_stream=False → the bus must not create the stream.
            assert "varco-events" not in fake_js.streams


# ── Publishing ────────────────────────────────────────────────────────────────


class TestPublish:
    async def test_publish_sends_to_correct_subject(
        self, fake_nc: FakeNatsClient, fake_js: FakeJetStream
    ) -> None:
        async with _started_bus(fake_nc, NatsEventBusSettings()) as bus:
            await bus.publish(OrderPlacedEvent(order_id="abc"), channel="orders")
        assert fake_js.published[0][0] == "varco.orders"

    async def test_published_bytes_contain_event_type(
        self, fake_nc: FakeNatsClient, fake_js: FakeJetStream
    ) -> None:
        async with _started_bus(fake_nc, NatsEventBusSettings()) as bus:
            await bus.publish(OrderPlacedEvent(order_id="1"), channel="orders")
        _, payload, _ = fake_js.published[0]
        raw = json.loads(payload.decode("utf-8"))
        assert raw["__event_type__"] == "order.placed.nats_test"

    async def test_publish_returns_none(self, fake_nc: FakeNatsClient) -> None:
        async with _started_bus(fake_nc, NatsEventBusSettings()) as bus:
            result = await bus.publish(OrderPlacedEvent(order_id="1"), channel="orders")
            assert result is None

    async def test_channel_prefix_applied(
        self, fake_nc: FakeNatsClient, fake_js: FakeJetStream
    ) -> None:
        config = NatsEventBusSettings(channel_prefix="prod.")
        async with _started_bus(fake_nc, config) as bus:
            await bus.publish(OrderPlacedEvent(order_id="1"), channel="orders")
        assert fake_js.published[0][0] == "varco.prod.orders"

    async def test_exactly_once_attaches_msg_id_header(
        self, fake_nc: FakeNatsClient, fake_js: FakeJetStream
    ) -> None:
        config = NatsEventBusSettings(delivery_semantics=NatsDeliverySemantics.EXACTLY_ONCE)
        event = OrderPlacedEvent(order_id="1")
        async with _started_bus(fake_nc, config) as bus:
            await bus.publish(event, channel="orders")
        _, _, headers = fake_js.published[0]
        # EXACTLY_ONCE → Nats-Msg-Id header == event_id enables JetStream dedup.
        assert headers == {"Nats-Msg-Id": str(event.event_id)}

    async def test_at_least_once_has_no_msg_id_header(
        self, fake_nc: FakeNatsClient, fake_js: FakeJetStream
    ) -> None:
        async with _started_bus(fake_nc, NatsEventBusSettings()) as bus:
            await bus.publish(OrderPlacedEvent(order_id="1"), channel="orders")
        assert fake_js.published[0][2] is None

    async def test_publish_many(self, fake_nc: FakeNatsClient, fake_js: FakeJetStream) -> None:
        async with _started_bus(fake_nc, NatsEventBusSettings()) as bus:
            await bus.publish_many(
                [
                    (OrderPlacedEvent(order_id="1"), "orders"),
                    (OrderPlacedEvent(order_id="2"), "orders"),
                ]
            )
        assert len(fake_js.published) == 2


# ── Subscribe ─────────────────────────────────────────────────────────────────


class TestSubscribe:
    async def test_subscribe_returns_subscription(self, fake_nc: FakeNatsClient) -> None:
        async def handler(e: Event) -> None: ...

        async with _started_bus(fake_nc, NatsEventBusSettings()) as bus:
            sub = bus.subscribe(OrderPlacedEvent, handler, channel="orders")
            assert isinstance(sub, Subscription)
            assert not sub.is_cancelled

    async def test_cancel_subscription(self, fake_nc: FakeNatsClient) -> None:
        async def handler(e: Event) -> None: ...

        async with _started_bus(fake_nc, NatsEventBusSettings()) as bus:
            sub = bus.subscribe(OrderPlacedEvent, handler, channel="orders")
            sub.cancel()
            assert sub.is_cancelled

    async def test_channel_all_opens_no_jetstream_consumer(
        self, fake_nc: FakeNatsClient, fake_js: FakeJetStream
    ) -> None:
        async def handler(e: Event) -> None: ...

        # CHANNEL_ALL is the default channel for subscribe().
        async with _started_bus(fake_nc, NatsEventBusSettings()) as bus:
            bus.subscribe(OrderPlacedEvent, handler)
            await asyncio.sleep(0)
            # Like KafkaEventBus, CHANNEL_ALL opens no broker-side consumer.
            assert bus._jetstream_subs == {}
            assert fake_js.push_subs == {}

    async def test_specific_channel_opens_one_consumer(self, fake_nc: FakeNatsClient) -> None:
        async def handler(e: Event) -> None: ...

        async with _started_bus(
            fake_nc,
            NatsEventBusSettings(),
            pre_subscribe=[(OrderPlacedEvent, handler, "orders")],
        ) as bus:
            assert "varco.orders" in bus._jetstream_subs

    async def test_two_subscriptions_same_channel_share_one_consumer(
        self, fake_nc: FakeNatsClient
    ) -> None:
        async def handler(e: Event) -> None: ...

        async with _started_bus(
            fake_nc,
            NatsEventBusSettings(),
            pre_subscribe=[
                (OrderPlacedEvent, handler, "orders"),
                (OrderPlacedEvent, handler, "orders"),
            ],
        ) as bus:
            # Deduplicated by subject — one JetStream consumer for the channel.
            assert len(bus._jetstream_subs) == 1


# ── Consumer dispatch ─────────────────────────────────────────────────────────


class TestConsumerDispatch:
    async def test_published_event_reaches_handler(self, fake_nc: FakeNatsClient) -> None:
        received: list[Event] = []

        async def handler(e: Event) -> None:
            received.append(e)

        async with _started_bus(
            fake_nc,
            NatsEventBusSettings(),
            pre_subscribe=[(OrderPlacedEvent, handler, "orders")],
        ) as bus:
            await bus.publish(OrderPlacedEvent(order_id="x"), channel="orders")

        assert len(received) == 1
        assert isinstance(received[0], OrderPlacedEvent)
        assert received[0].order_id == "x"

    async def test_channel_all_handler_receives_specific_channel_event(
        self, fake_nc: FakeNatsClient
    ) -> None:
        received: list[Event] = []

        async def specific(e: Event) -> None: ...

        async def catch_all(e: Event) -> None:
            received.append(e)

        # A concrete "orders" subscription opens the consumer; the CHANNEL_ALL
        # subscription receives the event via local dispatch.
        async with _started_bus(
            fake_nc,
            NatsEventBusSettings(),
            pre_subscribe=[(OrderPlacedEvent, specific, "orders")],
        ) as bus:
            bus.subscribe(OrderPlacedEvent, catch_all)  # channel=CHANNEL_ALL
            await bus.publish(OrderPlacedEvent(order_id="x"), channel="orders")

        assert len(received) == 1

    async def test_subscribe_after_start_opens_consumer(self, fake_nc: FakeNatsClient) -> None:
        received: list[Event] = []

        async def handler(e: Event) -> None:
            received.append(e)

        async with _started_bus(fake_nc, NatsEventBusSettings()) as bus:
            bus.subscribe(OrderPlacedEvent, handler, channel="orders")
            # subscribe() after start() schedules the consumer as a task.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            await bus.publish(OrderPlacedEvent(order_id="late"), channel="orders")

        assert len(received) == 1


# ── Acknowledgement semantics ─────────────────────────────────────────────────


class TestAckSemantics:
    def _msg(self, event: Event) -> FakeMsg:
        """Build a FakeMsg carrying a serialized event on the orders subject."""
        return FakeMsg("varco.orders", JsonEventSerializer().serialize(event))

    async def test_at_least_once_acks_after_dispatch(self) -> None:
        ack_state_at_handler_time: list[bool] = []
        bus = NatsEventBus(NatsEventBusSettings())

        msg = self._msg(OrderPlacedEvent(order_id="1"))

        async def handler(e: Event) -> None:
            # AT_LEAST_ONCE acks AFTER dispatch — not yet acked here.
            ack_state_at_handler_time.append(msg.acked)

        bus.subscribe(OrderPlacedEvent, handler, channel="orders")
        await bus._on_message(msg)

        assert ack_state_at_handler_time == [False]  # not acked during dispatch
        assert msg.acked is True  # acked afterwards

    async def test_at_most_once_acks_before_dispatch(self) -> None:
        ack_state_at_handler_time: list[bool] = []
        config = NatsEventBusSettings(delivery_semantics=NatsDeliverySemantics.AT_MOST_ONCE)
        bus = NatsEventBus(config)

        msg = self._msg(OrderPlacedEvent(order_id="1"))

        async def handler(e: Event) -> None:
            # AT_MOST_ONCE acks BEFORE dispatch — already acked here.
            ack_state_at_handler_time.append(msg.acked)

        bus.subscribe(OrderPlacedEvent, handler, channel="orders")
        await bus._on_message(msg)

        assert ack_state_at_handler_time == [True]

    async def test_message_naked_when_handler_raises(self) -> None:
        # Plan 019 / RT2-B: a raising handler under AT_LEAST_ONCE now naks
        # (immediate redelivery, bounded by max_deliver) instead of acking —
        # superseded by TestRedeliveryOnHandlerFailure below, kept here as the
        # direct regression check for this exact pre-existing scenario.
        bus = NatsEventBus(NatsEventBusSettings())
        msg = self._msg(OrderPlacedEvent(order_id="1"))

        async def boom(e: Event) -> None:
            raise RuntimeError("handler failed")

        bus.subscribe(OrderPlacedEvent, boom, channel="orders")
        await bus._on_message(msg)  # must not raise

        assert msg.naked is True
        assert msg.acked is False

    async def test_poison_payload_is_termed_not_acked(self) -> None:
        # Plan 019 / RT2-B: a payload that cannot be deserialized is term()ed
        # (never redelivered) rather than ack()ed — mirrors Kafka advancing
        # past bad payloads, but via the JetStream-native "never again" API
        # instead of a silent ack.
        bus = NatsEventBus(NatsEventBusSettings())
        msg = FakeMsg("varco.orders", b"not-valid-json")
        await bus._on_message(msg)  # must not raise
        assert msg.termed is True
        assert msg.acked is False


# ── Redelivery on handler failure (RT2-B) ───────────────────────────────────
#
# The current implementation acks unconditionally in a `finally`, regardless
# of outcome (bus.py:568-571) — every test below is expected to FAIL against
# that implementation because nak()/term() are never called and ack() is
# always called, contradicting the outcome-driven table in Plan 019 §RT2-B-nak.


class TestRedeliveryOnHandlerFailure:
    def _msg(self, event: Event, *, num_delivered: int = 1) -> FakeMsg:
        return FakeMsg(
            "varco.orders",
            JsonEventSerializer().serialize(event),
            num_delivered=num_delivered,
        )

    async def test_handler_raises_under_at_least_once_naks_not_acks(self) -> None:
        # AT_LEAST_ONCE + a raising handler must nak() for immediate
        # redelivery (research 005 §D), never ack() — acking would silently
        # drop the failed dispatch, which is the RT2-B bug.
        config = NatsEventBusSettings(max_deliver=5)
        bus = NatsEventBus(config)
        msg = self._msg(OrderPlacedEvent(order_id="1"), num_delivered=1)

        async def boom(e: Event) -> None:
            raise RuntimeError("handler failed")

        bus.subscribe(OrderPlacedEvent, boom, channel="orders")
        await bus._on_message(msg)  # must not raise

        assert msg.naked is True
        assert msg.acked is False
        assert msg.termed is False

    async def test_handler_succeeds_acks_not_naks(self) -> None:
        config = NatsEventBusSettings(max_deliver=5)
        bus = NatsEventBus(config)
        msg = self._msg(OrderPlacedEvent(order_id="1"), num_delivered=1)

        async def handler(e: Event) -> None:
            return None

        bus.subscribe(OrderPlacedEvent, handler, channel="orders")
        await bus._on_message(msg)

        assert msg.acked is True
        assert msg.naked is False
        assert msg.termed is False

    async def test_handler_raises_at_max_deliver_terms_not_naks(self) -> None:
        # num_delivered has already reached max_deliver — redelivery budget
        # exhausted, term() disables all future redeliveries (research 005 §A).
        config = NatsEventBusSettings(max_deliver=3)
        bus = NatsEventBus(config)
        msg = self._msg(OrderPlacedEvent(order_id="1"), num_delivered=3)

        async def boom(e: Event) -> None:
            raise RuntimeError("handler failed")

        bus.subscribe(OrderPlacedEvent, boom, channel="orders")
        await bus._on_message(msg)  # must not raise

        assert msg.termed is True
        assert msg.naked is False
        assert msg.acked is False

    async def test_deserialization_failure_terms_regardless_of_num_delivered(self) -> None:
        # A poison payload can never succeed on retry — term() unconditionally,
        # never nak() (which would infinite-loop a payload that can never parse).
        config = NatsEventBusSettings(max_deliver=5)
        bus = NatsEventBus(config)
        msg = FakeMsg("varco.orders", b"not-valid-json", num_delivered=1)

        await bus._on_message(msg)  # must not raise

        assert msg.termed is True
        assert msg.naked is False
        assert msg.acked is False

    async def test_at_most_once_acks_before_dispatch_even_when_handler_raises(self) -> None:
        # The documented weakness must survive the fix — AT_MOST_ONCE is
        # unchanged: pre-acked, message lost on handler failure.
        config = NatsEventBusSettings(
            delivery_semantics=NatsDeliverySemantics.AT_MOST_ONCE, max_deliver=5
        )
        bus = NatsEventBus(config)
        msg = self._msg(OrderPlacedEvent(order_id="1"), num_delivered=1)

        async def boom(e: Event) -> None:
            raise RuntimeError("handler failed")

        bus.subscribe(OrderPlacedEvent, boom, channel="orders")
        await bus._on_message(msg)  # must not raise

        assert msg.acked is True
        assert msg.naked is False
        assert msg.termed is False

    async def test_fire_forget_error_policy_acks_raising_handler(self) -> None:
        # FIRE_FORGET swallows the exception inside _dispatch — the bus sees a
        # successful dispatch and acks; this is FIRE_FORGET's documented
        # opt-out from redelivery (§RT2-B-nak's second ❌).
        from varco_core.event.base import ErrorPolicy

        config = NatsEventBusSettings(max_deliver=5)
        bus = NatsEventBus(config, error_policy=ErrorPolicy.FIRE_FORGET)
        msg = self._msg(OrderPlacedEvent(order_id="1"), num_delivered=1)

        async def boom(e: Event) -> None:
            raise RuntimeError("handler failed")

        bus.subscribe(OrderPlacedEvent, boom, channel="orders")
        await bus._on_message(msg)  # must not raise

        assert msg.acked is True
        assert msg.naked is False
        assert msg.termed is False


# ── repr ──────────────────────────────────────────────────────────────────────


class TestRepr:
    async def test_repr(self, fake_nc: FakeNatsClient) -> None:
        async with _started_bus(fake_nc, NatsEventBusSettings()) as bus:
            r = repr(bus)
            assert "NatsEventBus" in r
            assert "started=True" in r
