"""
models.py
=========
Domain model for the product catalog example.

``Product`` is a frozen dataclass — Pydantic's TypeAdapter handles
serialize/deserialize round-trips through RedisCache natively.

DESIGN: @dataclass over plain class
    ✅ Pydantic's JsonSerializer handles dataclasses without type hints on
       cache.get() — full type information is preserved after round-trips.
    ✅ frozen=True enforces immutability — cache hits are safe to read.
    ✅ Lightweight — no ORM or DomainModel dependency needed for this example.
    ❌ No validation — sufficient for a demo; use Pydantic BaseModel in production.

Thread safety:  ✅ Immutable — frozen=True.
Async safety:   ✅ No mutable state.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    """
    A catalog product.

    Attributes:
        id:          Unique product identifier.
        name:        Display name of the product.
        price:       Unit price in decimal form (e.g. ``9.99``).
        description: Optional product description.
    """

    id: str
    name: str
    price: float
    description: str = ""


__all__ = ["Product"]
