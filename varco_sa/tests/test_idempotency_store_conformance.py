"""
Real-Postgres conformance opt-in for ``SAIdempotencyStore``
(Plan 029 / D1, Step 15).

Depends on ``pythonpath = ["../testkit"]`` and the session-scoped
``postgres_url`` fixture (CLAUDE.md's conformance-suite convention).
"""

from __future__ import annotations

import pytest
from varco_conformance.idempotency_store import IdempotencyStoreConformance
from varco_sa.idempotency import SAIdempotencyStore

pytestmark = pytest.mark.integration


class TestSAIdempotencyStoreConformance(IdempotencyStoreConformance):
    @pytest.fixture
    async def store(self, postgres_url: str):
        store = SAIdempotencyStore(url=postgres_url)
        await store.start()
        yield store
        await store.stop()
