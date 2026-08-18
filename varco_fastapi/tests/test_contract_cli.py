"""
tests.test_contract_cli
=========================
Plan 009, Phase 8 (C3 part 2) — ``varco export-contract`` /
``varco gen-client`` / ``varco gen-client-stubs`` CLI subcommands
(``varco_fastapi/contract/cli.py``), registered via the ``varco.commands``
entry-point group.

RED until ``varco_fastapi/contract/cli.py`` lands and is registered as an
entry point.
"""

from __future__ import annotations

import json


class TestExportContractCli:
    def test_export_contract_writes_parseable_contract(self, tmp_path) -> None:
        from varco_core.cli.main import main

        out = tmp_path / "order.contract.json"
        exit_code = main(
            [
                "export-contract",
                "tests.fixtures.routers:OrderRouter",
                "-o",
                str(out),
            ]
        )
        assert exit_code == 0
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["service_name"]

    def test_export_contract_stdout_mode(self, capsys) -> None:
        from varco_core.cli.main import main

        exit_code = main(["export-contract", "tests.fixtures.routers:OrderRouter"])
        assert exit_code == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "routes" in data

    def test_export_contract_bad_import_exits_2(self, capsys) -> None:
        from varco_core.cli.main import main

        exit_code = main(["export-contract", "tests.fixtures.routers:DoesNotExist"])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert "DoesNotExist" in (captured.err + captured.out)

    def test_export_contract_non_router_target_exits_2(self, capsys) -> None:
        from varco_core.cli.main import main

        exit_code = main(["export-contract", "tests.fixtures.routers:Order"])
        assert exit_code == 2
        captured = capsys.readouterr()
        assert "Order" in (captured.err + captured.out)


class TestGenClientCli:
    def test_gen_client_writes_module(self, tmp_path) -> None:
        from varco_core.cli.main import main
        from varco_fastapi.contract.build import build_contract
        from tests.fixtures.routers import OrderRouter

        contract_path = tmp_path / "order.contract.json"
        contract = build_contract(OrderRouter, service_name="orders")
        contract_path.write_text(contract.to_json())

        out = tmp_path / "order_client.py"
        exit_code = main(
            [
                "gen-client",
                "-c",
                str(contract_path),
                "-o",
                str(out),
                "--class-name",
                "OrderClient",
            ]
        )
        assert exit_code == 0
        assert out.exists()


class TestGenClientStubsCli:
    def test_gen_client_stubs_check_mode_on_drift_exits_1(self, tmp_path) -> None:
        from varco_core.cli.main import main

        stale_stub = tmp_path / "client.pyi"
        stale_stub.write_text("class OrderClient:\n    pass\n")

        exit_code = main(
            [
                "gen-client-stubs",
                "tests.fixtures.routers:OrderRouter",
                "-o",
                str(stale_stub),
                "--check",
            ]
        )
        assert exit_code == 1
