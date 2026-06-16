"""
tests/conftest.py
=================
Ensure the example root is on ``sys.path`` so ``from app import ...`` and
``from authority import ...`` work regardless of the directory pytest is
invoked from.
"""

from __future__ import annotations

import sys
from pathlib import Path

_EXAMPLE_ROOT = str(Path(__file__).parent.parent.resolve())
if _EXAMPLE_ROOT not in sys.path:
    sys.path.insert(0, _EXAMPLE_ROOT)
