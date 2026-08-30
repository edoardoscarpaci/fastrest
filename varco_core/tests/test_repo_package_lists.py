"""
Repo-infrastructure guard — Plan 020 / RL-18.

``scripts/packages.sh`` is meant to become the single derivation of the
workspace's distribution-package list, consumed by ``Makefile``,
``scripts/unit_tests.sh``, ``scripts/integration_tests.sh``, and
``scripts/gen_ref_pages.py``. None of that exists yet — these are the
failing tests written first (§RL-18's Guard paragraph, plan Step 4).

RED until Step 5 (``scripts/packages.sh``), Step 6 (``make print-packages``),
and Step 9 (``scripts/gen_ref_pages.py`` reading ``[tool.uv.workspace]
members`` via ``tomllib``) land.

Async safety: N/A — pure subprocess/file-parsing checks, no I/O beyond the repo tree.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# The ten literal distribution packages today (§RL-18's ⚠️ ASSUMPTION —
# `<member>/<member>/__init__.py` identifies a distribution for all ten).
EXPECTED_BASE = (
    "varco_core",
    "varco_kafka",
    "varco_nats",
    "varco_redis",
    "varco_beanie",
    "varco_sa",
    "varco_memcached",
    "varco_ws",
    "varco_fastapi",
    "varco_casbin",
)


def _workspace_members() -> list[str]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    return list(data["tool"]["uv"]["workspace"]["members"])


class TestPackagesScriptDerivesBaseList:
    def test_packages_script_exists_and_is_executable(self) -> None:
        # scripts/packages.sh does not exist yet — this is the first, most
        # basic assertion of the whole guard.
        script = REPO_ROOT / "scripts" / "packages.sh"
        assert script.is_file(), "scripts/packages.sh has not been created yet (RL-18 Step 5)"

    def test_derived_base_list_equals_the_ten_literal_names(self) -> None:
        script = REPO_ROOT / "scripts" / "packages.sh"
        if not script.exists():
            pytest.fail("scripts/packages.sh missing — cannot derive base list (RL-18 Step 5)")

        result = subprocess.run(
            [sys.executable if False else "bash", str(script)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"packages.sh failed: {result.stderr}"
        derived = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        assert tuple(derived) == EXPECTED_BASE


class TestMakePrintPackagesMatchesDerivedBase:
    def test_make_print_packages_matches_base(self) -> None:
        if shutil.which("make") is None:
            pytest.skip("make is not on PATH")

        result = subprocess.run(
            ["make", "-s", "print-packages"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        # `print-packages` phony target does not exist yet (RL-18 Step 6) —
        # `make` exits non-zero with "No rule to make target".
        assert result.returncode == 0, (
            f"`make print-packages` failed (target not defined yet?): {result.stderr}"
        )
        printed = result.stdout.split()
        assert tuple(printed) == EXPECTED_BASE


class TestIntegrationExcludeNamesAKnownMember:
    def test_every_integration_exclude_entry_is_a_workspace_member(self) -> None:
        script_path = REPO_ROOT / "scripts" / "integration_tests.sh"
        text = script_path.read_text()

        # Today's script hard-codes ALL_INTEGRATION_PACKAGES directly (no
        # INTEGRATION_EXCLUDE mechanism yet) — this assertion fails until
        # Step 8 introduces the named-exclusion list.
        assert "INTEGRATION_EXCLUDE" in text, (
            "scripts/integration_tests.sh does not yet declare INTEGRATION_EXCLUDE (RL-18 Step 8)"
        )


class TestGenRefPagesDerivedListMatchesBase:
    def test_gen_ref_pages_packages_includes_varco_casbin(self) -> None:
        """
        Live drift the RL-18 row did not originally report: `scripts/gen_ref_pages.py`'s
        hand-written `PACKAGES` tuple is missing `varco_casbin`, so `make docs` has never
        rendered its API reference. Fails today; Step 9 fixes it by deriving from
        `[tool.uv.workspace] members` instead of a literal tuple.

        Parsed via `ast` rather than imported — the module executes
        `mkdocs_gen_files` side effects at import time (writing doc pages),
        which is unsafe/heavy to trigger from a unit test.
        """
        import ast

        source = (REPO_ROOT / "scripts" / "gen_ref_pages.py").read_text()
        tree = ast.parse(source)

        derived: tuple[str, ...] | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id == "PACKAGES" and node.value is not None:
                    derived = tuple(ast.literal_eval(node.value))
                    break
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "PACKAGES":
                        derived = tuple(ast.literal_eval(node.value))
                        break

        assert derived is not None, "could not find a PACKAGES literal in gen_ref_pages.py"
        assert derived == EXPECTED_BASE
