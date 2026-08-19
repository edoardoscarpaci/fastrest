"""
tests.test_cache_policy
=========================
Plan 010 Phase 0, step 4 — ``varco_core.cache.policy.CachePolicy``.

RED until ``varco_core/cache/policy.py`` lands.
"""

from __future__ import annotations

import dataclasses

import pytest


class TestCachePolicyDefaults:
    async def test_default_policy_requires_envelope_is_false(self) -> None:
        from varco_core.cache.policy import CachePolicy

        # D-4/D-5: the identity policy must not require the envelope wire
        # format — that is what keeps the default byte-identical to today.
        assert CachePolicy().requires_envelope is False

    async def test_default_policy_is_frozen(self) -> None:
        from varco_core.cache.policy import CachePolicy

        policy = CachePolicy()
        with pytest.raises(dataclasses.FrozenInstanceError):
            policy.ttl = 5.0  # type: ignore[misc]


class TestCachePolicyValidation:
    async def test_ttl_jitter_below_zero_raises(self) -> None:
        from varco_core.cache.policy import CachePolicy

        with pytest.raises(ValueError, match="ttl_jitter"):
            CachePolicy(ttl_jitter=-0.1)

    async def test_ttl_jitter_at_one_raises(self) -> None:
        from varco_core.cache.policy import CachePolicy

        # Half-open interval [0.0, 1.0) — 1.0 itself is invalid.
        with pytest.raises(ValueError, match="ttl_jitter"):
            CachePolicy(ttl_jitter=1.0)

    async def test_ttl_jitter_zero_is_valid(self) -> None:
        from varco_core.cache.policy import CachePolicy

        CachePolicy(ttl_jitter=0.0)

    async def test_soft_ttl_greater_equal_ttl_raises(self) -> None:
        from varco_core.cache.policy import CachePolicy

        # A soft TTL at or beyond the hard TTL can never fire.
        with pytest.raises(ValueError, match="soft_ttl"):
            CachePolicy(ttl=60.0, soft_ttl=60.0)

    async def test_soft_ttl_less_than_ttl_is_valid(self) -> None:
        from varco_core.cache.policy import CachePolicy

        CachePolicy(ttl=60.0, soft_ttl=30.0)

    async def test_stale_if_error_without_ttl_raises(self) -> None:
        from varco_core.cache.policy import CachePolicy

        with pytest.raises(ValueError, match="stale_if_error"):
            CachePolicy(stale_if_error=30.0)

    async def test_requires_envelope_true_when_soft_ttl_set(self) -> None:
        from varco_core.cache.policy import CachePolicy

        assert CachePolicy(ttl=60.0, soft_ttl=30.0).requires_envelope is True

    async def test_requires_envelope_true_when_negative_ttl_set(self) -> None:
        from varco_core.cache.policy import CachePolicy

        assert CachePolicy(negative_ttl=30.0).requires_envelope is True

    async def test_requires_envelope_true_when_stale_if_error_set(self) -> None:
        from varco_core.cache.policy import CachePolicy

        assert CachePolicy(ttl=60.0, stale_if_error=30.0).requires_envelope is True
