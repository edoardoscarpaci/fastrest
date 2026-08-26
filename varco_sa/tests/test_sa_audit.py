"""
Unit tests for varco_sa.audit — Plan 009, Phase 2 (R3) / Phase 6 (R4) /
Phase 10 (R6) additions.
====================================================================
Covers ``SAAuditRepository.delete_where``, ``list()``, and
``list_for_entity(..., tenant_id=)`` against an in-memory SQLite database.

RED until these methods land on ``SAAuditRepository``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from varco_core.service.audit import AuditEntry
from varco_sa.audit import SAAuditRepository, audit_metadata


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(audit_metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def repo(engine) -> SAAuditRepository:
    return SAAuditRepository(async_sessionmaker(engine, expire_on_commit=False))


def _entry(**kwargs) -> AuditEntry:
    defaults = dict(entity_type="Order", entity_id="1", action="create")
    defaults.update(kwargs)
    return AuditEntry(**defaults)


class TestSAAuditRepositoryDeleteWhere:
    async def test_delete_where_no_predicate_raises_value_error(
        self, repo: SAAuditRepository
    ) -> None:
        with pytest.raises(ValueError):
            await repo.delete_where()

    async def test_delete_where_older_than_sweeps_matching_rows(
        self, repo: SAAuditRepository
    ) -> None:
        for i in range(5):
            await repo.save(_entry(entity_id=str(i)))

        cutoff = datetime.now(UTC) + timedelta(seconds=1)
        total = 0
        while True:
            deleted = await repo.delete_where(older_than=cutoff, limit=2)
            total += deleted
            if deleted == 0:
                break
        assert total == 5


class TestSAAuditRepositoryList:
    async def test_list_filters_by_entity_type(self, repo: SAAuditRepository) -> None:
        await repo.save(_entry(entity_type="Order"))
        await repo.save(_entry(entity_type="Invoice"))

        results = await repo.list(entity_type="Order")
        assert all(e.entity_type == "Order" for e in results)
        assert len(results) == 1


class TestSAAuditRepositoryTenantFilter:
    async def test_list_for_entity_tenant_id_filters(
        self, repo: SAAuditRepository
    ) -> None:
        await repo.save(_entry(tenant_id="acme"))
        await repo.save(_entry(tenant_id="other"))

        results = await repo.list_for_entity("Order", "1", tenant_id="acme")
        assert len(results) == 1
        assert results[0].tenant_id == "acme"
