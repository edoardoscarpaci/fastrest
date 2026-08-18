"""
tests.test_client_stubs
=========================
Plan 009, Phase 7 (C2) — varco_fastapi.client.stubs (``.pyi`` emitter).

RED until ``varco_fastapi/client/stubs.py`` lands.
"""

from __future__ import annotations

import ast


from tests.fixtures.routers import OrderRouter


class TestRenderStub:
    def test_render_stub_output_parses(self) -> None:
        from varco_fastapi.client.stubs import render_stub
        from varco_fastapi.contract.build import build_contract

        contract = build_contract(OrderRouter, service_name="orders")
        stub_src = render_stub(contract, class_name="OrderClient")

        ast.parse(stub_src)

    def test_render_stub_contains_class_and_method(self) -> None:
        from varco_fastapi.client.stubs import render_stub
        from varco_fastapi.contract.build import build_contract

        contract = build_contract(OrderRouter, service_name="orders")
        stub_src = render_stub(contract, class_name="OrderClient")

        assert "class OrderClient" in stub_src
        assert "def cancel" in stub_src


class TestWriteStub:
    def test_write_stub_writes_file(self, tmp_path) -> None:
        from varco_fastapi.client.stubs import write_stub
        from varco_fastapi.contract.build import build_contract

        contract = build_contract(OrderRouter, service_name="orders")
        out = tmp_path / "client.pyi"
        write_stub(contract, out, class_name="OrderClient")

        assert out.exists()
        assert "class OrderClient" in out.read_text()


class TestStubCheckMode:
    def test_check_mode_detects_drift(self, tmp_path) -> None:
        """--check mode (surfaced via the CLI in Phase 8) must be able to
        detect a stub file that no longer matches the contract."""
        from varco_fastapi.client.stubs import render_stub
        from varco_fastapi.contract.build import build_contract

        contract = build_contract(OrderRouter, service_name="orders")
        fresh = render_stub(contract, class_name="OrderClient")
        stale = fresh.replace("def cancel", "def cancel_renamed")

        assert fresh != stale
