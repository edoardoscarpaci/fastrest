"""
conftest.py
===========
pytest configuration for the ``13-layered-cache-memcached`` example.

Adds the example's root directory to ``sys.path`` so sibling modules
(``app``, ``models``, ``cache_layer``, etc.) are importable.

DESIGN: sys.path manipulation at conftest level
    ✅ All test modules share the same path setup automatically.
    ✅ No package structure needed for the example source files.
    ❌ Side-effects on global sys.path — acceptable in a contained test runner.

Thread safety:  N/A — pytest runs one test at a time per worker.
Async safety:   ✅ All async fixtures use ``asyncio_mode = "auto"``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ── sys.path — insert example root BEFORE any local import ────────────────────
_EXAMPLE_ROOT = str(Path(__file__).parent.parent.resolve())
if _EXAMPLE_ROOT not in sys.path:
    sys.path.insert(0, _EXAMPLE_ROOT)
