"""
Unit tests for varco_core.flags (Plan 032 / D7 — feature-flag seam).

Covers: AbstractFeatureFlags's four typed resolutions (bool/string/numeric/
object), default-on-unknown-flag, tenant-scoped overrides via
FlagEvaluationContext (sourced from current_tenant(), never RequestContext —
CLAUDE.md's rule), and NullFeatureFlags's always-return-the-default contract.
"""

from __future__ import annotations

import pytest
from varco_core.service.tenant import tenant_context


def _import_flags():
    # Centralized import so every test fails the same way (ModuleNotFoundError)
    # until varco_core.flags exists, rather than each test failing differently.
    from varco_core.flags import (  # noqa: PLC0415
        AbstractFeatureFlags,
        FlagEvaluationContext,
        FlagResolution,
        InMemoryFeatureFlags,
        NullFeatureFlags,
    )

    return (
        AbstractFeatureFlags,
        FlagEvaluationContext,
        FlagResolution,
        InMemoryFeatureFlags,
        NullFeatureFlags,
    )


@pytest.fixture
def flags_module():
    return _import_flags()


class TestFlagEvaluationContext:
    async def test_current_reads_tenant_from_current_tenant(self, flags_module) -> None:
        # Rule: tenant comes from current_tenant(), never RequestContext.
        _, FlagEvaluationContext, *_ = flags_module
        with tenant_context("acme"):
            ctx = FlagEvaluationContext.current()
        assert ctx.tenant_id == "acme"

    async def test_current_has_no_tenant_outside_tenant_context(self, flags_module) -> None:
        _, FlagEvaluationContext, *_ = flags_module
        ctx = FlagEvaluationContext.current()
        assert ctx.tenant_id is None


class TestInMemoryFeatureFlagsResolutions:
    async def test_resolve_bool_returns_configured_value(self, flags_module) -> None:
        _, _, FlagResolution, InMemoryFeatureFlags, _ = flags_module
        flags = InMemoryFeatureFlags(flags={"new-checkout": True})
        resolution = await flags.resolve_bool("new-checkout", default=False)
        assert isinstance(resolution, FlagResolution)
        assert resolution.value is True

    async def test_resolve_string_returns_configured_value(self, flags_module) -> None:
        _, _, _, InMemoryFeatureFlags, _ = flags_module
        flags = InMemoryFeatureFlags(flags={"theme": "dark"})
        resolution = await flags.resolve_string("theme", default="light")
        assert resolution.value == "dark"

    async def test_resolve_numeric_returns_configured_value(self, flags_module) -> None:
        _, _, _, InMemoryFeatureFlags, _ = flags_module
        flags = InMemoryFeatureFlags(flags={"max-items": 42})
        resolution = await flags.resolve_numeric("max-items", default=10)
        assert resolution.value == 42

    async def test_resolve_object_returns_configured_value(self, flags_module) -> None:
        _, _, _, InMemoryFeatureFlags, _ = flags_module
        payload = {"limit": 5, "burst": True}
        flags = InMemoryFeatureFlags(flags={"rate-config": payload})
        resolution = await flags.resolve_object("rate-config", default={})
        assert resolution.value == payload

    @pytest.mark.parametrize(
        "resolver_name,default",
        [
            ("resolve_bool", False),
            ("resolve_string", "fallback"),
            ("resolve_numeric", 0),
            ("resolve_object", {}),
        ],
    )
    async def test_unknown_flag_returns_callers_default(
        self, flags_module, resolver_name, default
    ) -> None:
        # Edge case: an unconfigured key must never raise — it degrades to
        # the caller-supplied default, exactly like every other varco seam.
        _, _, _, InMemoryFeatureFlags, _ = flags_module
        flags = InMemoryFeatureFlags()
        resolver = getattr(flags, resolver_name)
        resolution = await resolver("does-not-exist", default=default)
        assert resolution.value == default

    async def test_tenant_scoped_override_wins_over_global_value(self, flags_module) -> None:
        _, FlagEvaluationContext, _, InMemoryFeatureFlags, _ = flags_module
        flags = InMemoryFeatureFlags(
            flags={"new-checkout": False},
            tenant_overrides={"acme": {"new-checkout": True}},
        )
        with tenant_context("acme"):
            ctx = FlagEvaluationContext.current()
            resolution = await flags.resolve_bool("new-checkout", default=False, context=ctx)
        assert resolution.value is True

    async def test_tenant_scoped_override_does_not_leak_to_other_tenants(
        self, flags_module
    ) -> None:
        _, FlagEvaluationContext, _, InMemoryFeatureFlags, _ = flags_module
        flags = InMemoryFeatureFlags(
            flags={"new-checkout": False},
            tenant_overrides={"acme": {"new-checkout": True}},
        )
        with tenant_context("globex"):
            ctx = FlagEvaluationContext.current()
            resolution = await flags.resolve_bool("new-checkout", default=False, context=ctx)
        assert resolution.value is False


class TestNullFeatureFlags:
    @pytest.mark.parametrize(
        "resolver_name,default",
        [
            ("resolve_bool", True),
            ("resolve_string", "x"),
            ("resolve_numeric", 7),
            ("resolve_object", {"a": 1}),
        ],
    )
    async def test_always_returns_callers_default(
        self, flags_module, resolver_name, default
    ) -> None:
        # NullFeatureFlags is a no-op seam — it must never invent a value.
        *_, NullFeatureFlags = flags_module
        flags = NullFeatureFlags()
        resolver = getattr(flags, resolver_name)
        resolution = await resolver("anything", default=default)
        assert resolution.value == default

    async def test_is_an_abstract_feature_flags_implementation(self, flags_module) -> None:
        AbstractFeatureFlags, _, _, _, NullFeatureFlags = flags_module
        assert isinstance(NullFeatureFlags(), AbstractFeatureFlags)
