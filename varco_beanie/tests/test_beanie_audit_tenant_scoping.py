"""
Tests for BACKLOG KI-9 — ``BeanieAuditRepository.list_for_entity`` must honour ``tenant_id``.

``varco_core.service.audit.AuditRepository.list_for_entity`` (the ABC) declares a
keyword-only ``tenant_id: str | None = None`` parameter, documented as a
**breaking** addition whose entire purpose is that an out-of-tree subclass that
does not accept it fails LOUDLY (``TypeError``) rather than silently ignoring
the tenant filter. ``BeanieAuditRepository`` is an in-tree subclass that today
does exactly the thing the ABC's docstring calls a security bug: it has no
``tenant_id`` parameter at all, and its Beanie query filters only on
``(entity_type, entity_id)``.

RED until Plan 020 Step 18 adds the parameter and the filter.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime

import pytest
from varco_core.service.audit import AuditEntry, AuditRepository


def _entry(**kwargs) -> AuditEntry:
    defaults: dict = dict(
        entity_type="Order",
        entity_id="1",
        action="create",
        occurred_at=datetime.now(UTC),
    )
    defaults.update(kwargs)
    return AuditEntry(**defaults)


class TestBeanieAuditRepositorySignatureMatchesTheABC:
    """Docker-free: signature conformance is the fast, always-run half of KI-9's coverage."""

    def test_list_for_entity_exposes_tenant_id_as_keyword_only_with_default_none(self) -> None:
        from varco_beanie.audit import BeanieAuditRepository

        sig = inspect.signature(BeanieAuditRepository.list_for_entity)
        assert "tenant_id" in sig.parameters, (
            "BeanieAuditRepository.list_for_entity has no tenant_id parameter (KI-9)"
        )
        tenant_param = sig.parameters["tenant_id"]
        assert tenant_param.kind is inspect.Parameter.KEYWORD_ONLY
        assert tenant_param.default is None

    def test_list_for_entity_parameter_names_and_kinds_match_the_abc(self) -> None:
        from varco_beanie.audit import BeanieAuditRepository

        abc_sig = inspect.signature(AuditRepository.list_for_entity)
        impl_sig = inspect.signature(BeanieAuditRepository.list_for_entity)

        abc_params = {name: p.kind for name, p in abc_sig.parameters.items() if name != "self"}
        impl_params = {name: p.kind for name, p in impl_sig.parameters.items() if name != "self"}
        assert impl_params == abc_params


@pytest.mark.integration
class TestBeanieAuditRepositoryTenantFilterIntegration:
    """Real-Mongo: the filter must actually scope the query, not just accept the kwarg."""

    @pytest.fixture
    async def audit_repo(self, mongo_url: str):
        from beanie import init_beanie
        from pymongo import AsyncMongoClient
        from varco_beanie.audit import AuditDocument, BeanieAuditRepository

        db_name = f"test_beanie_audit_tenant_{uuid.uuid4().hex[:8]}"
        client = AsyncMongoClient(mongo_url)
        db = client[db_name]
        await init_beanie(database=db, document_models=[AuditDocument])
        try:
            yield BeanieAuditRepository()
        finally:
            await client.drop_database(db_name)
            client.close()

    async def test_list_for_entity_filters_by_tenant_id(self, audit_repo) -> None:
        # Per-test namespacing (CLAUDE.md §shared containers): unique entity_type
        # so this test never collides with another test's rows on the shared
        # session-scoped container.
        entity_type = f"Order-{uuid.uuid4().hex[:8]}"

        await audit_repo.save(_entry(entity_type=entity_type, entity_id="1", tenant_id="t-a"))
        await audit_repo.save(_entry(entity_type=entity_type, entity_id="1", tenant_id="t-a"))
        await audit_repo.save(_entry(entity_type=entity_type, entity_id="1", tenant_id="t-b"))

        tenant_a = await audit_repo.list_for_entity(entity_type, "1", tenant_id="t-a")
        tenant_b = await audit_repo.list_for_entity(entity_type, "1", tenant_id="t-b")
        unscoped = await audit_repo.list_for_entity(entity_type, "1")

        assert len(tenant_a) == 2
        assert all(e.tenant_id == "t-a" for e in tenant_a)
        assert len(tenant_b) == 1
        assert tenant_b[0].tenant_id == "t-b"
        assert len(unscoped) == 3
