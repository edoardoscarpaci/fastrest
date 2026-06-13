"""
jobs.py
=======
Named task functions for the ``21-async-job-runner`` example.

Each function is registered as a ``VarcoTask`` via ``@varco_task``.  The
``TaskRegistry`` singleton holds the mapping so ``JobRunner`` can recover
pending jobs after a restart.

The single task here — ``generate_report`` — is synthetic: it sleeps briefly
to simulate work, then returns a JSON-safe dict that the caller can poll for.

DESIGN: synthetic sleep over real work
    ✅ No external dependencies (DB, queue, file I/O) keeps the example
       focused on the 202 + poll pattern rather than domain mechanics.
    ✅ Configurable ``rows`` parameter lets tests verify the result payload.
    ❌ Does not demonstrate real CPU or I/O — use ``04-profiling-hotspot``
       for CPU-intensive work patterns.

Thread safety:  ✅ Pure async function — no shared state.
Async safety:   ✅ Awaitable; cooperatively yields via ``asyncio.sleep``.
"""

from __future__ import annotations

import asyncio

from varco_core.job.task import TaskRegistry, varco_task

# ── Shared registry ───────────────────────────────────────────────────────────
# A single TaskRegistry is shared across the application.  In DI-based apps
# it is a container singleton; here we create it directly and expose it so
# app.py can wire it into JobRunner.
#
# DESIGN: module-level registry over per-task implicit global
#     ✅ Single import point — app.py imports ``registry`` instead of
#        wrangling task names by hand.
#     ✅ Makes ``@varco_task(registry=registry)`` explicit about which
#        registry each task belongs to (no hidden global state).
#     ❌ Module-level singleton — acceptable here because the example is
#        single-process and the registry is stateless after population.
registry = TaskRegistry()


# ── Task definition ───────────────────────────────────────────────────────────


@varco_task(name="generate_report", registry=registry)
async def generate_report(title: str, rows: int) -> dict:  # type: ignore[type-arg]
    """
    Synthetic report-generation task.

    Simulates slow work by sleeping for a short interval, then returns a
    JSON-safe summary dict the caller can retrieve via the poll endpoint.

    Args:
        title: Human-readable report title (echoed in the result).
        rows:  Number of "rows" to simulate processing.

    Returns:
        A dict with ``title``, ``rows``, and a fake ``csv_preview`` string.

    Edge cases:
        - ``rows <= 0`` → still returns a valid result (empty preview).
        - Task cancelled mid-sleep → ``asyncio.CancelledError`` propagates;
          ``JobRunner`` marks the job CANCELLED automatically.

    Thread safety:  ✅ Pure coroutine — no shared mutable state.
    Async safety:   ✅ Cooperatively yields via ``asyncio.sleep``.
    """
    # Simulate work proportional to the number of rows (capped to avoid slow tests)
    sleep_seconds = min(rows * 0.001, 0.05)
    await asyncio.sleep(sleep_seconds)

    # Build a fake CSV preview
    header = "id,value"
    preview_rows = [f"{i},{i * 10}" for i in range(1, min(rows + 1, 4))]
    csv_preview = "\n".join([header, *preview_rows])

    return {
        "title": title,
        "rows": rows,
        "csv_preview": csv_preview,
    }


__all__ = ["generate_report", "registry"]
