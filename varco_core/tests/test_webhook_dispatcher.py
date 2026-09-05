"""
Plan 031 (D4c) / Step 15 — red-mode tests for ``WebhookDispatcher``.

``varco_core/varco_core/webhook/dispatcher.py`` does not exist yet — every
test fails with ``ModuleNotFoundError`` on import.

Covers: successful delivery, 5xx retried, timeout retried, exhaustion lands
in the DLQ, auto-disable after N consecutive failures, a disabled
subscription is skipped.
"""

from __future__ import annotations

import pytest
from varco_core.event import InMemoryEventBus
from varco_core.event.dlq import InMemoryDeadLetterQueue
from varco_core.webhook.dispatcher import WebhookTriggerEvent

# NOTE: AbstractEventBus.publish() takes an Event instance, not a
# (type_name, payload) pair — every publish() call below constructs a
# WebhookTriggerEvent explicitly (mechanical fix; this file originally
# called `bus.publish("order.created", {...})`, which does not match
# publish()'s real signature: `channel` is keyword-only).


def _subscription(status: str = "ACTIVE", consecutive_failures: int = 0):
    from varco_core.webhook.models import WebhookSubscription

    return WebhookSubscription(
        tenant_id="tenant-1",
        target_url="https://example.com/hook",
        event_patterns=["order.*"],
        active_secrets=["whsec_abc"],
        status=status,
        consecutive_failures=consecutive_failures,
        signer="standard_webhooks",
        custom_headers={},
    )


@pytest.fixture
def bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
def dlq() -> InMemoryDeadLetterQueue:
    return InMemoryDeadLetterQueue()


@pytest.fixture
def repo():
    from varco_core.webhook.base import InMemoryWebhookSubscriptionRepository

    return InMemoryWebhookSubscriptionRepository()


async def test_dispatcher_is_an_event_consumer_never_holds_bus_directly() -> None:
    from varco_core.event.consumer import EventConsumer
    from varco_core.webhook.dispatcher import WebhookDispatcher

    assert issubclass(WebhookDispatcher, EventConsumer)


async def test_successful_delivery_sends_signed_request(
    bus: InMemoryEventBus, dlq: InMemoryDeadLetterQueue, repo, monkeypatch
) -> None:
    from varco_core.webhook.dispatcher import WebhookDispatcher

    sent = {}

    async def _fake_send(self, url, *, headers, body, timeout):
        sent["url"] = url
        sent["headers"] = headers

        class _Resp:
            status_code = 200

        return _Resp()

    monkeypatch.setattr(WebhookDispatcher, "_send", _fake_send, raising=False)

    sub = await repo.save(_subscription())
    dispatcher = WebhookDispatcher(repository=repo)
    dispatcher.register_to(bus, dlq=dlq)

    await bus.publish(
        WebhookTriggerEvent(
            matched_event_type="order.created",
            payload={"tenant_id": "tenant-1", "order_id": "o1"},
        )
    )
    await bus.drain()

    assert sent.get("url") == sub.target_url


async def test_5xx_response_is_retried(
    bus: InMemoryEventBus, dlq: InMemoryDeadLetterQueue, repo, monkeypatch
) -> None:
    from varco_core.webhook.dispatcher import WebhookDispatcher

    attempts = {"count": 0}

    async def _fake_send(self, url, *, headers, body, timeout):
        attempts["count"] += 1

        class _Resp:
            status_code = 500 if attempts["count"] < 2 else 200

        return _Resp()

    monkeypatch.setattr(WebhookDispatcher, "_send", _fake_send, raising=False)

    await repo.save(_subscription())
    dispatcher = WebhookDispatcher(repository=repo)
    dispatcher.register_to(bus, dlq=dlq)

    await bus.publish(
        WebhookTriggerEvent(
            matched_event_type="order.created",
            payload={"tenant_id": "tenant-1", "order_id": "o1"},
        )
    )
    await bus.drain()

    assert attempts["count"] >= 2


async def test_timeout_is_retried(
    bus: InMemoryEventBus, dlq: InMemoryDeadLetterQueue, repo, monkeypatch
) -> None:
    from varco_core.webhook.dispatcher import WebhookDispatcher

    attempts = {"count": 0}

    async def _fake_send(self, url, *, headers, body, timeout):
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise TimeoutError("simulated timeout")

        class _Resp:
            status_code = 200

        return _Resp()

    monkeypatch.setattr(WebhookDispatcher, "_send", _fake_send, raising=False)

    await repo.save(_subscription())
    dispatcher = WebhookDispatcher(repository=repo)
    dispatcher.register_to(bus, dlq=dlq)

    await bus.publish(
        WebhookTriggerEvent(
            matched_event_type="order.created",
            payload={"tenant_id": "tenant-1", "order_id": "o1"},
        )
    )
    await bus.drain()

    assert attempts["count"] >= 2


async def test_exhaustion_lands_in_dlq_never_raises(
    bus: InMemoryEventBus, dlq: InMemoryDeadLetterQueue, repo, monkeypatch
) -> None:
    from varco_core.webhook.dispatcher import WebhookDispatcher

    async def _always_fail(self, url, *, headers, body, timeout):
        class _Resp:
            status_code = 500

        return _Resp()

    monkeypatch.setattr(WebhookDispatcher, "_send", _always_fail, raising=False)

    await repo.save(_subscription())
    dispatcher = WebhookDispatcher(repository=repo)
    dispatcher.register_to(bus, dlq=dlq)

    await bus.publish(
        WebhookTriggerEvent(
            matched_event_type="order.created",
            payload={"tenant_id": "tenant-1", "order_id": "o1"},
        )
    )
    await bus.drain()

    entries = await dlq.pop_batch(limit=10)
    assert len(entries) == 1


async def test_subscription_auto_disabled_after_n_consecutive_failures(
    bus: InMemoryEventBus, dlq: InMemoryDeadLetterQueue, repo, monkeypatch
) -> None:
    from varco_core.webhook.dispatcher import WebhookDispatcher

    async def _always_fail(self, url, *, headers, body, timeout):
        class _Resp:
            status_code = 500

        return _Resp()

    monkeypatch.setattr(WebhookDispatcher, "_send", _always_fail, raising=False)

    sub = await repo.save(_subscription())
    dispatcher = WebhookDispatcher(repository=repo, disable_after_failures=1)
    dispatcher.register_to(bus, dlq=dlq)

    await bus.publish(
        WebhookTriggerEvent(
            matched_event_type="order.created",
            payload={"tenant_id": "tenant-1", "order_id": "o1"},
        )
    )
    await bus.drain()

    reloaded = await repo.find_by_id(sub.pk)
    assert reloaded.status == "DISABLED"


async def test_disabled_subscription_is_skipped_entirely(
    bus: InMemoryEventBus, dlq: InMemoryDeadLetterQueue, repo, monkeypatch
) -> None:
    from varco_core.webhook.dispatcher import WebhookDispatcher

    called = {"count": 0}

    async def _fake_send(self, url, *, headers, body, timeout):
        called["count"] += 1

        class _Resp:
            status_code = 200

        return _Resp()

    monkeypatch.setattr(WebhookDispatcher, "_send", _fake_send, raising=False)

    await repo.save(_subscription(status="DISABLED"))
    dispatcher = WebhookDispatcher(repository=repo)
    dispatcher.register_to(bus, dlq=dlq)

    await bus.publish(
        WebhookTriggerEvent(
            matched_event_type="order.created",
            payload={"tenant_id": "tenant-1", "order_id": "o1"},
        )
    )
    await bus.drain()

    assert called["count"] == 0
