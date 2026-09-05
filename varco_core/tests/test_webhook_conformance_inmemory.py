"""
Fast, no-Docker conformance run for
``InMemoryWebhookSubscriptionRepository`` (Plan 031 / D4, Step 22).

Deliberately unmarked — no ``@pytest.mark.integration``. Same
``pythonpath = ["../testkit"]`` line already used by
``test_conformance_inmemory.py``/``test_idempotency_conformance_inmemory.py``.
"""

from __future__ import annotations

import pytest
from varco_conformance.webhook_subscription import WebhookSubscriptionRepositoryConformance
from varco_core.webhook.base import InMemoryWebhookSubscriptionRepository


class TestInMemoryWebhookSubscriptionRepositoryConformance(
    WebhookSubscriptionRepositoryConformance
):
    @pytest.fixture
    async def repo(self) -> InMemoryWebhookSubscriptionRepository:
        return InMemoryWebhookSubscriptionRepository()
