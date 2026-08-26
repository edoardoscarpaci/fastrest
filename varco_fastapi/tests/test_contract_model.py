"""
tests.test_contract_model
===========================
Plan 009, Phase 0 (C3 part 1) — varco_fastapi.contract.model.

RED until ``varco_fastapi/contract/model.py`` lands.
"""

from __future__ import annotations

import dataclasses

import pytest


class TestServiceContractRoundTrip:
    def test_to_json_from_json_round_trip_equality(self) -> None:
        from varco_fastapi.contract.model import (
            CONTRACT_VERSION,
            ParamContract,
            RouteContract,
            ServiceContract,
        )

        contract = ServiceContract(
            contract_version=CONTRACT_VERSION,
            service_name="orders",
            routes=(
                RouteContract(
                    name="cancel",
                    method="POST",
                    path="/orders/{order_id}/cancel",
                    params=(
                        ParamContract(name="order_id", kind="path", schema={"type": "string"}),
                        ParamContract(name="limit", kind="query", schema={"type": "integer"}),
                    ),
                    crud_action=None,
                ),
            ),
            schemas={"OrderCreate": {"type": "object"}},
            base_path="",
            service_version="1.0.0",
        )

        raw = contract.to_json()
        restored = ServiceContract.from_json(raw)

        assert restored == contract

    def test_to_dict_from_dict_round_trip_equality(self) -> None:
        from varco_fastapi.contract.model import CONTRACT_VERSION, ServiceContract

        contract = ServiceContract(
            contract_version=CONTRACT_VERSION,
            service_name="empty",
            routes=(),
        )
        restored = ServiceContract.from_dict(contract.to_dict())
        assert restored == contract


class TestServiceContractVersioning:
    def test_major_version_bump_raises_contract_version_error(self) -> None:
        from varco_fastapi.contract.model import ContractVersionError, ServiceContract

        data = {
            "contract_version": "2.0",
            "service_name": "orders",
            "routes": [],
        }
        with pytest.raises(ContractVersionError):
            ServiceContract.from_dict(data)

    def test_unknown_minor_version_logs_warning_and_parses(self, caplog) -> None:
        import logging

        from varco_fastapi.contract.model import CONTRACT_VERSION, ServiceContract

        major = CONTRACT_VERSION.split(".")[0]
        data = {
            "contract_version": f"{major}.999",
            "service_name": "orders",
            "routes": [],
        }
        with caplog.at_level(logging.WARNING):
            contract = ServiceContract.from_dict(data)
        assert contract.service_name == "orders"
        assert any("999" in r.message or "minor" in r.message.lower() for r in caplog.records)


class TestServiceContractFrozen:
    def test_service_contract_is_frozen(self) -> None:
        from varco_fastapi.contract.model import CONTRACT_VERSION, ServiceContract

        contract = ServiceContract(
            contract_version=CONTRACT_VERSION, service_name="orders", routes=()
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            contract.service_name = "changed"  # type: ignore[misc]

    def test_route_contract_is_frozen(self) -> None:
        from varco_fastapi.contract.model import RouteContract

        route_c = RouteContract(name="get", method="GET", path="/orders/{id}")
        with pytest.raises(dataclasses.FrozenInstanceError):
            route_c.method = "POST"  # type: ignore[misc]

    def test_param_contract_is_frozen(self) -> None:
        from varco_fastapi.contract.model import ParamContract

        param = ParamContract(name="id", kind="path", schema={"type": "string"})
        with pytest.raises(dataclasses.FrozenInstanceError):
            param.kind = "query"  # type: ignore[misc]


class TestServiceContractRouteLookup:
    def test_route_returns_matching_route_contract(self) -> None:
        from varco_fastapi.contract.model import (
            CONTRACT_VERSION,
            RouteContract,
            ServiceContract,
        )

        route_c = RouteContract(name="cancel", method="POST", path="/orders/{id}/cancel")
        contract = ServiceContract(
            contract_version=CONTRACT_VERSION,
            service_name="orders",
            routes=(route_c,),
        )
        assert contract.route("cancel") is route_c

    def test_route_unknown_name_raises_key_error_listing_routes(self) -> None:
        from varco_fastapi.contract.model import (
            CONTRACT_VERSION,
            RouteContract,
            ServiceContract,
        )

        contract = ServiceContract(
            contract_version=CONTRACT_VERSION,
            service_name="orders",
            routes=(RouteContract(name="cancel", method="POST", path="/orders/{id}/cancel"),),
        )
        with pytest.raises(KeyError, match="cancel"):
            contract.route("does-not-exist")
