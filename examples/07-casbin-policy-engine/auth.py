"""
auth.py
=======
Simple header-based ``AuthContext`` builder for the Casbin policy example.

Real applications use ``JwtBearerAuth`` + ``TrustedIssuerRegistry`` (see the
``06-grant-based-authz`` example).  This example replaces the full JWT
machinery with a lightweight ``X-User-Id`` / ``X-User-Role`` header scheme so
tests can exercise the Casbin RBAC flow without RSA key generation overhead.

The ``HeaderAuth`` class implements ``AbstractServerAuth`` — the same interface
``JwtBearerAuth`` implements — so it plugs into ``RequestContextMiddleware``
without any code change in the router or service layers.

DESIGN: header-based auth over JWT for this example
    ✅ Zero crypto dependency — tests run faster and the focus stays on Casbin.
    ✅ ``HeaderAuth`` is one class implementing the same interface as
       ``JwtBearerAuth`` — the auth layer is swappable transparently.
    ❌ Trivially forgeable — never use this pattern in production.
    ❌ Does not exercise the JWT/authority machinery that real apps use.

Thread safety:  ✅ Stateless after construction.
Async safety:   ✅ ``__call__`` is ``async def``.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.exceptions import HTTPException

from varco_core.auth.base import AuthContext
from varco_fastapi.auth import AbstractServerAuth


class HeaderAuth(AbstractServerAuth):
    """
    Authentication strategy that reads caller identity from request headers.

    Extracts ``X-User-Id`` and ``X-User-Role`` from each request and builds an
    ``AuthContext``.  If ``X-User-Id`` is absent the request is rejected with
    HTTP 401.

    The ``AuthContext`` produced here is passed to service-layer hooks
    (``_check_entity``, ``_prepare_for_create``) and to the Casbin
    authorizer's ``RequestMapper`` to derive the Casbin ``sub`` value.

    Thread safety:  ✅ Stateless — no mutable instance state.
    Async safety:   ✅ ``__call__`` only reads request headers; no I/O.

    Example::

        auth = HeaderAuth()
        ctx = await auth(request)           # AuthContext(user_id="alice", ...)
    """

    async def __call__(self, request: Request) -> AuthContext:
        """
        Extract identity from request headers and return an ``AuthContext``.

        Args:
            request: The incoming Starlette/FastAPI request.

        Returns:
            ``AuthContext`` with ``user_id`` set to ``X-User-Id`` and
            ``roles`` containing the value of ``X-User-Role`` if present.

        Raises:
            HTTPException (401): ``X-User-Id`` header is missing or empty.

        Edge cases:
            - ``X-User-Role`` is optional; absent → empty roles set.
            - Multiple roles are not supported via headers; use JWT for that.
            - ``user_id`` is the raw header value — no validation or canonicalization.
        """
        user_id = request.headers.get("X-User-Id", "").strip()
        if not user_id:
            # Reject anonymous requests — the Casbin engine needs a subject.
            raise HTTPException(status_code=401, detail="X-User-Id header is required.")

        role = request.headers.get("X-User-Role", "").strip()
        # Roles are propagated to ctx.has_role() inside the service layer;
        # Casbin enforcement uses the subject string directly via PolicyEngine.
        roles: frozenset[str] = frozenset({role}) if role else frozenset()

        return AuthContext(user_id=user_id, roles=roles)


__all__ = ["HeaderAuth"]
