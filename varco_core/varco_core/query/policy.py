"""
varco_core.query.policy
==========================
``DatetimeCoercionPolicy`` — T3's declared timezone contract for the query
layer (Plan 011 D-10).

**Recommendation: ``"utc"``.** Brief 004 §B3 and its Options table both put
"assume UTC" as the correct API-layer default (Google Cloud, AWS, Azure).
varco agrees with the *destination* and disagrees only about whether a
published framework may arrive there without the operator's consent.

**Default: ``"naive"`` — byte-identical to today.** Attaching ``tzinfo=UTC``
is *not* a no-op: asyncpg rejects an aware ``datetime`` against a
``TIMESTAMP WITHOUT TIME ZONE`` column, so a working query would become a
runtime error on upgrade for every app with a naive timestamp column —
exactly the silent-upgrade breakage this repo's default-off convention
exists to prevent.

Two rules hold under **every** policy (brief 004 §B2/§B3):

1. An explicit offset always wins — an already-aware input is used verbatim,
   no policy is applied.
2. **Convert the bound, never the column.** Nothing in this module (or
   ``type_coercion.py``) may ever generate an ``AT TIME ZONE`` SQL fragment
   — that would defeat the index. ``coerce_datetime()`` only ever attaches
   ``tzinfo`` to a Python value; it never touches a column reference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = ["DatetimeCoercionPolicy"]


@dataclass(frozen=True)
class DatetimeCoercionPolicy:
    """
    Attributes:
        assume: How to interpret a **naive** datetime input.
            ``"naive"`` (default) — returned exactly as
            ``datetime.fromisoformat`` produced it, byte-identical to
            pre-Plan-011 behaviour.
            ``"utc"`` — the recommendation; attaches ``tzinfo=UTC``.
            ``"context"`` — opt-in per-user timezone, reads
            ``current_timezone()``; falls back to ``"utc"`` (and logs one
            DEBUG line) when no ambient timezone is resolved. Brief 004
            §B1: *"Varco would be pioneering this if implemented"* — no
            mainstream framework does this by default, hence opt-in only.
        log_naive: Emit one DEBUG line per coerced naive bound. Default
            ``True``.
    """

    assume: Literal["naive", "utc", "context"] = "naive"
    log_naive: bool = True
