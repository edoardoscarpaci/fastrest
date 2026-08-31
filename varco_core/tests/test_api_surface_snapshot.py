"""Tests for ``scripts/api_surface.py`` — the committed API-surface snapshot.

Plan 022 / Phase 0 Step 2, design section §D-AUDIT.

The script imports each distribution package's top-level module, walks
``__all__``, and emits per exported name its kind, defining module and (for
callables) the ``inspect.signature()`` string. ``--check`` diffs the live tree
against the committed snapshot and exits non-zero on a removal or a signature
change.

The script's own package list is derived by executing ``scripts/packages.sh``,
so this suite pins that derivation too.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "api_surface.py"
PACKAGES_SH = REPO_ROOT / "scripts" / "packages.sh"
SNAPSHOT_JSON = (
    REPO_ROOT / "design" / "api-freeze-and-standards" / "measurements" / "api-surface.json"
)


def _load_api_surface_module() -> Any:
    """Import ``scripts/api_surface.py`` as a module (it is not a package)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("varco_api_surface", SCRIPT_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def api_surface() -> Any:
    """The script under test, imported as a module."""
    return _load_api_surface_module()


def _run_check(*extra_args: str) -> subprocess.CompletedProcess[str]:
    """Invoke the script's ``--check`` mode as a real CLI subprocess."""
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--check", *extra_args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


class TestCheckMode:
    def test_check_exits_zero_on_unmodified_tree(self) -> None:
        # The committed snapshot must be in sync with HEAD at every phase boundary.
        result = _run_check()
        assert result.returncode == 0, result.stdout + result.stderr

    def test_check_exits_nonzero_when_symbol_removed_from_all(self, tmp_path: Path) -> None:
        # A synthetic snapshot claiming an extra symbol simulates that symbol having
        # been removed from the live tree's __all__ — never mutate the real snapshot.
        doctored = json.loads(SNAPSHOT_JSON.read_text())
        doctored["packages"]["varco_core"]["__NeverExportedSentinel__"] = {
            "kind": "class",
            "module": "varco_core.sentinel",
        }
        path = tmp_path / "api-surface.json"
        path.write_text(json.dumps(doctored))

        result = _run_check("--snapshot", str(path))

        assert result.returncode != 0
        assert "__NeverExportedSentinel__" in result.stdout + result.stderr

    def test_check_exits_nonzero_on_signature_change(self, tmp_path: Path) -> None:
        # Signature narrowing is the second break class --check must catch.
        doctored = json.loads(SNAPSHOT_JSON.read_text())
        entry = doctored["packages"]["varco_core"]["current_tenant"]
        entry["signature"] = "(a_parameter_that_does_not_exist: int) -> str"
        path = tmp_path / "api-surface.json"
        path.write_text(json.dumps(doctored))

        result = _run_check("--snapshot", str(path))

        assert result.returncode != 0
        assert "current_tenant" in result.stdout + result.stderr


class TestPackageListDerivation:
    def test_package_list_is_derived_from_packages_sh(self, api_surface: Any) -> None:
        # §D-AUDIT: the list must come from scripts/packages.sh so it cannot drift.
        expected = subprocess.run(
            ["bash", str(PACKAGES_SH)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()

        assert list(api_surface.discover_packages()) == expected

    def test_script_source_contains_no_hardcoded_package_list(self) -> None:
        # A hard-coded list is exactly the defect Plan 020 / RL-18 removed.
        source = SCRIPT_PATH.read_text()
        assert "packages.sh" in source
        assert '"varco_kafka"' not in source
        assert "'varco_kafka'" not in source


class TestSnapshotContents:
    @pytest.fixture
    def core_entries(self, api_surface: Any) -> dict[str, Any]:
        snapshot = api_surface.build_snapshot(["varco_core"])
        return snapshot["packages"]["varco_core"]

    def test_snapshot_covers_every_name_in_dunder_all(self, core_entries: dict[str, Any]) -> None:
        import varco_core

        assert set(core_entries) == set(varco_core.__all__)

    def test_class_export_records_kind_and_defining_module(
        self, core_entries: dict[str, Any]
    ) -> None:
        entry = core_entries["AbstractEventBus"]
        assert entry["kind"] == "class"
        # Measured, not assumed: AbstractEventBus is defined in event/base.py
        # (event/bus.py holds InMemoryEventBus), so the defining module the
        # snapshot records is varco_core.event.base.
        assert entry["module"] == "varco_core.event.base"

    def test_function_export_records_signature_string(self, core_entries: dict[str, Any]) -> None:
        # Callables carry inspect.signature() rendered as a string (§D-AUDIT).
        import inspect

        import varco_core

        entry = core_entries["current_tenant"]
        assert entry["kind"] == "function"
        assert entry["signature"] == str(inspect.signature(varco_core.current_tenant))

    def test_non_callable_non_class_export_records_constant_kind(
        self, core_entries: dict[str, Any]
    ) -> None:
        # Anything that is neither a class nor a function is a constant/alias, and
        # must carry NO signature key rather than a null one.
        constants = [n for n, e in core_entries.items() if e["kind"] == "constant"]
        assert constants
        assert all("signature" not in core_entries[n] for n in constants)

    def test_snapshot_json_is_deterministically_ordered(self, api_surface: Any) -> None:
        # A stable order is what makes the committed .json/.md diffable.
        entries = api_surface.build_snapshot(["varco_core"])["packages"]["varco_core"]
        assert list(entries) == sorted(entries)


class TestUnimportablePackageFailsLoudly:
    def test_build_snapshot_raises_naming_the_package_that_cannot_import(
        self, api_surface: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Edge case: a missing optional extra must never yield a silently smaller
        # snapshot — that would report every symbol in the package as removed.
        import importlib

        real_import_module = importlib.import_module

        def fake_import_module(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "varco_kafka":
                raise ImportError("No module named 'aiokafka'")
            return real_import_module(name, *args, **kwargs)

        monkeypatch.setattr(importlib, "import_module", fake_import_module)

        with pytest.raises(api_surface.ApiSurfaceError) as exc:
            api_surface.build_snapshot(["varco_kafka"])

        assert "varco_kafka" in str(exc.value)
        assert "aiokafka" in str(exc.value)

    def test_unimportable_package_is_not_silently_skipped_by_cli(self) -> None:
        # Same contract at the CLI boundary: a clear message, not a bare traceback,
        # and a non-zero exit.
        #
        # The import failure is provoked through the script's own public
        # ``--packages`` argument pointed at a name that cannot exist, rather
        # than through a ``VARCO_API_SURFACE_FAIL_IMPORT`` env hook: a
        # test-only backdoor in a production script is a permanent surface
        # addition that exists solely to be lied to, and it would itself be
        # untested. ``--packages`` is a genuinely useful narrowing flag that
        # happens to make this path reachable honestly.
        missing = "varco_definitely_not_an_installed_package"
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--check", "--packages", missing],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "could not import" in combined.lower()
        assert missing in combined
