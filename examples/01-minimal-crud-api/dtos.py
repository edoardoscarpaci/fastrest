"""
dtos
====
Pydantic DTOs for the Product API contract.

Three DTOs mirror the ``AsyncService[D, PK, C, R, U]`` type parameters:

    ``ProductCreate``  (C) — POST /products body.
    ``ProductRead``    (R) — GET response body; includes server-assigned ``pk``,
                             ``created_at``, and ``updated_at``.
    ``ProductUpdate``  (U) — PUT /products/{id} body; all fields optional so
                             partial updates work without re-sending unchanged data.

DESIGN: DTOs separate from the domain model
    ✅ API contract is independent of persistence.
    ✅ Pydantic validation lives here, not in the domain model.
    ✅ OpenAPI schema is generated from these types.
    ❌ One extra class per entity — justified by clear SRP separation.

Thread safety:  ✅ Pydantic models are effectively immutable after construction.
Async safety:   ✅ Pure value objects — no I/O.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from varco_core.dto import CreateDTO, ReadDTO, UpdateDTO


class ProductCreate(CreateDTO):
    """
    Payload for ``POST /products``.

    Args:
        name:        Product display name — required.
        description: Optional product description.
        price:       Unit price — required.
        in_stock:    Availability flag.  Defaults to ``True``.

    Raises:
        ValidationError: ``name`` or ``price`` is missing.
    """

    name: str
    description: str = ""
    price: float
    in_stock: bool = True


class ProductRead(ReadDTO):
    """
    Response body for ``GET /products/{id}`` and ``GET /products``.

    All timestamps are UTC — callers should convert to their local timezone
    for display.

    Args:
        pk:          Product UUID assigned by the repository on INSERT.
        name:        Product display name.
        description: Product description.
        price:       Unit price.
        in_stock:    Availability flag.
        created_at:  UTC timestamp when the product was first created.
        updated_at:  UTC timestamp of the most recent update.

    Edge cases:
        - ``updated_at`` is always populated — the in-memory repo sets it
          to ``created_at`` on the first save when no explicit value exists.
    """

    pk: UUID
    name: str
    description: str
    price: float
    in_stock: bool
    created_at: datetime
    updated_at: datetime


class ProductUpdate(UpdateDTO):
    """
    Payload for ``PUT /products/{id}``.

    All fields are optional — ``None`` means "no change" per the
    ``apply_update`` convention documented in ``AbstractDTOAssembler``.

    Args:
        name:        New display name.  ``None`` = keep existing.
        description: New description.  ``None`` = keep existing.
        price:       New unit price.  ``None`` = keep existing.
        in_stock:    New availability flag.  ``None`` = keep existing.

    Edge cases:
        - Sending ``{}`` (empty body) is valid and produces a no-op update.
    """

    name: str | None = None
    description: str | None = None
    price: float | None = None
    in_stock: bool | None = None


__all__ = ["ProductCreate", "ProductRead", "ProductUpdate"]
