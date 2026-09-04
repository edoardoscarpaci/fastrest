"""`import varco_core` in a fresh subprocess (Plan 028 / Phase 3, P2).

Ties Phase 0's lazy-import win (289.6 ms → 6.6 ms delta) to the same dashboard
as every other benchmark, so a reviewer looking at one PR comment sees import
cost next to request cost.

⚠️ **This benchmark is a subprocess, and CodSpeed instruments the parent
process only.** What it therefore measures under ``--codspeed`` is the parent's
``subprocess.run`` overhead, not the child's import work. It is kept anyway,
because uninstrumented (``make bench``, and any local wall-clock run) it is a
genuine end-to-end signal, and because the *authoritative* import gate is
``scripts/import_budget.py`` — a best-of-5, baseline-normalised measurement
wired into ``make lint`` and ``test.yml``. Read this series as a companion to
that gate, never as a replacement for it.
"""

from __future__ import annotations

import subprocess
import sys


def test_import_varco_core(benchmark) -> None:  # type: ignore[no-untyped-def]
    def _import_in_subprocess() -> int:
        return subprocess.run(
            [sys.executable, "-c", "import varco_core"],
            capture_output=True,
            check=True,
        ).returncode

    assert benchmark(_import_in_subprocess) == 0
