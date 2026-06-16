"""
dtos.py
=======
Pydantic DTOs for the blog post API contract.

Three DTOs map to the five ``AsyncService[D, PK, C, R, U]`` type parameters:

    ``PostCreate``  (C) — POST /v1/posts body.
    ``PostRead``    (R) — GET response body.  All fields, including server-assigned
                         ``pk``, ``created_at``, and ``updated_at``.
    ``PostUpdate``  (U) — PUT /v1/posts/{id} body.  All mutable fields required for
                         a full replacement update.

DESIGN: separate Create/Read/Update DTOs over a single combined model
    ✅ HTTP contract is explicit — clients see exactly which fields they can
       supply on each operation.
    ✅ OpenAPI schema is clean — no nullable sentinel fields in POST body.
    ✅ Validation (required vs optional) is enforced by Pydantic per operation type.
    ❌ One extra class per entity — justified by the SRP clarity.

Thread safety:  ✅ Pydantic models are immutable after construction.
Async safety:   ✅ Pure value objects — no I/O.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from varco_core.dto import CreateDTO, ReadDTO, UpdateDTO


class PostCreate(CreateDTO):
    """
    Payload for ``POST /v1/posts``.

    All three fields are required — there is no default for an unpublished post.

    Args:
        title:   Post headline, required.
        content: Post body text, required.
        author:  Author display name, required.

    Raises:
        ValidationError: Any required field is missing or empty.
    """

    title: str
    content: str
    author: str


class PostRead(ReadDTO):
    """
    Response body for ``GET /v1/posts/{id}`` and ``GET /v1/posts``.

    All fields are always present on a persisted post.

    Args:
        pk:         Post UUID assigned by the repository on INSERT.
        title:      Post headline.
        content:    Post body text.
        author:     Author display name.
        created_at: UTC timestamp when the post was first created.
        updated_at: UTC timestamp of the most recent update.

    Edge cases:
        - ``created_at`` / ``updated_at`` are always set for a persisted post.
          A missing value would indicate a data integrity bug in the service layer.
    """

    pk: UUID
    title: str
    content: str
    author: str
    created_at: datetime
    updated_at: datetime


class PostUpdate(UpdateDTO):
    """
    Payload for ``PUT /v1/posts/{id}``.

    All mutable fields are included — this is a full replacement, not a
    partial patch.  ``created_at`` and ``updated_at`` are server-managed
    and excluded from the update DTO.

    Args:
        title:   New post headline.
        content: New post body text.
        author:  New author display name.

    Edge cases:
        - ``author`` can be changed on UPDATE to support corrections.  Stricter
          access control (ownership checks) would be added at the service layer
          via ``AbstractAuthorizer``.
    """

    title: str
    content: str
    author: str


__all__ = ["PostCreate", "PostRead", "PostUpdate"]
