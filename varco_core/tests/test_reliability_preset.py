"""
tests.test_reliability_preset
===============================
Plan 009, Phase 9 (R5) — ReliabilityPreset + the ``@listen`` `_UNSET` sentinel
(RD-7).

RED until ``varco_core/reliability/`` lands and ``@listen``'s ``retry_policy=``/
``dlq=`` defaults switch from ``None`` to the private sentinel.

Uses InMemoryEventBus + InMemoryDeadLetterQueue per repo test conventions.
"""

from __future__ import annotations

import asyncio

import pytest

from varco_core.event import Event
from varco_core.event.consumer import EventConsumer, listen
from varco_core.event.dlq import InMemoryDeadLetterQueue
from varco_core.event.memory import InMemoryEventBus


@pytest.fixture
def instant_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Collapse retry-wrapper backoff sleeps to zero.

    ``ReliabilityPreset.durable()`` uses ``RetryPolicy.durable_delivery()``
    (``max_attempts=20``, ``base_delay=15.0``, ``max_delay=3600.0``) —
    correct for production, hours of wall clock in a test. Same pattern as
    ``test_audit.py``'s ``instant_backoff`` fixture: patch the shared
    ``asyncio`` module object (``varco_core.resilience.retry`` does a plain
    ``import asyncio`` and calls ``asyncio.sleep(delay)``), collapsing the
    delay while keeping the real attempt sequencing.
    """
    real_sleep = asyncio.sleep

    async def _no_delay(_delay: float, *args: object, **kwargs: object) -> None:
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _no_delay)


class SampleEvent(Event):
    __event_type__ = "test.reliability_preset.sample"


@pytest.fixture(autouse=True)
def _reset_default_preset():
    """Reliability preset default is process-global (RD-7's `off()` default) --
    reset it around every test so tests don't leak into each other."""
    from varco_core.reliability import ReliabilityPreset, set_default_reliability_preset

    set_default_reliability_preset(ReliabilityPreset.off())
    yield
    set_default_reliability_preset(ReliabilityPreset.off())


class TestReliabilityPresetOff:
    def test_off_preset_has_no_retry_or_dlq(self) -> None:
        from varco_core.reliability import ReliabilityPreset

        preset = ReliabilityPreset.off()
        assert preset.retry_policy is None
        assert preset.dlq is None
        assert preset.outbox is False
        assert preset.audit is False

    async def test_bare_listen_with_off_default_behaves_like_today(self) -> None:
        """Byte-identical to pre-Plan-009 behaviour: a bare @listen handler
        with no retry_policy/dlq re-raises on failure."""

        class FailingConsumer(EventConsumer):
            calls = 0

            @listen(SampleEvent, channel="ch")
            async def on_event(self, event: SampleEvent) -> None:
                FailingConsumer.calls += 1
                raise RuntimeError("handler always fails")

        bus = InMemoryEventBus()
        consumer = FailingConsumer()
        consumer.register_to(bus)
        # InMemoryEventBus defaults to DispatchMode.SYNC — publish() itself
        # propagates the handler's exception synchronously (no retry/DLQ
        # configured); there is nothing left to drain() afterwards.
        with pytest.raises(Exception):
            await bus.publish(SampleEvent(), channel="ch")


class TestReliabilityPresetDurable:
    def test_outbox_max_attempts_without_dlq_raises(self) -> None:
        from varco_core.reliability import ReliabilityPreset

        with pytest.raises(ValueError):
            ReliabilityPreset(outbox_max_attempts=5)

    async def test_durable_preset_gives_bare_listen_retry_and_dlq(
        self, instant_backoff
    ) -> None:
        from varco_core.reliability import (
            ReliabilityPreset,
            set_default_reliability_preset,
        )

        dlq = InMemoryDeadLetterQueue()
        preset = ReliabilityPreset.durable(dlq=dlq)
        set_default_reliability_preset(preset)

        class FlakyConsumer(EventConsumer):
            @listen(SampleEvent, channel="ch")
            async def on_event(self, event: SampleEvent) -> None:
                raise RuntimeError("always fails")

        bus = InMemoryEventBus()
        consumer = FlakyConsumer()
        consumer.register_to(bus)
        await bus.publish(SampleEvent(), channel="ch")
        await bus.drain()

        # A durable default preset routes exhausted retries to the DLQ instead
        # of propagating the exception out of drain().
        assert await dlq.count() == 1

    async def test_explicit_retry_policy_none_wins_over_durable_default(self) -> None:
        """RD-7: an explicitly passed retry_policy=None must NOT inherit the
        global default -- distinguishable from omission via the _UNSET sentinel."""
        from varco_core.reliability import (
            ReliabilityPreset,
            set_default_reliability_preset,
        )

        dlq = InMemoryDeadLetterQueue()
        set_default_reliability_preset(ReliabilityPreset.durable(dlq=dlq))

        class ExplicitOptOutConsumer(EventConsumer):
            @listen(SampleEvent, channel="ch", retry_policy=None, dlq=None)
            async def on_event(self, event: SampleEvent) -> None:
                raise RuntimeError("always fails")

        bus = InMemoryEventBus()
        consumer = ExplicitOptOutConsumer()
        consumer.register_to(bus)
        # SYNC dispatch (the default) propagates synchronously from publish().
        with pytest.raises(Exception):
            await bus.publish(SampleEvent(), channel="ch")
        # Nothing should have been pushed to the durable default's DLQ.
        assert await dlq.count() == 0

    async def test_late_set_default_preset_still_applies(self, instant_backoff) -> None:
        """A preset set AFTER the @listen-decorated class is defined still
        applies at register_to() time -- resolution must be deferred."""
        from varco_core.reliability import (
            ReliabilityPreset,
            set_default_reliability_preset,
        )

        class LateBoundConsumer(EventConsumer):
            @listen(SampleEvent, channel="ch")
            async def on_event(self, event: SampleEvent) -> None:
                raise RuntimeError("always fails")

        dlq = InMemoryDeadLetterQueue()
        # Preset is set AFTER the class body executed.
        set_default_reliability_preset(ReliabilityPreset.durable(dlq=dlq))

        bus = InMemoryEventBus()
        consumer = LateBoundConsumer()
        consumer.register_to(bus)
        await bus.publish(SampleEvent(), channel="ch")
        await bus.drain()

        assert await dlq.count() == 1


class TestListenUnsetSentinel:
    def test_omitted_retry_policy_is_distinguishable_from_explicit_none(self) -> None:
        """The stored metadata for an omitted retry_policy/dlq must be the
        private _UNSET sentinel, not None -- otherwise explicit None can't be
        told apart from omission (RD-7)."""

        class Omitted(EventConsumer):
            @listen(SampleEvent, channel="ch")
            async def on_event(self, event: SampleEvent) -> None: ...

        class Explicit(EventConsumer):
            @listen(SampleEvent, channel="ch", retry_policy=None, dlq=None)
            async def on_event(self, event: SampleEvent) -> None: ...

        omitted_entry = Omitted.on_event.__listen_entries__[0]
        explicit_entry = Explicit.on_event.__listen_entries__[0]

        from varco_core.event.consumer import _UNSET

        # The load-bearing assertion: omission is the private sentinel,
        # distinguishable from an explicit `retry_policy=None`.
        assert omitted_entry.retry_policy is _UNSET
        assert explicit_entry.retry_policy is None
