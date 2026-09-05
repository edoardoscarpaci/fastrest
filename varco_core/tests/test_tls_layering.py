"""
Plan 026 / Step 9 — import-layering guard for ``varco_core.tls``.

``varco_core.tls`` must never import ``varco_core.connection`` (no-cycle claim, §D-T3-bridge),
``varco_fastapi``, or any backend package (``varco_kafka``/``varco_redis``/``varco_sa``/
``varco_beanie``/...).

A ``sys.modules``-walk (even from a fresh subprocess) cannot detect this reliably in the full
suite: countless other tests import ``varco_core.connection`` before ``test_tls_layering.py``
runs in the same pytest process, so ``varco_core.connection`` is typically already present in
``sys.modules`` by the time ``varco_core.tls`` finishes importing — regardless of whether
``varco_core.tls`` itself imports it. (Note: as of Plan 028/P1a, ``varco_core/__init__.py`` is
PEP 562-lazy and does *not* itself eagerly import ``varco_core.connection`` — that used to be
this docstring's stated cause, but it was never the only one and is no longer true.) This makes
a runtime-import oracle structurally unusable here.

So this uses the AST-inspection alternative Step 9 itself names: walk every ``.py`` file under
``varco_core/varco_core/tls/`` and assert no **module-level, non-``TYPE_CHECKING``** import
statement references a forbidden package. A ``TYPE_CHECKING``-guarded import (e.g.
``store.py``'s import of ``varco_core.connection.ssl.SSLConfig`` for the ``to_ssl_config()``
type hint) and a deferred function-local import (``to_ssl_config()``'s own import inside its
body) are both legitimate and must be tolerated — only an unconditional, module-level import
would create the runtime import cycle / layer violation this guard exists to catch.
"""

from __future__ import annotations

import ast
from pathlib import Path

_FORBIDDEN_PREFIXES = (
    "varco_core.connection",
    "varco_fastapi",
    "varco_kafka",
    "varco_redis",
    "varco_sa",
    "varco_beanie",
    "varco_memcached",
    "varco_ws",
    "varco_casbin",
    "varco_nats",
)


def _is_forbidden(module_name: str | None) -> bool:
    if module_name is None:
        return False
    return module_name.startswith(_FORBIDDEN_PREFIXES)


def _is_type_checking_guard(node: ast.If) -> bool:
    """``if TYPE_CHECKING:`` or ``if typing.TYPE_CHECKING:`` — either spelling."""
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    return False


def _module_level_forbidden_imports(source: str, path: Path) -> list[str]:
    """
    Walk ``source``'s top-level statements only (not function bodies, not TYPE_CHECKING
    blocks) and collect a description of every forbidden import found there.
    """
    tree = ast.parse(source, filename=str(path))
    violations: list[str] = []

    for node in tree.body:  # top-level only — deferred imports live inside function bodies
        if isinstance(node, ast.If) and _is_type_checking_guard(node):
            continue  # TYPE_CHECKING-guarded imports never run at import time — allowed
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden(alias.name):
                    violations.append(f"{path}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module
            if node.level == 0 and _is_forbidden(module):
                violations.append(f"{path}: from {module} import ...")

    return violations


def test_varco_core_tls_has_no_module_level_forbidden_imports() -> None:
    import varco_core.tls as tls_pkg

    tls_dir = Path(tls_pkg.__file__).parent
    py_files = sorted(tls_dir.glob("*.py"))
    assert py_files, f"expected .py files under {tls_dir}"

    violations: list[str] = []
    for py_file in py_files:
        violations.extend(_module_level_forbidden_imports(py_file.read_text(), py_file))

    assert violations == [], (
        "varco_core.tls has a module-level import of a forbidden package "
        f"(layer rule / §D-T3-bridge no-cycle claim violated): {violations}"
    )
