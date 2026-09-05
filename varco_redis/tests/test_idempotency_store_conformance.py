"""
Real-Redis conformance opt-in for ``RedisIdempotencyStore``
(Plan 029 / D1, Step 15).

Depends on ``pythonpath = ["../testkit"]`` and the session-scoped
``redis_url`` fixture (CLAUDE.md's conformance-suite convention).
"""

from __future__ import annotations

import pytest
from varco_conformance.idempotency_store import IdempotencyStoreConformance
from varco_redis.idempotency import RedisIdempotencyStore

pytestmark = pytest.mark.integration


class TestRedisIdempotencyStoreConformance(IdempotencyStoreConformance):
    @pytest.fixture
    async def store(self, redis_url: str) -> RedisIdempotencyStore:
        return RedisIdempotencyStore(url=redis_url)
