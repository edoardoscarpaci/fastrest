"""
Plan 001 — 6th `S` TypeVar on ``VarcoCRUDRouter`` (failing-first / red-state tests).

See ``plans/001-crud-router-service-typevar.md`` for the full design. This module
covers Step 2's runtime regression tests:

- ``test_six_arg_subscription_runtime``    — 6-arg subscription dispatches a custom
                                              route through ``self.service``.
- ``test_five_arg_subscription_still_works`` — 5-arg subscription (default ``S``)
                                              keeps working (backward-compat guard).
- ``test_classvar_service_fallback``       — the removed-``ClassVar`` runtime
                                              equivalence (``_service`` set at class
                                              level, no DI) still resolves via both
                                              ``_service`` and the new ``service``
                                              accessor.
- ``test_service_property_raises_when_unset`` — ``router.service`` raises
                                              ``RuntimeError`` (not a bare ``None``)
                                              when ``_service`` was never set.
- ``test_resolve_type_args_ignores_service_arg`` — ``_resolve_type_args`` strips the
                                              trailing service-type arg so CRUD model
                                              resolution still receives exactly
                                              ``(D, PK, C, R, U)``.

RED STATE (before Plan 001 Step 3/4 land):
    ``S`` does not exist yet, so every test that subscripts a router class with 6
    type arguments fails with a ``TypeError`` ("Too many arguments") raised by
    ``Generic.__class_getitem__`` at the subscription expression itself — this
    happens *inside* the test function body (classes are defined locally), so it
    surfaces as a normal test failure, not a module-collection error. The
    ``service`` property tests fail with ``AttributeError`` (no such property yet)
    instead of the intended ``RuntimeError``/return value.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from varco_fastapi.router.base import _resolve_type_args
from varco_fastapi.router.crud import VarcoCRUDRouter
from varco_fastapi.router.endpoint import route
from varco_fastapi.router.presets import CRUDRouter

# ── Fixtures (naming mirrors the plan: _Rule / _RC / _RR / _RU / _RuleService) ─


class _Rule:
    """Minimal stand-in domain model (D type arg) — never instantiated by the router
    itself; only carried around as a generic type argument."""


class _RC(BaseModel):
    name: str


class _RR(BaseModel):
    id: UUID
    name: str


class _RU(BaseModel):
    name: str | None = None


class _RuleService:
    """
    Fake concrete service exposing a custom method beyond the CRUD surface.

    Deliberately NOT a subclass of ``AsyncService`` — ``VarcoCRUDRouter`` never
    does an ``isinstance`` check on the injected service at runtime (duck typing),
    matching the ``_MockService`` idiom in ``tests/milestone_e/test_router_split.py``.
    """

    async def custom_method(self) -> str:
        return "custom-result"

    async def create(self, body: Any, auth: Any = None) -> Any:
        return _RR(id=UUID("00000000-0000-0000-0000-000000000001"), name=body.name)

    async def get(self, pk: Any, auth: Any = None) -> Any:
        return _RR(id=pk, name="x")

    async def list(self, params: Any = None, auth: Any = None) -> list[Any]:
        return []

    async def count(self, params: Any = None, auth: Any = None) -> int:
        return 0

    async def update(self, pk: Any, body: Any, auth: Any = None) -> Any:
        return _RR(id=pk, name=body.name)

    async def patch(self, pk: Any, body: Any, auth: Any = None) -> Any:
        return _RR(id=pk, name=body.name or "x")

    async def delete(self, pk: Any, auth: Any = None) -> None:
        pass


class _FakeService:
    """Sentinel distinct from ``_RuleService`` — used purely for identity checks in
    the ClassVar-fallback / unset-property tests, which never dispatch a real route."""


# ── test_six_arg_subscription_runtime ──────────────────────────────────────────


async def test_six_arg_subscription_runtime():
    """6-arg subscription must let a custom @route call self.service.custom_method().

    This is the core runtime proof for the plan: asserts 6-arg subscription works
    and that the ``service`` accessor dispatches to the injected concrete service.
    """

    class RuleRouterT(CRUDRouter[_Rule, UUID, _RC, _RR, _RU, _RuleService]):
        _prefix = "/rules"

        @route("GET", "/custom")
        async def custom(self) -> dict:
            return {"result": await self.service.custom_method()}

    router = RuleRouterT(service=_RuleService())
    app = FastAPI()
    app.include_router(router.build_router())
    client = TestClient(app)
    resp = client.get("/rules/custom")
    assert resp.status_code == 200
    assert resp.json() == {"result": "custom-result"}


# ── test_five_arg_subscription_still_works ─────────────────────────────────────


async def test_five_arg_subscription_still_works():
    """5-arg subscription (default S) must keep building + dispatching standard CRUD.

    Backward-compat guard — pins the pre-existing contract so the S-typevar change
    cannot silently break routers that don't opt into the 6th argument.
    """

    class RuleRouterFive(CRUDRouter[_Rule, UUID, _RC, _RR, _RU]):
        _prefix = "/rules5"

    router = RuleRouterFive(service=_RuleService())
    app = FastAPI()
    app.include_router(router.build_router())
    client = TestClient(app)
    resp = client.post("/rules5/", json={"name": "widget"})
    assert resp.status_code == 201


# ── test_classvar_service_fallback ─────────────────────────────────────────────


async def test_classvar_service_fallback():
    """_service set at class level (no DI) must resolve via both getattr and .service.

    Guards the removed-ClassVar runtime equivalence promised by the plan: dropping
    `ClassVar` from the `_service` annotation must be invisible at runtime, and the
    existing `_service = _MockService()` test idiom (test_router_split.py) must keep
    working through the new `service` property too.
    """

    class CompatRouter(VarcoCRUDRouter[_Rule, UUID, _RC, _RR, _RU]):
        _prefix = "/compat"
        _service = _FakeService()

    router = CompatRouter()
    # Unaffected by the change — getattr/MRO lookup is untouched.
    assert getattr(router, "_service", None) is not None
    # New accessor must resolve the exact same class-level value.
    assert router.service is router._service


# ── test_service_property_raises_when_unset ─────────────────────────────────────


async def test_service_property_raises_when_unset():
    """router.service must raise RuntimeError (with a clear message) when unset.

    The property's whole point is a non-Optional, ergonomic accessor — silently
    returning None (or raising the wrong exception type) would defeat that purpose.
    """

    class UnsetServiceRouter(VarcoCRUDRouter[_Rule, UUID, _RC, _RR, _RU]):
        _prefix = "/unset"
        _service = None

    router = UnsetServiceRouter()
    with pytest.raises(RuntimeError, match="service"):
        _ = router.service


# ── test_resolve_type_args_ignores_service_arg ──────────────────────────────────


async def test_resolve_type_args_ignores_service_arg():
    """_resolve_type_args on a 6-arg subclass must return only the 5 model args.

    Guards against the service type leaking into CRUD model resolution (task
    payload (de)serialization, PK coercion, etc. at crud.py:280 all index
    0..4 assuming exactly (D, PK, C, R, U)).
    """

    class RuleRouterSix(VarcoCRUDRouter[_Rule, UUID, _RC, _RR, _RU, _RuleService]):
        _prefix = "/rules6"

    resolved = _resolve_type_args(RuleRouterSix)
    assert resolved is not None
    assert len(resolved) == 5
    assert resolved == (_Rule, UUID, _RC, _RR, _RU)
