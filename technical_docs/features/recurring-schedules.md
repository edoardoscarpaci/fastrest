# Recurring schedules — `varco_core.schedule`

Plan 032 (BACKLOG 3.1, row **D6** nice/M). Closes the loop the job subsystem's zoned fields
(`run_at_wall`/`run_at_tz`/`run_at_fold`, `varco_core/varco_core/job/base.py:258-273`) were built
for — CLAUDE.md's own sketch of *"a future `Schedule` entity that produces `Job` rows exactly
like these"*.

## No execution path lives here

`Schedule` only describes *when* and *what* to materialize. `ScheduleMaterializer` turns its due
occurrence(s) into ordinary `PENDING` `Job` rows via `AbstractJobStore.save()`. The existing
`AbstractJobRunner` runs those jobs completely unchanged — if a future change to this subsystem
starts writing a second runner, it has gone wrong (the plan's own Non-goal).

## §D-D6-cron — cron only; RRULE is parked

A 5-field cron expression (`minute hour day-of-month month day-of-week`) is a small,
well-understood grammar — `varco_core.schedule.cron` implements it in ~200 lines with **zero new
dependencies**. A complete RFC 5545 (RRULE) implementation means `dateutil.rrule` — a new runtime
dependency for a 🟢 backlog row, in a repo whose standing rule is zero new runtime dependencies
in `varco_core`. Parked; un-park trigger: *a consumer needs a recurrence cron cannot express, and
accepts the dependency.*

DST handling is not new work for this plan — it is the reason the zoned `Job` fields exist. The
materializer computes the next wall-clock occurrence from the cron expression in the schedule's
own timezone, then calls `resolve_zoned()` with the schedule's `GapPolicy`/`OverlapPolicy`
(`varco_core.tz.schedule`) to get the UTC instant. `run_at` receives the materialization;
`run_at_wall`/`run_at_tz`/`run_at_fold` receive the intent.

## Catch-up policy

What a materializer that was down for hours does with occurrences it missed while offline —
explicit per schedule, three behaviours, defaulting to `SKIP`:

| Policy | Behaviour |
|---|---|
| `SKIP` (default) | Materialize only the most recent due occurrence. Earlier missed ones are lost. |
| `FIRE_ONCE` | Materialize one job representing the missed window, then resume normal per-occurrence materialization on the next call. |
| `BACKFILL_ALL` | Materialize every missed occurrence, oldest first, bounded by `max_backfill`. |

DESIGN: `SKIP` as the default
  ✅ A "send the nightly digest" schedule that missed three nights should send tonight's digest,
     not three at once — the surprising-and-expensive failure mode is the one to avoid by
     default.
  ❌ A schedule with real catch-up semantics (billing runs) must opt in explicitly. Correct: that
     is a decision the schedule's owner must make consciously, not one varco should guess at.

## Materialization safety — what the code actually does (a deliberate deviation from the plan's draft mechanism)

The plan's design narrative originally proposed reusing the job store's fenced-lease primitives
(`try_claim`/`renew`/`save(expected_epoch=)` → `StaleLeaseError`) as the materializer's mutual-
exclusion mechanism, on the theory that "one locking model" beats two.

**That does not fit the shape of the problem, and the implementation deviates from it
deliberately** (`varco_core/varco_core/schedule/materializer.py`'s own DESIGN note): a lease
needs a persisted row to hold it, and any row saved through `AbstractJobStore.save()` is
indistinguishable from a real job to every caller of `list_by_status()`/`all_jobs()`/
`delete_where()`. A synthetic "schedule lock" row corrupts every job-count invariant downstream
code relies on — this broke the concurrent-materializer test's `store.all_jobs() == 1` assertion
outright during implementation, not just stylistically.

**What ships instead:**

1. **A deterministic occurrence `Job.job_id`** — `uuid5(NAMESPACE_URL, f"varco:schedule:
   {schedule_id}:{wall.isoformat()}")`. Two materializers (different processes, different hosts)
   computing the same occurrence converge on the *same physical row* via `AbstractJobStore.save()`'s
   own idempotent-upsert semantics — not two rows. This is the real **cross-process** backstop,
   reinforced by `UNIQUE(schedule_id)` on the `SAScheduleRepository`/`BeanieScheduleRepository`
   `Schedule` row (⚠️ see the note below on why this is `UNIQUE(schedule_id)`, not
   `UNIQUE(schedule_id, run_at)` — `Schedule` has no `run_at` column at all; the occurrence-level
   uniqueness lives entirely in the deterministic job id).
2. **A lazily-created, per-schedule `asyncio.Lock`** (a module-level registry in
   `materializer.py`, keyed by `schedule_id`, never constructed at import time or in `__init__` —
   CLAUDE.md's standing rule) serializes the "compute occurrences → check-existing →
   save" section **within one process**. This closes the check-then-create race a bare
   idempotent id alone would still leave open for two materializer calls racing inside the same
   event loop.

```python
# varco_core/varco_core/schedule/materializer.py (abridged)
async def materialize(self, schedule: Schedule, *, now=None) -> list[Job]:
    ...
    async with _lock_for(schedule.schedule_id):
        for wall in self._compute_occurrences(schedule, current):
            job = self._build_job(schedule, wall)          # deterministic job_id
            if await self._job_store.get(job.job_id) is not None:
                continue                                     # already materialized — no-op
            await self._job_store.save(job)
            jobs.append(job)
    return jobs
```

**What this buys and what it does not:**

- ✅ In-process double-materialization is fully closed by the lock.
- ✅ Cross-process double-materialization is closed by the deterministic id + the DB unique
  index on `schedule_id` — not by any lock, distributed or otherwise.
- ❌ The lock coordinates exactly one process. A genuinely distributed race (two materializer
  processes both computing the same occurrence at the same instant) still relies purely on the
  deterministic id + unique index, never on this lock. This is accepted: duplicating a
  distributed-lock model in Python here would be exactly the "second locking model" the plan
  explicitly warned against introducing.

## Pitfalls

| Pitfall | Why it happens | Fix |
|---|---|---|
| **DST gaps and ambiguity** — a cron occurrence lands on a spring-forward gap (the wall time never existed) or a fall-back overlap (the wall time happens twice) | Every IANA zone with DST has exactly these two failure modes twice a year; a naive "just compute UTC" schedule fires at the wrong wall-clock hour | The materializer always resolves through `resolve_zoned()` with the schedule's `GapPolicy` (default `NEXT_VALID`) and `OverlapPolicy` (default `FIRST`) — never bypass this by hand-computing a UTC offset |
| **Catch-up surprise** — a schedule down for six hours "loses" five missed nightly-digest runs and only sends one | `SKIP` (the default `CatchUpPolicy`) is deliberately lossy for missed occurrences — chosen because most schedules should not burst-fire on recovery | If catch-up semantics matter (billing, SLA-bound jobs), opt into `FIRE_ONCE` or `BACKFILL_ALL` explicitly on that `Schedule`; do not assume `SKIP` is safe for every use case |
| **Materializer downtime and `last_materialized_at`** | The materializer never mutates `Schedule.last_materialized_at` itself — "storage is dumb", the same split as every other `DomainModel` in this codebase. If the caller never persists the field after materializing, the next call re-derives from `now` instead of the true last-run marker | The caller (a periodic sweep/cron trigger, not shipped here) must persist `last_materialized_at` via the repository after each successful materialization, or `SKIP`/`FIRE_ONCE`'s "missed window" boundary silently resets every call |
| Expecting `try_claim`/`StaleLeaseError` semantics on `Schedule` | The plan's own draft narrative proposed this; the shipped code does not use it — see the DESIGN section above | Use the deterministic-id + per-process-lock model as documented; do not add a lease to `Schedule` to "match the job store" |
| Assuming `UNIQUE(schedule_id, run_at)` exists on the `Schedule` table | That index describes *occurrences*, which live on the **jobs** table via the deterministic `job_id`, not on `Schedule` (which has no `run_at` column) | The uniqueness guarantee you want is `Job.job_id`'s determinism, not a `Schedule` table constraint |
| Treating `varco_core.schedule` as reachable from top-level `varco_core` | Deliberately not re-exported — same PEP 562 import-budget reasoning as `varco_core.webhook`/`varco_core.idempotency` | Import submodules directly: `from varco_core.schedule.entity import Schedule` |

## Type/module map

- `varco_core.schedule.cron` — `parse_cron()`, `CronSchedule` (immutable, `next_after()`/`at_or_before()`).
- `varco_core.schedule.entity` — `Schedule` (a `DomainModel`), `CatchUpPolicy`.
- `varco_core.schedule.materializer` — `ScheduleMaterializer`.
- `varco_core.schedule.repository` — `AbstractScheduleRepository`, `InMemoryScheduleRepository`.
- `varco_sa.schedule` — `SAScheduleRepository` (own `Table`/`MetaData`, registered via
  `register_framework_metadata()`, migration revision `0007_schedules_table`).
- `varco_beanie.schedule` — `BeanieScheduleRepository`, `ScheduleDocument`.
