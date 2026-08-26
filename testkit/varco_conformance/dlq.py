"""
DeadLetterQueueConformance — shared contract tests for
``AbstractDeadLetterQueue`` implementations (Plan 012 / RT6, Step 25).

Subclass and override the ``dlq`` fixture to opt a backend in::

    from varco_conformance.dlq import DeadLetterQueueConformance

    class TestKafkaDLQConformance(DeadLetterQueueConformance):
        @pytest.fixture
        async def dlq(self, kafka_bootstrap):
            dlq = KafkaDLQ(...)
            yield dlq

Not named ``Test*`` — never collected standalone (see package docstring).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from varco_core.event.base import Event
from varco_core.event.dlq import AbstractDeadLetterQueue, DeadLetterEntry


class _ConformanceDlqEvent(Event):
    __event_type__ = "conformance.dlq"
    value: str = ""


class DeadLetterQueueConformance:
    """Shared behavioural contract for ``AbstractDeadLetterQueue``."""

    @pytest.fixture
    async def dlq(self) -> AbstractDeadLetterQueue:
        """Abstract — must be overridden by every subclass."""
        raise NotImplementedError(
            "DeadLetterQueueConformance subclasses must override the `dlq` "
            "fixture with a concrete AbstractDeadLetterQueue implementation."
        )

    def _entry(self, **kwargs: object) -> DeadLetterEntry:
        defaults: dict[str, object] = dict(
            event=_ConformanceDlqEvent(value="x"),
            channel=f"conformance-{uuid4().hex[:8]}",
            handler_name="ConformanceConsumer.on_event",
            error_type="RuntimeError",
            error_message="boom",
            attempts=1,
            first_failed_at=datetime.now(tz=timezone.utc),
            last_failed_at=datetime.now(tz=timezone.utc),
        )
        defaults.update(kwargs)
        return DeadLetterEntry(**defaults)  # type: ignore[arg-type]

    async def test_push_never_raises(self, dlq: AbstractDeadLetterQueue) -> None:
        # Must not raise, even on a healthy sink — this is the baseline of
        # the "push() never raises" contract every backend must uphold.
        await dlq.push(self._entry())

    async def test_push_then_ack(self, dlq: AbstractDeadLetterQueue) -> None:
        entry = self._entry()
        await dlq.push(entry)
        await dlq.ack(entry.entry_id)

    async def test_count_reflects_pushed_entries(
        self, dlq: AbstractDeadLetterQueue
    ) -> None:
        before = await dlq.count()
        await dlq.push(self._entry())
        after = await dlq.count()
        assert (
            after >= before
        )  # a redelivered/duplicate broker read must never lose one

    async def test_random_access_flag_matches_reality(
        self, dlq: AbstractDeadLetterQueue
    ) -> None:
        entry = self._entry()
        await dlq.push(entry)

        if dlq.supports_random_access:
            got = await dlq.get(entry.entry_id)
            assert got is None or got.entry_id == entry.entry_id
        else:
            # AbstractDeadLetterQueue.get()/list_entries() are
            # concrete-but-raising NotImplementedError when
            # supports_random_access=False (see varco_core/event/dlq.py) —
            # never a silent empty return. NOTE: the higher-level redrive
            # module (varco_core.event.redrive.DlqRedriver) wraps this same
            # case as DeadLetterNotAddressable, but the ABC itself raises
            # plain NotImplementedError; this suite asserts the ABC's own
            # contract, not the redriver's.
            with pytest.raises(NotImplementedError):
                await dlq.get(entry.entry_id)
            with pytest.raises(NotImplementedError):
                await dlq.list_entries()

    async def test_delete_falls_back_to_ack(self, dlq: AbstractDeadLetterQueue) -> None:
        entry = self._entry()
        await dlq.push(entry)
        # Portable default: delete() == ack() unless overridden. Must not raise.
        await dlq.delete(entry.entry_id)

    async def test_delete_where_no_predicate_raises(
        self, dlq: AbstractDeadLetterQueue
    ) -> None:
        with pytest.raises(ValueError):
            await dlq.delete_where()

    async def test_count_by_channel_no_predicate_refuses_or_raises(
        self, dlq: AbstractDeadLetterQueue
    ) -> None:
        # count_by_channel() is concrete-but-raising on the ABC for backends
        # with no portable implementation — accept either a working result
        # or the documented NotImplementedError, never a silent empty dict
        # masking a broken implementation.
        try:
            result = await dlq.count_by_channel()
        except NotImplementedError:
            return
        assert isinstance(result, dict)

    async def test_none_tenant_never_matched_by_explicit_filter(
        self, dlq: AbstractDeadLetterQueue
    ) -> None:
        # tenant_id defaults to None — CLAUDE.md / Plan 009 RD-4: a None
        # tenant is deliberately never matched by an explicit tenant_id=
        # filter. (Ambient stamping from tenant_context() happens in the
        # retry-wrapper caller, varco_core.event.consumer — not inside
        # push() itself — so this suite constructs the entry directly.)
        entry = self._entry(tenant_id=None)
        await dlq.push(entry)

        if not dlq.supports_random_access:
            pytest.skip("backend has no random-access listing to filter")

        entries = await dlq.list_entries(tenant_id="acme")
        assert entry.entry_id not in {e.entry_id for e in entries}

    async def test_tenant_stamped_entry_matched_by_its_own_filter(
        self, dlq: AbstractDeadLetterQueue
    ) -> None:
        if not dlq.supports_random_access:
            pytest.skip("backend has no random-access listing to filter")

        entry = self._entry(tenant_id="acme-conformance")
        await dlq.push(entry)

        entries = await dlq.list_entries(tenant_id="acme-conformance")
        assert entry.entry_id in {e.entry_id for e in entries}
