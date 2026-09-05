"""Shared scaffolding for the CodSpeed benchmark harness (Plan 028 / Phase 3, P2).

Everything here is deliberately *constructed*, never fixtured from a running
service: a benchmark that depends on a container, a clock or a network is not a
benchmark, it is a flaky test. See ``benchmarks/README.md`` for the three
standing rules this directory obeys.

The only cross-module concern is a fixed, deterministic filter string and a
deterministic timestamp, so two benchmarks that both touch the query layer are
measuring the same shape of work run to run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

# A filter string exercising every branch of the grammar exactly once: a
# quoted string, an integer, a float, LIKE, IN, IS NULL, a parenthesised
# group, and both boolean connectives. Fixed rather than generated — CodSpeed
# compares a benchmark against its own history, so the *input* must not move.
FILTER_QUERY: Final[str] = (
    'name = "Alice" AND age > 18 AND price <= 9.99 '
    'AND email LIKE "%@example.com" AND (tier IN (1, 2, 3) OR deleted_at IS NULL)'
)

#: Fixed instant used wherever a DTO or entity needs a timestamp. Never
#: ``datetime.now()`` — a benchmark must not measure the clock, and CodSpeed's
#: instrumented VM makes real-time calls unusually expensive.
FIXED_TS: Final[datetime] = datetime(2026, 1, 1, tzinfo=UTC)
