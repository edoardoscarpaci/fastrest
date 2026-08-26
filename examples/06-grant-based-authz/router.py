"""
router
======
FastAPI router for the ``Document`` entity.

``DocumentRouter`` extends ``VarcoCRUDRouter`` with three mixins:
    CreateMixin  — POST   /v1/documents
    ReadMixin    — GET    /v1/documents/{id}
    DeleteMixin  — DELETE /v1/documents/{id}

UPDATE (PUT) and LIST (GET /v1/documents) are intentionally omitted to keep
the example focused on the three authorization scenarios described in the
task:
    1. CREATE  — requires ``docs:write`` grant
    2. READ    — requires any authenticated token (``docs:read`` grant optional)
    3. DELETE  — requires ownership OR admin role

All authorization is enforced at the service layer — the router only handles
HTTP method / path routing and delegates to ``DocumentService``.

DESIGN: router is thin — no business logic
    ✅ All auth lives in ``DocumentService`` / ``DocumentAuthorizer``.
    ✅ Swap the router for a CLI or async worker — service logic unchanged.
    ✅ ``_auth`` is set by ``create_app()`` so the ``JwtBearerAuth`` instance
       uses the live ``TrustedIssuerRegistry``.
    ❌ Three mixins vs. five — deliberately minimal for the example scope.

Thread safety:  ✅ ClassVars are read-only after ``create_app()`` sets ``_auth``.
Async safety:   ✅ All handlers are ``async def``.
"""

from __future__ import annotations

from uuid import UUID

from dtos import DocumentCreate, DocumentRead, DocumentUpdate
from models import Document
from varco_fastapi.router.crud import VarcoCRUDRouter
from varco_fastapi.router.mixins import CreateMixin, DeleteMixin, ReadMixin


class DocumentRouter(
    # Mixin order: each mixin contributes one endpoint.
    # CreateMixin → ReadMixin → DeleteMixin is conventional left-to-right.
    CreateMixin,
    ReadMixin,
    DeleteMixin,
    VarcoCRUDRouter[Document, UUID, DocumentCreate, DocumentRead, DocumentUpdate],
):
    """
    FastAPI router for ``/documents``.

    Provides:
        POST   /v1/documents          — create a document (requires docs:write)
        GET    /v1/documents/{id}     — read a document (authenticated)
        DELETE /v1/documents/{id}     — delete a document (owner or admin)

    Authorization is enforced by ``DocumentService`` / ``DocumentAuthorizer``,
    not here.  The router declares HTTP routing and response shapes only.

    Class attributes:
        _prefix:  All document routes live under ``/documents``.
        _version: API version prefix → ``/v1/documents``.
        _tags:    OpenAPI tag for all routes.
        _auth:    Set by ``create_app()`` to ``JwtBearerAuth(registry=registry)``.

    Thread safety:  ✅ ClassVars are read-only after ``create_app()`` returns.
    Async safety:   ✅ All handlers are ``async def``.
    """

    _prefix = "/documents"
    _tags = ["documents"]
    _version = "v1"
    # _auth is injected by create_app() — must be set before build_router().


__all__ = ["DocumentRouter"]
