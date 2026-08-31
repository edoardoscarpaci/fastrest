"""Tests for ``scripts/bump.py`` — the lockstep-version bump mechanism.

Plan 023 / Phase 1 Step 5, design section §RL-9-bump / §RL-9-pins.

RED MODE: ``scripts/bump.py`` does not exist yet. Every test in this module
must fail — most with an ``ImportError``/``FileNotFoundError`` because the
script is not there, and the two "on the real tree" tests failing on the
*current* (pre-freeze) divergent version set once the script exists.

The script:
  * derives its package list by executing ``scripts/packages.sh`` (never a
    hand-written list — Plan 020 / RL-18);
  * rewrites ``[project].version`` in all ten distribution ``pyproject.toml``
    files;
  * rewrites sibling ``varco-*`` requirement strings inside
    ``[project].dependencies`` and the two shipped
    ``[project.optional-dependencies]`` entries to the canonical
    ``~=<major>.0`` compatible-release pin;
  * never touches ``[dependency-groups]`` sibling entries, ``[tool.uv.sources]``,
    or ``examples/**``;
  * supports ``--set X.Y.Z``, ``--bump major|minor|patch``, ``--dry-run``,
    and ``--check`` (exit 1 on divergence, table naming the divergent
    package).
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "bump.py"
PACKAGES_SH = REPO_ROOT / "scripts" / "packages.sh"

# The ten distribution packages this workspace ships today (verified via
# scripts/packages.sh — "examples" is a workspace member but not a
# distribution and must never appear here).
EXPECTED_PACKAGES = {
    "varco_core",
    "varco_kafka",
    "varco_nats",
    "varco_redis",
    "varco_sa",
    "varco_beanie",
    "varco_memcached",
    "varco_ws",
    "varco_fastapi",
    "varco_casbin",
}


def _load_bump_module() -> Any:
    """Import ``scripts/bump.py`` as a module (same pattern as api_surface.py).

    Loading it directly (rather than shelling out for every assertion) lets
    unit tests exercise its internal TOML-rewrite helpers, not just the CLI.
    """
    spec = importlib.util.spec_from_file_location("varco_bump", SCRIPT_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bump_module() -> Any:
    """The script under test, imported as a module.

    WHY: fails immediately with FileNotFoundError today — scripts/bump.py
    does not exist. This is the "right" failure for red mode.
    """
    return _load_bump_module()


@pytest.fixture
def workspace_copy(tmp_path: Path) -> Path:
    """A throwaway copy of the entire workspace tree for destructive edits.

    WHY: the bump script mutates ``pyproject.toml`` files and runs
    ``uv lock`` — tests must never do this against the real tree except in
    the two tests explicitly marked as reading (not writing) the real tree.
    """
    dest = tmp_path / "workspace"
    shutil.copytree(
        REPO_ROOT,
        dest,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".venv", "dist", "site"),
    )
    return dest


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Invoke scripts/bump.py as a real CLI subprocess against ``cwd``."""
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


class TestScriptExists:
    def test_bump_script_file_exists(self) -> None:
        # Sanity check that gives a clear, single-line failure reason before
        # any other test's ImportError obscures "the file is simply missing".
        assert SCRIPT_PATH.is_file(), f"{SCRIPT_PATH} does not exist yet (Plan 023 Phase 1)"


class TestPackageDerivation:
    def test_package_list_is_derived_from_packages_sh(self, bump_module: Any) -> None:
        # RL-18: the package list must never be hand-written in the script.
        packages = set(bump_module.discover_packages(REPO_ROOT))
        assert packages == EXPECTED_PACKAGES

    def test_examples_is_never_a_target(self, bump_module: Any) -> None:
        packages = set(bump_module.discover_packages(REPO_ROOT))
        assert "examples" not in packages


class TestSetVersion:
    def test_set_rewrites_all_ten_project_versions(
        self, bump_module: Any, workspace_copy: Path
    ) -> None:
        bump_module.set_version(workspace_copy, "3.0.0")
        for pkg in EXPECTED_PACKAGES:
            text = (workspace_copy / pkg / "pyproject.toml").read_text()
            assert 'version       = "3.0.0"' in text or 'version = "3.0.0"' in text

    def test_set_pins_sibling_dependency_requirement(
        self, bump_module: Any, workspace_copy: Path
    ) -> None:
        bump_module.set_version(workspace_copy, "3.0.0")
        text = (workspace_copy / "varco_kafka" / "pyproject.toml").read_text()
        assert '"varco-core~=3.0"' in text
        assert '"varco-core"' not in text

    def test_set_pins_fastapi_ws_optional_extra(
        self, bump_module: Any, workspace_copy: Path
    ) -> None:
        bump_module.set_version(workspace_copy, "3.0.0")
        text = (workspace_copy / "varco_fastapi" / "pyproject.toml").read_text()
        assert '"varco-ws~=3.0"' in text

    def test_set_pins_casbin_fastapi_optional_extra(
        self, bump_module: Any, workspace_copy: Path
    ) -> None:
        bump_module.set_version(workspace_copy, "3.0.0")
        text = (workspace_copy / "varco_casbin" / "pyproject.toml").read_text()
        assert '"varco-fastapi~=3.0"' in text

    def test_set_never_touches_dependency_groups_sibling_entries(
        self, bump_module: Any, workspace_copy: Path
    ) -> None:
        # varco_core's dev-group varco-fastapi entry must remain unpinned/untouched.
        before = (workspace_copy / "varco_core" / "pyproject.toml").read_text()
        dep_groups_before = before.split("[dependency-groups]")[1]
        bump_module.set_version(workspace_copy, "3.0.0")
        after = (workspace_copy / "varco_core" / "pyproject.toml").read_text()
        dep_groups_after = after.split("[dependency-groups]")[1]
        assert dep_groups_before == dep_groups_after

    def test_set_never_touches_examples(self, bump_module: Any, workspace_copy: Path) -> None:
        examples_before = (workspace_copy / "examples" / "pyproject.toml").read_text()
        bump_module.set_version(workspace_copy, "3.0.0")
        examples_after = (workspace_copy / "examples" / "pyproject.toml").read_text()
        assert examples_before == examples_after

    def test_set_never_touches_uv_sources(self, bump_module: Any, workspace_copy: Path) -> None:
        target = workspace_copy / "varco_kafka" / "pyproject.toml"
        before = target.read_text()
        sources_before = before.split("[tool.uv.sources]")[1]
        bump_module.set_version(workspace_copy, "3.0.0")
        after = target.read_text()
        sources_after = after.split("[tool.uv.sources]")[1]
        assert sources_before == sources_after


class TestRoundTripFidelity:
    def test_set_same_version_on_real_tree_is_byte_identical(
        self, bump_module: Any, workspace_copy: Path
    ) -> None:
        # Guards tomlkit reformatting the aligned `version       = "…"` style.
        # Reads varco_core's real current version and re-writes it unchanged.
        current = (workspace_copy / "varco_core" / "pyproject.toml").read_text().splitlines()[2]
        before_all = {
            pkg: (workspace_copy / pkg / "pyproject.toml").read_text() for pkg in EXPECTED_PACKAGES
        }
        # Extract "1.2.0" out of `version       = "1.2.0"`.
        current_version = current.split('"')[1]
        bump_module.set_version(workspace_copy, current_version)
        for pkg in EXPECTED_PACKAGES:
            after = (workspace_copy / pkg / "pyproject.toml").read_text()
            assert after == before_all[pkg], f"{pkg}/pyproject.toml reformatted unexpectedly"


class TestDryRun:
    def test_dry_run_writes_nothing(self, workspace_copy: Path) -> None:
        before = (workspace_copy / "varco_core" / "pyproject.toml").read_text()
        result = _run_cli("--set", "3.0.0", "--dry-run", cwd=workspace_copy)
        after = (workspace_copy / "varco_core" / "pyproject.toml").read_text()
        assert result.returncode == 0
        assert after == before

    def test_dry_run_prints_a_diff(self, workspace_copy: Path) -> None:
        result = _run_cli("--set", "3.0.0", "--dry-run", cwd=workspace_copy)
        assert "3.0.0" in result.stdout


class TestCheckMode:
    def test_check_exits_zero_on_coherent_workspace(
        self, bump_module: Any, workspace_copy: Path
    ) -> None:
        bump_module.set_version(workspace_copy, "3.0.0")
        result = _run_cli("--check", cwd=workspace_copy)
        assert result.returncode == 0

    def test_check_exits_one_and_names_divergent_package_on_doctored_copy(
        self, bump_module: Any, workspace_copy: Path
    ) -> None:
        bump_module.set_version(workspace_copy, "3.0.0")
        # Doctor one package to diverge.
        target = workspace_copy / "varco_redis" / "pyproject.toml"
        text = target.read_text()
        text = text.replace('version       = "3.0.0"', 'version       = "3.0.1"').replace(
            'version = "3.0.0"', 'version = "3.0.1"'
        )
        target.write_text(text)
        result = _run_cli("--check", cwd=workspace_copy)
        assert result.returncode == 1
        assert "varco_redis" in result.stdout or "varco-redis" in result.stdout

    def test_workspace_versions_are_coherent(self) -> None:
        # Runs --check against the REAL tree. Expected to fail until Phase 3
        # lands (real pyproject.toml files still carry divergent versions:
        # 1.2.0/2.1.1/2.2.0/... today). Flip to a plain assertion in Step 17.
        result = _run_cli("--check", cwd=REPO_ROOT)
        assert result.returncode == 0


class TestBumpArithmetic:
    @pytest.mark.parametrize(
        "start,part,expected",
        [
            ("1.2.3", "major", "2.0.0"),
            ("1.2.3", "minor", "1.3.0"),
            ("1.2.3", "patch", "1.2.4"),
        ],
    )
    def test_bump_arithmetic(self, bump_module: Any, start: str, part: str, expected: str) -> None:
        assert bump_module.bump_version(start, part) == expected
