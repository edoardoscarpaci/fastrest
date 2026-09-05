"""
varco_core.flags
==================
Feature-flag evaluation seam (Plan 032 / D7) — a varco-shaped
``AbstractFeatureFlags`` ABC, not a transcription of OpenFeature's
pre-1.0 provider surface. See ``varco_core.flags.base`` for the full
rationale and ``varco_core.flags.di`` for the opt-in DI wiring.

⚠️ Deliberately **not** re-exported from top-level ``varco_core`` —
keeping this out of ``varco_core/__init__.py``'s lazy ``_LAZY`` map means
importing ``varco_core`` never pays for this subpackage, and an app that
wants flags imports ``varco_core.flags`` explicitly (same pattern as
``varco_core.webhook``).
"""

from __future__ import annotations

from varco_core.flags.base import AbstractFeatureFlags, FlagEvaluationContext, FlagResolution
from varco_core.flags.memory import InMemoryFeatureFlags
from varco_core.flags.null import NullFeatureFlags

__all__ = [
    "AbstractFeatureFlags",
    "FlagEvaluationContext",
    "FlagResolution",
    "InMemoryFeatureFlags",
    "NullFeatureFlags",
]
