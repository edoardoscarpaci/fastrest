"""
conftest.py
===========
Test configuration for the ``03-observability-metrics`` example.

Adds the example root directory to ``sys.path`` so that test modules can do
``from app import create_app`` without installing the example as a package.

This pattern is consistent across all examples in the catalog.

Thread safety:  ✅ Path manipulation runs once at collection time.
Async safety:   ✅ No async operations.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Insert the example root at the front of sys.path so that ``import app``
# resolves to examples/03-observability-metrics/app.py rather than any
# installed package named ``app``.
_EXAMPLE_ROOT = str(Path(__file__).parent.parent.resolve())
if _EXAMPLE_ROOT not in sys.path:
    sys.path.insert(0, _EXAMPLE_ROOT)
