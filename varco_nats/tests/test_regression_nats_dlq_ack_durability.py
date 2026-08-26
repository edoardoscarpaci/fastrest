"""
Regression guard: ``NatsDLQ.ack()`` must wait for the server to process the ack.

User reports: ``test_push_pop_ack_round_trip`` fails at
``assert await dlq.count() == 0`` — the entry is still in the stream right
after ``ack()`` returned.  Correct behaviour is a count of 0, because
``AbstractDeadLetterQueue.ack``'s contract is "Removes the entry from the DLQ
so it is not returned by future ``pop_batch`` calls" — a postcondition that
must hold when ``ack()`` returns, not eventually.

Root cause: nats-py's ``Msg.ack()`` is fire-and-forget — it publishes to the
reply subject and returns without a round trip
(``await self._client.publish(self.reply)``).  ``Msg.ack_sync()`` issues a
``request`` and waits for the server to confirm.  Measured against a real
JetStream server: count was 1 immediately after ``ack()`` and 0 one second
later.  Beyond the failing assertion this breaks ``DlqRedriver``'s
publish-then-ack policy — a process exiting right after ``ack()`` returns can
lose the ack entirely and redeliver the dead letter.

Plan 012 / RT2, Step 13 audit note: fully fake-backed (``_FakeMsg`` below) —
no real NATS broker required.
"""

from __future__ import annotations

import uuid

from varco_nats.dlq import NatsDLQ


class _FakeMsg:
    """Records which acknowledgement API the DLQ used."""

    def __init__(self) -> None:
        self.ack_called = False
        self.ack_sync_called = False

    async def ack(self) -> None:
        self.ack_called = True

    async def ack_sync(self, timeout: float = 1.0) -> _FakeMsg:
        self.ack_sync_called = True
        return self


def _dlq_with_in_flight(msg: _FakeMsg, entry_id: uuid.UUID) -> NatsDLQ:
    dlq = NatsDLQ.__new__(NatsDLQ)  # bypass __init__/connection setup
    dlq._in_flight = {str(entry_id): msg}
    return dlq


async def test_regression_ack_waits_for_server_confirmation() -> None:
    """ack() must use the confirming API, not fire-and-forget publish."""
    entry_id = uuid.uuid4()
    msg = _FakeMsg()
    dlq = _dlq_with_in_flight(msg, entry_id)

    await dlq.ack(entry_id)

    assert msg.ack_sync_called, (
        "NatsDLQ.ack() used fire-and-forget Msg.ack(); the entry may still be "
        "in the stream when ack() returns, violating the ABC postcondition."
    )
    assert not msg.ack_called


async def test_regression_ack_removes_the_entry_from_in_flight() -> None:
    """A successfully acked entry must not be re-ackable from _in_flight."""
    entry_id = uuid.uuid4()
    msg = _FakeMsg()
    dlq = _dlq_with_in_flight(msg, entry_id)

    await dlq.ack(entry_id)
    assert dlq._in_flight == {}


async def test_regression_ack_of_unknown_entry_is_a_silent_noop() -> None:
    """The ABC documents an unknown entry_id as a no-op, not an error."""
    dlq = _dlq_with_in_flight(_FakeMsg(), uuid.uuid4())
    await dlq.ack(uuid.uuid4())  # must not raise


async def test_regression_ack_timeout_does_not_propagate() -> None:
    """
    A timed-out ack must not blow up a redrive loop.

    The entry stays in ``_in_flight`` so a later ``ack()`` can retry it — a
    duplicate ack is harmless to JetStream, a lost entry is not.
    """

    class _TimingOutMsg(_FakeMsg):
        async def ack_sync(self, timeout: float = 1.0):
            raise TimeoutError("server did not confirm")

    entry_id = uuid.uuid4()
    msg = _TimingOutMsg()
    dlq = _dlq_with_in_flight(msg, entry_id)

    await dlq.ack(entry_id)  # must not raise
    assert str(entry_id) in dlq._in_flight, "entry must remain retryable"
