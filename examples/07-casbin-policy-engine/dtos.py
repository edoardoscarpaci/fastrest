"""
dtos
====
Pydantic DTOs for the Document API contract.

Three DTOs mirror the ``AsyncService[D, PK, C, R, U]`` type parameters:

    ``DocumentCreate``  (C) — POST /v1/documents body.
    ``DocumentRead``    (R) — GET response body; includes server-assigned
                              ``pk`` and timestamps.
    ``DocumentUpdate``  (U) — PUT /v1/documents/{id} body; all fields optional.

DESIGN: simple DTOs with no authorization metadata
    Authorization is fully delegated to the Casbin policy engine at the
    service layer.  DTOs carry only domain data — no grants or owner fields.

    ✅ DTOs are clean data contracts; authorization is not a DTO concern.
    ✅ The same DTO can be reused across authorization strategies.
    ❌ Cannot express per-document ACL via the DTO alone.

Thread safety:  ✅ Pydantic models are effectively immutable after construction.
Async safety:   ✅ Pure value objects — no I/O.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from varco_core.dto import CreateDTO, ReadDTO, UpdateDTO


class DocumentCreate(CreateDTO):
    """
    Payload for ``POST /v1/documents``.

    Callers must hold the ``writer`` or ``admin`` role (enforced by the Casbin
    engine, not at the DTO level) to use this endpoint.

    Args:
        title:   Document title — required, must be non-empty.
        content: Document body — optional, defaults to empty string.

    Raises:
        ValidationError: ``title`` is missing or empty.
    """

    title: str
    content: str = ""


class DocumentRead(ReadDTO):
    """
    Response body for ``GET /v1/documents/{id}`` and listing.

    All timestamps are UTC; clients should localise for display.

    Args:
        pk:         Document UUID assigned by the repository on INSERT.
        title:      Document title.
        content:    Document body.
        created_at: UTC timestamp of creation.
        updated_at: UTC timestamp of most recent update.

    Edge cases:
        - ``updated_at`` is always set (the in-memory repo sets it to
          ``created_at`` on the first save when no prior value exists).
    """

    pk: UUID
    title: str
    content: str
    created_at: datetime
    updated_at: datetime


class DocumentUpdate(UpdateDTO):
    """
    Payload for ``PUT /v1/documents/{id}``.

    All fields are optional — ``None`` means "no change".

    Args:
        title:   New document title.  ``None`` = keep existing.
        content: New document body.  ``None`` = keep existing.

    Edge cases:
        - Sending ``{}`` (empty body) is valid — produces a no-op update.
    """

    title: str | None = None
    content: str | None = None


__all__ = ["DocumentCreate", "DocumentRead", "DocumentUpdate"]
