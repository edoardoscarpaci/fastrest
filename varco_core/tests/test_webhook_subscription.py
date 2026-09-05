"""
Plan 031 (D4a) / Step 4 — red-mode tests for ``varco_core.webhook``.

Nothing under ``varco_core/varco_core/webhook/`` exists yet — every test here is
expected to fail with ``ModuleNotFoundError`` on the first import line.

Covers:
  - ``WebhookSubscription`` / ``WebhookDelivery`` entities (models.py)
  - ``WebhookSubscriptionRepository`` ABC (base.py)
  - ``WebhookSettings`` (settings.py, ``VARCO_WEBHOOK_`` prefix)
  - basic CRUD against an in-memory repository implementation supplied by a fixture
"""

from __future__ import annotations

import pytest


def test_webhook_subscription_module_importable() -> None:
    # Pins the expected module location per §D-D4-home before anything else works.
    from varco_core.webhook.models import WebhookSubscription  # noqa: F401


def test_webhook_delivery_module_importable() -> None:
    from varco_core.webhook.models import WebhookDelivery  # noqa: F401


def test_webhook_subscription_repository_abc_importable() -> None:
    from varco_core.webhook.base import WebhookSubscriptionRepository  # noqa: F401


def test_webhook_settings_importable_and_env_prefixed() -> None:
    from varco_core.webhook.settings import WebhookSettings

    settings = WebhookSettings()
    # Pydantic BaseSettings model_config carries the documented env prefix.
    assert settings.model_config.get("env_prefix") == "VARCO_WEBHOOK_"


def test_webhook_subscription_is_a_domain_model() -> None:
    from varco_core.model import DomainModel
    from varco_core.webhook.models import WebhookSubscription

    assert issubclass(WebhookSubscription, DomainModel)


def test_webhook_subscription_has_expected_fields() -> None:
    from varco_core.webhook.models import WebhookSubscription

    sub = WebhookSubscription(
        tenant_id="tenant-1",
        target_url="https://example.com/hook",
        event_patterns=["order.created"],
        active_secrets=["whsec_abc"],
        status="ACTIVE",
        consecutive_failures=0,
        signer="standard_webhooks",
        custom_headers={},
    )
    assert sub.tenant_id == "tenant-1"
    assert sub.target_url == "https://example.com/hook"
    assert sub.event_patterns == ["order.created"]
    assert sub.status == "ACTIVE"


class TestInMemoryRepositoryContract:
    """
    Basic CRUD contract against whatever in-memory implementation ships
    alongside the ABC — mirrors the "unit tests with an in-memory repository"
    requirement from Step 4. Fails today because neither the ABC nor any
    in-memory implementation exists.
    """

    @pytest.fixture
    def repo(self):
        from varco_core.webhook.base import InMemoryWebhookSubscriptionRepository

        return InMemoryWebhookSubscriptionRepository()

    async def test_save_then_find_by_id_round_trips(self, repo) -> None:
        from varco_core.webhook.models import WebhookSubscription

        sub = WebhookSubscription(
            tenant_id="tenant-1",
            target_url="https://example.com/hook",
            event_patterns=["order.*"],
            active_secrets=["whsec_abc"],
            status="ACTIVE",
            consecutive_failures=0,
            signer="standard_webhooks",
            custom_headers={},
        )
        saved = await repo.save(sub)
        found = await repo.find_by_id(saved.pk)
        assert found is not None
        assert found.target_url == "https://example.com/hook"

    async def test_find_by_tenant_never_returns_another_tenants_subscriptions(self, repo) -> None:
        from varco_core.webhook.models import WebhookSubscription

        mine = WebhookSubscription(
            tenant_id="tenant-1",
            target_url="https://example.com/hook",
            event_patterns=["order.*"],
            active_secrets=["whsec_abc"],
            status="ACTIVE",
            consecutive_failures=0,
            signer="standard_webhooks",
            custom_headers={},
        )
        theirs = WebhookSubscription(
            tenant_id="tenant-2",
            target_url="https://evil.example.com/hook",
            event_patterns=["order.*"],
            active_secrets=["whsec_def"],
            status="ACTIVE",
            consecutive_failures=0,
            signer="standard_webhooks",
            custom_headers={},
        )
        await repo.save(mine)
        await repo.save(theirs)

        results = await repo.find_by_tenant("tenant-1")
        assert all(r.tenant_id == "tenant-1" for r in results)
