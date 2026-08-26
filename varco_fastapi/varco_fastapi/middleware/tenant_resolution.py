"""
varco_fastapi.middleware.tenant_resolution
=============================================
``TenantResolutionMiddleware`` — resolves the request's tenant status
**before** ``pool.ensure()`` (Plan 007, Phase 10, step 3-4).

DESIGN: catalog status checked before ``pool.ensure()``, always
    Mirrors ``varco_core.tenancy.routing.route_request`` — a non-``active``
    tenant never causes an engine/binding to be created. This middleware
    is the HTTP-layer caller of that routing decision; a request with no
    tenant header passes through untouched (public routes still work), and
    a request that resolves to a status other than ``active`` gets the
    documented HTTP code (503/403/410/404) — never a default-database
    fallback, never a bare 500.
"""

from __future__ import annotations

from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp
from varco_core.tenancy.catalog import TenantNotFoundError
from varco_core.tenancy.routing import routing_decision_for_status
from varco_core.tenancy.settings import TenantStatus


class TenantResolutionMiddleware(BaseHTTPMiddleware):
    """
    Resolves ``header``'s tenant id (if present) into an active
    ``tenant_context()``, rejecting non-``active`` tenants before any
    resource is created.

    Args:
        app:     The ASGI application to wrap.
        catalog: An ``AbstractTenantCatalog``-shaped object (``get()``).
        pool:    A resource-pool-shaped object with an async ``ensure()``.
        header:  The HTTP header carrying the tenant id. Defaults to
                 ``"X-Tenant-Id"``.

    Edge cases:
        - No tenant header -> passes through untouched (public routes
          still work).
        - Unknown tenant -> 404.
        - ``ensure()`` is called at most once per request, and only for an
          ``active`` tenant.
    """

    def __init__(
        self, app: ASGIApp, *, catalog: Any, pool: Any, header: str = "X-Tenant-Id"
    ) -> None:
        super().__init__(app)
        self._catalog = catalog
        self._pool = pool
        self._header = header

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        tenant_id = request.headers.get(self._header)
        if tenant_id is None:
            return await call_next(request)

        try:
            descriptor = await self._catalog.get(tenant_id)
            status_value = (
                descriptor.status.value
                if hasattr(descriptor.status, "value")
                else descriptor.status
            )
        except TenantNotFoundError:
            status_value = TenantStatus.DELETED.value

        decision = routing_decision_for_status(status_value)
        if not decision.routable:
            return JSONResponse(
                status_code=decision.http_status,
                content={"detail": f"Tenant {tenant_id!r}: {decision.reason}"},
            )

        from varco_core.service.tenant import tenant_context

        with tenant_context(tenant_id):
            await self._pool.ensure(tenant_id)
            return await call_next(request)
