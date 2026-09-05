"""
varco_core.webhook.metrics
============================
``install_webhook_metrics()`` — a process-global side effect registering
counters for webhook delivery (Plan 031 / D4c, Step 16).

CLAUDE.md's `install_*` taxonomy shape (a): no container, no return value,
called once at process startup. Modelled on
``varco_core.observability.cache.install_cache_metrics``/
``varco_core.observability.reliability.install_reliability_metrics``, scaled
down — webhooks need simple counters, not the DLQ pack's observable-gauge
event-loop plumbing.

Thread safety:  ✅ ``Metric`` instances are safe to share/call concurrently.
Async safety:   ✅ Recording helpers are synchronous.
"""

from __future__ import annotations

from varco_core.observability.metric import Metric

__all__ = ["install_webhook_metrics", "record_delivery_attempt", "record_delivery_outcome"]

_attempts = Metric(
    "webhook.delivery_attempts",
    kind="counter",
    description="Outbound webhook delivery attempts",
)
_outcomes = Metric(
    "webhook.delivery_outcomes",
    kind="counter",
    description="Outbound webhook delivery outcomes (delivered/failed/exhausted)",
)

_installed = False


def install_webhook_metrics() -> None:
    """
    Register the webhook delivery metric instruments.

    Idempotent — calling more than once is a no-op after the first call
    (``Metric`` instrument creation is itself lazy/idempotent, but this
    guard keeps the intent explicit and matches
    ``install_reliability_metrics``'s own idempotency note).
    """
    global _installed
    _installed = True


def record_delivery_attempt(*, subscription_id: str) -> None:
    """Record one delivery attempt (call once per HTTP send, not per event)."""
    _attempts.add(subscription=subscription_id)


def record_delivery_outcome(*, subscription_id: str, outcome: str) -> None:
    """Record a terminal delivery outcome — ``outcome`` is one of
    ``"delivered"``/``"failed"``/``"exhausted"``."""
    _outcomes.add(subscription=subscription_id, outcome=outcome)
