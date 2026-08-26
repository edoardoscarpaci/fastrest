"""
Failing import-guard test (Plan 007, Phase 10, step 2): varco_fastapi.tenancy
must import only varco_core.tenancy — never varco_sa, varco_beanie,
sqlalchemy, or pymongo.
"""

from __future__ import annotations

import pathlib
import re


def test_no_forbidden_imports_in_varco_fastapi_tenancy() -> None:
    tenancy_dir = pathlib.Path(__file__).parent.parent / "varco_fastapi" / "tenancy"

    assert tenancy_dir.is_dir(), "varco_fastapi/varco_fastapi/tenancy/ does not exist yet"

    forbidden = re.compile(r"\b(varco_sa|varco_beanie|sqlalchemy|pymongo)\b")
    offenders = []
    for path in tenancy_dir.rglob("*.py"):
        text = path.read_text()
        for line in text.splitlines():
            if line.strip().startswith(("import", "from")) and forbidden.search(line):
                offenders.append(f"{path}: {line.strip()}")

    assert offenders == [], f"Forbidden imports found: {offenders}"
