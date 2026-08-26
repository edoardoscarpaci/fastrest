"""
Unit tests for varco_sa.audit — Plan 009, Phase 12 (R8) hash chaining.
=========================================================================
Covers ``SAAuditRepository(hash_chain=True)`` against an in-memory SQLite
database: chained saves verify, a manual UPDATE is detected, and 20
concurrent tasks produce a single unbroken chain.

RED until ``hash_chain=`` lands on ``SAAuditRepository``.
"""

from __future__ import annotations

import asyncio

import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from varco_core.service.audit import AuditEntry, AuditRepository
from varco_sa.audit import SAAuditRepository, audit_metadata


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(audit_metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def chained_repo(engine) -> SAAuditRepository:
    return SAAuditRepository(
        async_sessionmaker(engine, expire_on_commit=False), hash_chain=True
    )


def _entry(**kwargs) -> AuditEntry:
    defaults = dict(entity_type="Order", entity_id="1", action="create")
    defaults.update(kwargs)
    return AuditEntry(**defaults)


class TestChainedSavesVerify:
    async def test_sequential_saves_produce_a_verifiable_chain(
        self, chained_repo: SAAuditRepository
    ) -> None:
        for i in range(5):
            await chained_repo.save(_entry(entity_id=str(i)))

        entries = await chained_repo.list(limit=10)
        assert AuditRepository.verify_chain(entries) is True


class TestManualTamperDetected:
    async def test_manual_update_breaks_chain_verification(
        self, chained_repo: SAAuditRepository, engine
    ) -> None:
        await chained_repo.save(_entry())
        await chained_repo.save(_entry())

        # Directly corrupt one row's diff via raw SQL -- bypasses the
        # repository's own hash-chain bookkeeping entirely.
        async with engine.begin() as conn:
            await conn.execute(
                sa.text("UPDATE varco_audit_log SET diff = '{\"tampered\": true}'")
            )

        entries = await chained_repo.list(limit=10)
        assert AuditRepository.verify_chain(entries) is not True


class TestConcurrentSavesUnbrokenChain:
    async def test_twenty_concurrent_saves_produce_one_unbroken_chain(
        self, chained_repo: SAAuditRepository
    ) -> None:
        async def _save(i: int) -> None:
            await chained_repo.save(_entry(entity_id=str(i)))

        await asyncio.gather(*(_save(i) for i in range(20)))

        entries = await chained_repo.list(limit=100)
        assert len(entries) == 20
        assert AuditRepository.verify_chain(entries) is True
