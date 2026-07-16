"""
conftest
========
Test configuration for the composite deployment example.

The example root is not an installed package, so its local modules
(``composite``, ``orders_service``, ``billing_service``) are not importable by
default.  Insert the example root onto ``sys.path`` so the smoke tests can
``from composite import ...`` exactly as application code would.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ── sys.path — insert example root so local modules are importable ────────────
_EXAMPLE_ROOT = str(Path(__file__).resolve().parent.parent)
if _EXAMPLE_ROOT not in sys.path:
    sys.path.insert(0, _EXAMPLE_ROOT)
