"""
router.py
=========
FastAPI router for the ``Document`` entity in the Casbin policy example.

``DocumentRouter`` extends ``VarcoCRUDRouter`` with four mixins — Create,
Read, Update, and Delete — providing the standard REST surface.  The Casbin
engine enforces access control for every operation via the service layer; the
router itself performs no authorization logic.

DESIGN: router is thin — authorization lives in the Casbin engine, not here
    ✅ Same router pattern as other examples; Casbin authorization is
       transparent to the HTTP layer.
    ✅ Swapping the Casbin model (ACL → RBAC → ABAC) requires no router
       changes — only policy rules change.
    ❌ No per-route access annotations (unlike ``require_roles`` guard);
       authorization intent is visible only in the policy store.

Thread safety:  ✅ ClassVars are read-only after ``create_app()`` sets ``_auth``.
Async safety:   ✅ All handlers are ``async def``.
"""

from __future__ import annotations

from uuid import UUID

from varco_fastapi.router.crud import VarcoCRUDRouter
from varco_fastapi.router.mixins import (
    CreateMixin,
    DeleteMixin,
    ReadMixin,
    UpdateMixin,
)

from dtos import DocumentCreate, DocumentRead, DocumentUpdate
from models import Document


class DocumentRouter(
    # Mixin order: CREATE → READ → UPDATE → DELETE (conventional left-to-right)
    CreateMixin,
    ReadMixin,
    UpdateMixin,
    DeleteMixin,
    VarcoCRUDRouter[Document, UUID, DocumentCreate, DocumentRead, DocumentUpdate],
):
    """
    FastAPI router for ``/v1/documents``.

    Provides:
        POST   /v1/documents          — create a document (requires "write" in Casbin)
        GET    /v1/documents/{id}     — read a document (requires "read" in Casbin)
        PUT    /v1/documents/{id}     — update a document (requires "write" in Casbin)
        DELETE /v1/documents/{id}     — delete a document (requires "delete" in Casbin)

    Authorization is enforced by ``DocumentService`` via ``PolicyEngineAuthorizer``
    → ``CasbinPolicyEngine``.  Callers without the appropriate Casbin policy
    receive HTTP 403.

    Class attributes:
        _prefix:  All document routes live under ``/documents``.
        _version: API version prefix → ``/v1/documents``.
        _tags:    OpenAPI tag for all routes.
        _auth:    Set by ``create_app()`` to the ``HeaderAuth`` instance.

    Thread safety:  ✅ ClassVars are read-only after ``create_app()`` returns.
    Async safety:   ✅ All handlers are ``async def``.
    """

    _prefix = "/documents"
    _tags = ["documents"]
    _version = "v1"
    # _auth is injected by create_app() before build_router() is called.


__all__ = ["DocumentRouter"]
