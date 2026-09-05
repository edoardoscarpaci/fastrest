"""
Fast, no-Docker conformance run for ``InMemoryIdempotencyStore``
(Plan 029 / D1, Step 15).

Deliberately unmarked — no ``@pytest.mark.integration``. Depends on the
same ``pythonpath = ["../testkit"]`` line already used by
``test_conformance_inmemory.py``; until ``varco_core.idempotency`` exists
this fails with ``ModuleNotFoundError``, which is the RED state this file
is meant to produce.
"""

from __future__ import annotations

import pytest
from varco_conformance.idempotency_store import IdempotencyStoreConformance
from varco_core.idempotency.memory import InMemoryIdempotencyStore


class TestInMemoryIdempotencyStoreConformance(IdempotencyStoreConformance):
    @pytest.fixture
    async def store(self) -> InMemoryIdempotencyStore:
        return InMemoryIdempotencyStore()
