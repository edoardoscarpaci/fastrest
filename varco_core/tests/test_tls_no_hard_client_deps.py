"""
Plan 027 / Step 10 — structural guard: ``varco_core.tls.clients`` must never import
httpx/aiohttp/urllib3/requests at module scope (§D-T4-adapters, locked BACKLOG.md:37).

``varco_core/varco_core/tls/clients.py`` does not exist yet — the first test fails with
``FileNotFoundError`` and the second fails with ``ModuleNotFoundError`` (importing
``varco_core.tls`` today does not yet re-export anything from ``clients.py``, but more
fundamentally the subprocess assertion about the four libraries staying out of
``sys.modules`` has nothing to key off until the module exists and is wired into
``varco_core.tls.__init__``).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_CLIENTS_MODULE = Path(__file__).resolve().parents[1] / "varco_core" / "tls" / "clients.py"
_WATCHED_LIBRARIES = {"httpx", "aiohttp", "urllib3", "requests"}


def _top_level_module(name: str) -> str:
    return name.split(".")[0]


def test_client_library_imports_are_all_inside_function_bodies() -> None:
    source = _CLIENTS_MODULE.read_text()
    tree = ast.parse(source, filename=str(_CLIENTS_MODULE))

    module_level_offenders: list[str] = []
    function_level_hits: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [_top_level_module(alias.name) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [_top_level_module(node.module)] if node.module else []
        else:
            continue

        watched = [n for n in names if n in _WATCHED_LIBRARIES]
        if not watched:
            continue

        # Walk up the module to see if this import node is nested inside any
        # FunctionDef/AsyncFunctionDef — a plain ast.walk() gives us no parent pointers,
        # so we re-parse via a parent-tracking pass instead.
        function_level_hits.extend(watched)

    # Re-derive strictly: any Import/ImportFrom of a watched library found directly under
    # ast.Module.body (i.e. NOT nested inside a function) is a module-level offender.
    for node in tree.body:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Import):
                names = [_top_level_module(alias.name) for alias in sub.names]
            elif isinstance(sub, ast.ImportFrom):
                names = [_top_level_module(sub.module)] if sub.module else []
            else:
                continue
            watched = [n for n in names if n in _WATCHED_LIBRARIES]
            if watched and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                module_level_offenders.extend(watched)

    assert function_level_hits, (
        "expected at least one of httpx/aiohttp/urllib3/requests to be imported "
        "somewhere in clients.py"
    )
    assert not module_level_offenders, (
        f"found module-level (not function-body) imports of: {module_level_offenders}"
    )


def test_importing_varco_core_tls_leaves_all_four_clients_out_of_sys_modules() -> None:
    script = (
        "import sys\n"
        "import varco_core.tls\n"
        "watched = {'httpx', 'aiohttp', 'urllib3', 'requests'}\n"
        "leaked = watched & set(sys.modules)\n"
        "assert not leaked, f'leaked into sys.modules: {leaked}'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
