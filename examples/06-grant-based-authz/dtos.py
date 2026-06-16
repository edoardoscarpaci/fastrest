"""
dtos
====
Pydantic DTOs for the Document API contract.

Three DTOs mirror the ``AsyncService[D, PK, C, R, U]`` type parameters:

    ``DocumentCreate``  (C) — POST /documents body.
    ``DocumentRead``    (R) — GET response body; includes server-assigned
                              ``pk``, ``owner_id``, and timestamps.
    ``DocumentUpdate``  (U) — PUT /documents/{id} body; all fields optional
                              for partial updates.

DESIGN: ``owner_id`` in ``DocumentRead`` (read-only, not in ``DocumentCreate``)
    ✅ Callers never submit ``owner_id`` — the service stamps it from the JWT.
    ✅ ``DocumentRead`` exposes it so callers can verify ownership locally.
    ✅ No DTO-level validation needed — the JWT provides the authoritative value.
    ❌ Exposing ``owner_id`` reveals the user's subject identifier — acceptable
       for an example; a real API might omit it from public read responses.

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

    The caller must have a ``docs:write`` grant in their JWT to use this
    endpoint (enforced by the service authorizer, not at the DTO level).

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
        owner_id:   Subject of the user who created the document.  Used by
                    clients to determine if they are the owner.
        created_at: UTC timestamp of creation.
        updated_at: UTC timestamp of most recent update.

    Edge cases:
        - ``updated_at`` is always set (the in-memory repo sets it to
          ``created_at`` on the first save when no prior value exists).
    """

    pk: UUID
    title: str
    content: str
    owner_id: str | None
    created_at: datetime
    updated_at: datetime


class DocumentUpdate(UpdateDTO):
    """
    Payload for ``PUT /v1/documents/{id}``.

    All fields are optional — ``None`` means "no change".
    Ownership is not updatable via this DTO.

    Args:
        title:   New document title.  ``None`` = keep existing.
        content: New document body.  ``None`` = keep existing.

    Edge cases:
        - Sending ``{}`` (empty body) is valid — produces a no-op update.
        - ``owner_id`` cannot be changed via this DTO; transfer-of-ownership
          is out of scope for this example.
    """

    title: str | None = None
    content: str | None = None


__all__ = ["DocumentCreate", "DocumentRead", "DocumentUpdate"]
