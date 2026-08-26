"""
varco_core.context.request
=============================
``RequestContext`` — the single aggregate ambient value I2 (locale) and T1
(timezone) both build on (Plan 011 D-6).

One ``AmbientVar[RequestContext]`` rather than one ``ContextVar`` per
concern:

DESIGN: one aggregate over N independent ContextVars
    ✅ One middleware pass, one token, one reset — two independent vars means
       two tokens whose reset order must be right, and four states
       (locale-only / timezone-only / both / neither) to test instead of one.
    ✅ Merge-on-nest semantics (below) are defined once, in one place, which
       is what makes "the timezone middleware runs after the locale
       middleware" a non-event instead of a landmine.
    ✅ Adding a third request-scoped concern later (currency? unit system?)
       is a new field on ``RequestContext``, not a new module-level global.
    ❌ A consumer that only wants the locale still reads the whole record.
       Irrelevant in practice — it is a frozen-dataclass attribute read.

**Tenant is deliberately absent from ``RequestContext``.**
``varco_core.service.tenant.current_tenant()`` stays the single source of
truth — two places to ask "who is the tenant" is how they diverge, and
``tenant_context()`` is already load-bearing across ``TenantAwareService``,
``tenancy_cache_key()``, RLS, the DLQ tenant stamp, and the audit trail.
Composition with the tenant is by *ordering* (the tenant middleware runs
before the localization middleware, so tenant-default lookup works), never
by containment.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, Any

from varco_core.context.ambient import AmbientVar

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo

__all__ = [
    "RequestContext",
    "current_request_context",
    "current_locale",
    "current_timezone",
    "request_context",
    "arequest_context",
]


@dataclasses.dataclass(frozen=True)
class RequestContext:
    """
    Ambient, request-scoped locale/timezone/extras.

    Args:
        locale: Resolved locale tag (e.g. ``"fr"``, ``"fr-CA"``), or ``None``
            when I2 is disabled or no locale has been resolved yet.
        timezone: Resolved ``ZoneInfo``, or ``None`` when T1 is disabled or
            no timezone has been resolved yet.
        extras: Free-form request-scoped string extras for future concerns
            (currency, unit system, ...). Empty by default.
    """

    locale: str | None = None
    timezone: ZoneInfo | None = None
    extras: Mapping[str, str] = dataclasses.field(default_factory=dict)


_request_context: AmbientVar[RequestContext] = AmbientVar(
    "varco_core.context.request_context", default=None
)


def current_request_context() -> RequestContext:
    """
    Return the active ``RequestContext``, or an empty one — **never** ``None``.

    Callers should never null-check the return value; an empty
    ``RequestContext()`` is byte-identical to "nothing configured" (RD-1).
    """
    ctx = _request_context.get()
    return ctx if ctx is not None else RequestContext()


def current_locale() -> str | None:
    """Shortcut for ``current_request_context().locale``."""
    return current_request_context().locale


def current_timezone() -> ZoneInfo | None:
    """Shortcut for ``current_request_context().timezone``."""
    return current_request_context().timezone


def _merged(
    *,
    locale: str | None,
    timezone: ZoneInfo | None,
    extras: Mapping[str, str] | None,
) -> RequestContext:
    """Build the next ``RequestContext`` by merging overrides onto the
    enclosing context — setting a locale must not blank an already-resolved
    timezone (D-6)."""
    current = current_request_context()
    changes: dict[str, Any] = {}
    if locale is not None:
        changes["locale"] = locale
    if timezone is not None:
        changes["timezone"] = timezone
    if extras is not None:
        changes["extras"] = extras
    if not changes:
        return current
    return dataclasses.replace(current, **changes)


@contextmanager
def request_context(
    *,
    locale: str | None = None,
    timezone: ZoneInfo | None = None,
    extras: Mapping[str, str] | None = None,
) -> Iterator[RequestContext]:
    """
    Synchronous CM that **merges** overrides onto the enclosing
    ``RequestContext`` for the duration of the block (D-6).

    Args:
        locale: Locale to set, or ``None`` to leave the enclosing locale
            unchanged.
        timezone: Timezone to set, or ``None`` to leave the enclosing
            timezone unchanged.
        extras: Extras mapping to set, or ``None`` to leave unchanged.

    Yields:
        The merged ``RequestContext`` now active.
    """
    merged = _merged(locale=locale, timezone=timezone, extras=extras)
    with _request_context.scope(merged) as value:
        yield value


@asynccontextmanager
async def arequest_context(
    *,
    locale: str | None = None,
    timezone: ZoneInfo | None = None,
    extras: Mapping[str, str] | None = None,
) -> AsyncIterator[RequestContext]:
    """Async counterpart of ``request_context()`` — same merge semantics."""
    merged = _merged(locale=locale, timezone=timezone, extras=extras)
    async with _request_context.ascope(merged) as value:
        yield value
