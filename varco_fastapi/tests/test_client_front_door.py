"""
tests.test_client_front_door
==============================
Plan 009, Phase 3 (C1) — varco_fastapi.client.front_door.

RED until ``varco_fastapi/client/front_door.py`` and
``varco_fastapi/client/advanced.py`` land, and ``varco_fastapi.client``'s
``__all__``/``__getattr__`` shim demotes ``make_client``/``GenericClient``/
``OpenAPIClient``/``ClientConfigurator``/``generate_client``.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from tests.fixtures.routers import OrderRouter


class TestClientFor:
    async def test_client_for_returns_working_client_against_asgi_transport(
        self,
    ) -> None:
        from varco_fastapi.client.front_door import client_for

        app = FastAPI()
        app.include_router(OrderRouter(service=None).build_router())

        client = client_for(OrderRouter, base_url="http://testserver")
        # Point the underlying transport at the ASGI app instead of the
        # network. httpx.AsyncClient has no public `.transport` attribute
        # (only the private `_transport` it actually dispatches through —
        # see httpx._client.AsyncClient); the original `.transport =`
        # assignment was a silent no-op that always hit the real network.
        client._client._transport = httpx.ASGITransport(app=app)  # type: ignore[attr-defined]

        resp = await client._client.get("/orders")  # type: ignore[attr-defined]
        assert resp.status_code in (200, 501, 422)

    def test_client_for_non_router_raises_type_error_naming_varco_router(self) -> None:
        from varco_fastapi.client.front_door import client_for

        class NotARouter:
            pass

        with pytest.raises(TypeError, match="VarcoRouter"):
            client_for(NotARouter, base_url="http://testserver")


class TestClientClassForMemoization:
    def test_repeated_calls_return_the_identical_class(self) -> None:
        from varco_fastapi.client.front_door import client_class_for

        cls_a = client_class_for(OrderRouter)
        cls_b = client_class_for(OrderRouter)
        assert cls_a is cls_b


class TestBindClientsFrom:
    async def test_bind_clients_from_resolves_and_passes_validate_bindings(
        self,
    ) -> None:
        from providify import DIContainer
        from varco_conformance.providify_health import assert_no_structural_di_issues
        from varco_fastapi.client.base import VarcoClient
        from varco_fastapi.di import bind_clients_from

        container = DIContainer()
        bind_clients_from(container, OrderRouter)

        resolved = await container.aget(VarcoClient[OrderRouter])
        assert resolved is not None
        container.validate_bindings()
        assert_no_structural_di_issues(container)


class TestDemotedNamesShim:
    @pytest.mark.parametrize(
        "name",
        [
            "make_client",
            "GenericClient",
            "OpenAPIClient",
            "ClientConfigurator",
            "generate_client",
        ],
    )
    def test_demoted_name_raises_attribute_error_naming_new_path(self, name: str) -> None:
        import varco_fastapi.client as client_pkg

        with pytest.raises(AttributeError, match="advanced"):
            getattr(client_pkg, name)

    @pytest.mark.parametrize(
        "name",
        [
            "make_client",
            "GenericClient",
            "OpenAPIClient",
            "ClientConfigurator",
            "generate_client",
        ],
    )
    def test_demoted_name_importable_from_advanced_shelf(self, name: str) -> None:
        import varco_fastapi.client.advanced as advanced

        assert hasattr(advanced, name)

    def test_front_door_names_still_in_all(self) -> None:
        import varco_fastapi.client as client_pkg

        assert "client_for" in client_pkg.__all__
        assert "client_class_for" in client_pkg.__all__
        assert "make_client" not in client_pkg.__all__
        assert "GenericClient" not in client_pkg.__all__
