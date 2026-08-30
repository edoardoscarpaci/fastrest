#!/usr/bin/env bash
# packages.sh — the single derivation of the workspace's distribution-package
# list (Plan 020 / RL-18).
#
# DESIGN: derive a **base list** from `[tool.uv.workspace] members`, apply
# **locally-declared, named exclusions** at each consumer that needs one
#   ✅ A new workspace member is picked up by all consumers (Makefile,
#      scripts/unit_tests.sh, scripts/integration_tests.sh,
#      scripts/gen_ref_pages.py) from one edit to `members` in root
#      pyproject.toml — no more hand-copied, driftable lists.
#   ✅ The 9-vs-10 asymmetry some consumers need (e.g. `varco_core` has no
#      broker-facing tests, so it is excluded from the integration runner)
#      stays *visible and reasoned* at the site that needs it, via a locally
#      declared, named exclusion array with its own comment — never baked
#      into this script.
#   ❌ Two mechanisms instead of one (derivation + exclusion). Accepted: the
#      alternative is encoding the exclusion as a second hand-written list,
#      which is the defect this script removes.
#
# Distinguishes "distribution package" from "workspace member" by one rule:
# a member is a distribution iff `<member>/<member>/__init__.py` exists.
# This excludes `examples` (a workspace member but not an importable
# distribution) *structurally* rather than by name, so a future non-
# distribution member needs no edit here.
#
# Uses stdlib `tomllib` (Python >= 3.11) via bare `python3`, not `uv run
# python`:
#   ✅ No venv dependency — `Makefile:lint`/`gen_ref_pages.py` must work
#      before/without a `uv sync`.
#   ✅ Correct TOML parsing, no regex fragile to comments/trailing commas.
#   ❌ Requires `python3 >= 3.11` on PATH. Accepted — the repo targets
#      3.12/3.13 and CI provides it. Fails loudly (never silently falls back
#      to a hard-coded list) if the requirement is not met.
#
# Usage:
#   scripts/packages.sh          # prints one distribution-package name per
#                                 # line, in [tool.uv.workspace] members order
#   make -s print-packages       # same list, space-separated (Makefile target)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

python3 - "$ROOT" <<'PYEOF'
import sys
import tomllib
from pathlib import Path

root = Path(sys.argv[1])
data = tomllib.loads((root / "pyproject.toml").read_text())
members = data["tool"]["uv"]["workspace"]["members"]

for member in members:
    # A member is a distribution package iff it has an importable package
    # directory of the same name nested inside it (<member>/<member>/__init__.py).
    # This structurally excludes non-distribution members (e.g. "examples")
    # without naming them.
    if (root / member / member / "__init__.py").is_file():
        print(member)
PYEOF
