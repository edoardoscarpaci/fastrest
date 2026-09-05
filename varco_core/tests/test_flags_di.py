"""
Unit tests for varco_core.flags.di (Plan 032 / D7).

Mirrors varco_casbin.di's enable_policy_authorizer precedent: NullFeatureFlags
is bound by default (nothing changes for an app that never opts in), and
enable_feature_flags(container) is the only way to swap in a real backend.
"""

from __future__ import annotations

from providify import DIContainer
from varco_core.flags import AbstractFeatureFlags, InMemoryFeatureFlags, NullFeatureFlags
from varco_core.flags.di import enable_feature_flags


async def test_null_feature_flags_bound_by_default() -> None:
    container = DIContainer()
    container.scan("varco_core.flags", recursive=True)
    flags = await container.aget(AbstractFeatureFlags)
    assert isinstance(flags, NullFeatureFlags)
    await container.ashutdown()


async def test_enable_feature_flags_swaps_the_default() -> None:
    container = DIContainer()
    container.scan("varco_core.flags", recursive=True)

    # Pre-opt-in: the no-op default resolves.
    before = await container.aget(AbstractFeatureFlags)
    assert isinstance(before, NullFeatureFlags)

    enable_feature_flags(container)
    after = await container.aget(AbstractFeatureFlags)
    assert isinstance(after, InMemoryFeatureFlags)
    await container.ashutdown()
