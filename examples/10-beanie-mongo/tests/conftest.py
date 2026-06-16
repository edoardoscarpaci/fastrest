"""
conftest.py
===========
pytest configuration for the ``10-beanie-mongo`` example.

Adds the example's root directory to ``sys.path`` so sibling modules
(``app``, ``models``, ``service``, etc.) are importable without a package
structure.

All imports are placed BEFORE the ``sys.path`` manipulation block to avoid
ruff E402 warnings.

DESIGN: session-scoped MongoDB container
    ✅ One Docker container shared across all tests — avoids repeated
       startup overhead (~2 s per container).
    ✅ ``BeanieRepositoryProvider.init()`` called once per session — Beanie
       document registration is global state, safe to share across tests.
    ✅ Tests create their own records and assert only on those records —
       no empty-DB assumption.
    ❌ Requires Docker daemon to be running.

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
