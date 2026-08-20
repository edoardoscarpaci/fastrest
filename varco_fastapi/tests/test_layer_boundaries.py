"""
Red-mode tests for Plan 011 Phase 7, step 76 — RD-4's guard.

Plan line (step 76): "importing every varco_fastapi module added by this
plan leaves varco_sa, varco_beanie, babel, icu, and dateutil absent from
sys.modules. Same seam rule as AbstractEventBus / AbstractMigrator /
varco_core.tenancy."
"""

from __future__ import annotations

import subprocess
import sys

FORBIDDEN_MODULES = ("varco_sa", "varco_beanie", "babel", "icu", "dateutil")

PLAN_011_FASTAPI_MODULES = (
    "varco_fastapi.middleware.localization",
    "varco_fastapi.i18n",
)


def test_importing_new_fastapi_modules_leaves_forbidden_modules_absent() -> None:
    # Run in a subprocess so sys.modules pollution from the rest of the test
    # suite (which may have already imported varco_sa for unrelated tests)
    # cannot produce a false pass.
    script = (
        "import sys\n"
        + "\n".join(f"import {m}" for m in PLAN_011_FASTAPI_MODULES)
        + "\n"
        + f"forbidden = {FORBIDDEN_MODULES!r}\n"
        "leaked = [m for m in forbidden if m in sys.modules]\n"
        "assert not leaked, f'leaked: {leaked}'\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
