"""Tests for ``scripts/import_budget.py`` — the import-time budget (Plan 028 / Phase 1, P1b).

RED MODE: the script does not exist yet. Every test in this module fails,
most with ``FileNotFoundError`` from the module-loading fixture.

Specification asserted here (Steps 9-10, §D-P1-oq4 and the Edge-cases list) —
the seams the tests need, and therefore the surface the implementation must
expose:

  * ``discover_targets(repo_root) -> list[str]`` — derives the target list by
    **executing** ``scripts/packages.sh`` (RL-18; the precedent set by
    ``scripts/api_surface.py`` and ``scripts/bump.py``), and **fails loudly**
    rather than returning an empty list when the script is absent or unusable.
  * ``BUDGET_PATH`` — module-level ``Path`` to
    ``design/async-performance-patterns/measurements/import-budget.json``,
    whose per-target shape is
    ``{"measured_ms": ..., "ceiling_ms": ..., "observations": []}``.
  * ``measure_delta_ms(target) -> float`` — best-of-5 subprocess
    ``-X importtime`` minus a same-methodology ``import sys`` baseline. The
    tests monkeypatch this: real measurement is neither fast nor deterministic,
    and what is under test is the *flag semantics*, not the timer.
  * ``main(argv) -> int`` — ``--check`` / ``--update`` / ``--warn-only``.

Deliberately NOT asserted: any absolute millisecond value. That is what the
committed JSON is for, and asserting it here would make the unit suite
runner-speed-dependent — the exact failure mode §D-P1-oq4 exists to avoid.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "import_budget.py"
BUDGET_JSON = (
    REPO_ROOT / "design" / "async-performance-patterns" / "measurements" / "import-budget.json"
)

# The ten distribution packages scripts/packages.sh yields today. "examples"
# is a workspace member but not a distribution and must never appear.
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


def _load_budget_module() -> Any:
    """Import ``scripts/import_budget.py`` as a module (the bump.py pattern)."""
    spec = importlib.util.spec_from_file_location("varco_import_budget", SCRIPT_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def budget_module() -> Any:
    # WHY: fails immediately with FileNotFoundError today — the script does
    # not exist. That is the "right" red-mode failure.
    return _load_budget_module()


@pytest.fixture
def fake_budget(tmp_path: Path) -> Path:
    """A two-target budget file with a generous and a punitive ceiling."""
    path = tmp_path / "import-budget.json"
    path.write_text(
        json.dumps(
            {
                "varco_core": {"measured_ms": 40.0, "ceiling_ms": 80.0, "observations": []},
                "varco_redis": {"measured_ms": 10.0, "ceiling_ms": 20.0, "observations": [1.0]},
            },
            indent=2,
        )
        + "\n"
    )
    return path


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args],
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
    )


class TestScriptExists:
    def test_import_budget_script_file_exists(self) -> None:
        # A single-line failure reason before other tests' ImportErrors
        # obscure "the file is simply missing".
        assert SCRIPT_PATH.is_file(), f"{SCRIPT_PATH} does not exist yet (Plan 028 Step 9)"

    def test_committed_budget_json_exists(self) -> None:
        # Step 10: the ceilings are a reviewable committed artifact, not a
        # value computed at runtime.
        assert BUDGET_JSON.is_file(), f"{BUDGET_JSON} does not exist yet (Plan 028 Step 10)"


class TestTargetDerivation:
    def test_targets_are_derived_by_executing_packages_sh(self, budget_module: Any) -> None:
        # RL-18: never a hand-written list.
        assert set(budget_module.discover_targets(REPO_ROOT)) == EXPECTED_PACKAGES

    def test_examples_is_never_a_target(self, budget_module: Any) -> None:
        # "examples" is a workspace member with no importable distribution.
        assert "examples" not in budget_module.discover_targets(REPO_ROOT)

    def test_source_contains_no_hardcoded_package_list(self) -> None:
        # The structural half of RL-18: a derivation that is shadowed by a
        # literal fallback list is not a derivation.
        source = SCRIPT_PATH.read_text()
        literals = [pkg for pkg in EXPECTED_PACKAGES - {"varco_core"} if f'"{pkg}"' in source]
        assert not literals, f"hard-coded package names in the script: {literals}"


class TestEmptyTargetListFailsLoudly:
    """Edge case: "must fail with a clear message, not silently measure an
    empty target list (the RL-18 failure mode)."""

    def test_discover_targets_raises_when_packages_sh_is_missing(
        self, budget_module: Any, tmp_path: Path
    ) -> None:
        # A tree with no scripts/packages.sh at all.
        with pytest.raises(Exception) as exc:  # noqa: PT011 - type is the script's choice
            budget_module.discover_targets(tmp_path)
        assert "packages.sh" in str(exc.value)

    def test_discover_targets_never_returns_an_empty_list(
        self, budget_module: Any, tmp_path: Path
    ) -> None:
        # The precise silent failure the edge case names: packages.sh present
        # and exiting 0, but printing nothing.
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        fake = scripts / "packages.sh"
        fake.write_text("#!/usr/bin/env bash\nexit 0\n")
        fake.chmod(0o755)
        with pytest.raises(Exception) as exc:  # noqa: PT011
            budget_module.discover_targets(tmp_path)
        assert "no packages" in str(exc.value).lower() or "empty" in str(exc.value).lower()

    def test_cli_exits_nonzero_with_a_clear_message_on_an_empty_target_list(
        self, tmp_path: Path
    ) -> None:
        # End-to-end: run from a directory where the derivation cannot work.
        result = _run_cli("--check", cwd=tmp_path)
        assert result.returncode != 0
        assert "packages.sh" in (result.stdout + result.stderr)


class TestCheckFlag:
    def test_check_exits_zero_when_every_target_is_under_its_ceiling(
        self, budget_module: Any, fake_budget: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(budget_module, "BUDGET_PATH", fake_budget)
        monkeypatch.setattr(budget_module, "measure_delta_ms", lambda target: 5.0)
        assert budget_module.main(["--check"]) == 0

    def test_check_exits_one_when_a_target_exceeds_its_ceiling(
        self, budget_module: Any, fake_budget: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The whole point of the gate: a new eager top-level import fails CI.
        monkeypatch.setattr(budget_module, "BUDGET_PATH", fake_budget)
        monkeypatch.setattr(budget_module, "measure_delta_ms", lambda target: 999.0)
        assert budget_module.main(["--check"]) == 1

    def test_check_names_the_offending_target_and_both_numbers(
        self,
        budget_module: Any,
        fake_budget: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # An actionable failure names what regressed and by how much.
        monkeypatch.setattr(budget_module, "BUDGET_PATH", fake_budget)
        monkeypatch.setattr(budget_module, "measure_delta_ms", lambda target: 999.0)
        budget_module.main(["--check"])
        captured = capsys.readouterr()
        out = captured.out + captured.err
        assert "varco_core" in out
        assert "999" in out
        assert "80" in out

    def test_check_never_writes_to_the_budget_file(
        self, budget_module: Any, fake_budget: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # --check is read-only; only --update writes.
        before = fake_budget.read_text()
        monkeypatch.setattr(budget_module, "BUDGET_PATH", fake_budget)
        monkeypatch.setattr(budget_module, "measure_delta_ms", lambda target: 5.0)
        budget_module.main(["--check"])
        assert fake_budget.read_text() == before


class TestWarnOnlyFlag:
    def test_warn_only_exits_zero_even_when_over_the_ceiling(
        self, budget_module: Any, fake_budget: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Step 9 lands the script warn-only; Step 14 flips it. Until then an
        # over-budget measurement must never fail CI.
        monkeypatch.setattr(budget_module, "BUDGET_PATH", fake_budget)
        monkeypatch.setattr(budget_module, "measure_delta_ms", lambda target: 999.0)
        assert budget_module.main(["--check", "--warn-only"]) == 0

    def test_warn_only_still_reports_the_breach(
        self,
        budget_module: Any,
        fake_budget: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Warn-only must still be *visible*, or the ten observations Step 13
        # collects never exist.
        monkeypatch.setattr(budget_module, "BUDGET_PATH", fake_budget)
        monkeypatch.setattr(budget_module, "measure_delta_ms", lambda target: 999.0)
        budget_module.main(["--check", "--warn-only"])
        captured = capsys.readouterr()
        assert "varco_core" in captured.out + captured.err


class TestUpdateFlag:
    def test_update_rewrites_measured_values(
        self, budget_module: Any, fake_budget: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(budget_module, "BUDGET_PATH", fake_budget)
        monkeypatch.setattr(budget_module, "measure_delta_ms", lambda target: 33.5)
        budget_module.main(["--update"])
        data = json.loads(fake_budget.read_text())
        assert data["varco_core"]["measured_ms"] == pytest.approx(33.5)

    def test_update_never_touches_ceilings(
        self, budget_module: Any, fake_budget: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Step 9, explicit: "--update (rewrite measured values, never
        # ceilings)". A ceiling that moves itself is a ratchet, which
        # §D-P1-oq4 rejects.
        monkeypatch.setattr(budget_module, "BUDGET_PATH", fake_budget)
        monkeypatch.setattr(budget_module, "measure_delta_ms", lambda target: 999.0)
        budget_module.main(["--update"])
        data = json.loads(fake_budget.read_text())
        assert data["varco_core"]["ceiling_ms"] == pytest.approx(80.0)
        assert data["varco_redis"]["ceiling_ms"] == pytest.approx(20.0)

    def test_update_preserves_the_observations_array(
        self, budget_module: Any, fake_budget: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Steps 13-14 read `observations` to justify the flip to a gate;
        # --update must not discard that evidence.
        monkeypatch.setattr(budget_module, "BUDGET_PATH", fake_budget)
        monkeypatch.setattr(budget_module, "measure_delta_ms", lambda target: 12.0)
        budget_module.main(["--update"])
        data = json.loads(fake_budget.read_text())
        assert data["varco_redis"]["observations"] == [1.0]

    def test_update_exits_zero_even_when_over_the_ceiling(
        self, budget_module: Any, fake_budget: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # --update records; it does not judge.
        monkeypatch.setattr(budget_module, "BUDGET_PATH", fake_budget)
        monkeypatch.setattr(budget_module, "measure_delta_ms", lambda target: 999.0)
        assert budget_module.main(["--update"]) == 0


class TestFlagCombination:
    def test_check_and_update_together_are_rejected(self, budget_module: Any) -> None:
        # Mutually exclusive: one compares, the other rewrites the values
        # being compared against.
        with pytest.raises(SystemExit) as exc:
            budget_module.main(["--check", "--update"])
        assert exc.value.code != 0


class TestBudgetFileShape:
    def test_every_target_has_measured_ceiling_and_observations(self) -> None:
        # Step 10's committed shape. Asserted against the real file so the
        # artifact cannot drift from what --check/--update expect.
        data = json.loads(BUDGET_JSON.read_text())
        for target, entry in data.items():
            assert {"measured_ms", "ceiling_ms", "observations"} <= set(entry), target

    def test_every_ceiling_is_above_its_measured_value(self) -> None:
        # §D-P1-oq4: ceilings are ~2x the measurement — headroom is visible,
        # never negative.
        data = json.loads(BUDGET_JSON.read_text())
        for target, entry in data.items():
            assert entry["ceiling_ms"] > entry["measured_ms"], target

    def test_budget_covers_every_derived_target(self, budget_module: Any) -> None:
        # A package with no budget entry is a package with no gate.
        data = json.loads(BUDGET_JSON.read_text())
        assert set(budget_module.discover_targets(REPO_ROOT)) <= set(data)
