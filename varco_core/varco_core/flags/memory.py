"""
varco_core.flags.memory
========================
``InMemoryFeatureFlags`` — the dict-backed ``AbstractFeatureFlags``
implementation for tests and small deployments (Plan 032 / D7).

Not a scanned ``@Singleton`` — bound to ``AbstractFeatureFlags`` only via
``varco_core.flags.di.enable_feature_flags(container)``, so importing this
module never silently activates it (see that module's docstring).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from varco_core.flags.base import AbstractFeatureFlags, FlagEvaluationContext, FlagResolution

__all__ = ["InMemoryFeatureFlags"]


class InMemoryFeatureFlags(AbstractFeatureFlags):
    """
    Dict-backed feature flags with optional per-tenant overrides.

    Args:
        flags: Global flag values, keyed by flag name. Any value type — the
            typed ``resolve_*`` methods trust the caller to store the
            matching shape.
        tenant_overrides: ``{tenant_id: {flag_name: value}}``. A tenant
            override wins over the global value; a tenant not present here
            (or a key not present under that tenant) falls through to
            ``flags``, then to the caller's ``default``.

    Thread safety:  ⚠️ Backed by plain dicts — construct once, treat as
                       read-only after construction (no mutation API is
                       exposed). Safe to share across tasks if never mutated
                       after ``__init__``.
    Async safety:   ✅ No I/O — every ``resolve_*`` call is synchronous work
                       wrapped in a coroutine to satisfy the ABC.

    Edge cases:
        - An override under a tenant not currently evaluating (a different
          ``context.tenant_id``, or no context at all) never leaks —
          ``test_tenant_scoped_override_does_not_leak_to_other_tenants``.
    """

    def __init__(
        self,
        *,
        flags: Mapping[str, Any] | None = None,
        tenant_overrides: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self._flags: dict[str, Any] = dict(flags) if flags is not None else {}
        self._tenant_overrides: dict[str, dict[str, Any]] = (
            {tenant: dict(overrides) for tenant, overrides in tenant_overrides.items()}
            if tenant_overrides is not None
            else {}
        )

    def _resolve(
        self, key: str, default: Any, context: FlagEvaluationContext | None
    ) -> FlagResolution[Any]:
        """
        Shared lookup: tenant override → global value → caller's default.

        Args:
            key: Flag name.
            default: Caller-supplied fallback.
            context: Targeting context; only ``context.tenant_id`` is
                consulted today.

        Returns:
            A ``FlagResolution`` whose ``reason`` names which tier matched.
        """
        if context is not None and context.tenant_id is not None:
            tenant_flags = self._tenant_overrides.get(context.tenant_id)
            if tenant_flags is not None and key in tenant_flags:
                return FlagResolution(value=tenant_flags[key], reason="TENANT_OVERRIDE")
        if key in self._flags:
            return FlagResolution(value=self._flags[key], reason="STATIC")
        return FlagResolution(value=default, reason="DEFAULT")

    async def resolve_bool(
        self, key: str, default: bool, *, context: FlagEvaluationContext | None = None
    ) -> FlagResolution[bool]:
        return self._resolve(key, default, context)

    async def resolve_string(
        self, key: str, default: str, *, context: FlagEvaluationContext | None = None
    ) -> FlagResolution[str]:
        return self._resolve(key, default, context)

    async def resolve_numeric(
        self, key: str, default: float, *, context: FlagEvaluationContext | None = None
    ) -> FlagResolution[float]:
        return self._resolve(key, default, context)

    async def resolve_object(
        self, key: str, default: Any, *, context: FlagEvaluationContext | None = None
    ) -> FlagResolution[Any]:
        return self._resolve(key, default, context)
