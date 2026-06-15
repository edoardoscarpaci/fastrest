"""
dtos
====
Pydantic DTOs for the Note API contract.

Three DTOs mirror the ``AsyncService[D, PK, C, R, U]`` type parameters:

    ``NoteCreate``  (C) — POST /v1/notes body.
    ``NoteRead``    (R) — GET response; includes server-assigned fields.
    ``NoteUpdate``  (U) — PATCH /v1/notes/{id} body; all fields optional.

``tenant_id`` is intentionally ABSENT from ``NoteCreate`` and ``NoteUpdate``.
The service layer stamps it from the authenticated ``AuthContext`` so no
HTTP client can inject a foreign tenant ID.

DESIGN: DTOs separate from the domain model
    ✅ API contract is independent of persistence.
    ✅ ``tenant_id`` absent from DTOs prevents caller injection of foreign values.
    ✅ Pydantic validation lives here; business-rule validation in the service.
    ❌ One extra class per entity — justified by clear SRP separation.

Thread safety:  ✅ Pydantic models are effectively immutable after construction.
Async safety:   ✅ Pure value objects — no I/O.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from varco_core.dto import CreateDTO, ReadDTO, UpdateDTO


class NoteCreate(CreateDTO):
    """
    Payload for ``POST /v1/notes``.

    Args:
        title:   Note title — required; must not be blank (service validates).
        content: Note body text — optional.

    Raises:
        ValidationError: ``title`` is missing from the request body.

    Edge cases:
        - Empty-string ``title`` passes Pydantic validation but raises
          ``ServiceValidationError`` at the service layer (business rule).
    """

    title: str
    content: str = ""


class NoteRead(ReadDTO):
    """
    Response body for ``GET /v1/notes/{id}`` and list endpoints.

    ``tenant_id`` is included so callers can confirm which tenant returned
    the note (useful for debugging; strip it in production if needed).

    Args:
        pk:         Note UUID.
        tenant_id:  Owning tenant's identifier.
        title:      Note title.
        content:    Note body.
        created_at: UTC timestamp when the note was created.
        updated_at: UTC timestamp of the most recent update.
        deleted_at: ``None`` for active notes; UTC datetime when soft-deleted.

    Edge cases:
        - ``deleted_at`` is ``None`` for active notes returned from the normal
          list endpoint.  It is non-null only in the ``/deleted`` endpoint.
    """

    pk: UUID
    tenant_id: str
    title: str
    content: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class NoteUpdate(UpdateDTO):
    """
    Payload for ``PATCH /v1/notes/{id}``.

    All fields are optional — ``None`` means "no change" per the
    ``apply_update`` convention in ``AbstractDTOAssembler``.

    Args:
        title:   New title.  ``None`` = keep existing.
        content: New body.   ``None`` = keep existing.

    Edge cases:
        - Sending ``{}`` (empty body) produces a no-op update — valid.
        - Empty-string ``title`` passes Pydantic validation but the service
          validator will reject it with ``ServiceValidationError``.
    """

    title: str | None = None
    content: str | None = None


__all__ = ["NoteCreate", "NoteRead", "NoteUpdate"]
