"""
varco_core.context
=====================
X1 — the ambient request-scoped context primitive (Plan 011 Phase 0).

Public surface:

    from varco_core.context import (
        AmbientVar, Resolved, resolve_precedence,
        RequestContext, current_request_context, current_locale,
        current_timezone, request_context, arequest_context,
        TenantDefaultsProvider, TenantLocalizationDefaults,
        NullTenantDefaults, StaticTenantDefaults,
    )

Nothing here touches ``varco_core.service.tenant._current_tenant`` or
``varco_core.tracing._correlation_id`` — see ``request.py``'s module
docstring for why tenant is deliberately absent from ``RequestContext``.
"""

from __future__ import annotations

from varco_core.context.ambient import AmbientVar
from varco_core.context.defaults import (
    NullTenantDefaults,
    StaticTenantDefaults,
    TenantDefaultsProvider,
    TenantLocalizationDefaults,
)
from varco_core.context.precedence import Resolved, resolve_precedence
from varco_core.context.request import (
    RequestContext,
    arequest_context,
    current_locale,
    current_request_context,
    current_timezone,
    request_context,
)

__all__ = [
    "AmbientVar",
    "Resolved",
    "resolve_precedence",
    "RequestContext",
    "current_request_context",
    "current_locale",
    "current_timezone",
    "request_context",
    "arequest_context",
    "TenantDefaultsProvider",
    "TenantLocalizationDefaults",
    "NullTenantDefaults",
    "StaticTenantDefaults",
]
