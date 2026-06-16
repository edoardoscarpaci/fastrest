"""
dtos.py
=======
Data Transfer Objects for the ``17-transactional-outbox`` example.

``OrderCreate`` carries the HTTP POST body.
``OrderRead`` is returned on every successful read.
``OrderUpdate`` supports partial PUT.

DESIGN: pydantic DTOs over plain dataclasses for HTTP validation
    ✅ Pydantic validates incoming JSON automatically in FastAPI.
    ✅ ``ReadDTO`` base class enforces ``pk``, ``created_at``, ``updated_at``
       on every response DTO — callers always get audit fields.
    ❌ DTOs are an extra layer; for a simple example this is boilerplate.
       Justified because it matches the production pattern.

Thread safety:  ✅ Immutable Pydantic models.
Async safety:   ✅ No I/O.
"""

from __future__ import annotations

from varco_core.dto import CreateDTO, ReadDTO, UpdateDTO


class OrderCreate(CreateDTO):
    """
    HTTP request body for ``POST /v1/orders``.

    Attributes:
        amount: Order total.  Must be provided by the caller.
    """

    amount: float


class OrderRead(ReadDTO):
    """
    HTTP response body for order endpoints.

    Inherits ``pk``, ``created_at``, ``updated_at`` from ``ReadDTO``.

    Attributes:
        amount: Order total.
        status: Current lifecycle state (``"pending"`` / ``"fulfilled"``).
    """

    amount: float
    status: str


class OrderUpdate(UpdateDTO):
    """
    HTTP request body for ``PUT /v1/orders/{id}``.

    All fields are optional for partial-update semantics.

    Attributes:
        amount: New order total.  ``None`` means "no change".
        status: New status.  ``None`` means "no change".
    """

    amount: float | None = None
    status: str | None = None


__all__ = ["OrderCreate", "OrderRead", "OrderUpdate"]
