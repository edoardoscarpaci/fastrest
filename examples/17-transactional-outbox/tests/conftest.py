"""
conftest.py
===========
pytest configuration for the ``17-transactional-outbox`` example.

Adds the example's root directory to ``sys.path`` so sibling modules
(``app``, ``models``, ``service``, ``consumer``, ``router``) are importable.

All imports are placed BEFORE the ``sys.path`` manipulation block so that
ruff does not raise ``E402 Module level import not at top of file``.

DESIGN: session-scoped PostgreSQL container
    ✅ One Docker container shared across all tests — fast overall.
    ✅ Tests are additive — they create their own data and assert only on
       their own records; they do not assume an empty database.
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
