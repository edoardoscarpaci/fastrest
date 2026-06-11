"""
router
======
FastAPI router for the ``Product`` entity.

``ProductRouter`` extends ``VarcoCRUDRouter`` with the five standard CRUD
mixins to expose the full product catalog API.

Endpoints registered
--------------------
    POST   /v1/products          — create a product
    GET    /v1/products/{id}     — fetch a product by UUID
    PUT    /v1/products/{id}     — full update
    DELETE /v1/products/{id}     — delete a product
    GET    /v1/products          — list all products

DESIGN: router is thin — no business logic
    All logic lives in ``ProductService`` (DI-injected via ``VarcoCRUDRouter``).
    The router only declares HTTP method, path, and response model.

    ✅ Router can be swapped for a CLI runner or async worker without
       changing the service.
    ✅ CRUD mixins contribute one endpoint each — adding or removing
       a mixin is a one-line change.
    ❌ One extra class per entity — necessary to express the routing contract.

Thread safety:  ✅ ClassVars read-only after ``build_router()`` returns.
Async safety:   ✅ All handlers are ``async def``.
"""

from __future__ import annotations

from uuid import UUID

from providify import Singleton

from varco_fastapi.router.crud import VarcoCRUDRouter
from varco_fastapi.router.mixins import (
    CreateMixin,
    DeleteMixin,
    ListMixin,
    ReadMixin,
    UpdateMixin,
)

from dtos import ProductCreate, ProductRead, ProductUpdate
from models import Product


@Singleton
class ProductRouter(
    # Mixin order: each mixin contributes one endpoint via __init_subclass__.
    # Order does not affect behaviour — left-to-right is conventional.
    CreateMixin,
    ReadMixin,
    UpdateMixin,
    DeleteMixin,
    ListMixin,
    VarcoCRUDRouter[Product, UUID, ProductCreate, ProductRead, ProductUpdate],
):
    """
    FastAPI router for ``/products``.

    Provides the following endpoints:
        POST   /v1/products          — create a product
        GET    /v1/products/{id}     — fetch a product by UUID
        PUT    /v1/products/{id}     — full update
        DELETE /v1/products/{id}     — delete a product
        GET    /v1/products          — list with pagination

    Service injection:
        ``_service`` is injected by providify via ``Inject[AsyncService[...]]``
        in ``VarcoCRUDRouter.__init__``.  The concrete type resolves to
        ``ProductService`` because it is bound under the matching generic alias.

    Thread safety:  ✅ ``_prefix`` and ``_tags`` are read-only ClassVars.
    Async safety:   ✅ All handlers are ``async def``.
    """

    _prefix = "/products"
    _tags = ["products"]
    _version = "v1"


__all__ = ["ProductRouter"]
