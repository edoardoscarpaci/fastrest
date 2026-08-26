"""
tests.test_dlq_tenancy
========================
Plan 009, Phase 6 (R4) — tenant-scoped DLQ.

RED until ``DeadLetterEntry.tenant_id`` lands and the consumer's
``_make_retry_wrapper`` stamps it from the ambient
``varco_core.service.tenant.tenant_context()``.
"""

from __future__ import annotations

from varco_core.event import Event
from varco_core.event.consumer import EventConsumer, listen
from varco_core.event.dlq import DeadLetterEntry, InMemoryDeadLetterQueue
from varco_core.event.memory import InMemoryEventBus
from varco_core.resilience.retry import RetryPolicy
from varco_core.service.tenant import tenant_context


class SampleEvent(Event):
    __event_type__ = "test.dlq_tenancy.sample"


class TestDeadLetterEntryTenantIdField:
    def test_tenant_id_defaults_to_none(self) -> None:
        entry = DeadLetterEntry(
            event=SampleEvent(),
            channel="orders",
            handler_name="H.h",
            error_type="E",
            error_message="msg",
            attempts=1,
        )
        assert entry.tenant_id is None

    def test_tenant_id_can_be_set_explicitly(self) -> None:
        entry = DeadLetterEntry(
            event=SampleEvent(),
            channel="orders",
            handler_name="H.h",
            error_type="E",
            error_message="msg",
            attempts=1,
            tenant_id="acme",
        )
        assert entry.tenant_id == "acme"


class TestAmbientTenantStamping:
    async def test_entry_pushed_inside_tenant_context_carries_tenant_id(self) -> None:
        dlq = InMemoryDeadLetterQueue()

        class FailingConsumer(EventConsumer):
            @listen(
                SampleEvent,
                channel="ch",
                retry_policy=RetryPolicy(max_attempts=1),
                dlq=dlq,
            )
            async def on_event(self, event: SampleEvent) -> None:
                raise RuntimeError("boom")

        bus = InMemoryEventBus()
        FailingConsumer().register_to(bus)

        with tenant_context("acme"):
            await bus.publish(SampleEvent(), channel="ch")
            await bus.drain()

        assert await dlq.count() == 1
        [entry] = await dlq.pop_batch(limit=10)
        assert entry.tenant_id == "acme"

    async def test_entry_pushed_outside_tenant_context_has_none_tenant(self) -> None:
        dlq = InMemoryDeadLetterQueue()

        class FailingConsumer(EventConsumer):
            @listen(
                SampleEvent,
                channel="ch",
                retry_policy=RetryPolicy(max_attempts=1),
                dlq=dlq,
            )
            async def on_event(self, event: SampleEvent) -> None:
                raise RuntimeError("boom")

        bus = InMemoryEventBus()
        FailingConsumer().register_to(bus)

        await bus.publish(SampleEvent(), channel="ch")
        await bus.drain()

        [entry] = await dlq.pop_batch(limit=10)
        assert entry.tenant_id is None


class _TenantFilterableDLQ(InMemoryDeadLetterQueue):
    """Test double reproducing the documented tenant_id filtering contract
    (the None-tenant asymmetry, RD/edge-case table) for list_entries/
    delete_where."""

    async def list_entries(  # type: ignore[override]
        self,
        *,
        limit=50,
        offset=0,
        channel=None,
        source=None,
        tenant_id=None,
        older_than=None,
        newer_than=None,
    ):
        results = list(self._entries)
        if tenant_id is not None:
            results = [e for e in results if e.tenant_id == tenant_id]
        return results[offset : offset + limit]

    async def delete_where(  # type: ignore[override]
        self, *, older_than=None, source=None, channel=None, tenant_id=None, limit=None
    ) -> int:
        if (
            older_than is None
            and source is None
            and channel is None
            and tenant_id is None
        ):
            raise ValueError("delete_where() requires at least one predicate.")
        matching = [
            e for e in self._entries if tenant_id is None or e.tenant_id == tenant_id
        ]
        if limit is not None:
            matching = matching[:limit]
        for e in matching:
            self._entries.remove(e)
        return len(matching)


def _entry(tenant_id: str | None) -> DeadLetterEntry:
    return DeadLetterEntry(
        event=SampleEvent(),
        channel="orders",
        handler_name="H.h",
        error_type="E",
        error_message="msg",
        attempts=1,
        tenant_id=tenant_id,
    )


class TestListEntriesTenantScoping:
    async def test_tenant_scoped_list_excludes_none_tenant_entry(self) -> None:
        """A None-tenant entry is NOT "every tenant" -- documented asymmetry."""
        dlq = _TenantFilterableDLQ()
        await dlq.push(_entry("acme"))
        await dlq.push(_entry(None))

        results = await dlq.list_entries(tenant_id="acme")
        assert len(results) == 1
        assert results[0].tenant_id == "acme"

    async def test_no_tenant_filter_returns_every_entry(self) -> None:
        dlq = _TenantFilterableDLQ()
        await dlq.push(_entry("acme"))
        await dlq.push(_entry(None))

        results = await dlq.list_entries()
        assert len(results) == 2


class TestDeleteWhereTenantScoping:
    async def test_delete_where_tenant_id_scopes_correctly(self) -> None:
        dlq = _TenantFilterableDLQ()
        await dlq.push(_entry("acme"))
        await dlq.push(_entry("other"))

        deleted = await dlq.delete_where(tenant_id="acme")
        assert deleted == 1
        remaining = await dlq.list_entries()
        assert len(remaining) == 1
        assert remaining[0].tenant_id == "other"
