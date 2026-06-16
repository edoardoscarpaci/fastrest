"""
conftest.py
===========
pytest configuration for the ``19-resilience-payment-gateway`` example.

Adds the example's root directory to ``sys.path`` so sibling modules
(``app``, ``gateway``, ``router``, ``stub``) are importable without a
package structure.

DESIGN: conftest.py for sys.path over inline manipulation in test_smoke.py
    ✅ Runs before any test module is imported — no E402 ruff violations.
    ✅ Single location for path setup; all test files in this package benefit.
    ❌ Implicit — developers must know to look here for the path setup.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The example root is one level above this tests/ directory.
_EXAMPLE_ROOT = str(Path(__file__).parent.parent.resolve())
if _EXAMPLE_ROOT not in sys.path:
    sys.path.insert(0, _EXAMPLE_ROOT)
