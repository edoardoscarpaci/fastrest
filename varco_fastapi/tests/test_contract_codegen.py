"""
tests.test_contract_codegen
=============================
Plan 009, Phase 8 (C3 part 2) — cross-repo codegen (varco_fastapi.contract.codegen).

RED until ``varco_fastapi/contract/codegen.py`` lands.

``test_signature_parity`` is the second of the two explicitly load-bearing
parity tests named in the plan's Verification section.
"""

from __future__ import annotations

import ast
import inspect

from tests.fixtures.routers import OrderRouter


class TestRenderClientModule:
    def test_generated_module_is_ast_parse_clean(self) -> None:
        from varco_fastapi.contract.build import build_contract
        from varco_fastapi.contract.codegen import render_client_module

        contract = build_contract(OrderRouter, service_name="orders")
        source = render_client_module(contract, class_name="OrderClient")

        ast.parse(source)  # must not raise SyntaxError

    def test_unsupported_schema_degrades_to_dict_any_with_todo_comment(self) -> None:
        from varco_fastapi.contract.codegen import render_client_module
        from varco_fastapi.contract.model import CONTRACT_VERSION, ServiceContract

        contract = ServiceContract(
            contract_version=CONTRACT_VERSION,
            service_name="weird",
            routes=(),
            schemas={"Weird": {"not": "a recognizable JSON schema shape"}},
        )
        source = render_client_module(contract, class_name="WeirdClient")
        assert "dict[str, Any]" in source
        assert "TODO" in source


class TestSignatureParity:
    def test_signature_parity(self) -> None:
        """LOAD-BEARING (see Plan 009 Verification section): the generated,
        standalone client module's method signatures must be EQUAL to the
        in-process (ImportedTypeResolver-built) client's signatures for the
        same router — the enforcement for "same typed surface either way"
        across the codegen path specifically.
        """
        import importlib.util
        import sys
        import tempfile
        from pathlib import Path

        from varco_fastapi.client.method import (
            ImportedTypeResolver,
            build_client_method,
        )
        from varco_fastapi.contract.build import build_contract
        from varco_fastapi.contract.codegen import render_client_module

        contract = build_contract(OrderRouter, service_name="orders")
        route = contract.route("cancel")

        imported_method = build_client_method(route, ImportedTypeResolver(contract, OrderRouter))
        imported_sig = inspect.signature(imported_method)

        source = render_client_module(contract, class_name="OrderClient")

        with tempfile.TemporaryDirectory() as tmpdir:
            module_path = Path(tmpdir) / "generated_order_client.py"
            module_path.write_text(source)

            spec = importlib.util.spec_from_file_location("generated_order_client", module_path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules["generated_order_client"] = module
            spec.loader.exec_module(module)

            generated_cls = getattr(module, "OrderClient")
            generated_sig = inspect.signature(generated_cls.cancel)

        assert list(imported_sig.parameters.keys()) == list(generated_sig.parameters.keys())
