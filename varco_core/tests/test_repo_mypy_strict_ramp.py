"""Repo-invariant tests for Plan 021 (complete the mypy strictness ramp).

These assert on the END STATE of the root pyproject.toml's [tool.mypy] table
that Plan 021 must reach -- not on any intermediate phase. They are RED-mode
tests: as of authoring, none of Plan 021's phases have landed, so every test
here is expected to FAIL against the current (pre-Plan-021) config.

Companion to test_repo_ci_invariants.py's test_no_bare_type_ignore_in_source_trees
(Plan 017 / RL-6-mypy) -- that test already enforces "no bare type: ignore
anywhere under varco_*/varco_*" today. This file adds the Plan 021-specific
completion criteria: strict = true, disallow_any_unimported = true,
disallow_any_expr NOT enabled, and an empty [[tool.mypy.overrides]] section
(RL-14b's stated completion criterion).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT_PYPROJECT = REPO_ROOT / "pyproject.toml"


def _load_root_pyproject() -> dict:
    return tomllib.loads(ROOT_PYPROJECT.read_text())


def test_tool_mypy_strict_is_enabled():
    """Plan 021 §D1 / Phase 7: the end state collapses every individually
    landed flag into a single `strict = true`."""
    data = _load_root_pyproject()
    mypy = data.get("tool", {}).get("mypy")
    assert mypy is not None, "root pyproject.toml has no [tool.mypy] table"
    assert mypy.get("strict") is True, (
        "[tool.mypy] must carry 'strict = true' (Plan 021 Phase 7 end state)"
    )


def test_tool_mypy_disallow_any_unimported_is_enabled():
    """Plan 021 Phase 8: disallow_any_unimported is NOT part of --strict but
    is landed separately as its own flag once the 7 measured errors are fixed."""
    data = _load_root_pyproject()
    mypy = data.get("tool", {}).get("mypy", {})
    assert mypy.get("disallow_any_unimported") is True, (
        "[tool.mypy] must carry 'disallow_any_unimported = true' (Plan 021 Phase 8)"
    )


def test_tool_mypy_disallow_any_expr_stays_off():
    """Plan 021 Non-goals: disallow_any_expr stays off, permanently -- not
    part of --strict (brief 004 §1) and explicitly never adopted."""
    data = _load_root_pyproject()
    mypy = data.get("tool", {}).get("mypy", {})
    assert "disallow_any_expr" not in mypy, (
        "[tool.mypy] must never set disallow_any_expr -- it is a permanent "
        "Non-goal of Plan 021, not merely unset by omission"
    )


def test_tool_mypy_overrides_section_is_empty():
    """RL-14b's stated completion criterion (Plan 021 Phase 1 / step 63):
    the ten check_untyped_defs [[tool.mypy.overrides]] blocks are hoisted to
    a single global flag and deleted -- the overrides section ends up empty."""
    data = _load_root_pyproject()
    mypy = data.get("tool", {}).get("mypy", {})
    overrides = mypy.get("overrides", [])
    assert overrides == [], (
        "[[tool.mypy.overrides]] must be empty (RL-14b completion criterion); "
        f"found {len(overrides)} block(s): {overrides}"
    )


def test_no_check_untyped_defs_override_blocks_in_raw_toml():
    """Belt-and-suspenders text-level check alongside the parsed-TOML
    assertion above: no per-package check_untyped_defs override block
    survives anywhere in the file (Plan 021 Phase 1 / G3 hoist)."""
    text = ROOT_PYPROJECT.read_text()
    assert "[[tool.mypy.overrides]]" not in text, (
        "root pyproject.toml must contain no [[tool.mypy.overrides]] blocks "
        "once Plan 021 Phase 1's G3 hoist lands"
    )


def _workspace_members() -> list[str]:
    data = _load_root_pyproject()
    members = data["tool"]["uv"]["workspace"]["members"]
    return [m for m in members if m != "examples"]


def test_no_bare_type_ignore_under_varco_source_trees():
    """Plan 021 §D6 / Verification step 6: every '# type: ignore' anywhere
    under varco_*/varco_* must carry an explicit [<code>] -- a bare
    '# type: ignore' is never acceptable. Duplicates the intent of
    test_repo_ci_invariants.test_no_bare_type_ignore_in_source_trees (kept
    here too so this file stands alone as Plan 021's own completion-criteria
    suite; both must independently pass)."""
    import re

    bare_ignore_re = re.compile(r"#\s*type:\s*ignore(?!\[)")
    members = _workspace_members()
    offenders: list[str] = []
    for m in members:
        src_dir = REPO_ROOT / m / m
        if not src_dir.is_dir():
            continue
        for path in src_dir.rglob("*.py"):
            text = path.read_text(errors="ignore")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if bare_ignore_re.search(line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "bare '# type: ignore' found (must carry [<code>]):\n" + "\n".join(
        offenders
    )
