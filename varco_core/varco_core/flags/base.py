"""
varco_core.flags.base
======================
``AbstractFeatureFlags`` — varco's own feature-flag seam (Plan 032 / D7).

**Not** a transcription of OpenFeature's ``AbstractProvider``. The
OpenFeature Python SDK sits at 0.10.0 and the spec itself at 0.9.0 (verified
2026-09-04, `design/research/004-flags-asyncapi-and-sbom-tooling.md` §1) —
both pre-1.0, and the SDK shipped a breaking change (``set_provider()`` no
longer blocking) inside a *minor* release. Shaping a gated, lockstep-
versioned public ABC around a still-churning pre-1.0 spec would import that
churn permanently. See `technical_docs/features/feature-flags.md` for the
full version evidence and the un-park trigger.

Four typed resolutions (bool/string/numeric/object) — the smallest surface
that covers every real flag shape, per brief 004 §1's own enumeration of
OpenFeature's resolution methods. A future ``OpenFeatureFlags`` adapter is
purely additive: a new implementation of this ABC, translating one shape to
the other, once the SDK actually reaches 1.0.

DESIGN: async resolution (Open question 1)
    ✅ Matches every other varco seam (``AbstractCache``, ``AbstractEventBus``,
       ...) — no special-cased sync exception for flags.
    ✅ Permits a remote provider (a flag service behind HTTP/gRPC) without a
       second, sync-only ABC bolted on later.
    ❌ A purely local, in-process flag source pays one coroutine-scheduling
       hop it does not need. Accepted — negligible next to a hot-path cache
       lookup, and every varco seam makes the same trade.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from varco_core.context.request import current_request_context
from varco_core.service.tenant import current_tenant

T = TypeVar("T")

__all__ = [
    "AbstractFeatureFlags",
    "FlagEvaluationContext",
    "FlagResolution",
]


@dataclass(frozen=True)
class FlagEvaluationContext:
    """
    Ambient targeting context for one flag evaluation.

    Attributes:
        tenant_id: The evaluating tenant, or ``None`` outside a
            ``tenant_context()`` block. Sourced from
            ``varco_core.service.tenant.current_tenant()`` — **never**
            ``RequestContext`` (CLAUDE.md's rule: tenant has exactly one
            source of truth).
        attributes: Free-form request-scoped targeting attributes, sourced
            from ``RequestContext.extras`` (locale, a request-scoped A/B
            cohort id, ...). Empty by default.

    Thread safety:  ✅ Frozen — safe to share across tasks.
    Async safety:   ✅ Pure value; ``current()`` reads ambient ``ContextVar``
                       state synchronously.
    """

    tenant_id: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def current(cls) -> FlagEvaluationContext:
        """
        Build a ``FlagEvaluationContext`` from the ambient request state.

        Returns:
            A context whose ``tenant_id`` reflects ``current_tenant()`` and
            whose ``attributes`` reflect the active ``RequestContext``'s
            ``extras`` — both default (``None`` / empty) outside any
            ambient scope.
        """
        return cls(
            tenant_id=current_tenant(),
            attributes=dict(current_request_context().extras),
        )


@dataclass(frozen=True)
class FlagResolution(Generic[T]):
    """
    The result of one flag evaluation.

    Attributes:
        value: The resolved value — the caller's ``default`` if the flag is
            unconfigured, never ``None`` unless the caller's own default was
            ``None``.
        reason: Free-form, implementation-defined explanation (e.g.
            ``"STATIC"``, ``"TENANT_OVERRIDE"``, ``"DEFAULT"``). Optional —
            no implementation is required to populate it.

    Thread safety:  ✅ Frozen — safe to share across tasks.
    """

    value: T
    reason: str | None = None


class AbstractFeatureFlags(ABC):
    """
    Feature-flag evaluation seam — four typed resolutions.

    Implement this to provide flag evaluation: ``InMemoryFeatureFlags``
    (this package) for tests and small deployments, ``NullFeatureFlags``
    (this package) as the always-off DI default, and (once the un-park
    trigger fires) an ``OpenFeatureFlags`` adapter.

    Every resolver takes the caller's ``default`` and must degrade to it on
    any "flag not found" condition — a feature-flag seam that raises on an
    unconfigured key is unusable in the exact case (an unreleased flag) it
    exists for.

    Async safety: ✅ All methods are ``async def`` — see the module
                     docstring's Open-question-1 DESIGN note.
    """

    @abstractmethod
    async def resolve_bool(
        self, key: str, default: bool, *, context: FlagEvaluationContext | None = None
    ) -> FlagResolution[bool]:
        """
        Resolve a boolean flag.

        Args:
            key: The flag's stable identifier.
            default: Returned (wrapped in a ``FlagResolution``) if ``key``
                is unconfigured.
            context: Targeting context. ``None`` evaluates with no targeting
                (global value only, no tenant override).

        Returns:
            The resolved ``FlagResolution[bool]``.

        Edge cases:
            - Unknown ``key`` → ``default``, never an exception.
        """

    @abstractmethod
    async def resolve_string(
        self, key: str, default: str, *, context: FlagEvaluationContext | None = None
    ) -> FlagResolution[str]:
        """Resolve a string flag. See ``resolve_bool`` for the shared contract."""

    @abstractmethod
    async def resolve_numeric(
        self, key: str, default: float, *, context: FlagEvaluationContext | None = None
    ) -> FlagResolution[float]:
        """Resolve a numeric flag. See ``resolve_bool`` for the shared contract."""

    @abstractmethod
    async def resolve_object(
        self, key: str, default: Any, *, context: FlagEvaluationContext | None = None
    ) -> FlagResolution[Any]:
        """Resolve a structured (dict/list) flag. See ``resolve_bool`` for the
        shared contract."""
