"""
store.py
========
In-memory product store.

``ProductStore`` is a simple dict-backed fake database.  The example uses it
as the authoritative source of truth behind the layered cache.

Thread safety:  ❌  Not thread-safe — single event loop.
Async safety:   ✅  All methods are ``async def`` (consistent with cache API).
"""

from __future__ import annotations

from models import Product


class ProductStore:
    """
    Fake in-memory product database.

    Args:
        initial: Optional dict of ``{id: Product}`` seed data.

    Thread safety:  ❌  Not thread-safe.
    Async safety:   ✅  All methods are ``async def``.

    Edge cases:
        - ``get()`` returns ``None`` for unknown ids (no ``KeyError``).
        - ``save()`` is idempotent — saving the same product twice replaces.
        - ``delete()`` is a no-op for unknown ids.
    """

    def __init__(self, initial: dict[str, Product] | None = None) -> None:
        self._data: dict[str, Product] = dict(initial or {})

    async def get(self, product_id: str) -> Product | None:
        """
        Return the product or ``None`` if not found.

        Args:
            product_id: Unique product identifier.

        Returns:
            ``Product`` or ``None``.
        """
        return self._data.get(product_id)

    async def save(self, product: Product) -> None:
        """
        Insert or replace a product.

        Args:
            product: Product to persist.
        """
        self._data[product.id] = product

    async def delete(self, product_id: str) -> bool:
        """
        Remove a product.

        Args:
            product_id: Product identifier to remove.

        Returns:
            ``True`` if the product existed; ``False`` otherwise.
        """
        existed = product_id in self._data
        self._data.pop(product_id, None)
        return existed

    async def all(self) -> list[Product]:
        """Return all products as a list."""
        return list(self._data.values())

    def __len__(self) -> int:
        return len(self._data)


__all__ = ["ProductStore"]
