"""
tests.test_cache_jitter
=========================
Plan 010 Phase 4, step 38 — ``CachePolicy.effective_ttl()`` symmetric
fractional jitter.

RED until ``varco_core/cache/policy.py`` gains ``effective_ttl()``.
"""

from __future__ import annotations

import random

from varco_core.cache.policy import CachePolicy


class TestTtlJitter:
    async def test_jittered_ttls_lie_within_bounds_and_are_not_all_equal(self) -> None:
        policy = CachePolicy(ttl=100.0, ttl_jitter=0.2)
        rng = random.Random(1234)

        ttls = [policy.effective_ttl(rng=rng) for _ in range(1000)]

        assert all(80.0 <= t <= 120.0 for t in ttls)
        assert len(set(ttls)) > 1  # not a synchronized expiry cliff

    async def test_zero_jitter_returns_exact_ttl_deterministically(self) -> None:
        policy = CachePolicy(ttl=100.0, ttl_jitter=0.0)
        rng = random.Random(1)

        for _ in range(20):
            assert policy.effective_ttl(rng=rng) == 100.0

    async def test_none_ttl_effective_ttl_is_none(self) -> None:
        policy = CachePolicy(ttl=None)
        assert policy.effective_ttl() is None
