"""
tests.fixtures.routers
========================
Shared router fixtures for Plan 009's contract / codegen / client-parity test
suites (Phase 0, Phase 7, Phase 8).

``OrderRouter`` is a full CRUD router plus one custom ``@route`` (path + query +
body params) — deliberately mirrors the shapes called out in the plan's
"Tests" sections (CRUD-only router with 6 routes; a custom route with a path
param, a query param, and a Pydantic body).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel
from varco_fastapi.router.endpoint import route
from varco_fastapi.router.presets import CRUDRouter, GenericRouter


class Order:
    """Minimal stand-in domain model (D type arg)."""


class OrderCreate(BaseModel):
    name: str


class OrderRead(BaseModel):
    id: UUID
    name: str


class OrderUpdate(BaseModel):
    name: str | None = None


class OrderService:
    """Fake concrete service — duck-typed, not an AsyncService subclass."""

    async def create(self, body: Any, auth: Any = None) -> Any:
        return OrderRead(id=UUID(int=1), name=body.name)

    async def get(self, pk: Any, auth: Any = None) -> Any:
        return OrderRead(id=pk, name="x")

    async def list(self, params: Any = None, auth: Any = None) -> list[Any]:
        return []

    async def count(self, params: Any = None, auth: Any = None) -> int:
        return 0

    async def update(self, pk: Any, body: Any, auth: Any = None) -> Any:
        return OrderRead(id=pk, name=body.name)

    async def patch(self, pk: Any, body: Any, auth: Any = None) -> Any:
        return OrderRead(id=pk, name=body.name or "x")

    async def delete(self, pk: Any, auth: Any = None) -> None:
        return None

    async def cancel_order(self, order_id: UUID, limit: int, reason: OrderCreate) -> dict:
        return {"order_id": str(order_id), "limit": limit, "reason": reason.name}


class OrderRouter(CRUDRouter[Order, UUID, OrderCreate, OrderRead, OrderUpdate, OrderService]):
    """CRUD router (6 standard routes) + one custom route with a path param
    (``order_id: UUID``), a query param (``limit: int``), and a Pydantic body
    (``reason: OrderCreate``) — used across Phase 0/7/8 tests."""

    _prefix = "/orders"

    @route("POST", "/{order_id}/cancel")
    async def cancel(self, order_id: UUID, limit: int, reason: OrderCreate) -> dict:
        return await self.service.cancel_order(order_id, limit, reason)


class EmptyGenericRouter(GenericRouter):
    """A GenericRouter with zero declared routes — used for the "router with
    zero routes" edge case (a valid, empty contract)."""

    _prefix = "/empty"
