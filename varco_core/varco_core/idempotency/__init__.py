"""
varco_core.idempotency
======================
Backend-agnostic contract for HTTP idempotency-key deduplication
(Plan 029 / D1a — see the plan's §D-D1-home for why this is a new
top-level package rather than an extension of ``varco_core.service.inbox``).

Only the contract, the value object, the fingerprint function, the
settings, and the single-process default implementation live here. The
ASGI middleware that actually wires this into an HTTP request/response
cycle lives in ``varco_fastapi.middleware.idempotency`` — the same seam
rule CLAUDE.md states for TLS trust, migrations, and multitenancy: the
backend-agnostic contract lives in ``varco_core``, the HTTP adapter lives
in ``varco_fastapi``, never the reverse.

⚠️ Deliberately **not** imported eagerly by ``varco_core/__init__.py`` — it
is reachable only via ``from varco_core.idempotency import ...`` (PEP 562
lazy import budget, Plan 028). Adding a top-level ``from varco_core.idempotency
import X`` re-export to ``varco_core/__init__.py``'s eager block would
reintroduce import cost this plan is not authorized to spend.

Public API::

    from varco_core.idempotency.base import AbstractIdempotencyStore, ReserveOutcome
    from varco_core.idempotency.record import IdempotencyRecord
    from varco_core.idempotency.memory import InMemoryIdempotencyStore
    from varco_core.idempotency.fingerprint import compute_fingerprint
    from varco_core.idempotency.settings import IdempotencySettings
"""

from __future__ import annotations

from varco_core.idempotency.base import AbstractIdempotencyStore, ReserveOutcome
from varco_core.idempotency.fingerprint import compute_fingerprint
from varco_core.idempotency.memory import InMemoryIdempotencyStore
from varco_core.idempotency.record import IdempotencyRecord
from varco_core.idempotency.settings import IdempotencySettings

__all__ = [
    "AbstractIdempotencyStore",
    "ReserveOutcome",
    "IdempotencyRecord",
    "InMemoryIdempotencyStore",
    "compute_fingerprint",
    "IdempotencySettings",
]
