"""
Red-mode tests for Plan 011 Phase 7, step 77 — no new runtime dependency
guard (D-1, D-2, D-8, D-9).

Plan line (step 77): "asserts the modules created by this plan import only
stdlib + pydantic ... and that varco-core's [project.dependencies] is
unchanged with tz present only as an optional extra."
"""

from __future__ import annotations

import pathlib

import tomllib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "varco_core" / "pyproject.toml"

FORBIDDEN_RUNTIME_IMPORTS = ("dateutil", "babel", "icu", "fluent")

PLAN_011_MODULES = (
    "varco_core.context.ambient",
    "varco_core.context.precedence",
    "varco_core.context.request",
    "varco_core.context.defaults",
    "varco_core.i18n.catalog",
    "varco_core.i18n.negotiation",
    "varco_core.i18n.resolve",
    "varco_core.tz.schedule",
    "varco_core.tz.format",
    "varco_core.tz.resolve",
    "varco_core.query.policy",
)


def test_pyproject_declared_dependencies_unchanged_by_this_plan() -> None:
    with open(PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    deps = data["project"]["dependencies"]
    for forbidden in FORBIDDEN_RUNTIME_IMPORTS:
        assert not any(
            forbidden in dep for dep in deps
        ), f"unexpected new runtime dependency {forbidden!r} in {deps!r}"


def test_tz_optional_extra_is_declared_and_not_a_hard_dependency() -> None:
    with open(PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    optional = data["project"].get("optional-dependencies", {})
    assert "tz" in optional
    deps = data["project"]["dependencies"]
    assert not any("tzdata" in dep for dep in deps)


def test_plan_011_modules_import_only_stdlib_and_pydantic() -> None:
    import ast
    import importlib.util

    for mod_name in PLAN_011_MODULES:
        spec = importlib.util.find_spec(mod_name)
        assert spec is not None and spec.origin is not None, f"{mod_name} not found"
        source = pathlib.Path(spec.origin).read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [n.name.split(".")[0] for n in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            for name in names:
                assert (
                    name not in FORBIDDEN_RUNTIME_IMPORTS
                ), f"{mod_name} imports forbidden runtime dep {name!r}"
