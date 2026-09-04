"""
Real-MongoDB conformance opt-in for ``BeanieIdempotencyStore``
(Plan 029 / D1, Step 15).

Depends on ``pythonpath = ["../testkit"]`` and the session-scoped
``mongo_url`` fixture (CLAUDE.md's conformance-suite convention).
"""

from __future__ import annotations

import uuid

import pytest
from varco_beanie.idempotency import BeanieIdempotencyStore, IdempotencyDocument
from varco_conformance.idempotency_store import IdempotencyStoreConformance

pytestmark = pytest.mark.integration


class TestBeanieIdempotencyStoreConformance(IdempotencyStoreConformance):
    @pytest.fixture
    async def store(self, mongo_url: str):
        from beanie import init_beanie
        from pymongo import AsyncMongoClient

        db_name = f"test_idempotency_conformance_{uuid.uuid4().hex[:8]}"
        client = AsyncMongoClient(mongo_url)
        db = client[db_name]
        await init_beanie(database=db, document_models=[IdempotencyDocument])
        try:
            yield BeanieIdempotencyStore()
        finally:
            await client.drop_database(db_name)
            await client.close()
