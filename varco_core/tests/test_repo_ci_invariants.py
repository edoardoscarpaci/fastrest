"""Structural / repo-invariant tests for Plan 017 (CI green: workflows + lint/type gates).

These are not behavioural unit tests — they assert on the shape of repo-level
configuration (root pyproject.toml, Makefile, scripts/, .github/workflows/) that
Plan 017 introduces. They live in varco_core/tests because varco_core's suite
always runs (every other workspace member depends on it) and `make test` /
CI enforce it going forward.

RED-mode note: as of authoring, none of the plan's Phase A-G steps have been
implemented yet, so most tests here are expected to FAIL — the missing
files/config are exactly what the implementer must add.

PyYAML is NOT installed in this venv (verified empirically before writing this
file), and the plan does not license adding a new dependency to satisfy a test
file. Workflow-file assertions therefore use targeted regex/line-based parsing
rather than `yaml.safe_load()`.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT_PYPROJECT = REPO_ROOT / "pyproject.toml"
MAKEFILE = REPO_ROOT / "Makefile"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _load_root_pyproject() -> dict:
    return tomllib.loads(ROOT_PYPROJECT.read_text())


def _workspace_members() -> list[str]:
    """The [tool.uv.workspace] members list, used to derive the expected ten
    distributed packages without hard-coding them (so the test can't go stale).
    """
    data = _load_root_pyproject()
    members = data["tool"]["uv"]["workspace"]["members"]
    return [m for m in members if m != "examples"]


# ---------------------------------------------------------------------------
# Item 1 — [dependency-groups] lint group with exact pins (Steps 1-2, §RL-6-tooling)
# ---------------------------------------------------------------------------


def test_dependency_groups_lint_has_exact_ruff_and_mypy_pins():
    """§RL-6-tooling: ruff/mypy must be exact-pinned dev deps, not resolved ad hoc
    via `uvx`, so a local green means CI is green too."""
    data = _load_root_pyproject()
    groups = data.get("dependency-groups", {})
    lint = groups.get("lint")
    assert lint is not None, "root pyproject.toml has no [dependency-groups] lint group"
    assert "ruff==0.16.4" in lint, f"lint group does not exact-pin ruff==0.16.4: {lint}"
    assert "mypy==2.3.1" in lint, f"lint group does not exact-pin mypy==2.3.1: {lint}"


def test_dependency_groups_dev_includes_lint_group():
    """§RL-6-tooling: `dev` must pull in `lint` via PEP 735 include-group so
    `uv sync` installs the linters by default."""
    data = _load_root_pyproject()
    groups = data.get("dependency-groups", {})
    dev = groups.get("dev", [])
    include_entries = [
        item for item in dev if isinstance(item, dict) and item.get("include-group") == "lint"
    ]
    assert include_entries, f"dev group does not include-group 'lint': {dev}"


# ---------------------------------------------------------------------------
# Item 2 — [tool.ruff] table, exact contents (Step 4, §RL-6-ruff)
# ---------------------------------------------------------------------------


def test_tool_ruff_table_exact_settings():
    """§RL-6-ruff: providify's config verbatim. The ignore list in particular
    must not be widened — the plan is explicit about this."""
    data = _load_root_pyproject()
    ruff = data.get("tool", {}).get("ruff")
    assert ruff is not None, "root pyproject.toml has no [tool.ruff] table"
    assert ruff.get("line-length") == 100
    assert ruff.get("target-version") == "py312"

    lint = ruff.get("lint")
    assert lint is not None, "root pyproject.toml has no [tool.ruff.lint] table"
    assert lint.get("select") == [
        "E",
        "F",
        "I",
        "UP",
    ], f"select must be exactly ['E','F','I','UP'], got {lint.get('select')}"
    assert lint.get("ignore") == ["E501", "UP046", "UP047"], (
        f"ignore must be exactly ['E501','UP046','UP047'] (never widened), got {lint.get('ignore')}"
    )


# ---------------------------------------------------------------------------
# Item 3 — [tool.mypy] table (Step 15, §RL-6-mypy)
# ---------------------------------------------------------------------------


def test_tool_mypy_table_settings():
    """§RL-6-mypy: mandatory monorepo plumbing flags. mypy_path's exact form is
    left unverified by the plan (settled empirically at Step 15) — we assert
    only that it is set and every named entry is a real directory."""
    data = _load_root_pyproject()
    mypy = data.get("tool", {}).get("mypy")
    assert mypy is not None, "root pyproject.toml has no [tool.mypy] table"
    assert mypy.get("python_version") == "3.12"
    assert mypy.get("explicit_package_bases") is True
    assert mypy.get("namespace_packages") is True
    assert mypy.get("ignore_missing_imports") is True
    # warn_unused_ignores was an individually-landed ramp flag (Plan 020) —
    # Plan 021 Phase 7 collapsed the whole landed ramp into `strict = true`
    # (mypy 2.3.1's 13-flag bundle, which includes warn_unused_ignores; see
    # pyproject.toml's [tool.mypy] comment block). Assert the umbrella flag
    # instead of the individual one it now subsumes.
    assert mypy.get("strict") is True

    mypy_path = mypy.get("mypy_path")
    assert mypy_path, "mypy_path must be set (exact form unverified by the plan)"
    entries = [e for e in re.split(r"[:,]", str(mypy_path)) if e.strip()]
    assert entries, f"mypy_path has no parseable entries: {mypy_path!r}"
    for entry in entries:
        candidate = REPO_ROOT / entry.strip()
        assert candidate.is_dir(), f"mypy_path entry {entry!r} is not an existing directory"


# ---------------------------------------------------------------------------
# Item 4 — every distributed package ships py.typed (Step 13, §RL-6-pytyped)
# ---------------------------------------------------------------------------


def test_every_distributed_package_ships_py_typed():
    """§RL-6-pytyped: derived by globbing the workspace member list, not
    hard-coded, so this test can't go stale. Currently RED for varco_nats."""
    members = _workspace_members()
    assert len(members) == 10, f"expected ten distributed packages, got {members}"
    missing = [m for m in members if not (REPO_ROOT / m / m / "py.typed").is_file()]
    assert not missing, f"packages missing py.typed: {missing}"


# ---------------------------------------------------------------------------
# Item 5 — no bare `# type: ignore` under any source tree (§RL-6-mypy)
# ---------------------------------------------------------------------------

_BARE_IGNORE_RE = re.compile(r"#\s*type:\s*ignore(?!\[)")


def test_no_bare_type_ignore_in_source_trees():
    """§RL-6-mypy: 'every one carries a specific error code; a bare
    `# type: ignore` is never acceptable.'"""
    members = _workspace_members()
    offenders: list[str] = []
    for m in members:
        src_dir = REPO_ROOT / m / m
        if not src_dir.is_dir():
            continue
        for path in src_dir.rglob("*.py"):
            text = path.read_text(errors="ignore")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if _BARE_IGNORE_RE.search(line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "bare '# type: ignore' found (must carry [<code>]):\n" + "\n".join(
        offenders
    )


# ---------------------------------------------------------------------------
# Item 6 — scripts/unit_tests.sh (Step 20, §RL-5-parity)
# ---------------------------------------------------------------------------


def test_unit_tests_script_exists_executable_and_excludes_integration_flag():
    """§RL-5-parity: the accumulate-and-summarize unit runner must not select
    integration tests via `-m integration` (they're opt-in, not opt-out)."""
    script = REPO_ROOT / "scripts" / "unit_tests.sh"
    assert script.is_file(), "scripts/unit_tests.sh does not exist"
    import os

    assert os.access(script, os.X_OK), "scripts/unit_tests.sh is not executable"
    contents = script.read_text()
    assert "-m integration" not in contents, (
        "scripts/unit_tests.sh must not pass '-m integration' "
        "(it should run everything except integration tests)"
    )


# ---------------------------------------------------------------------------
# Item 7 — Makefile PACKAGES has all ten members (Step 10)
# ---------------------------------------------------------------------------


def test_makefile_packages_contains_all_ten_workspace_members():
    """Step 10 (Plan 017) / Plan 020 RL-18: `varco_casbin` was silently
    excluded from lint/format/type-check/test/build/publish under the OLD
    hand-written ``PACKAGES := \\ ...`` literal block.

    Plan 020 / RL-18 replaced that literal with `PACKAGES :=
    $(shell $(CURDIR)/scripts/packages.sh)` — a single derivation from
    `[tool.uv.workspace] members` (see `varco_core/tests/test_repo_package_lists.py`
    for the dedicated RL-18 guard). This test now asserts the Makefile
    delegates to that script rather than re-parsing a literal block that no
    longer exists, and cross-checks the script's actual output against the
    workspace members it derives from — the same "can't go stale a second
    time" property, achieved through the script rather than through Makefile
    text.
    """
    expected = set(_workspace_members())
    assert len(expected) == 10

    makefile_text = MAKEFILE.read_text()
    assert re.search(
        r"PACKAGES\s*:=\s*\$\(shell\s+\$\(CURDIR\)/scripts/packages\.sh\)", makefile_text
    ), (
        "Makefile's PACKAGES must delegate to scripts/packages.sh (Plan 020 / RL-18), "
        "not hand-list workspace members"
    )

    result = subprocess.run(
        [str(REPO_ROOT / "scripts" / "packages.sh")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"scripts/packages.sh failed: {result.stderr}"
    found = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    missing = expected - found
    assert not missing, f"scripts/packages.sh output is missing: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Item 8 — Makefile uses `uv run ruff`, never `uvx` (Step 10, §RL-6-tooling)
# ---------------------------------------------------------------------------


def test_makefile_uses_uv_run_ruff_not_uvx():
    """§RL-6-tooling: `uvx ruff` resolves the newest ruff at invocation time;
    only `uv run ruff` reads the pinned version from uv.lock."""
    makefile_text = MAKEFILE.read_text()
    assert "uvx" not in makefile_text, "Makefile must not invoke 'uvx' anywhere"
    assert re.search(r"RUFF\s*:=\s*uv run ruff", makefile_text), (
        "Makefile RUFF variable must be 'uv run ruff'"
    )


# ---------------------------------------------------------------------------
# Items 9-11 — workflow files (regex-based, no PyYAML available)
# ---------------------------------------------------------------------------


def _read_workflow(name: str) -> str:
    path = WORKFLOWS_DIR / name
    assert path.is_file(), f".github/workflows/{name} does not exist"
    return path.read_text()


def _count_comment_lines(text: str) -> tuple[int, int]:
    lines = text.splitlines()
    total = len(lines)
    commented = sum(1 for line in lines if line.strip().startswith("#") or not line.strip())
    return commented, total


def test_test_workflow_is_live_yaml_not_fully_commented():
    """§RL-5-shape: test.yml today is 100% dead comments (92/92). It must
    become a live workflow — this test fails while every line is still '#'."""
    text = _read_workflow("test.yml")
    non_comment_non_blank = [
        line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")
    ]
    assert non_comment_non_blank, "test.yml is still 100% commented — nothing is live"
    assert re.search(r"^jobs:\s*$", text, re.MULTILINE), "test.yml has no top-level 'jobs:' key"


def test_test_workflow_has_exactly_lint_unit_all_green_jobs():
    """§RL-5-shape: exactly three jobs, no more, no less."""
    text = _read_workflow("test.yml")
    jobs_match = re.search(r"^jobs:\s*\n((?:^  \S.*\n(?:^(?:    |\t).*\n)*)*)", text, re.MULTILINE)
    assert jobs_match, "could not locate a 'jobs:' block in test.yml"
    job_names = re.findall(r"^  (\w[\w-]*):\s*$", jobs_match.group(1), re.MULTILINE)
    assert set(job_names) == {
        "lint",
        "unit",
        "all-green",
    }, f"test.yml jobs must be exactly {{lint, unit, all-green}}, got {set(job_names)}"


def test_test_workflow_unit_job_has_fail_fast_false_and_python_matrix():
    """§RL-5-shape / §RL-5-py313: fail-fast: false so one Python leg failing
    doesn't hide the other; matrix must include 3.12 (3.13 may legitimately be
    dropped per Step 12's decision table — we don't assert on that)."""
    text = _read_workflow("test.yml")
    assert re.search(r"fail-fast:\s*false", text), "no 'fail-fast: false' found in test.yml"
    assert re.search(r"python-version:\s*\[[^\]]*3\.12[^\]]*\]", text), (
        "unit job matrix must include python-version 3.12"
    )


def test_test_workflow_all_green_job_shape():
    """§RL-5-shape: all-green must depend on both other jobs, always run, and
    assert on `.result == 'success'` explicitly — bare success() accepts a
    skipped upstream (research 002 §5)."""
    text = _read_workflow("test.yml")
    assert re.search(r"needs:\s*\[\s*lint\s*,\s*unit\s*\]", text) or re.search(
        r"needs:\s*\n\s*-\s*lint\s*\n\s*-\s*unit", text
    ), "all-green job must have needs: [lint, unit]"
    assert re.search(r"if:\s*always\(\)", text), "all-green job must have if: always()"
    assert "needs.lint.result" in text and "'success'" in text or '"success"' in text
    assert "needs.unit.result" in text, (
        "all-green must assert on needs.<job>.result == 'success' explicitly, "
        "not a bare success() call"
    )


def test_test_workflow_permissions_contents_read():
    """§RL-5-pinning: least-privilege workflow-level permissions."""
    text = _read_workflow("test.yml")
    assert re.search(r"^permissions:\s*\n\s*contents:\s*read", text, re.MULTILINE) or re.search(
        r"permissions:\s*\{\s*contents:\s*read\s*\}", text
    ), "test.yml must set workflow-level permissions.contents: read"


def test_integration_workflow_is_live_yaml():
    """§RL-5-integration: integration.yml today is 100% dead comments (200/200)."""
    text = _read_workflow("integration.yml")
    non_comment_non_blank = [
        line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")
    ]
    assert non_comment_non_blank, "integration.yml is still 100% commented — nothing is live"
    assert re.search(r"^jobs:\s*$", text, re.MULTILINE), "integration.yml has no 'jobs:' key"


def test_integration_workflow_permissions_contents_read():
    text = _read_workflow("integration.yml")
    assert re.search(r"^permissions:\s*\n\s*contents:\s*read", text, re.MULTILINE) or re.search(
        r"permissions:\s*\{\s*contents:\s*read\s*\}", text
    ), "integration.yml must set workflow-level permissions.contents: read"


def test_integration_workflow_has_no_services_block():
    """§RL-5-integration: resurrecting `services:` blocks is explicitly wrong —
    the repo moved to session-scoped testcontainers fixtures."""
    text = _read_workflow("integration.yml")
    live_lines = [line for line in text.splitlines() if not line.strip().startswith("#")]
    live_text = "\n".join(live_lines)
    assert not re.search(r"^\s*services:\s*$", live_text, re.MULTILINE), (
        "integration.yml must not declare a 'services:' block anywhere"
    )


def test_integration_workflow_sets_no_bare_broker_env_names():
    """§RL-5-integration: bare env names must never be injected — only the
    namespaced VARCO_TEST_<SERVICE>_URL contract is honoured by conftests, and
    a stray bare name could point destructive tests at a real database."""
    text = _read_workflow("integration.yml")
    live_lines = [line for line in text.splitlines() if not line.strip().startswith("#")]
    live_text = "\n".join(live_lines)
    for bare_name in (
        "KAFKA_BOOTSTRAP_SERVERS",
        "REDIS_URL",
        "MONGODB_URL",
        "DATABASE_URL",
    ):
        assert not re.search(rf"^\s*{bare_name}:", live_text, re.MULTILINE), (
            f"integration.yml must not set bare env var {bare_name}"
        )


def test_integration_workflow_invokes_clean_room_make_target():
    text = _read_workflow("integration.yml")
    live_lines = [line for line in text.splitlines() if not line.strip().startswith("#")]
    live_text = "\n".join(live_lines)
    assert "make integration-test-clean" in live_text, (
        "integration.yml must invoke 'make integration-test-clean'"
    )


def test_integration_workflow_triggers():
    """§RL-5-triggers: push(main) + schedule (nightly) + workflow_dispatch —
    not on every PR."""
    text = _read_workflow("integration.yml")
    live_lines = [line for line in text.splitlines() if not line.strip().startswith("#")]
    live_text = "\n".join(live_lines)
    on_match = re.search(r"^on:\s*\n((?:^  \S.*\n(?:^(?:    |\t).*\n)*)*)", live_text, re.MULTILINE)
    assert on_match, "integration.yml has no live 'on:' trigger block"
    on_block = on_match.group(1)
    assert "push" in on_block, "integration.yml must trigger on push"
    assert "main" in on_block, "integration.yml push trigger must target main"
    assert "schedule" in on_block, "integration.yml must trigger on schedule (nightly)"
    assert "workflow_dispatch" in on_block, "integration.yml must trigger on workflow_dispatch"


# ---------------------------------------------------------------------------
# Item 11 — all `uses:` lines pinned by 40-hex SHA (§RL-5-pinning)
# ---------------------------------------------------------------------------


def test_all_uses_lines_pinned_by_commit_sha():
    """§RL-5-pinning: floating tags are rejected — every action reference must
    be a 40-hex commit SHA (a trailing '# vN' comment is fine, that's just a
    human-readable label)."""
    offenders: list[str] = []
    for name in ("test.yml", "integration.yml"):
        path = WORKFLOWS_DIR / name
        if not path.is_file():
            offenders.append(f"{name}: file missing")
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            if (
                stripped.startswith("#")
                or not stripped.startswith("uses:")
                and "uses:" not in stripped
            ):
                continue
            m = re.search(r"uses:\s*([^\s#]+)", stripped)
            if not m:
                continue
            ref = m.group(1)
            if "@" not in ref:
                offenders.append(f"{name}:{lineno}: uses without @ref: {ref}")
                continue
            sha = ref.rsplit("@", 1)[1]
            if not SHA_RE.match(sha):
                offenders.append(f"{name}:{lineno}: not a 40-hex SHA: {ref}")
    assert not offenders, "unpinned/floating action refs found:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# Item 12 — publish.yml resurrected as release.yml (Plan 023 / Step 26)
# ---------------------------------------------------------------------------
#
# Plan 017's own version of this test explicitly deferred: "publish.yml must
# remain fully commented (Non-goal: RL-10 owns its resurrection)". Plan 023
# is that resurrection — §RL-10-publish `git rm`'d the dead publish.yml and
# replaced it with a real, live release.yml (load-bearing filename — see
# that workflow's own header comment for why the name must never change
# again without redoing all ten PyPI trusted-publisher configs).


def test_publish_workflow_was_deleted_and_replaced_by_release_workflow():
    """Plan 023 / Step 26: the never-run, fully-commented publish.yml is gone;
    a live release.yml takes its place."""
    assert not (WORKFLOWS_DIR / "publish.yml").is_file(), (
        "publish.yml should have been git rm'd by Plan 023 Step 26 "
        "(replaced by release.yml — RL-10-publish)"
    )
    release_path = WORKFLOWS_DIR / "release.yml"
    assert release_path.is_file(), ".github/workflows/release.yml must exist (Plan 023 / RL-10)"
    text = release_path.read_text()
    non_comment_non_blank = [
        line for line in text.splitlines() if line.strip() and not line.strip().startswith("#")
    ]
    assert non_comment_non_blank, "release.yml must be a live workflow, not fully commented"


# ---------------------------------------------------------------------------
# Item 13 — .git-blame-ignore-revs exists and is non-empty (Step 9)
# ---------------------------------------------------------------------------


def test_git_blame_ignore_revs_exists_and_non_empty():
    """Step 9: lists the Step 5/6/7 ruff-sweep commit SHAs so `git blame`
    stays useful through the RL-8 API-surface audit."""
    path = REPO_ROOT / ".git-blame-ignore-revs"
    assert path.is_file(), ".git-blame-ignore-revs does not exist at repo root"
    assert path.read_text().strip(), ".git-blame-ignore-revs exists but is empty"
