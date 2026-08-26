"""
Real-Postgres conformance opt-in (Plan 012 / RT6, Step 27).

Consumes the session-scoped ``postgres_url`` fixture that Phase 1 (Step 7)
adds to ``varco_sa/tests/conftest.py``. Until that fixture exists, every
test class below errors at fixture-resolution time with
``fixture 'postgres_url' not found``.

Also depends on ``pythonpath = ["../testkit"]`` in
``varco_sa/pyproject.toml`` — until then every import below fails with
``ModuleNotFoundError: No module named 'varco_conformance'``.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from varco_conformance.dlq import DeadLetterQueueConformance
from varco_conformance.job_store import JobStoreConformance
from varco_sa.dlq import SADeadLetterQueue
from varco_sa.job_store import SAJobStore

pytestmark = pytest.mark.integration


class TestSAJobStoreConformance(JobStoreConformance):
    @pytest.fixture
    async def store(self, postgres_url: str):
        engine = create_async_engine(postgres_url)
        try:
            store = SAJobStore(engine)
            await store.ensure_table()
            yield store
        finally:
            await engine.dispose()


class TestSADeadLetterQueueConformance(DeadLetterQueueConformance):
    @pytest.fixture
    async def dlq(self, postgres_url: str):
        engine = create_async_engine(postgres_url)
        try:
            dlq = SADeadLetterQueue(engine)
            await dlq.ensure_table()
            yield dlq
        finally:
            await engine.dispose()
