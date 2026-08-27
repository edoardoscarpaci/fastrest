"""
Real-broker NATS delivery-semantics coverage (Plan 018 / RT2, Step 8).

``varco_nats`` advertises three ``NatsDeliverySemantics`` values but, before
this module, none of them had any real-JetStream verification — the only
real-broker tests in the package were ``test_nats_integration.py``'s two.
These three tests drive the two source branches that implement the
guarantees:

- ``bus.py:549`` — ``pre_ack = semantics is NatsDeliverySemantics.AT_MOST_ONCE``
  (ack **before** dispatch vs ack **after** dispatch).
- ``bus.py:376`` — the ``EXACTLY_ONCE`` publish path attaching the
  ``Nats-Msg-Id`` header so JetStream collapses producer-retry duplicates.

The at-most-once test asserts a **documented weakness** (data loss on a
handler failure), not a strength — the same principle §RT5-eos applies to
``KafkaDeliverySemantics.AT_MOST_ONCE``. A guarantee whose failure mode is
untested is a guarantee nobody can reason about.

Per-test namespacing: the ``nats_url`` container is session-scoped and
shared with every other test in this package, so every test owns a
``uuid4().hex[:8]``-suffixed stream / subject prefix / durable name.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from varco_core.event import Event
from varco_nats.bus import NatsEventBus
from varco_nats.config import NatsDeliverySemantics, NatsEventBusSettings

pytestmark = pytest.mark.integration

# JetStream delivery is asynchronous and a durable consumer's ack-wait
# redelivery is seconds-scale — generous, explicitly commented deadlines
# (CLAUDE.md's convention: widen the margin, never mark it xfail).
_DELIVERY_TIMEOUT = 20.0
_REDELIVERY_TIMEOUT = 45.0
_QUIET_PERIOD = 8.0


class SemanticsOrderEvent(Event):
    __event_type__ = "order.semantics.nats"
    order_id: str


def _settings(
    nats_url: str,
    *,
    run_id: str,
    semantics: NatsDeliverySemantics,
    max_deliver: int = 5,
) -> NatsEventBusSettings:
    """Build a fully namespaced settings object for one test."""
    return NatsEventBusSettings(
        servers=nats_url,
        stream_name=f"sem-{run_id}",
        subject_prefix=f"sem{run_id}",
        durable_name=f"sem-durable-{run_id}",
        delivery_semantics=semantics,
        max_deliver=max_deliver,
    )


async def _poll_until(predicate, timeout: float) -> bool:
    """Poll ``predicate`` to a deadline — never sleep-then-assert."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    return predicate()


async def test_at_least_once_redelivers_after_handler_raises(nats_url: str) -> None:
    """
    Under ``AT_LEAST_ONCE`` a message whose handler raised must be delivered
    again — the message was never successfully processed, so the guarantee
    ("at least one *successful* dispatch") is unmet until it is.

    Fixed by Plan 019 / RT2-B: ``_on_message`` now ``nak()``s a raising
    handler's message, which is an explicit request for **immediate**
    redelivery (research 005 §D) rather than the ack-wait-driven timeout the
    old (buggy) unconditional-ack implementation would have needed to be
    waited out even if it had been fixed a different way.

    Edge cases:
        - Redelivery is still asynchronous broker round-trip work, so the
          deadline stays generous even though the common case is now fast.
    """
    run_id = uuid.uuid4().hex[:8]
    settings = _settings(nats_url, run_id=run_id, semantics=NatsDeliverySemantics.AT_LEAST_ONCE)

    deliveries: list[str] = []

    async def failing_handler(event: SemanticsOrderEvent) -> None:
        deliveries.append(event.order_id)
        raise RuntimeError("simulated handler failure")

    bus = NatsEventBus(settings)
    bus.subscribe(SemanticsOrderEvent, failing_handler, channel="orders")
    async with bus:
        await bus.publish(SemanticsOrderEvent(order_id="o-1"), channel="orders")
        await _poll_until(lambda: len(deliveries) >= 2, _REDELIVERY_TIMEOUT)

    assert len(deliveries) >= 2, (
        "AT_LEAST_ONCE must redeliver a message whose handler raised; "
        f"the broker delivered it {len(deliveries)} time(s)"
    )
    assert set(deliveries) == {"o-1"}


async def test_at_most_once_does_not_redeliver_after_handler_raises(nats_url: str) -> None:
    """
    Under ``AT_MOST_ONCE`` a message whose handler raised is **lost** — the
    documented weakness of the mode (``bus.py:549``'s ``pre_ack`` branch acks
    before dispatch). Asserting the weakness is as load-bearing as asserting
    the strength: a silent upgrade to at-least-once would change the mode's
    cost/latency contract without anyone noticing.
    """
    run_id = uuid.uuid4().hex[:8]
    settings = _settings(nats_url, run_id=run_id, semantics=NatsDeliverySemantics.AT_MOST_ONCE)

    deliveries: list[str] = []

    async def failing_handler(event: SemanticsOrderEvent) -> None:
        deliveries.append(event.order_id)
        raise RuntimeError("simulated handler failure")

    bus = NatsEventBus(settings)
    bus.subscribe(SemanticsOrderEvent, failing_handler, channel="orders")
    async with bus:
        await bus.publish(SemanticsOrderEvent(order_id="o-1"), channel="orders")
        # Wait for the first delivery, then hold a quiet period long enough
        # that a redelivery (if the mode were wrong) would have arrived.
        assert await _poll_until(lambda: len(deliveries) >= 1, _DELIVERY_TIMEOUT), (
            "the message was never delivered even once"
        )
        await asyncio.sleep(_QUIET_PERIOD)

    assert deliveries == ["o-1"], (
        "AT_MOST_ONCE acks before dispatch — a failed handler must lose the "
        f"message, never see it again; got {deliveries}"
    )


async def test_exactly_once_dedups_duplicate_publish(nats_url: str) -> None:
    """
    Under ``EXACTLY_ONCE`` the publish path stamps ``Nats-Msg-Id`` with the
    event id (``bus.py:376``), so publishing the *same* ``Event`` instance
    twice inside the stream's duplicate window yields exactly one delivery.

    Edge cases:
        - The dedup key is ``event.event_id``, so two *distinct* events with
          identical payloads are NOT deduped — that is deliberate and is why
          this test republishes one instance rather than two equal ones.
    """
    run_id = uuid.uuid4().hex[:8]
    settings = _settings(nats_url, run_id=run_id, semantics=NatsDeliverySemantics.EXACTLY_ONCE)

    deliveries: list[str] = []

    async def handler(event: SemanticsOrderEvent) -> None:
        deliveries.append(str(event.event_id))

    bus = NatsEventBus(settings)
    bus.subscribe(SemanticsOrderEvent, handler, channel="orders")
    async with bus:
        event = SemanticsOrderEvent(order_id="o-dup")
        await bus.publish(event, channel="orders")
        await bus.publish(event, channel="orders")

        assert await _poll_until(lambda: len(deliveries) >= 1, _DELIVERY_TIMEOUT), (
            "the deduped event was never delivered at all"
        )
        # Quiet period: if dedup did not engage, the second copy arrives here.
        await asyncio.sleep(_QUIET_PERIOD)

    assert len(deliveries) == 1, (
        "EXACTLY_ONCE attaches Nats-Msg-Id so a repeat publish inside the "
        f"duplicate window is collapsed by JetStream; got {len(deliveries)} deliveries"
    )


async def test_at_least_once_stops_redelivering_after_max_deliver(nats_url: str) -> None:
    """
    A permanently-raising handler must not nak() forever — once
    ``num_delivered`` reaches ``max_deliver`` the message is term()ed and
    redelivery stops. This is the guard against "fixed RT2-B" silently
    meaning "infinite redelivery loop" (Plan 019 §RT2-B-nak, Edge cases).

    Edge cases:
        - Deliveries must plateau at exactly ``max_deliver`` — polling a while
          past the point they should have stopped confirms no further
          redelivery occurs, not just that the count eventually reaches it.
    """
    run_id = uuid.uuid4().hex[:8]
    settings = _settings(
        nats_url, run_id=run_id, semantics=NatsDeliverySemantics.AT_LEAST_ONCE
    ).model_copy(update={"max_deliver": 3})

    deliveries: list[str] = []

    async def failing_handler(event: SemanticsOrderEvent) -> None:
        deliveries.append(event.order_id)
        raise RuntimeError("simulated handler failure")

    bus = NatsEventBus(settings)
    bus.subscribe(SemanticsOrderEvent, failing_handler, channel="orders")
    async with bus:
        await bus.publish(SemanticsOrderEvent(order_id="o-1"), channel="orders")
        await _poll_until(lambda: len(deliveries) >= 3, _REDELIVERY_TIMEOUT)
        # Quiet period past the point redelivery should have stopped — proves
        # the count plateaus rather than merely having reached 3 once.
        await asyncio.sleep(_QUIET_PERIOD)

    assert len(deliveries) == 3, (
        "redelivery must stop exactly at max_deliver, never grow past it; "
        f"got {len(deliveries)} deliveries"
    )
