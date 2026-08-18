"""
varco_core.reliability
========================
``ReliabilityPreset`` (Plan 009, Phase 9 / R5) — "opt into durability once".

Lives in its own subpackage rather than ``varco_core.resilience`` (RD-6): it
composes ``varco_core.event`` (DLQ), ``varco_core.resilience`` (RetryPolicy),
and ``varco_core.service`` (outbox/audit); putting it in ``resilience/``
would make that package import ``event``, and ``event.consumer`` already
imports ``resilience`` — a cycle. ``varco_fastapi`` imports
``varco_core.reliability`` only (a core seam), never a backend.
"""

from __future__ import annotations

from varco_core.reliability.preset import ReliabilityPreset
from varco_core.reliability.wiring import (
    get_default_reliability_preset,
    set_default_reliability_preset,
)

__all__ = [
    "ReliabilityPreset",
    "get_default_reliability_preset",
    "set_default_reliability_preset",
]
