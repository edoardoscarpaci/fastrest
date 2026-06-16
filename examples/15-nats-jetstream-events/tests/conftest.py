"""
conftest.py
===========
pytest configuration for the ``15-nats-jetstream-events`` example.

Adds the example's root directory to ``sys.path`` so sibling modules
(``app``, ``events``, ``consumer``, ``router``) are importable without
a package structure.

DESIGN: conftest.py for sys.path over inline manipulation in test_smoke.py
    ✅ Runs before any test module is imported — no E402 ruff violations.
    ✅ Single location for path setup; all test files benefit.
    ❌ Implicit — developers must know to look here for the path setup.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Insert the example root so ``from app import create_app`` works in tests.
_EXAMPLE_ROOT = str(Path(__file__).parent.parent.resolve())
if _EXAMPLE_ROOT not in sys.path:
    sys.path.insert(0, _EXAMPLE_ROOT)
