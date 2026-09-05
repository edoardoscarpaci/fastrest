"""
varco_core.schedule
=====================
Recurring schedules (Plan 032 / D6) — a ``Schedule`` entity materializes
``Job`` rows via a hand-rolled, zero-dependency cron parser
(``varco_core.schedule.cron``) and DST-safe ``resolve_zoned()`` reuse
(``varco_core.schedule.materializer``). No execution path lives here — the
existing ``AbstractJobRunner`` runs the produced jobs unchanged.

⚠️ Deliberately **not** re-exported from top-level ``varco_core`` — same
PEP 562 import-budget reasoning as ``varco_core.idempotency``/
``varco_core.webhook``. Import the submodules directly::

    from varco_core.schedule.entity import CatchUpPolicy, Schedule
    from varco_core.schedule.cron import parse_cron
    from varco_core.schedule.materializer import ScheduleMaterializer
"""

from __future__ import annotations

__all__: list[str] = []
