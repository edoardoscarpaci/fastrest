"""
store.py
=========
In-memory product store — the fake "database" behind the cache.

The store tracks ``get_calls`` so tests can assert whether a request was
served from the cache (no increment) or from the DB (incremented).

DESIGN: in-memory dict over a real DB
    ✅ Keeps the example focused on caching — no DB setup, migrations, or ORM.
    ✅ ``get_calls`` counter makes cache hit/miss assertions trivial.
    ✅ Thread safety is irrelevant — all access is single-loop async.
    ❌ Not persistent — in a real app this would be replaced by an ORM repository.

Thread safety:  ❌ Plain dict — single event loop only.
Async safety:   ✅ All methods are ``async def``.
"""

from __future__ import annotations

from models import Product


class ProductStore:
    """
    Minimal in-memory product database.

    Attributes:
        get_calls: Counter incremented every time ``get()`` is called.
                   Test code uses this to verify cache hit/miss behaviour.

    Args:
        None — store starts empty.
    """

    def __init__(self) -> None:
        self._data: dict[str, Product] = {}
        self.get_calls: int = 0

    async def get(self, product_id: str) -> Product | None:
        """
        Return the product with ``product_id``, or ``None`` if absent.

        Increments ``get_calls`` on every call — tests use this counter to
        verify whether the cache absorbed the request.

        Args:
            product_id: Product identifier to look up.

        Returns:
            The ``Product`` or ``None``.
        """
        self.get_calls += 1
        return self._data.get(product_id)

    async def save(self, product: Product) -> None:
        """
        Persist (insert or overwrite) a product.

        Args:
            product: Product to store.  The ``id`` field is used as the key.
        """
        self._data[product.id] = product

    async def all(self) -> list[Product]:
        """
        Return all stored products (unsorted).

        Returns:
            List of all ``Product`` instances.
        """
        return list(self._data.values())


__all__ = ["ProductStore"]
