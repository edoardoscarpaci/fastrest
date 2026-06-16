"""
models.py
=========
Product domain model for the query-filtering example.

A plain frozen dataclass — no database, no DomainModel subclassing needed.
The ``TypeCoercionRegistry`` is built directly from the field annotations so
the query system can coerce filter strings (``"true"``, ``"49.99"``) to the
correct Python types at request time.

DESIGN: plain ``@dataclass(frozen=True)`` over ``DomainModel``
    ✅ No ORM, no DB — the example stays focused on the query system itself.
    ✅ Frozen → hashable, safe to use as dict key, safe across async contexts.
    ✅ ``TypeCoercionRegistry`` can be built from ``__annotations__`` alone —
       no varco_sa / varco_beanie required.
    ❌ Missing ``DomainModel`` lifecycle hooks (``@PostConstruct``, etc.) —
       acceptable because this is a read-only catalog with no mutations.

Thread safety:  ✅ Frozen dataclass — immutable after construction.
Async safety:   ✅ Value object — safe to share across coroutines.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    """
    A catalog product with price, category, and stock information.

    Attributes:
        id:        Unique integer identifier (1-based).
        name:      Display name of the product.
        price:     Price in USD as a float.
        category:  Product category string (e.g. ``"electronics"``).
        in_stock:  Whether the product is currently available.

    Thread safety:  ✅ Frozen dataclass — immutable.
    Async safety:   ✅ Value object.

    Edge cases:
        - ``price`` is stored as ``float`` — filter strings like ``"49.99"``
          must be coerced before comparison.  ``TypeCoercionRegistry`` handles
          this automatically when the visitor is built via ``build_registry()``.
        - ``in_stock`` filter strings ``"true"`` / ``"false"`` are coerced via
          ``coerce_boolean`` which accepts ``"true"`` / ``"1"`` / ``"yes"`` as
          truthy.
    """

    id: int
    name: str
    price: float
    category: str
    in_stock: bool

    def to_dict(self) -> dict:
        """
        Serialise the product to a JSON-compatible dict.

        Returns:
            Dict with all five fields — safe for ``JSONResponse``.
        """
        return {
            "id": self.id,
            "name": self.name,
            "price": self.price,
            "category": self.category,
            "in_stock": self.in_stock,
        }


__all__ = ["Product"]
