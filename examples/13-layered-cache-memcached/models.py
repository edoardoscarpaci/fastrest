"""
models.py
=========
Domain model for the layered-cache example.

``Product`` is a plain frozen dataclass — no database backing, just an
in-memory store.  Keeping it simple lets the example focus on the cache
tier rather than ORM integration.

Thread safety:  ✅ Immutable — frozen=True.
Async safety:   ✅ No mutable state.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    """
    A simple product entity.

    Attributes:
        id:    Unique string identifier (caller-assigned, e.g. ``"p-1"``).
        name:  Display name.
        price: Price in arbitrary currency units.
    """

    id: str
    name: str
    price: float


__all__ = ["Product"]
