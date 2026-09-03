"""
Plan 024 / Phase 3, Step 29 — the `lint` job's step list in `test.yml` must
run `scripts/api_surface.py --check`, so the gate cannot be silently removed
(same repo-guard spirit as `test_repo_tooling_pins.py` and
`test_bump_script.py::test_workspace_versions_are_coherent`).

RED MODE: the `lint` job (`.github/workflows/test.yml`) has no api-surface
step yet — Step 28 of Plan 024 has not landed. This test parses the YAML and
must FAIL until a step running `scripts/api_surface.py --check` is added
after the `mypy` step.

Async safety: N/A — pure file-parsing check, no I/O beyond the repo tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("yaml", reason="pyyaml not a repo dependency; skip if absent")

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "test.yml"


def _lint_job_steps() -> list[dict]:
    data = yaml.safe_load(WORKFLOW_PATH.read_text())
    return data["jobs"]["lint"]["steps"]


def test_lint_job_step_list_contains_api_surface_check() -> None:
    steps = _lint_job_steps()
    run_commands = [step.get("run", "") for step in steps]

    matches = [cmd for cmd in run_commands if "api_surface.py" in cmd and "--check" in cmd]

    assert matches, (
        "Expected a lint-job step running "
        "'python scripts/api_surface.py --check' (Plan 024 Step 28); "
        f"found run commands: {run_commands!r}"
    )


def test_api_surface_check_step_runs_after_mypy() -> None:
    steps = _lint_job_steps()
    run_commands = [step.get("run", "") for step in steps]

    mypy_index = next((i for i, cmd in enumerate(run_commands) if "mypy" in cmd), None)
    api_surface_index = next(
        (i for i, cmd in enumerate(run_commands) if "api_surface.py" in cmd and "--check" in cmd),
        None,
    )

    assert mypy_index is not None, "Expected a mypy step in the lint job"
    assert api_surface_index is not None, "Expected an api-surface --check step in the lint job"
    assert api_surface_index > mypy_index
