"""
Plan 031 (D4c) / Step 14 — structural guard: the webhook HTTP send path must
never import ``httpx`` at module scope. Modelled directly on
``varco_core/tests/test_tls_no_hard_client_deps.py``.

``varco_core/varco_core/webhook/transport.py`` (or wherever the HTTP send
path lives) does not exist yet — the first test fails with
``FileNotFoundError``.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_TRANSPORT_MODULE = Path(__file__).resolve().parents[1] / "varco_core" / "webhook" / "transport.py"
_WATCHED_LIBRARIES = {"httpx"}


def _top_level_module(name: str) -> str:
    return name.split(".")[0]


def test_httpx_import_is_inside_a_function_body_never_module_scope() -> None:
    source = _TRANSPORT_MODULE.read_text()
    tree = ast.parse(source, filename=str(_TRANSPORT_MODULE))

    function_level_hits: list[str] = []
    module_level_offenders: list[str] = []

    for node in tree.body:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Import):
                names = [_top_level_module(alias.name) for alias in sub.names]
            elif isinstance(sub, ast.ImportFrom):
                names = [_top_level_module(sub.module)] if sub.module else []
            else:
                continue
            watched = [n for n in names if n in _WATCHED_LIBRARIES]
            if not watched:
                continue
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function_level_hits.extend(watched)
            else:
                module_level_offenders.extend(watched)

    assert function_level_hits, "expected httpx to be imported somewhere in transport.py"
    assert not module_level_offenders, (
        f"found module-level (not function-body) imports of: {module_level_offenders}"
    )


def test_importing_varco_core_webhook_leaves_httpx_out_of_sys_modules() -> None:
    script = (
        "import sys\n"
        "import varco_core.webhook\n"
        "assert 'httpx' not in sys.modules, 'httpx leaked into sys.modules'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
