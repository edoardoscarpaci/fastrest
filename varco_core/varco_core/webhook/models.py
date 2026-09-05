"""
varco_core.webhook.models
==========================
``WebhookSubscription`` / ``WebhookDelivery`` — the entities behind outbound
webhooks (Plan 031 / D4a, Step 1, §D-D4-entity).

``WebhookSubscription`` is a ``DomainModel`` (same base every other entity in
this codebase uses) but its persistence path is a hand-rolled framework
table in ``varco_sa``/``varco_beanie`` (the same shape as
``varco_sa.idempotency``/``varco_sa.dlq``) rather than the generic
``@register`` + ``AsyncRepository[T]`` translation layer — a webhook
subscription is a small, framework-owned, cross-cutting resource (mirrors
every other framework table), not an application domain entity a service
layer CRUDs through a generic repository.

Thread safety:  ❌ Mutate from one task only — same as every ``DomainModel``.
Async safety:   ✅ Safe to pass across ``await`` boundaries once built.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from varco_core.meta import PKStrategy, PrimaryKey, pk_field
from varco_core.model import DomainModel

__all__ = ["WebhookSubscription", "WebhookDelivery"]


@dataclass(kw_only=True)
class WebhookSubscription(DomainModel):
    """
    An application's registration to receive signed HTTP callbacks for
    events matching ``event_patterns`` (§D-D4-entity).

    Attributes:
        tenant_id:             Owning tenant — a subscription must never fan
                                out across tenants (``TenantScope.TENANT``,
                                the default scope for this entity).
        target_url:            The HTTPS endpoint to deliver to. Validated
                                by ``varco_core.webhook.ssrf.validate_target``
                                at delivery time, not at construction time —
                                DNS can change between registration and
                                delivery, so the check must be per-attempt.
        event_patterns:        Glob-style patterns (``"order.*"``) matched
                                against an event's type name.
        active_secrets:        One or more HMAC secrets, newest last.
                                Multiple entries support zero-downtime
                                rotation (§D-D4-signing) — all active
                                secrets sign every delivery, and a receiver
                                accepts any. Stored **encrypted at rest** by
                                the SA/Beanie repositories via the existing
                                ``FieldEncryptor`` — this in-memory
                                dataclass field itself is plaintext, exactly
                                like every other ``DomainModel`` whose
                                repository applies encryption at the
                                persistence boundary (§D-D2/D-D4-signing:
                                "no new crypto path").
        status:                ``"ACTIVE"`` or ``"DISABLED"``. A disabled
                                subscription is skipped entirely by
                                ``WebhookDispatcher`` (§D-D4-delivery).
        consecutive_failures:  Count of consecutive delivery failures across
                                distinct events. Reset to ``0`` on any
                                successful delivery. Drives auto-disable.
        signer:                Which ``WebhookSigner`` implementation to use
                                — ``"standard_webhooks"`` (default) or
                                ``"rfc9421"`` (§D-D4-signing).
        custom_headers:        Extra static headers sent with every
                                delivery (e.g. a receiver-side routing key).
                                Never used to carry the signature itself.
        created_at/updated_at: Bookkeeping timestamps, UTC.

    Edge cases:
        - ``event_patterns=[]`` means the subscription matches nothing —
          not an error, just an inert registration.
        - ``active_secrets=[]`` means no signature can be produced;
          callers (the admin surface) should refuse to create such a
          subscription, but the entity itself does not enforce it — the
          same "storage is dumb, validation is the caller's job" split as
          every other ``DomainModel`` in this codebase.
    """

    pk: Annotated[UUID, PrimaryKey(PKStrategy.UUID_AUTO)] = pk_field()

    tenant_id: str
    target_url: str
    event_patterns: list[str] = field(default_factory=list)
    active_secrets: list[str] = field(default_factory=list)
    status: str = "ACTIVE"
    consecutive_failures: int = 0
    signer: str = "standard_webhooks"
    custom_headers: dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    class Meta:
        table = "webhook_subscriptions"


@dataclass(kw_only=True)
class WebhookDelivery(DomainModel):
    """
    A record of one delivery *attempt sequence* for one subscription/event
    pair (§D-D4-entity, open question 2 — "persist failures only" default).

    This is deliberately a lightweight, mostly-observability entity: the
    real failure-handling machinery is the existing DLQ
    (``AbstractDeadLetterQueue``), not this record. ``WebhookDelivery`` is
    what the admin surface lists/replays through
    (``varco_fastapi.webhook.mount_webhook_admin``); the DLQ entry (via
    ``DlqRedriver``) is what actually re-sends.

    Attributes:
        subscription_id: The ``WebhookSubscription.pk`` this delivery
                          belongs to.
        event_type:      The matched event type name (e.g. ``"order.created"``).
        webhook_id:      The stable ``webhook-id`` sent to the receiver —
                          the value a receiver should deduplicate on
                          (§D-D4-delivery's at-least-once guidance).
        status:          ``"delivered"`` / ``"failed"`` / ``"exhausted"``.
        attempts:        Total attempts made for this delivery.
        last_response_status: HTTP status of the final attempt, if any.
        created_at:      UTC timestamp of the first attempt.
    """

    pk: Annotated[UUID, PrimaryKey(PKStrategy.UUID_AUTO)] = pk_field()

    subscription_id: Any
    event_type: str
    webhook_id: str
    status: str = "failed"
    attempts: int = 0
    last_response_status: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    class Meta:
        table = "webhook_deliveries"
