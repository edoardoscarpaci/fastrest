# Dead Letter Queues — one concept, three producers

Plan 005, Phase 3 (gap U-6). Closes: "the outbox relay silently drops an
undeserializable/unpublishable entry, and there is no bounded-retry path for
either the relay or the job store."

## One `DeadLetterEntry`, three sources

Before this phase, `DeadLetterEntry` was event-shaped — it always carried a
`DomainEvent`. Two more producers need to dead-letter things that are not
typed events: `OutboxRelay` (a raw `OutboxEntry` whose payload may not even
deserialize) and the job runner (a `Job`). Rather than build a second,
job-specific DLQ abstraction — explicitly rejected by U-17 §4 — `DeadLetterEntry`
was generalised **additively**:

```python
class DeadLetterSource(StrEnum):
    CONSUMER = "consumer"          # an EventConsumer handler exhausted retries (existing)
    OUTBOX_RELAY = "outbox_relay"  # OutboxRelay could not deserialize/publish an entry
    JOB = "job"                    # a job exhausted max_attempts in JobRunner

# DeadLetterEntry gains, all defaulted:
source: DeadLetterSource = DeadLetterSource.CONSUMER
source_ref: str | None = None      # outbox entry_id / job_id, as str
payload: bytes | None = None       # raw bytes when `event` could not be deserialized
```

`event: DomainEvent` became `DomainEvent | None`. Every pre-Phase-3
construction site — positional or keyword, without `source=` — keeps working
unchanged; `source` defaults to `CONSUMER`, which is simply the new explicit
name for what a consumer's retry-exhaustion push already was.

```
                              push()
EventConsumer (@listen retries exhausted) ──┐
OutboxRelay (deserialize/publish failure) ──┼──► AbstractDeadLetterQueue
JobRunner (max_attempts exhausted)        ──┘         │
                                                       ▼
                                    InMemoryDeadLetterQueue (tests)
                                    RedisDLQ / KafkaDLQ (backends)
                                    SADeadLetterQueue (varco_sa, this phase)
```

## The `push()` contract — restated, now triply load-bearing

`AbstractDeadLetterQueue.push()` **must never raise**. This was already true
for the consumer retry wrapper (`_make_retry_wrapper` in
`varco_core.event.consumer` cannot recover from a DLQ failure), and is now
equally true for `OutboxRelay._relay_entry()` and `JobRunner._handle_job_failure()`
— neither has anywhere to put a `push()` failure either. Every implementation
(`InMemoryDeadLetterQueue`, `RedisDLQ`, `KafkaDLQ`, `SADeadLetterQueue`) logs
and swallows exceptions inside `push()`.

## `OutboxRelay` retry + dead-letter path

`OutboxEntry` gained `attempts: int = 0`, `last_error: str | None = None`,
`next_attempt_at: datetime | None = None` — all defaulted, so `from_event()`
and every pre-existing construction site is unaffected. `OutboxRepository`
gained a **concrete** (not abstract) `mark_failed()` — a repository written
before this method existed keeps importing; the default implementation logs
one warning per repository class and no-ops, degrading the relay to today's
unbounded-retry behaviour.

`OutboxRelay.__init__` gained `retry_policy`, `dlq`, `max_attempts`, all
`None`-defaulted:

- With **no** `retry_policy`: byte-identical to today — an entry that fails
  to publish is left in place, logged, and retried on the next tick.
- With a `retry_policy`: on publish failure, `attempts` increments and
  `next_attempt_at = now + retry_policy.compute_delay(attempts)` is written
  via `mark_failed()`. `_relay_once()` client-side-filters entries whose
  `next_attempt_at > now` — this needs no `get_pending()` signature change,
  so it works against every existing `OutboxRepository`, not just SA/Beanie.
- Once `attempts >= max_attempts`, the entry is pushed to the `dlq`
  (`source=OUTBOX_RELAY`, `source_ref=str(entry_id)`) **and deleted**.
  Constructing `OutboxRelay(max_attempts=...)` without a `dlq` raises
  `ValueError` at construction — deleting a poison entry with nowhere to put
  it is silent data loss, so the relay refuses that configuration outright.
- The pre-existing "payload failed to deserialize → delete" branch now
  dead-letters first (`event=None`, `payload=entry.payload`) when a `dlq` is
  wired, then deletes — today it deletes silently, which is a real, unreported
  loss this phase closes.

**Why dead-lettering deletes the entry** (rather than leaving it for a
relay/DLQ tandem to reconcile): per-tenant FIFO delivery means a single
poison row at the head of the queue stops the whole stream behind it — the
exact failure U-6 §1 names. Deleting after a successful `dlq.push()` is what
unblocks the stream; the DLQ is now the durable record of the poison entry.

## `durable_delivery()` preset — report, not request

U-6 §3 observes the shipped `RetryPolicy` default (`max_attempts=3`,
≈7 seconds total) is a poor fit for durable delivery, compared to
Oban's 20 attempts or Sidekiq's 25. This is filed as a **report**, not a
request to change the global default — every existing caller of the bare
`RetryPolicy()` default keeps its exact timing. Instead, a named preset:

```python
@classmethod
def durable_delivery(cls) -> RetryPolicy:
    return cls(max_attempts=20, base_delay=15.0, max_delay=3600.0, jitter=True)
```

`OutboxRelay` and `AuditConsumer` use this preset when the caller does not
supply an explicit policy of their own — see below.

## `AuditConsumer` — safe-by-default, opt out explicitly

U-6 §2: "safe-by-default is the right polarity for an audit trail."
`AuditConsumer` gained a class attribute
`_default_retry_policy = RetryPolicy.durable_delivery()` and an `__init__(...,
dlq=None)`; `register_to()` passes both **unless the caller overrode them** on
`@listen` or the `register_to()` call itself. Fire-and-forget is the
explicit opt-out: pass `retry_policy=None` deliberately if you want the old
best-effort behaviour back.

## `varco_sa.dlq.SADeadLetterQueue` — a durable DLQ, built anyway

⚠️ **Scope note.** U-6 explicitly states a durable DLQ is *not* an upstream
ask — the filer builds their own over the ABC. This module ships anyway
because `OutboxRelay`'s new dead-letter path (above) now needs somewhere
durable to put poison entries, and the only DLQ shipped before this phase was
`InMemoryDeadLetterQueue` — a `deque(maxlen=10_000)` lost on every restart.
`SADeadLetterQueue` makes the relay's dead-letter path useful out of the box
without changing what the filer asked for.

```python
from sqlalchemy.ext.asyncio import create_async_engine
from varco_sa.dlq import SADeadLetterQueue

engine = create_async_engine("postgresql+asyncpg://...")
dlq = SADeadLetterQueue(engine)
await dlq.ensure_table()             # creates varco_dead_letters idempotently

relay = OutboxRelay(
    outbox=relay_repo,
    bus=bus,
    retry_policy=RetryPolicy.durable_delivery(),
    dlq=dlq,
    max_attempts=20,
)
```

Table `varco_dead_letters`: `entry_id`, `source`, `source_ref`, `channel`,
`handler_name`, `event_type`, `payload`, `error_type`, `error_message`,
`attempts`, `first_failed_at`, `last_failed_at`. Raw Core (not ORM) over a
dedicated `MetaData`, same pattern as `SAJobStore`/`varco_sa.outbox` — no
dependency on the application's `DeclarativeBase`. `push()` never raises: any
exception is logged and swallowed, per the ABC contract.

⚠️ This repository does not ship an Alembic environment for its own
infrastructure tables — see the note in `technical_docs/features/crypto-shredding.md`.
Generate the migration from `dead_letters_metadata` via `autogenerate`
against your application's Alembic environment.

The SA outbox table gained `attempts INT NOT NULL DEFAULT 0`,
`last_error TEXT NULL`, `next_attempt_at TIMESTAMPTZ NULL` in the same
change, and `SAOutboxRepository`/`SARelayOutboxRepository` both implement
`mark_failed()` natively as a single `UPDATE`.

## Pitfalls

| Pitfall | Fix |
|---|---|
| A poison outbox row silently stops a stream | Wire `retry_policy=` + `dlq=` on `OutboxRelay` |
| `max_attempts` set without a `dlq` | `ValueError` by design — refuse to configure silent data loss |
| `AuditConsumer` never retries a transient DB error | It does by default now (`durable_delivery()`); pass `retry_policy=None` for the old fire-and-forget behaviour |
| A DLQ implementation raises from `push()` | Contract violation — callers (relay, job runner, retry wrapper) cannot recover; fix the implementation to log+swallow |
