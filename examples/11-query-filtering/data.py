"""
data.py
=======
Hardcoded 20-item product catalog for the query-filtering example.

Provides enough variety across price, category, and stock status to make all
filter combinations in the smoke tests meaningful.  The list is intentionally
ordered by ``id`` ascending so ``sort=-price`` tests are easy to verify.

No database, no Docker — everything lives in process.

Thread safety:  ✅ Module-level tuple — immutable after import.
Async safety:   ✅ Read-only; safe to share across coroutines.
"""

from __future__ import annotations

from models import Product

# ── 20 products across four categories with varied price + stock ──────────────
# Products are spread across:
#   - "electronics"  (ids 1–5)
#   - "clothing"     (ids 6–10)
#   - "books"        (ids 11–15)
#   - "home"         (ids 16–20)
#
# Price range: $4.99 – $999.99
# Roughly half in-stock to make in_stock filters non-trivial.
PRODUCTS: tuple[Product, ...] = (
    Product(
        id=1,
        name="Wireless Headphones",
        price=79.99,
        category="electronics",
        in_stock=True,
    ),
    Product(
        id=2,
        name="Mechanical Keyboard",
        price=149.99,
        category="electronics",
        in_stock=True,
    ),
    Product(id=3, name="USB-C Hub", price=34.99, category="electronics", in_stock=False),
    Product(id=4, name="4K Monitor", price=499.99, category="electronics", in_stock=True),
    Product(id=5, name="Webcam HD", price=89.99, category="electronics", in_stock=False),
    Product(id=6, name="Cotton T-Shirt", price=19.99, category="clothing", in_stock=True),
    Product(id=7, name="Denim Jacket", price=89.99, category="clothing", in_stock=True),
    Product(id=8, name="Running Shoes", price=129.99, category="clothing", in_stock=False),
    Product(id=9, name="Wool Socks", price=9.99, category="clothing", in_stock=True),
    Product(
        id=10,
        name="Winter Widget Hat",
        price=24.99,
        category="clothing",
        in_stock=False,
    ),
    Product(id=11, name="Python Cookbook", price=49.99, category="books", in_stock=True),
    Product(id=12, name="Design Patterns", price=44.99, category="books", in_stock=True),
    Product(id=13, name="Widget Engineering", price=59.99, category="books", in_stock=False),
    Product(id=14, name="Clean Code", price=39.99, category="books", in_stock=True),
    Product(
        id=15,
        name="Domain-Driven Design",
        price=54.99,
        category="books",
        in_stock=False,
    ),
    Product(id=16, name="Coffee Maker", price=79.99, category="home", in_stock=True),
    Product(id=17, name="Air Purifier", price=199.99, category="home", in_stock=True),
    Product(id=18, name="Smart LED Bulb", price=14.99, category="home", in_stock=True),
    Product(id=19, name="Widget Desk Lamp", price=34.99, category="home", in_stock=False),
    Product(id=20, name="Standing Desk", price=999.99, category="home", in_stock=True),
)

__all__ = ["PRODUCTS"]
