"""
Real-JetStream ``NatsDLQ`` ack-durability coverage (Plan 018 / RT2, Step 10).

Broker-side counterpart to the fake-backed regression suite in
``varco_nats/tests/test_regression_nats_dlq_ack_durability.py`` (whose
``_FakeMsg`` proves ``NatsDLQ.ack()`` calls the **confirming** nats-py API,
``Msg.ack_sync()``, rather than fire-and-forget ``Msg.ack()``).

Division of labour, stated in both directions:

- The **fake** proves the *call shape* — which nats-py method is invoked,
  what happens to ``_in_flight``, that an unknown ``entry_id`` is a no-op,
  that a timed-out ack does not propagate. No broker needed, runs in
  milliseconds.
- **This module** proves the *durability the call shape exists for*: after
  ``ack()`` returns, the entry is genuinely gone server-side, so a fresh
  consumer over the same DLQ stream never sees it again. That is the
  postcondition ``AbstractDeadLetterQueue.ack`` promises and the one a fake
  can never establish.

Per-test namespacing: DLQ stream/subject names carry a ``uuid4().hex[:8]``
run id because ``nats_url`` is session-scoped and shared.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from varco_core.event import Event
from varco_core.event.dlq import DeadLetterEntry
from varco_nats.config import NatsEventBusSettings
from varco_nats.dlq import NatsDLQ

pytestmark = pytest.mark.integration


class DlqOrderEvent(Event):
    __event_type__ = "order.dlq.nats"
    order_id: str


def _entry(order_id: str) -> DeadLetterEntry:
    return DeadLetterEntry.from_failure(
        event=DlqOrderEvent(order_id=order_id),
        channel="orders",
        handler_name="on_order",
        last_exc=RuntimeError("boom"),
        attempts=3,
        first_failed_at=datetime.now(UTC),
    )


async def test_acked_entry_does_not_reappear_to_a_fresh_consumer(nats_url: str) -> None:
    """
    push → pop → ack → drop and recreate the consumer → the entry is gone.

    Recreating the ``NatsDLQ`` (and therefore its pull consumer) is what
    makes this a *durability* assertion rather than an in-process bookkeeping
    one: the second instance shares no ``_in_flight`` state with the first,
    so anything it sees came from the broker.

    Edge cases:
        - A second, *unacked* entry is pushed alongside the acked one as a
          positive control. Without it, an assertion of "the fresh consumer
          popped nothing" would also pass if the stream were simply
          unreachable or misnamed. The control asserts on ``count()``, not
          on a second ``pop_batch()``: the survivor is still *in flight* to
          the first instance's durable consumer until JetStream's ack-wait
          deadline elapses, so a fresh ``pop_batch()`` legitimately returns
          nothing for ~30 s. ``count()`` reads the stream state directly and
          is deterministic — WorkQueue retention deletes an entry the moment
          it is acked, so a count of exactly 1 proves the acked entry is
          gone AND the un-acked one is not.
    """
    run_id = uuid.uuid4().hex[:8]
    settings = NatsEventBusSettings(
        servers=nats_url,
        stream_name=f"dlqit-{run_id}",
        subject_prefix=f"dlqit{run_id}",
    )
    dlq_stream = f"dlqit-{run_id}-dlq"
    dlq_subject = f"dlqit{run_id}.__dlq__"

    acked_id: uuid.UUID

    async with NatsDLQ(settings, dlq_stream=dlq_stream, dlq_subject=dlq_subject) as dlq:
        await dlq.push(_entry("o-acked"))
        await dlq.push(_entry("o-survivor"))

        popped = await dlq.pop_batch(limit=10)
        assert len(popped) == 2, f"expected both pushed entries, got {len(popped)}"

        target = next(e for e in popped if e.event.order_id == "o-acked")  # type: ignore[attr-defined]
        acked_id = target.entry_id
        await dlq.ack(acked_id)

    # A brand-new DLQ instance — new connection, new pull consumer, no
    # shared _in_flight bookkeeping with the instance that acked.
    async with NatsDLQ(settings, dlq_stream=dlq_stream, dlq_subject=dlq_subject) as fresh:
        redelivered = await fresh.pop_batch(limit=10)
        remaining = await fresh.count()

    ids = {e.entry_id for e in redelivered}
    assert acked_id not in ids, (
        "an acked dead letter reappeared to a fresh consumer — ack() returned "
        "before the server durably recorded it (see the _FakeMsg regression "
        "suite in test_regression_nats_dlq_ack_durability.py for the call-shape half)"
    )
    assert remaining == 1, (
        "positive control: the acked entry must be gone from the stream and "
        f"the un-acked one must remain; got a stream count of {remaining}"
    )
