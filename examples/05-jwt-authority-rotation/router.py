"""
router.py
=========
``AuthRouter`` — protected ``/me`` endpoint for the
``05-jwt-authority-rotation`` example.

This module is intentionally thin — ``/me`` is wired as a plain FastAPI
route (not a ``GenericRouter`` method) in ``app.py`` so the handler can
accept a raw ``Request`` argument and read the ``kid`` from the JWT header.

The ``build_router()`` helper returns an ``APIRouter`` that ``app.py``
includes; this keeps the auth-endpoint code separate from the ``create_app``
factory while remaining readable.

Endpoints:
    GET /me   — requires a valid Bearer JWT; returns ``sub`` and ``kid``

DESIGN: plain ``APIRouter`` over ``GenericRouter``
    ✅ Handler needs the raw ``Request`` to extract ``kid`` from the JWT
       header — ``GenericRouter`` custom handlers only receive ``ctx`` and
       path params (``_make_custom_handler`` does not pass ``Request``).
    ✅ ``Depends(server_auth)`` replicates exactly what ``JwtBearerAuth``
       does when set as ``_auth`` on a ``GenericRouter`` — same code path.
    ✅ No service, repository, or broker needed — plain FastAPI is simpler.
    ❌ No automatic OpenAPI guard annotation (acceptable for a demo).

Thread safety:  ✅ Router is built once; ``build_protected_router`` is sync.
Async safety:   ✅ Handler is async; no blocking I/O.
"""

from __future__ import annotations

import jwt as _jwt
from fastapi import APIRouter, Depends, Request
from varco_core.auth.base import AuthContext
from varco_fastapi.auth import JwtBearerAuth


def build_protected_router(server_auth: JwtBearerAuth) -> APIRouter:
    """
    Build an ``APIRouter`` with the ``GET /me`` endpoint.

    Uses ``Depends(server_auth)`` to inject the verified ``AuthContext`` into
    the handler — identical to what ``GenericRouter`` does with ``_auth``.

    Args:
        server_auth: The ``JwtBearerAuth`` instance built in ``create_app()``.
                     Must use ``required=True`` so that missing/invalid tokens
                     result in 401 rather than anonymous access.

    Returns:
        Configured ``APIRouter`` ready for ``app.include_router()``.

    Edge cases:
        - The ``APIRouter`` must be included in ``app`` AFTER ``create_app()``
          sets up the middleware — ``RequestContextMiddleware`` is what sets the
          ``AuthContext`` ContextVar for all other routes on the app.  The
          ``/me`` handler uses ``Depends(server_auth)`` directly, so it works
          independently of the middleware ordering.
    """
    router = APIRouter(tags=["jwt-rotation"])

    @router.get("/me", summary="Caller identity + signing key")
    async def me(
        request: Request,
        ctx: AuthContext = Depends(server_auth),
    ) -> dict:
        """
        Return the caller's subject and the kid that signed the incoming token.

        The ``kid`` is extracted from the JWT ``Authorization`` header without
        re-verifying the signature (the signature was already checked by
        ``server_auth`` above via ``Depends``).  It lets callers — and tests —
        trace which key is currently active after rotation.

        Args:
            request: Raw FastAPI ``Request`` — used to read the ``Authorization``
                     header and extract the ``kid`` from the JWT header.
            ctx:     Verified ``AuthContext`` populated by ``server_auth``.

        Returns:
            ``{"subject": <sub>, "kid": <kid-or-null>}``

        Raises:
            HTTPException 401: Raised by ``server_auth`` (``Depends``) when the
                Bearer token is missing or invalid.

        Edge cases:
            - ``jwt.get_unverified_header()`` never verifies the signature; it
              only base64-decodes the first JWT segment (header).
            - Tokens from ``JwtAuthority.sign()`` always carry a ``kid`` in the
              header — ``None`` here indicates a third-party token.
        """
        auth_header: str = request.headers.get("authorization", "")
        raw_token = auth_header.removeprefix("Bearer ").strip()
        kid: str | None = None
        if raw_token:
            try:
                header = _jwt.get_unverified_header(raw_token)
                kid = header.get("kid")
            except Exception:  # noqa: BLE001
                # Signature already verified — only way to land here is a
                # header-segment corruption after passing JwtBearerAuth.
                # Return None rather than crashing.
                pass

        return {"subject": ctx.user_id, "kid": kid}

    return router


__all__ = ["build_protected_router"]
