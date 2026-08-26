"""
Real-MongoDB conformance opt-in (Plan 012 / RT6, Step 27).

Consumes the session-scoped ``mongo_url`` fixture that Phase 1 (Step 7)
adds to ``varco_beanie/tests/conftest.py``. Until that fixture exists,
every test class below errors at fixture-resolution time with
``fixture 'mongo_url' not found``.

Also depends on ``pythonpath = ["../testkit"]`` in
``varco_beanie/pyproject.toml`` — until then every import below fails with
``ModuleNotFoundError: No module named 'varco_conformance'``.

Note: Beanie Documents bind to the process-global registry via
``init_beanie()`` — per Step 8's isolation rule, this fixture uses a
per-test, uniquely-named database on the shared session container so
concurrent conformance runs never collide.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient
from varco_beanie.dlq import BeanieDeadLetterQueue, DeadLetterDocument
from varco_beanie.job_store import BeanieJobStore, JobDocument
from varco_conformance.dlq import DeadLetterQueueConformance
from varco_conformance.job_store import JobStoreConformance

pytestmark = pytest.mark.integration


class TestBeanieJobStoreConformance(JobStoreConformance):
    @pytest.fixture
    async def store(self, mongo_url: str):
        client = AsyncIOMotorClient(mongo_url)
        db_name = f"conformance_{uuid4().hex[:8]}"
        await init_beanie(database=client[db_name], document_models=[JobDocument])
        try:
            yield BeanieJobStore()
        finally:
            await client.drop_database(db_name)
            client.close()


class TestBeanieDeadLetterQueueConformance(DeadLetterQueueConformance):
    @pytest.fixture
    async def dlq(self, mongo_url: str):
        client = AsyncIOMotorClient(mongo_url)
        db_name = f"conformance_{uuid4().hex[:8]}"
        await init_beanie(database=client[db_name], document_models=[DeadLetterDocument])
        try:
            yield BeanieDeadLetterQueue()
        finally:
            await client.drop_database(db_name)
            client.close()
