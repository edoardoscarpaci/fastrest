"""
tests.test_client_typed_routes
================================
Plan 009, Phase 7 (C2) — typed custom-route client methods.

RED until ``varco_fastapi/client/method.py`` lands and ``_VarcoClientMeta``
(``client/base.py``) builds custom-route methods through ``build_client_method``.

``test_resolver_parity`` is one of the two explicitly load-bearing parity
tests named in the plan's Verification section — it must never be deleted;
it is the only thing enforcing "identical typed surface whether the peer's
router class is importable or only its exported contract is available".
"""

from __future__ import annotations

import inspect

import pytest

from tests.fixtures.routers import OrderRouter


class TestBuildClientMethodSignature:
    def test_cancel_method_has_expected_signature(self) -> None:
        from varco_fastapi.client.method import (
            ImportedTypeResolver,
            build_client_method,
        )
        from varco_fastapi.contract.build import build_contract

        contract = build_contract(OrderRouter, service_name="orders")
        route = contract.route("cancel")
        resolver = ImportedTypeResolver(contract, OrderRouter)

        method = build_client_method(route, resolver)
        sig = inspect.signature(method)

        params = sig.parameters
        assert "order_id" in params
        assert "limit" in params
        assert "reason" in params
        assert "with_async" in params
        # Everything except the body param must be keyword-only.
        assert params["order_id"].kind == inspect.Parameter.KEYWORD_ONLY
        assert params["limit"].kind == inspect.Parameter.KEYWORD_ONLY

    def test_wrong_kwarg_raises_type_error(self) -> None:
        from varco_fastapi.client.method import (
            ImportedTypeResolver,
            build_client_method,
        )
        from varco_fastapi.contract.build import build_contract

        contract = build_contract(OrderRouter, service_name="orders")
        route = contract.route("cancel")
        resolver = ImportedTypeResolver(contract, OrderRouter)
        method = build_client_method(route, resolver)
        sig = inspect.signature(method)

        with pytest.raises(TypeError):
            sig.bind(self=object(), not_a_real_param=1)

    def test_zero_param_route_signature(self) -> None:
        from varco_fastapi.client.method import (
            ImportedTypeResolver,
            build_client_method,
        )
        from varco_fastapi.contract.build import build_contract

        contract = build_contract(OrderRouter, service_name="orders")
        list_route = contract.route("list") if _has_route(contract, "list") else None
        if list_route is None:
            pytest.skip("no zero-param route on this fixture router")
        resolver = ImportedTypeResolver(contract, OrderRouter)
        method = build_client_method(list_route, resolver)
        sig = inspect.signature(method)
        assert "with_async" in sig.parameters


def _has_route(contract, name: str) -> bool:
    try:
        contract.route(name)
        return True
    except KeyError:
        return False


class TestResolverParity:
    def test_resolver_parity(self) -> None:
        """LOAD-BEARING (see Plan 009 Verification section): a client built via
        SynthesizedTypeResolver (cross-repo, from an exported .contract.json)
        must produce a signature EQUAL to the one built via ImportedTypeResolver
        (in-process, from the real router class) for the same route.

        This is the enforcement mechanism for "same typed surface either way" —
        both resolvers feed the same build_client_method(). If this test is
        ever deleted the parity guarantee silently degrades to documentation.
        """
        from varco_fastapi.client.method import (
            ImportedTypeResolver,
            SynthesizedTypeResolver,
            build_client_method,
        )
        from varco_fastapi.contract.build import build_contract

        contract = build_contract(OrderRouter, service_name="orders")
        route = contract.route("cancel")

        imported_method = build_client_method(route, ImportedTypeResolver(contract, OrderRouter))
        synthesized_method = build_client_method(route, SynthesizedTypeResolver(contract))

        imported_sig = inspect.signature(imported_method)
        synthesized_sig = inspect.signature(synthesized_method)

        assert list(imported_sig.parameters.keys()) == list(synthesized_sig.parameters.keys())
        for name in imported_sig.parameters:
            assert imported_sig.parameters[name].kind == synthesized_sig.parameters[name].kind
