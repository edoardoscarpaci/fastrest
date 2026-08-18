"""
varco_fastapi.contract.cli
=============================
``export-contract`` / ``gen-client`` / ``gen-client-stubs`` — contributed to
the ``varco`` CLI via the ``varco.commands`` entry-point group (Plan 009,
Phase 8 / C3 part 2). ``varco_core`` cannot import ``varco_fastapi``
(dependency graph), so these subcommands live here, not in
``varco_core/cli/main.py`` — ``main.py:42`` already discovers them.

```
varco export-contract app.routers:OrderRouter [-o order.contract.json]
                      [--service-name N] [--service-version V] [--strict]
varco gen-client       -c order.contract.json -o order_client.py [--class-name OrderClient]
varco gen-client-stubs (app.routers:OrderRouter | -c order.contract.json)
                       -o client.pyi [--check]
```

Exit codes: ``0`` ok, ``1`` drift detected (``--check``), ``2`` usage error.

Thread safety:  N/A — a one-shot CLI process.
Async safety:   N/A — synchronous; no I/O beyond module import + file writes.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from typing import Any


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register ``export-contract``, ``gen-client``, ``gen-client-stubs``."""
    export_p = subparsers.add_parser(
        "export-contract", help="Export a router's ServiceContract to JSON"
    )
    export_p.set_defaults(_run=_run_export_contract)
    export_p.add_argument("target", help="module:RouterClass")
    export_p.add_argument(
        "-o", "--output", default=None, help="Output path (default: stdout)"
    )
    export_p.add_argument("--service-name", default=None)
    export_p.add_argument("--service-version", default=None)
    export_p.add_argument("--strict", action="store_true")

    gen_p = subparsers.add_parser(
        "gen-client", help="Generate a standalone typed client module"
    )
    gen_p.set_defaults(_run=_run_gen_client)
    gen_p.add_argument(
        "-c", "--contract", required=True, help="Path to a .contract.json"
    )
    gen_p.add_argument("-o", "--output", required=True, help="Output .py path")
    gen_p.add_argument("--class-name", default=None)

    stubs_p = subparsers.add_parser(
        "gen-client-stubs", help="Generate/check a .pyi client stub"
    )
    stubs_p.set_defaults(_run=_run_gen_client_stubs)
    stubs_p.add_argument("target", nargs="?", default=None, help="module:RouterClass")
    stubs_p.add_argument(
        "-c",
        "--contract",
        default=None,
        help="Path to a .contract.json (alternative to target)",
    )
    stubs_p.add_argument("-o", "--output", required=True, help="Output .pyi path")
    stubs_p.add_argument("--class-name", default=None)
    stubs_p.add_argument(
        "--check", action="store_true", help="Exit 1 on drift instead of writing"
    )


def _resolve_target(target: str) -> Any:
    """Resolve ``module:ClassName`` → the class object, or ``None``."""
    if ":" not in target:
        return None
    module_name, _, attr_name = target.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None
    return getattr(module, attr_name, None)


def _run_export_contract(args: argparse.Namespace) -> int:
    from varco_fastapi.contract.build import build_contract
    from varco_fastapi.router.base import VarcoRouter

    router_cls = _resolve_target(args.target)
    if router_cls is None:
        print(f"Could not import {args.target!r}.", file=sys.stderr)
        return 2
    if not (isinstance(router_cls, type) and issubclass(router_cls, VarcoRouter)):
        print(
            f"{args.target!r} is not a VarcoRouter subclass (got {router_cls!r}).",
            file=sys.stderr,
        )
        return 2

    contract = build_contract(
        router_cls,
        service_name=args.service_name,
        service_version=args.service_version,
        strict=args.strict,
    )
    raw = contract.to_json()
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(raw)
    else:
        print(raw)
    return 0


def _run_gen_client(args: argparse.Namespace) -> int:
    from pathlib import Path

    from varco_fastapi.contract.codegen import render_client_module
    from varco_fastapi.contract.model import ServiceContract

    contract = ServiceContract.from_json(Path(args.contract).read_text())
    class_name = args.class_name or f"{contract.service_name}Client"
    source = render_client_module(contract, class_name=class_name)
    Path(args.output).write_text(source)
    return 0


def _run_gen_client_stubs(args: argparse.Namespace) -> int:
    from pathlib import Path

    from varco_fastapi.client.stubs import render_stub

    if args.contract:
        from varco_fastapi.contract.model import ServiceContract

        contract = ServiceContract.from_json(Path(args.contract).read_text())
    elif args.target:
        from varco_fastapi.contract.build import build_contract

        router_cls = _resolve_target(args.target)
        if router_cls is None:
            print(f"Could not import {args.target!r}.", file=sys.stderr)
            return 2
        contract = build_contract(router_cls)
    else:
        print(
            "Specify either a target (module:Router) or -c/--contract.", file=sys.stderr
        )
        return 2

    class_name = args.class_name or f"{contract.service_name}Client"
    fresh = render_stub(contract, class_name=class_name)

    out_path = Path(args.output)
    if args.check:
        existing = out_path.read_text() if out_path.exists() else ""
        if existing != fresh:
            print(
                f"Stub {out_path} is stale relative to the current contract.",
                file=sys.stderr,
            )
            return 1
        return 0

    out_path.write_text(fresh)
    return 0


__all__ = ["register"]
