"""
Repo-infrastructure guard — Plan 020 / RL-19.

RL-19 records an *unplanned but correct* change already in the tree:
``.pre-commit-config.yaml``'s ruff ``rev`` was bumped ``v0.4.1`` → ``v0.16.4``
because ``v0.4.1`` predates rule codes (``UP046``/``UP047``) now referenced in
``[tool.ruff.lint] ignore`` and could not parse the config.

The failure RL-19 fixed was *silent* — the pre-commit hook only broke at
commit time, on one developer's machine, with a config-parse error rather
than a lint error. This guard turns the next such divergence into a loud CI
failure instead: it asserts ``.pre-commit-config.yaml``'s ruff ``rev`` always
names the same version as root ``pyproject.toml``'s ``[dependency-groups]
lint`` ruff pin — the two are allowed to diverge only during a deliberate,
short transition window, never silently.

Async safety: N/A — pure file-parsing checks, no I/O beyond the repo tree.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _lint_group_ruff_pin() -> str:
    """Return the exact ``ruff==X.Y.Z`` pin from ``[dependency-groups] lint``."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    lint_group = data["dependency-groups"]["lint"]
    for entry in lint_group:
        if isinstance(entry, str) and entry.startswith("ruff=="):
            return entry
    raise AssertionError("no 'ruff==' pin found in [dependency-groups] lint")


def _precommit_ruff_rev() -> str | None:
    """
    Return the ``rev:`` value of the ``ruff-pre-commit`` repo block, or ``None``
    if ``.pre-commit-config.yaml`` is absent.

    Parsed with a targeted regex rather than a YAML library — this repo has no
    existing YAML-parsing dependency and the file's shape (one ``rev:`` line
    immediately following the ``astral-sh/ruff-pre-commit`` repo URL) is
    simple and stable enough not to warrant adding one just for this guard.
    """
    config_path = REPO_ROOT / ".pre-commit-config.yaml"
    if not config_path.is_file():
        return None

    text = config_path.read_text()
    match = re.search(
        r"astral-sh/ruff-pre-commit.*?\n\s*rev:\s*(\S+)",
        text,
        flags=re.DOTALL,
    )
    if match is None:
        raise AssertionError(
            ".pre-commit-config.yaml exists but no ruff-pre-commit 'rev:' was found"
        )
    return match.group(1)


class TestRuffPinParity:
    """
    DESIGN: assert `.pre-commit-config.yaml`'s ruff rev equals the
    `[dependency-groups] lint` pin (§RL-19, plan 020).

    ✅ The failure RL-19 fixed was silent — this test makes the next
       divergence a loud CI failure instead.
    ❌ Couples two files that could legitimately diverge for a transition
       window. Accepted — the transition window is a deliberate act and this
       test names the exact reason it exists.
    """

    def test_precommit_ruff_rev_matches_the_lint_group_pin(self) -> None:
        precommit_rev = _precommit_ruff_rev()
        if precommit_rev is None:
            pytest.skip(".pre-commit-config.yaml is absent")

        lint_pin = _lint_group_ruff_pin()  # e.g. "ruff==0.16.4"
        lint_version = lint_pin.split("==", 1)[1]
        precommit_version = precommit_rev.lstrip("v")

        assert precommit_version == lint_version, (
            f".pre-commit-config.yaml's ruff rev ({precommit_rev!r}) does not match "
            f"root pyproject.toml's [dependency-groups] lint pin ({lint_pin!r}). "
            "See BACKLOG RL-19 — this is the exact silent divergence that guard exists "
            "to catch loudly instead of at commit time on one developer's machine."
        )
