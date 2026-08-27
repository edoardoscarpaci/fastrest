# Plan 019 — Plan 018 findings: fix the xfails

BACKLOG.md §*"Plan 018 findings — new rows filed during reliability-floor implementation"*
(`BACKLOG.md:156-167`), eight rows: **RT2-B**, **RT2-C**, **RT7a-redis-claim-guard**,
**RT7b-port-remap** (🔴 must), **WD-1**, **RT9-beanie-migrations** (🟡 should), **RT7-toxiproxy**,
**RT4-ws-scale** (🟢 nice).

Grounded in research briefs
[005 — NATS JetStream ack semantics & stream existence](../design/reliability-release/research/005-nats-jetstream-ack-semantics-and-stream-existence.md),
[006 — Docker restart port stability](../design/reliability-release/research/006-docker-restart-port-stability-and-testcontainers.md),
[007 — MongoDB distributed migration lock](../design/reliability-release/research/007-mongodb-distributed-migration-lock.md).

## Goal

**This plan inverts Plan 018's central Non-goal.** Plan 018 forbade every production-code change
and converted each contract violation it found into `@pytest.mark.xfail(strict=True)` + a BACKLOG
row (`plans/018-…md:22-24`). This plan is where those xfails are *paid off*: `varco_nats` gains
real JetStream redelivery on handler failure and a `ChannelManager` implementation that satisfies
its own ABC; `varco_redis`'s `RedisJobStore` stops refusing a legitimate re-claim for up to
`claim_ttl` after a correct reap; `testkit/varco_chaos` stops assuming a Docker host port survives
a restart, and the in-tree claim that it does (research 002 §1, CLAUDE.md, Plan 018) is corrected
at source; and `varco_beanie`'s migration lock gains the real-Mongo coverage RT9 left behind.

**Every `xfail(strict=True)` marker is removed in the same step as the fix that makes it pass** —
`strict=True` means a fixed bug turns the marker itself red, so a plan that fixes without
unmarking is a plan that leaves the suite broken. The three markers in scope:

| Marker | File | Removed by |
|---|---|---|
| RT2-B | `varco_nats/tests/test_nats_semantics_integration.py:76-87` | Step 8 |
| RT2-C (×3) | `varco_nats/tests/test_nats_channel_integration.py:72-128` | Step 17 |
| RT7a | `varco_redis/tests/test_redis_job_lease_crash.py:60-75` | Step 24 |

After this plan: **zero `xfail(strict=True)` markers remain from Plan 018**, five BACKLOG rows are
closed ✅ with evidence, and three are carried forward with a written disposition rather than
silently dropped.

## Non-goals

- **No new chaos *scenarios*.** RT7b here is a mechanism fix to the three existing restart-based
  chaos modules, not new coverage. `test_kafka_chaos.py`, `test_sa_chaos.py`,
  `test_migration_chaos.py` keep their current assertions verbatim.
- **No Toxiproxy** (RT7-toxiproxy). Plan 018 §RT7-toxiproxy's four ❌s all still hold and none of
  research 006's findings change them — 006 lists Toxiproxy as *option (4)*, and options (1)+(2)
  close this plan's actual problem at a fraction of the cost. Disposition recorded, row stays
  open for 3.1 (§deferrals).
- **No many-connection WS/SSE scale test** (RT4-ws-scale). Still blocked on the undocumented
  GitHub Actions fd limit (research 004 §Evidence Gaps). Row stays open (§deferrals).
- **No WS production-code change** (WD-1). It is a *watch item* about test-margin calibration, not
  a defect; §WD-1 records the one condition under which it becomes a code change.
- **No `RedisJobStore` Lua rewrite.** The atomic-claim-in-Lua redesign (§RT7a-guard's rejected
  alternative) stays a BACKLOG row — this plan makes the guard key correct, not redundant.
- **No new NATS DLQ wiring for exhausted redeliveries.** `$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES`
  (research 005 §B) is a genuine capability varco does not consume; a message that exhausts
  `max_deliver` is `term()`ed and logged here, and the advisory-subject→`NatsDLQ` bridge is filed
  as a new row (§deferrals). Wiring it now would grow RT2-B from S to L.
- **No Phase 4 / Phase 5 (RL-8…RL-13) work.** No version bumps, no release engineering.
- **No `ChannelManager` ABC *signature* change.** §RT2-C-contract tightens two docstrings and adds
  a conformance suite; the five abstract methods keep their exact signatures.

---

## Status corrections to BACKLOG.md's Plan-018-findings table

Verified in source this session, per the U-8 *"verify in source, not from documentation"*
discipline the repo enforces on itself. Apply these in Step 42.

| BACKLOG / scout claim | Reality (verified) |
|---|---|
| RT9-beanie's Evidence cell cites **`varco_beanie/varco_beanie/migration.py`**, and the scout reported *"no production `migration.py` was found"* | **Both are wrong in opposite directions.** It is a **package**, not a module: `varco_beanie/varco_beanie/migration/{migrator,store,indexes,framework,base}.py`. `BeanieMigrator` is at `migration/migrator.py:72`. The row is correctly *sized*; only its path is wrong. |
| RT9-beanie: *"Mongo has no advisory-lock primitive equivalent to Postgres's, so the lock-timeout scenario would need a different mechanism entirely"* | **The mechanism already exists and is already the researched-correct one.** `MigrationStore.acquire()` (`migration/store.py:86-145`) is exactly research 007 §A's sanctioned pattern: one conditional `find_one_and_update(..., upsert=True)` on `{_id: "__lock__", $or: [{expires_at: {$lt: now}}, {owner: owner}]}`, with `_id` uniqueness supplying atomicity and `DuplicateKeyError` read as "another live owner holds it" (`:121-138`). `heartbeat()`/`release()` (`:147-162`) exist, and `BeanieMigrator.upgrade()` polls `acquire()` to a `lock_timeout` deadline and raises `MigrationLockTimeout` (`migration/migrator.py:173-193`) — the same D2 shape as `AlembicMigrator`. **RT9-beanie is a pure test-coverage row, not a design row.** |
| Research 007's *"crash recovery is bounded by the 60-second TTL monitor → set the test timeout to 180 s"* | **Does not apply to varco.** varco's lock expiry is an **application-level `expires_at` predicate evaluated at acquire time**, not a Mongo TTL index — the `$or: [{expires_at: {$lt: now}}, …]` filter reclaims a dead holder's document *immediately* once its `expires_at` has passed. No TTL index, no TTL monitor, no 60–120 s window. Crashed-holder recovery is bounded by `lock_timeout`, so the RT9-beanie tests are seconds-scale and deterministic (§RT9-beanie). Research 007's headline recommendation is *already implemented*; only its test-timing advice is inapplicable. |
| RT7b-port-remap: *"Not yet verified against a native Linux dockerd (the actual GitHub Actions runner)"* | **Research 006 settles it without needing the experiment.** Docker's own Engine API reference states *"The allocated port might be changed when restarting the container"* (006 §A); moby's libnetwork portmapper releases ephemeral ports on unmap and re-requests on map (006 §A); 006 §B concludes the behaviour is **platform-independent and version-stable from moby v1.3.0 to v29.1**, and 006 §F states GHA `ubuntu-latest` (Docker v29.1, native Linux) *"will exhibit identical port reallocation behavior to WSL2"*. **This is designed behaviour, not a WSL2 bug and not a flake** — treat the nightly `chaos` job as already-known-broken on restart, not as pending evidence. |
| Research **002 §1**'s *"CRITICAL FINDING — Port survivorship"* (that docker-py `restart()` preserves the host port), on which Plan 018 §RT7-shape, its Edge-cases table (`:780`), and `testkit/varco_chaos/containers.py:32-46`'s DESIGN block all rest | **Overturned by research 006.** Corrected in-tree in Step 27 (a superseded banner on 002 §1, not a rewrite — the register keeps its history). |
| Scout: *"testcontainers may cache the exposed port"* (research 006 §C Evidence Gap 1) | **Closed by source inspection this session.** `DockerContainer.get_exposed_port()` → `_get_exposed_port()` → `self.get_docker_client().port(c.id, port)` (`.venv/…/testcontainers/core/container.py:247-258`) — a live daemon query on **every** call, no cache. `KafkaContainer.get_bootstrap_server()` (`…/testcontainers/kafka/__init__.py:158-161`) and `PostgresContainer.get_connection_url()` (`…/testcontainers/postgres/__init__.py:73-88`) both re-derive from it. **Re-querying after a restart genuinely works** — which is what makes §RT7b-port's option (1) viable. |
| — (new, found while planning RT2-B) | **`NatsEventBusSettings.ack_wait_seconds` (`varco_nats/config.py:187-188`) is dead configuration.** `_open_jetstream_consumer` calls `self._js.subscribe(subject, durable=…, cb=…, manual_ack=True)` (`bus.py:516-521`) with **no `config=ConsumerConfig(…)`**, so the documented `VARCO_NATS_ACK_WAIT_SECONDS` env var has never reached the broker. Fixed as part of §RT2-B-nak (Step 5), which needs the same call site anyway. |
| — (new, found while planning RT7a) | **`RedisJobStore.try_claim()` leaks its guard key on *every* non-success path, not only on reap.** The `SET NX EX` at `job_store.py:599` is released in the `except` (`:639-642`) and on the future-`run_at` branch (`:622`), but the `raw is None` (`:610-612`) and `status != PENDING` (`:615-617`) returns fall straight out of the `try` with the key still held for `claim_ttl`. Same defect class as the filed row, same fix site (Step 22). |

---

## Design

### §scope — four 🔴 fixed, one 🟡 fixed, one 🟡 dispositioned, two 🟢 deferred ✅

| Row | Severity | This plan | Where |
|---|---|---|---|
| RT2-B | 🔴 must, S | **Fixed** — `nak()`/`term()` + real consumer config | Phase 1 |
| RT2-C | 🔴 must, M | **Fixed** — declaration-registry semantics + a `ChannelManager` conformance suite | Phase 2 |
| RT7a-redis-claim-guard | 🔴 must, S | **Fixed** — reap releases the guard; `try_claim` stops leaking it | Phase 3 |
| RT7b-port-remap | 🔴 must, M | **Fixed** — `ChaosContainer` owns the URL; Kafka pins its host port; the record is corrected | Phase 4 |
| RT9-beanie-migrations | 🟡 should, M→S | **Fixed** (coverage only — the lock already exists, see Status corrections) | Phase 5 |
| WD-1 | 🟡 should, S | **Dispositioned, not changed** — watch item with a written trigger condition | §WD-1 + Step 42 |
| RT7-toxiproxy | 🟢 nice, M | **Deferred to 3.1**, disposition re-affirmed against research 006 | §deferrals |
| RT4-ws-scale | 🟢 nice, M | **Deferred**, unchanged precondition | §deferrals |

Phase order is severity-first, then dependency: Phases 1–3 are independent production fixes in
three different packages and could run in any order; Phase 4 is test infrastructure that unblocks
the nightly `chaos` job; Phase 5 is the 🟡 that only exists because Plan 018 ran out of room.
**Cut line: Phase 5.** Cutting it leaves every 🔴 closed and every remaining row dispositioned.

### §xfail-pairing — the fix and the unmarking are one step, never two ✅

CLAUDE.md §Test Conventions: *"`strict=True` means the xfail itself fails loudly if the underlying
bug is ever fixed, so the marker doesn't silently rot."* That property is exactly what makes a
two-step "fix now, unmark later" sequence broken — the tree is red in between.

- ✅ The suite is green at every commit boundary.
- ✅ The xfail's `reason=` text is the fix's acceptance criterion, already written down by
  Plan 018 — the implementer does not have to re-derive what "fixed" means.
- ❌ A commit therefore touches both `varco_*/varco_*/` and `varco_*/tests/`, which the repo's
  Plan-018-era convention treated as a smell. Accepted and inverted **for this plan only**:
  this is the plan whose entire purpose is production fixes.

**Rejected — remove the markers first, land the fixes after.** ✅ Smaller diffs. ❌ Every
intermediate commit is red on `make integration-test`, and a bisect through the range is
meaningless. Rejected.

**Rejected — flip `strict=True` → `strict=False` and leave the tests xfail-tolerant.** ✅ Never
red. ❌ Destroys the one property the marker exists for and would hide a regression of the very
bug just fixed. Rejected outright.

### §RT2-B-nak — `nak()` on handler failure, `term()` at `max_deliver`, `ack()` on success ✅

Today `_on_message` (`varco_nats/varco_nats/bus.py:525-571`) acks in a `finally` regardless of
outcome, and says so in its own docstring (`:532-536`): *"ack AFTER dispatch, whether or not a
handler raised … JetStream only redelivers on a process crash."* A handler that merely raises is
never retried, so `AT_LEAST_ONCE` delivers *at-least-once*, never *at-least-one-**successful**-
dispatch*, which is the guarantee `test_at_least_once_redelivers_after_handler_raises` asserts.

Research 005 §A supplies the API: **`msg.nak(delay=None)`** is *"the sanctioned API for
handler-failure retry"*; `msg.term()` *"terminates a message and disables all future
redeliveries"*; there is **no** `nak_with_delay()`. Research 005 §D: an explicit `nak()` triggers
**immediate** redelivery, where simply not acking waits out `ack_wait` (default 30 s) — the
difference between a 1-second test and a 35-second one.

**Chosen — outcome-driven acknowledgement, with a bounded redelivery count:**

```
_on_message(msg)
  AT_MOST_ONCE          → ack()  before dispatch      (unchanged, bus.py:552)
  deserialization error → term()                       (a poison payload can never succeed)
  dispatch succeeded    → ack()
  dispatch raised:
        num_delivered >= max_deliver → term() + WARNING (redelivery budget exhausted)
        otherwise                    → nak()            (immediate redelivery, research 005 §D)
```

with `msg.metadata.num_delivered` (research 005 §C — *"direct attribute access, no getter"*) as
the delivery counter, and `_safe_nak`/`_safe_term` mirroring the existing `_safe_ack`
(`bus.py:573-594`) so an ack-path failure can never kill the consumer.

**The consumer must actually be configured**, which it currently is not (Status corrections):
`_open_jetstream_consumer` (`bus.py:516-521`) gains
`config=ConsumerConfig(ack_wait=…, max_deliver=…)`, wiring the already-documented-but-dead
`ack_wait_seconds` (`config.py:187`) and a **new** `NatsEventBusSettings.max_deliver: int = 5`
(env `VARCO_NATS_MAX_DELIVER`). Research 005 §B: without `max_deliver`, JetStream's default is
unlimited redelivery — a permanently-failing handler naked in a loop is an infinite hot loop, and
shipping that would be strictly worse than the bug being fixed. **The bound is load-bearing, not
polish.**

- ✅ Closes RT2-B with the canonical nats-py pattern — research 005's Librarian's Note:
  *"Redelivery semantics will immediately become correct."*
- ✅ Makes `AT_LEAST_ONCE` mean the same thing on NATS as `KafkaEventBus`'s
  commit-after-successful-dispatch, which `config.py:74-80` already claims it mirrors.
- ✅ Fixes the dead `ack_wait_seconds` at the one call site that has to change anyway.
- ❌ **Behaviour change for existing deployments.** A handler that raises today is dropped;
  after this it is retried up to `max_deliver` times. An app with a non-idempotent NATS handler
  and no `@listen(retry_policy=…)` will now see repeat side-effects. This is a CHANGELOG
  **BREAKING**-flagged behaviour change (Step 41), not a silent fix.
- ❌ Interaction with `ErrorPolicy` is subtle and must be documented: only `COLLECT_ALL`
  (the default, `bus.py:178`) and `FAIL_FAST` propagate a handler exception out of `_dispatch`
  (`bus.py:629-649`). Under `FIRE_FORGET` the exception is swallowed, the dispatch "succeeds",
  and the message is acked — i.e. **`FIRE_FORGET` opts out of redelivery**, which is coherent
  with its name but must be written down (Steps 6, 39).

**Rejected — do not ack at all and let `ack_wait` expire.** ✅ One deleted line; no new API
surface. ❌ Research 005 §D: redelivery latency becomes `ack_wait` (30 s default) per attempt,
turning the acceptance test into a multi-minute integration test and making every real retry
30 s slow. ❌ Indistinguishable, from the broker's side, from a consumer that hung — it discards
information the consumer actually has. Rejected.

**Rejected — `nak(delay=…)` with an exponential backoff schedule.** ✅ Server-side backoff for
free (research 005 §B's `backoff` list). ❌ varco already owns a retry model
(`varco_core.resilience.RetryPolicy`, reused by `@listen(retry_policy=…)` and `AbstractJobRunner`);
CLAUDE.md's standing rule is *"no second retry model"*. A broker-side backoff schedule configured
separately from the handler-side one is exactly that second model. Rejected — bare `nak()`, with
the handler-level policy remaining the place backoff is expressed.

**Rejected — route exhausted messages to `NatsDLQ` via the max-deliveries advisory.** ✅ The
complete story (research 005 §B: *"there is no automatic DLQ"*, applications must subscribe to
`$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.<STREAM>.<CONSUMER>`). ❌ A second subscription with
its own lifecycle, its own failure modes and its own `@PostConstruct` wiring — an L-sized feature
bolted onto an S-sized fix, and `@listen(dlq=…)` already gives handlers a DLQ path.
Rejected → BACKLOG row (§deferrals).

### §RT2-C-contract — `channel_exists()` means **declared-or-present**, and the ABC is the winner ✅

Three implementations disagree today:

| Backend | `channel_exists()` today | Satisfies `declare → exists`? |
|---|---|---|
| Kafka (`varco_kafka/channel.py:302-350`) | broker topic metadata | ✅ (a topic is a real object) |
| Redis (`varco_redis/channel.py:192-209`) | local declaration registry | ✅ |
| NATS (`varco_nats/channel.py:377-415`) | *"the channel's subject currently carries any message"* | ❌ |

**Verdict: the ABC wins, and the invariant it means is the round-trip.**
`varco_core/varco_core/event/channel.py:167-179` says *"Return `True` if the channel exists on the
backend"*, and `declare_channel` (`:129-131`) says *"Create the channel on the backend if it does
not already exist. Idempotent."* Read together, the only contract a caller can act on is
**`declare_channel(c)` ⟹ `channel_exists(c)` is `True` until `delete_channel(c)`**. A predicate
that answers `False` immediately after a successful `declare_channel` is not a weaker reading of
that docstring — it is a different question.

**Is Redis's local registry also wrong? No — and the ABC already says so.** `delete_channel`'s
own Edge-cases block (`varco_core/varco_core/event/channel.py:162-163`) reads *"Redis: Pub/Sub
channels are ephemeral; this updates the local declaration registry only."* The ABC has therefore
already blessed a declaration registry as a legitimate implementation where the broker has no
per-channel object. NATS's shape is the *same* shape — every channel is a subject under one
stream's `{prefix}.>` wildcard (`varco_nats/channel.py:196-203, 281-292`), so there is no
per-channel object there either. Redis is conformant; NATS simply answered a different question.

**Chosen — NATS gets a declaration registry, plus broker evidence, plus a separately-named
message-count predicate:**

```python
declare_channel(c)  # ensure the stream (unchanged) AND record c in self._declared
channel_exists(c)   # stream exists AND (c in self._declared OR subject carries messages)
list_channels()     # sorted(set(self._declared) | {subjects carrying messages})
delete_channel(c)   # purge the subject (unchanged) AND discard c from self._declared
channel_has_messages(c)   # NEW, NATS-only: today's `channel_exists` body, verbatim
```

The `OR subject carries messages` half is what keeps this better than a pure registry: a channel
declared by *another process* is still discoverable once it carries data, so `list_channels()`
remains useful for operational introspection — which is the value today's implementation has and
the reason it must not simply be deleted. `channel_has_messages()` preserves the exact current
predicate under an honest name (not on the ABC — it is a NATS affordance, and adding it to the
ABC would force Kafka/Redis to answer a question their brokers cannot).

**Enforcement, not documentation** — `testkit/varco_conformance/channel_manager.py` (new, the
fifth module alongside `event_bus`/`cache`/`job_store`/`dlq`) holds the four round-trips as a
shared contract suite, subclassed by Kafka, Redis and NATS. This is the mechanism CLAUDE.md
already prescribes for exactly this failure ("A package's suite is green but the ABC is violated")
and it is why RT2-C could not be caught before: **`ChannelManager` is the one core ABC with no
conformance module.**

- ✅ The round-trip becomes machine-checked across all three backends, so the next backend cannot
  reintroduce a private interpretation.
- ✅ The three xfail'd assertions in `test_nats_channel_integration.py` become green with no test
  edits beyond deleting the markers; the currently-green
  `test_delete_channel_then_channel_exists_is_false` (`:102-110`) stays green because
  `delete_channel` now also discards the registry entry.
- ❌ The registry is **per-manager-instance and process-local** — a fresh `NatsStreamManager` in
  another pod reports `False` for a channel declared elsewhere that has never carried a message.
  Identical to Redis's documented limitation; stated in the docstring and in the conformance
  suite's own contract text rather than pretended away.
- ❌ `declare_channel`'s `channel` argument stops being *"only used for logging"*
  (`varco_nats/channel.py:292`) — a docstring three other places quote. All of them are updated
  in the same commit (Steps 18-19, 39-40).

**Rejected — make NATS's "has messages" reading the contract and change Kafka + Redis to match.**
✅ One implementation already conforms; no NATS change. ❌ It would break Kafka (a declared empty
topic genuinely exists) and Redis (pub/sub has no message store at all — the predicate is
*unimplementable* there), i.e. it would convert one non-conformant backend into two. And it
contradicts the ABC's own `declare_channel` docstring. Rejected.

**Rejected — one JetStream stream per channel, so a channel becomes a real broker object.** ✅ The
"truest" existence predicate: research 005 §E's `jsm.stream_info(name)` raising `NotFoundError`,
exactly as the brief's Librarian's Note suggests. ❌ It is a **wire-format and topology change**,
not a bug fix: `NatsEventBusSettings.stream_name` (`config.py`) is a single stream with a
`{prefix}.>` wildcard that the bus, the DLQ and every existing deployment share; splitting it
per-channel changes retention, replica and dedup-window management from one object to N, and
breaks every existing stream. Research 005 §E's advice is right for a *stream*-keyed manager;
varco's manager is *subject*-keyed. Rejected — and this divergence from the brief is deliberate
and recorded, not an oversight.

**Rejected — leave NATS as-is and relax the ABC docstring to "backend-defined".** ✅ Zero code.
❌ An ABC whose contract is "whatever each backend decided" is not a contract; `ChannelManager`
would become undocumentable and the conformance suite unwritable. Rejected.

### §RT7a-guard — the reaper releases the guard it did not create; `try_claim` stops leaking its own ✅

`RedisJobStore.try_claim()` acquires a `SET NX EX claim_ttl` guard key (`job_store.py:595-599`,
default `claim_ttl=30`) whose stated purpose (`:566-577`) is *"only one caller succeeds even under
concurrent invocations"* and *"EX TTL auto-expires if the runner crashes after claiming but before
updating the job"*. `reap_expired_leases()` (`:721-757`) correctly resets the job to `PENDING` and
advances `lease_epoch`, but never touches the guard — so worker B's legitimate re-claim is refused
for up to 30 s after a *correct* reap. `SAJobStore.reap_expired_leases()` has no second guard key
and does not exhibit it: a genuine cross-backend disagreement in a four-method fencing protocol.

**Chosen — two changes at the same site, both narrow:**

1. `reap_expired_leases()` deletes `self._claim_key(job.job_id)` for **each job it actually
   reaps**, immediately after the `save(new_job)` that publishes the advanced `lease_epoch`.
2. `try_claim()` releases the guard on **every** non-success path (Status corrections: `:610-612`
   and `:615-617` currently leak it), by hoisting the body into a `try/…/finally` guarded by a
   `claimed = False` flag set only just before the successful `return running_job`.

Ordering in (1) is load-bearing: `save()` first, `delete(claim_key)` second. A crash between them
leaves the job correctly `PENDING` with the guard expiring on its own TTL — i.e. exactly today's
(merely slow) behaviour, never a lost job.

- ✅ The two backends agree again, which is the property `test_redis_job_lease_crash.py`'s twin
  with `test_sa_job_lease_crash.py` exists to hold.
- ✅ (2) is strictly correct by ownership: those early returns are inside the `try` **after this
  call's own `SET NX`**, so the key being released is unambiguously this caller's.
- ✅ The green sibling `test_renewed_lease_keeps_a_second_worker_locked_out`
  (`test_redis_job_lease_crash.py:138-170`) stays green and does not become vacuous: worker B is
  refused there because the job is `RUNNING` (`job_store.py:615-617`), not because of the guard —
  verified by reading the branch order. **If that test goes red, the fix is wrong; do not adjust
  the test.**
- ❌ In (1) the reaper deletes a key another (dead) worker created. Justified precisely because
  reap is the "previous owner is gone" authority and pairs the delete with a `lease_epoch`
  advance — but it *is* a cross-owner delete and it is why §Risks carries an explicit assumption
  about it.
- ❌ The guard's protective window after a reap shrinks from `claim_ttl` to zero. That window was
  never load-bearing (it guarded an *in-flight* claim, and after a reap there is no in-flight
  claim), but the claim path's non-atomicity is unchanged and still documented at `:574-577`.

**Rejected — shorten the default `claim_ttl` (e.g. 30 s → 2 s).** ✅ A one-character fix that makes
the failing test pass (confirmed by Plan 018's own ad-hoc probe: green at `claim_ttl=1`). ❌ It
does not fix anything — it shortens the window in which the store is wrong, and simultaneously
weakens the crash-recovery guarantee the TTL exists for. This is the "weaken the test to match
observed behaviour" move in disguise. Rejected.

**Rejected — delete the guard-key concept and make `try_claim` a single atomic Lua script.** ✅ The
structurally correct end state: one atomic check-and-claim, no second key, no non-atomicity note
at `:574-577`, and the module docstring already floats it (*"a real multi-replica deployment should
extend this with a Lua claim script"*). ❌ Substantially larger than the filed row (a Lua script
plus its own N-concurrent-claimers integration test), and it changes the `lease_ttl=None`
no-lease path — which has no `lease_epoch` fence at all and therefore genuinely relies on the
guard key. Rejected **for this plan** and filed as a row (§deferrals).

### §RT7b-port — the container owns its URL; Kafka pins its host port; the record is corrected ✅

Research 006 §A settles the mechanism: Docker's own Engine API reference states *"The allocated
port might be changed when restarting the container"*, and moby's libnetwork portmapper releases
ephemeral ports on unmap and re-requests on map. 006 §B: platform-independent, version-stable
moby v1.3.0 → v29.1. 006 §F: GitHub Actions `ubuntu-latest` behaves identically. **Research 002 §1
is simply wrong** and everything built on it must move.

006 §D enumerates four options. The plan takes **(1) re-query, universally** and **(2) pin,
for Kafka only** — and the reason it is not (1) alone is a second finding from source:

> `KafkaContainer.tc_start()` (`.venv/…/testcontainers/kafka/__init__.py:163-184`) writes
> `/tc-start.sh` **into the container** with
> `export KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://{host}:{port}` as a **literal**, resolved from
> `get_exposed_port()` at first boot. A docker `restart()` re-runs that same on-disk script, so
> the broker comes back **advertising the pre-restart host port**. Re-querying the bootstrap
> address is therefore *necessary but not sufficient* for Kafka: the client would connect to the
> new port, receive metadata pointing at the old one, and fail on the first produce/fetch.

**Chosen:**

- **`ChaosContainer` gains URL ownership** (`testkit/varco_chaos/containers.py`):
  `__init__(container, *, ready=…, url_factory: Callable[[DockerContainer], str] | None = None)`
  and a `url` **property** that calls `url_factory(self._container)` **fresh on every access**
  (never memoised). `restart()` re-derives and logs the URL after `wait_ready()`. A container
  constructed without a `url_factory` raises `ValueError` on `.url` — the same fail-loudly shape
  `wait_ready()` already uses for a missing `ready` predicate (`containers.py:192-196`).
  This is what makes it structurally impossible for a caller to cache a stale DSN.
- **`_CHAOS_DSN` / `_CHAOS_BOOTSTRAP` module dicts are deleted** (`varco_sa/tests/test_sa_chaos.py:45,66`,
  `varco_kafka/tests/test_kafka_chaos.py:51-55,74`, and the equivalent in
  `varco_fastapi/tests/test_migration_chaos.py`). Every read site becomes `chaos.url`.
- **Kafka additionally pins its host port** via a new `testkit/varco_chaos/ports.py`
  `reserve_host_port()` helper (bind `("", 0)`, read the port, close, return it) passed to
  `KafkaContainer().with_bind_ports(9093, port)` (`.venv/…/core/container.py:116-132`) in
  `kafka_container_chaos`. A pinned publish is not drawn from the ephemeral pool, so it is not
  re-allocated — and the baked advertised listener stays correct across the restart.
- **The record is corrected in four places** (Step 27): a *superseded* banner on research 002 §1
  pointing at 006; CLAUDE.md's *"Chaos `restart()` port instability"* pitfall row rewritten from
  "observed once on WSL2, may not reproduce" to "documented Docker behaviour, always"; Plan 018's
  §RT7-shape rejection paragraph (`:104-109`), Edge-cases row (`:780`) and Risks bullet (`:864-870`)
  annotated as superseded by this plan; and `ChaosContainer`'s own DESIGN block (`:32-46`) rewritten.

- ✅ Fixes the class of bug, not the three instances: any future chaos module gets URL freshness
  for free and cannot opt into the old pattern without deleting a property.
- ✅ Keeps `restart()` as the mechanism (a true cold-broker recovery) rather than downgrading
  three tests to `paused()`, which would prove strictly less.
- ✅ Zero new dependencies — `socket` from the stdlib and a testcontainers method already present.
- ❌ Two mechanisms, and a reader must know why Kafka is special. Mitigated by putting the
  `tc-start.sh` finding verbatim in the `kafka_container_chaos` fixture docstring, where the
  person debugging it will be.
- ❌ `reserve_host_port()` has an unavoidable **TOCTOU window** between closing the probe socket
  and docker binding the port. Small, and a collision fails loudly at container start (not
  mid-test); ⚠️ recorded in §Risks.

**Rejected — pin the host port for all three restart-based containers.** ✅ Uniform; one mechanism
to explain. ❌ Extends the TOCTOU collision risk to containers that do not need it (Postgres
re-queries perfectly well), and on a busy CI runner every pinned port is a new way for an
unrelated test session to collide. Rejected — pin only where re-querying provably cannot work.

**Rejected — switch the three restart tests to `paused()`.** ✅ Research 006 §D option (3): pause
*never* remaps a port, and `ChaosContainer.paused()` already exists and is verified green in
`test_redis_chaos.py` / `test_nats_health_chaos.py`. ❌ It changes what the tests assert: a paused
broker black-holes (the process is frozen, sockets stay open), it never *restarts*. The outbox
tests' whole claim is *"entries survive a broker that is genuinely gone and are republished when
it returns"*, and `test_migration_chaos.py` needs the lock-holding **connection** to actually die.
Downgrading them would silently weaken three chaos guarantees to buy a port fix.
Rejected — kept as the documented fallback if pinning proves flaky on CI.

**Rejected — Toxiproxy as the stable front door (006 §D option 4).** ✅ Solves port stability *and*
unlocks latency/bandwidth faults. ❌ Every ❌ in Plan 018 §RT7-toxiproxy still stands (no
testcontainers-python module, unresolved Python client, untested on Actions, every connection URL
rerouted). Paying an L-sized dependency to fix an S-sized port lookup is the wrong trade.
Rejected → stays a 3.1 row.

### §RT9-beanie — coverage, not design: the researched lock is already implemented ✅

Per Status corrections, `MigrationStore.acquire()/heartbeat()/release()`
(`varco_beanie/varco_beanie/migration/store.py:86-162`) already implement research 007 §A's
`findOneAndUpdate` + upsert + `_id`-uniqueness pattern with an application-level `expires_at`
predicate, and `BeanieMigrator.upgrade()` (`migration/migrator.py:160-252`) already polls to a
`lock_timeout` deadline, raises `MigrationLockTimeout`, and cancels its heartbeat in a `finally`.
Nothing here needs to be built. What is missing is that **none of it has ever run against a real
mongod** — `varco_beanie/tests/test_beanie_migrator.py` and `test_beanie_migration_lock.py` both
drive a hand-rolled fake collection.

**Chosen — one new integration module, `varco_beanie/tests/test_beanie_migration_integration.py`,
on the existing session-scoped `mongo_url` fixture (`varco_beanie/tests/conftest.py:48-73`), with
a `uuid4().hex[:8]`-suffixed database name per test** (the standing per-test namespacing rule):

1. `test_index_mode_upgrade_creates_indexes_and_is_idempotent` — the index-mode lifecycle
   (`migration/indexes.py:69-97` compares `(key_fields, unique)` signatures): upgrade, assert the
   indexes exist via `list_indexes()`, upgrade again, assert no error and no duplicate index.
   Research 007 §E confirms `createIndex` is idempotent (MongoDB 4.4+), so the second call is a
   documented no-op — this test asserts varco's reconciliation agrees.
2. `test_two_concurrent_migrators_serialize_and_only_one_applies` — two `BeanieMigrator`s
   `asyncio.gather`ed against one database; exactly one reports applied revisions, the other
   returns `skipped_locked=True` (`migration/migrator.py:186-206`).
3. `test_lock_timeout_raises_when_holder_never_releases` — the test itself writes a live lock
   document with a far-future `expires_at`; a migrator with a 1 s `lock_timeout` raises
   `MigrationLockTimeout`. Deterministic — the holder is the test.
4. `test_crashed_holder_lock_is_reclaimed_after_expiry` — write a lock document whose `expires_at`
   is already in the past (a holder that died without releasing), then assert the next
   `acquire()`/`upgrade()` proceeds. **Seconds, not 180 s** — the `$or: [{expires_at: {$lt: now}}]`
   filter is evaluated at acquire time, so research 007 §B's 60–120 s TTL-monitor window does not
   apply (Status corrections).
5. `test_duplicate_key_on_concurrent_upsert_is_read_as_lock_lost` — drive two `acquire()` calls
   racing on an absent lock document and assert exactly one wins, exercising `store.py:121-138`'s
   `DuplicateKeyError` branch against a **real** mongod, where the fake can never raise it.

- ✅ Closes RT9's stated residual with real-broker evidence and no production change.
- ✅ No chaos marker and no container lifecycle control needed — every scenario is expressible by
  writing the lock document the test wants, which is *more* deterministic than killing anything.
- ✅ `varco_beanie` is already in `scripts/integration_tests.sh:109`'s
  `ALL_INTEGRATION_PACKAGES` — zero runner changes.
- ❌ (5) depends on Mongo raising `E11000` on the racing upsert, which is timing-dependent; if it
  proves flaky, drive the two `acquire()` calls against a **pre-seeded live lock** instead (the
  deterministic half of the same branch) rather than retrying it into health.
- ❌ Does not cover data-mutation migrations (varco's Beanie migrator is index-mode). Out of
  scope and unchanged by this plan.

**Rejected — add a TTL index on `expires_at` to match research 007 §A's full recipe.** ✅ Lock
documents self-clean; matches mongock/migrate-mongo. ❌ varco's acquire-time `expires_at`
predicate already reclaims a dead holder's lock *immediately*, whereas a TTL index would add a
60–120 s deletion lag (007 §B) that buys nothing, and on a standalone (non-replica-set) mongod —
which is exactly what `MongoDbContainer("mongo:7")` starts — 007 §B states the TTL background
thread does not run at all, so the index would be inert in tests and misleading in docs.
Rejected. **The absence of a TTL index here is correct and deliberate; write that down** (Step 33).

**Rejected — a `varco_beanie` chaos test that restarts mongod.** ✅ Symmetry with
`test_migration_chaos.py`. ❌ Needs the `chaos` marker registered in `varco_beanie/pyproject.toml`
(it is not — only `integration` is, at `:70-72`), a module-scoped `mongo_container_chaos` fixture,
and it would prove less than test (4): a restarted mongod still holds the lock document, so
recovery is governed by `expires_at`, which (4) tests directly and deterministically. Rejected.

### §WD-1 — a watch item with a written trigger, not a code change ✅

`varco_ws/tests/test_ws_backpressure_integration.py:38-51` needs `_N=6000` × 64 KiB (~384 MB in
flight) before assertion (1) (`slow_received < _N`) engages; the module records that 2000×16 KiB
and 3000×64 KiB passed **vacuously**. Root cause (module docstring `:24-25`): uvicorn's
`websockets_impl` buffers server-side writes with **no `write_limit`** applied, so the client's
`max_queue=1` does not propagate into varco's per-client `asyncio.Queue` until the OS/uvicorn
buffer saturates. Plan 018 §RT4-backpressure declined `write_limit` deliberately.

**Chosen — change nothing, and state the trigger condition.** The margin is thin but the test is
3/3 clean locally, the calibration table is in the module, and the module's own Edge-cases table
already governs the failure mode (*"raise the volume, never relax the assertion"*). The row stays
open as a **watch item** with one written escalation rule, recorded in BACKLOG (Step 42):

> If `test_ws_backpressure_integration.py` fails on CI **twice** (any two runs, not necessarily
> consecutive), the response is **not** a third volume increase — it is to thread
> `ws_max_queue`/`write_limit` through the test fixture's `uvicorn.Config` so backpressure
> engages at a deterministic byte count, converting a calibrated test into a specified one.

- ✅ No production change for a test-margin issue, and no speculative `uvicorn.Config` plumbing
  for a test that currently passes.
- ✅ The escalation is pre-decided, so the next person to see it red does not re-litigate.
- ❌ ~384 MB and ~95 s per run is a real cost on every integration run until the trigger fires.
  Accepted — it is the price of asserting the module's central DESIGN claim over a real socket.

**Rejected — set `write_limit` on the server now.** ✅ Would let `_N` drop by an order of
magnitude. ❌ It changes the *production* uvicorn configuration path (or forks the test fixture
away from it), so the test would no longer exercise the same server configuration real users run —
trading a slow honest test for a fast unrepresentative one. Rejected until the trigger fires.

### §deferrals — three rows carried forward, each with a disposition ✅

| Row | Disposition |
|---|---|
| **RT7-toxiproxy** (🟢, 3.1) | Re-affirmed against research 006. 006 §D lists it as option (4) and the ecosystem's choice *"when deterministic, repeatable fault injection is needed (latency, bandwidth, drops)"* — none of which this plan needs. Plan 018 §RT7-toxiproxy's four ❌s are unchanged. **Precondition for revisiting: research 002 §Evidence Gaps 1–4 close** (an upstream `testcontainers.toxiproxy` Python module, or a vetted client). |
| **RT4-ws-scale** (🟢) | Unchanged: blocked on undocumented GHA file-descriptor limits (research 004 §Evidence Gaps). **Precondition: a measured fd limit on `ubuntu-latest`**, at which point the connection count can be chosen rather than guessed. |
| **NATS max-deliveries advisory → DLQ** (🟡, **new row**) | Filed by §RT2-B-nak. A message that exhausts `max_deliver` is `term()`ed and logged; the `$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.<STREAM>.<CONSUMER>` subscription that would route it to `NatsDLQ` (research 005 §B) is a separate feature with its own lifecycle. |
| **`RedisJobStore` atomic Lua claim** (🟡, **new row**) | Filed by §RT7a-guard. Would make the guard key redundant and close the `:574-577` non-atomicity note; needs an N-concurrent-claimers integration test and must handle the `lease_ttl=None` path that has no epoch fence. |

---

## Steps

Ordered TDD-first **where the test does not already exist**. For RT2-B, RT2-C and RT7a the failing
test *does* already exist as an `xfail(strict=True)` — those steps therefore begin by *running*
it (confirming the xfail), and the marker deletion is bundled with the fix per §xfail-pairing.

### Phase 0 — baseline (5 min, no edits)

1. [x] **Confirm the starting state.** Run, and record the counts in the Phase-1 commit message:
       `uv run pytest varco_nats/tests/test_nats_semantics_integration.py varco_nats/tests/test_nats_channel_integration.py -m integration -q`
       (expect 1 + 3 `xfail`) and
       `uv run pytest varco_redis/tests/test_redis_job_lease_crash.py -m integration -q`
       (expect 1 `xfail`, 1 passed). A marker that reports `xpass` here means the bug is already
       gone and the corresponding phase is a no-op — stop and re-scope rather than "fixing" it.

### Phase 1 — RT2-B: NATS redelivers on handler failure (§RT2-B-nak)

2. [x] `varco_nats/varco_nats/config.py` — add `max_deliver: int = 5` to `NatsEventBusSettings`
       (env `VARCO_NATS_MAX_DELIVER`), with a docstring naming research 005 §B: JetStream's
       default is unlimited redelivery, so an unbounded `nak()` loop is the failure mode this
       field exists to prevent. Update the `AT_LEAST_ONCE` prose at `config.py:74-80` — it
       currently says redelivery happens only *"on a crash"*.
3. [x] `varco_nats/tests/fakes.py` — extend `FakeMsg` (`:99`) to record `nak()`/`term()` calls
       alongside its existing `ack()` recording, and expose a settable
       `metadata.num_delivered` (research 005 §C — `msg.metadata.num_delivered`, direct attribute
       access). No behaviour change to existing users of the fake.
4. [x] `varco_nats/tests/test_nats_bus.py` — **failing unit tests first**, one per branch of the
       §RT2-B-nak table: handler raises under `AT_LEAST_ONCE` → `nak()` called, `ack()` not;
       handler succeeds → `ack()`, no `nak()`; `num_delivered >= max_deliver` + raise → `term()`,
       no `nak()`; deserialization failure → `term()` regardless of `num_delivered`;
       `AT_MOST_ONCE` → `ack()` before dispatch, unchanged even when the handler raises;
       `ErrorPolicy.FIRE_FORGET` + raising handler → `ack()` (the exception never leaves
       `_dispatch`, `bus.py:629-640`).
5. [x] `varco_nats/varco_nats/bus.py` — implement. (a) `_open_jetstream_consumer` (`:516-521`):
       pass `config=ConsumerConfig(ack_wait=self._config.ack_wait_seconds, max_deliver=self._config.max_deliver)`
       to `self._js.subscribe(...)`, wiring the dead `ack_wait_seconds` (Status corrections).
       (b) `_on_message` (`:525-571`): replace the unconditional `finally`-ack with the outcome
       table in §RT2-B-nak, tracking deserialization failure separately from dispatch failure.
       (c) add `_safe_nak` / `_safe_term` mirroring `_safe_ack` (`:573-594`) — neither may ever
       propagate. Rewrite `_on_message`'s docstring (`:526-546`) — its current text is the
       specification of the bug.
6. [x] Same file — `DESIGN:` block on `_on_message` recording ✅ immediate redelivery via `nak()`
       vs ❌ waiting out `ack_wait` (research 005 §D), ✅ bounded by `max_deliver` vs ❌ infinite
       nak loop (005 §B), and the `ErrorPolicy.FIRE_FORGET` opt-out (§RT2-B-nak's second ❌).
7. [x] **Verify unit:** `uv run pytest varco_nats/tests/test_nats_bus.py varco_nats/tests/test_nats_config.py -q`
       — all green, including the pre-existing tests (a `_on_message` rewrite is exactly the
       change that regresses them).
8. [x] `varco_nats/tests/test_nats_semantics_integration.py` — **delete the
       `@pytest.mark.xfail(...)` block at `:76-87`** and its `_REDELIVERY_TIMEOUT` comment about
       ack-wait-driven latency if `nak()` makes it stale. Add
       `test_at_least_once_stops_redelivering_after_max_deliver`: a permanently-raising handler on
       a bus with `max_deliver=3`; assert deliveries stop at 3 and do not grow — the guard against
       "fixed" meaning "infinite loop".
9. [x] **Verify integration:** `uv run pytest varco_nats/tests/ -m integration -q` (bound it:
       `timeout 480`). Acceptance: `test_at_least_once_redelivers_after_handler_raises` **passes**;
       `test_at_most_once_does_not_redeliver_after_handler_raises` still passes (the documented
       weakness must survive the fix); no `xfail`/`xpass` reported. Run **twice** — redelivery
       tests are timing-sensitive.
       Commit: `fix(nats): nak on handler failure, term at max_deliver — closes RT2-B`.

### Phase 2 — RT2-C: `channel_exists()` means declared-or-present (§RT2-C-contract)

10. [x] `varco_core/varco_core/event/channel.py` — tighten `channel_exists`'s docstring
        (`:166-179`) to state the round-trip invariant explicitly: *"`declare_channel(c)` implies
        `channel_exists(c)` is `True` until `delete_channel(c)`"*, plus an Edge-cases note that
        backends without a per-channel broker object (Redis pub/sub, NATS subjects) satisfy this
        via a **process-local declaration registry**, so a manager in another process may report
        `False` for a channel it never declared itself. Mirror the note on `list_channels`
        (`:181-196`). **No signature change.**
11. [x] `testkit/varco_conformance/channel_manager.py` (**new**) — `ChannelManagerConformance`,
        the fifth conformance module, following `event_bus.py`'s shape exactly: an abstract
        `manager` fixture (raising `NotImplementedError`), a class name deliberately **not**
        `Test*`, and four contract tests — declare→exists; declare→`list_channels()` contains;
        delete→not exists; declare twice is idempotent and still exists. Module docstring states
        the contract in prose and names the process-local-registry carve-out.
12. [x] `testkit/varco_conformance/__init__.py` — export it alongside the existing four.
13. [x] `varco_nats/tests/test_nats_channel_integration.py` — add
        `class TestNatsChannelManagerConformance(ChannelManagerConformance)` with a `manager`
        fixture yielding a started `NatsStreamManager` on a `uuid4().hex[:8]`-namespaced stream,
        deleting the stream in a `finally` (reuse the existing `_manager` helper at `:46-69`).
        **Expected red** at this point — that is the failing test for Step 15.
14. [x] `varco_kafka/tests/` + `varco_redis/tests/` — the same two thin subclasses against their
        existing channel-manager fixtures. **Expected green immediately** (both already conform);
        if either is red, that is a new finding and gets the Plan-018 treatment
        (`xfail(strict=True)` + a BACKLOG row), *not* a widening of the contract.
15. [x] `varco_nats/varco_nats/channel.py` — implement §RT2-C-contract:
        `self._declared: dict[str, ChannelConfig | None] = {}` in `__init__` (`:232-241`);
        `declare_channel` (`:281-340`) records it (and the `channel` argument stops being
        "logging only" — fix `:292` and the class docstring at `:200-203, 224-229`);
        `channel_exists` (`:377-415`) becomes *stream exists AND (declared OR subject carries
        messages)*; `list_channels` (`:417-451`) returns the sorted union; `delete_channel`
        (`:342-375`) purges **and** discards the registry entry. Add
        `channel_has_messages(channel) -> bool` carrying today's `channel_exists` body verbatim
        and today's docstring, under the honest name.
16. [x] Same file — `DESIGN:` block on the class recording ✅ ABC round-trip satisfied /
        ✅ broker evidence retained via the `OR carries messages` half / ❌ registry is
        process-local / ❌ **rejected**: one stream per channel (research 005 §E's shape) as a
        topology and wire-format change, per §RT2-C-contract.
17. [x] `varco_nats/tests/test_nats_channel_integration.py` — **delete
        `_CHANNEL_EXISTS_ABC_GAP_REASON` (`:72-79`) and all three `@pytest.mark.xfail` decorators
        (`:82, :92, :113`)**, and rewrite the module docstring's ⚠️ paragraph (`:10-18`) to state
        the settled contract instead of the gap.
18. [x] `varco_nats/tests/test_nats_channel.py` — extend the unit suite (against
        `fakes.FakeJetStream`) for the registry paths: declare→exists with an **empty** stream;
        delete→not exists even while the subject still carries messages elsewhere;
        `channel_has_messages` returns the old semantics; `list_channels` unions registry and
        subject counts without duplicates.
19. [x] **Verify:** `uv run pytest varco_nats/tests/ varco_redis/tests/ varco_kafka/tests/ -m integration -q`
        (bound: `timeout 480`, run per-package if the Kafka boot dominates) plus the three unit
        suites. Acceptance: all four tests in `test_nats_channel_integration.py` green, three
        conformance subclasses green, zero xfails.
        Commit: `fix(nats): channel_exists honours the declare→exists contract; add ChannelManager conformance suite — closes RT2-C`.

### Phase 3 — RT7a: the reaper releases the claim guard (§RT7a-guard)

20. [x] `varco_redis/tests/test_redis_job_store.py` — **failing unit tests first**, on the existing
        `FakeRedis` + `_make_store(claim_ttl=…)` harness (`:33, :114`):
        (a) after `reap_expired_leases()` reaps a job, the claim key is **absent** from the fake's
        keyspace and a subsequent `try_claim()` by a second owner **succeeds**, at the default
        `claim_ttl=30`;
        (b) `try_claim()` on a **missing** job releases the guard (`job_store.py:610-612`);
        (c) `try_claim()` on a **non-PENDING** job releases the guard (`:615-617`);
        (d) the existing future-`run_at` release (`:619-623`) and exception release (`:639-642`)
        still hold — regression cover for the refactor.
21. [x] Same file — a **negative** unit test: `reap_expired_leases()` must **not** delete the claim
        key of a job it did **not** reap (a live, unexpired lease). This is the guard against
        "delete every claim key on every reap tick", which would pass (a) and be wrong.
22. [x] `varco_redis/varco_redis/job_store.py` — implement both halves of §RT7a-guard:
        (1) `reap_expired_leases` (`:721-757`) deletes `self._claim_key(job.job_id)` **after**
        `await self.save(new_job)`, inside the per-job loop, for reaped jobs only;
        (2) `try_claim` (`:594-642`) releases its own guard on every non-success path via a
        `claimed = False` flag + `finally`. Update the `DESIGN:` block at `:566-577` — the ❌
        bullet about the two unlinked keys stays (it is still true), and a new ✅ records that the
        guard is now released by the reaper that supersedes it, with the `save`-then-`delete`
        ordering rationale (a crash between them degrades to today's TTL behaviour, never to a
        lost job).
23. [x] **Verify unit:** `uv run pytest varco_redis/tests/test_redis_job_store.py -q` — all green,
        including `TestRedisJobStoreTryClaim` (`:322`), `TestRedisJobStoreLease` (`:467`) and
        `TestRedisJobStoreConcurrentClaimFencing` (`:536`), which the `try_claim` refactor is most
        likely to disturb.
24. [x] `varco_redis/tests/test_redis_job_lease_crash.py` — **delete the
        `@pytest.mark.xfail(...)` block at `:60-75`.** Change nothing else in the module.
25. [x] **Verify integration:** `uv run pytest varco_redis/tests/ -m integration -q` and
        `uv run pytest varco_sa/tests/test_sa_job_lease_crash.py -m integration -q`. Acceptance:
        **both** twin tests green on both backends — the cross-backend agreement is the point.
        `test_renewed_lease_keeps_a_second_worker_locked_out` (`:138`) **must still pass**; if it
        does not, the fix is wrong (§RT7a-guard's third ✅) — do not touch the test.
        Commit: `fix(redis): release the claim guard on reap and on every failed try_claim — closes RT7a`.

### Phase 4 — RT7b: no caller can cache a stale DSN (§RT7b-port)

26. [x] `testkit/varco_chaos/ports.py` (**new**) — `reserve_host_port() -> int`: bind a socket to
        `("", 0)`, read `getsockname()[1]`, close, return. Docstring must state the **TOCTOU
        window** plainly (the port is free at reservation, not guaranteed free at `docker run`)
        and that a collision surfaces as a container-start failure, never as a mid-test flake.
27. [x] **Correct the record** (U-8 discipline — one commit, four files):
        (a) `design/reliability-release/research/002-testcontainers-chaos-fault-injection.md` §1 —
        prepend a `> ⚠️ **SUPERSEDED by research 006 §A/§B/§F**` banner; do **not** rewrite the
        original text (the register keeps its history, as U-8's own "Maintainer response" lesson
        records);
        (b) `CLAUDE.md` §Common Pitfalls — rewrite the *"Chaos `restart()` port instability"* row
        from a WSL2-specific "not yet fixed" observation to documented Docker behaviour
        (research 006 §A/§B), with the fix column pointing at `ChaosContainer.url`;
        (c) `plans/018-…md` — annotate `:104-109` (§RT7-shape's rejection), `:780` (Edge-cases
        row) and `:864-870` (the Risks bullet) as superseded by this plan, in place;
        (d) `testkit/varco_chaos/containers.py:32-46` — replace the port-survivorship DESIGN block
        with the truth: docker-py `restart()` preserves the container **ID** (which is why
        `.stop()`+`.start()` is still forbidden) but **not** the ephemeral host port, hence the
        `url` property. Keep the log-offset DESIGN block (`:48-74`) verbatim — it is correct and
        independent.
28. [x] `testkit/varco_chaos/containers.py` — implement `url_factory` + the `url` property per
        §RT7b-port: re-derive on **every** access, never memoise; `ValueError` when no factory was
        supplied (mirroring `wait_ready()`'s `:192-196`); `restart()` re-derives and logs the URL
        after `wait_ready()` returns. Full `Args`/`Raises`/`Edge cases` docstrings.
29. [x] `varco_sa/tests/test_sa_chaos.py` — delete `_CHAOS_DSN` (`:45`, `:66`); pass
        `url_factory=lambda c: c.get_connection_url(driver="asyncpg")` to the `ChaosContainer` in
        `postgres_container_chaos` (`:48-70`); replace every read with `chaos.url`. Keep the
        existing `assert url.startswith("postgresql+asyncpg://")` shape check, moved to the factory
        or the fixture body.
30. [x] `varco_kafka/tests/test_kafka_chaos.py` — delete `_CHAOS_BOOTSTRAP` (`:51-55`, `:74`,
        `:148`, `:226`); in `kafka_container_chaos` (`:58-75`) reserve a host port (Step 26) and
        `KafkaContainer().with_bind_ports(9093, port)`, and pass
        `url_factory=lambda c: c.get_bootstrap_server()`. The fixture docstring **must** carry the
        `tc-start.sh` finding verbatim (§RT7b-port): the advertised listener is baked into an
        on-disk script at first boot, so re-querying alone cannot fix Kafka. Verify the container
        port constant against the installed `KafkaContainer.port` rather than hardcoding blindly.
31. [x] `varco_fastapi/tests/test_migration_chaos.py` — same treatment as Step 29 (Postgres,
        re-query; no pinning).
32. [x] **Verify:** `make chaos-test-clean` — bound it (`timeout 900`), run **3×**, record the
        flake count in the commit message per Plan 018's standing rule. Acceptance: the three
        restart-based modules pass on a run where the host port **does** move (confirm by logging
        `chaos.url` before and after `restart()` in one exploratory run — for Postgres it should
        differ; for the pinned Kafka container it should not).
        Commit: `fix(chaos): ChaosContainer owns its URL; pin Kafka's host port; correct research 002 §1 — closes RT7b`.

### Phase 5 — RT9-beanie: real-Mongo migration lock coverage (§RT9-beanie) — **cut line**

33. [x] `varco_beanie/tests/test_beanie_migration_integration.py` (**new**,
        `pytestmark = pytest.mark.integration`) — the five tests in §RT9-beanie on the
        session-scoped `mongo_url` fixture (`varco_beanie/tests/conftest.py:48-73`), each with a
        `uuid4().hex[:8]`-suffixed database name. Module docstring must record: (a) the lock is
        `find_one_and_update` + upsert + `_id` uniqueness (research 007 §A), already implemented at
        `migration/store.py:86-145`; (b) expiry is an **acquire-time `expires_at` predicate, not a
        TTL index**, deliberately — so research 007 §B's 60–120 s TTL-monitor window and its 180 s
        test-timeout advice **do not apply**, and a TTL index would be inert on the standalone
        mongod `MongoDbContainer` starts (007 §B).
34. [x] `varco_beanie/tests/test_beanie_migration_lock.py` — docstring only: cross-reference the
        new integration module and state the division of labour (branch coverage against the fake
        here, real `DuplicateKeyError`/concurrency there), mirroring what Plan 018 did for
        `test_kafka_eos.py`.
35. [x] **Verify:** `uv run pytest varco_beanie/tests/ -m integration -q` (bound: `timeout 480`).
        Any red result that reveals a genuine `BeanieMigrator`/`MigrationStore` defect follows the
        **standing** rule, not this plan's inversion: `xfail(strict=True)` + a BACKLOG row. This
        plan's licence to edit production code covers the four named 🔴 rows, not new findings.
        Commit: `test(beanie): real-Mongo migration lock and index-mode lifecycle coverage — closes RT9-beanie`.

### Phase 6 — docs, register, close-out (same commits as the code where possible)

36. [x] `varco_nats/README.md` — the delivery-semantics section: `AT_LEAST_ONCE` now **redelivers
        on handler failure** via `nak()`, bounded by `max_deliver`; `VARCO_NATS_MAX_DELIVER` and
        the now-live `VARCO_NATS_ACK_WAIT_SECONDS` in the env-var table; the
        `ErrorPolicy.FIRE_FORGET` opt-out; and the settled `channel_exists` contract plus the new
        `channel_has_messages()`.
37. [x] `ARCHITECTURE.md` — update wherever the NATS `ChannelManager` predicate or the event-bus
        ack semantics are described (grep `channel_exists` / `AT_LEAST_ONCE`).
38. [x] `README.md` (root) — if the `ChannelManager` contract is described in the event-system
        section, add the one-line round-trip invariant. Keep it short; the design lives here.
39. [x] `CLAUDE.md` §*Key Abstractions* — one sentence under the event-system rules: `ChannelManager`
        implementations must satisfy `declare → exists`, and the contract is enforced by
        `testkit/varco_conformance/channel_manager.py` (now **five** modules, not four — update
        every place that says four, including §Test Conventions' conformance paragraph).
40. [x] `CLAUDE.md` §*Common Pitfalls* — two new rows plus the Step 27(b) rewrite:
        (a) **"NATS handler raises under `FIRE_FORGET`"** → symptom "message is acked and never
        retried despite `AT_LEAST_ONCE`" → cause "the exception never leaves `_dispatch`
        (`bus.py:629-640`), so the bus sees a successful dispatch" → fix "use `COLLECT_ALL`
        (the default) or `FAIL_FAST` if you want broker-level redelivery";
        (b) **"assuming a chaos container's URL is stable"** → symptom "`ConnectionRefusedError`
        after `restart()` even though `wait_ready()` returned" → cause "Docker re-allocates
        ephemeral host ports on restart by design (research 006 §A)" → fix "read `chaos.url` at
        every use; pin the host port when the server advertises its own mapped address (Kafka)".
41. [x] `CHANGELOG.md` `## [Unreleased]` — **flag the behaviour changes explicitly**:
        ⚠️ BREAKING(behaviour) `varco_nats` `AT_LEAST_ONCE`/`EXACTLY_ONCE` now redeliver a message
        whose handler raised (bounded by the new `VARCO_NATS_MAX_DELIVER`, default 5) — a
        non-idempotent NATS handler that previously saw one delivery may now see up to five;
        ⚠️ BREAKING(behaviour) `NatsStreamManager.channel_exists()`/`list_channels()` now report
        declared channels (previously: only channels carrying messages), with
        `channel_has_messages()` preserving the old predicate; `VARCO_NATS_ACK_WAIT_SECONDS` now
        actually reaches the broker; `RedisJobStore` releases its claim guard on reap and on every
        failed `try_claim`. Plus the test-only items (conformance module, chaos URL handling,
        Beanie migration coverage).
42. [x] `BACKLOG.md` §*Plan 018 findings* — apply the **Status corrections** table above, then:
        mark **RT2-B**, **RT2-C**, **RT7a-redis-claim-guard**, **RT7b-port-remap** and
        **RT9-beanie-migrations** ✅ **done (Plan 019)** with evidence (the now-green test names),
        **retaining the rows** as a record rather than deleting them (the repo's habit); rewrite
        **WD-1** with §WD-1's written trigger condition; re-affirm **RT7-toxiproxy** and
        **RT4-ws-scale** with §deferrals' preconditions; file the two new rows from §deferrals
        (NATS max-deliveries advisory → DLQ; `RedisJobStore` atomic Lua claim); and correct
        RT9-beanie's `varco_beanie/varco_beanie/migration.py` path to the package path.
43. [x] **Final:** `make lint && make type-check`, then bounded per-package runs —
        `make test PKG=varco_nats`, `PKG=varco_redis`, `PKG=varco_beanie`, `PKG=varco_core`,
        then `make integration-test-clean` and `make chaos-test-clean` (each `timeout`-bounded,
        piped through `| tail -40`). **Assert zero `xfail`/`xpass` from the three Plan-018
        markers** — `uv run pytest varco_nats/tests/ varco_redis/tests/ -m integration -rxX -q`
        must report none of them.

---

## Edge cases

| Input / state | Expected behaviour |
|---|---|
| NATS handler raises, `ErrorPolicy.COLLECT_ALL` (default) | `nak()` → immediate redelivery (research 005 §D). `num_delivered` increments. |
| NATS handler raises `max_deliver` times | `term()` + one WARNING naming subject and `num_delivered`. **No further redelivery** — asserted by Step 8's new test. |
| NATS handler raises, `ErrorPolicy.FIRE_FORGET` | `ack()` — the exception never leaves `_dispatch` (`bus.py:629-640`), so the bus cannot distinguish it from success. Documented, not fixed (Step 40a). |
| NATS payload fails to deserialize | `term()`, **never `nak()`** — a poison payload cannot succeed on a retry, and naking it is an infinite loop. Mirrors `KafkaEventBus` advancing past bad payloads. |
| NATS `AT_MOST_ONCE`, handler raises | Unchanged: pre-acked before dispatch, message lost. The documented weakness must survive the fix (Step 9's acceptance criterion). |
| `declare_channel(c)` on NATS, nothing published | `channel_exists(c)` → `True` (registry). `channel_has_messages(c)` → `False`. Both correct, different questions. |
| `delete_channel(c)` on NATS while the subject still holds messages | Purge **and** registry discard → `channel_exists(c)` is `False`. Keeps `test_delete_channel_then_channel_exists_is_false` (`:102-110`) green. |
| A second `NatsStreamManager` in another process asks about a declared-but-empty channel | `False` — the registry is process-local. Documented in the ABC (Step 10), the class docstring (Step 15) and the conformance suite's contract text. Identical to Redis's long-standing behaviour. |
| `reap_expired_leases()` reaps a job | `save(PENDING, epoch+1)` **then** `delete(claim_key)`. A crash between the two leaves today's behaviour (guard expires on its own TTL) — degraded, never lost. |
| `reap_expired_leases()` sees a **live** lease | Nothing is touched, guard included (Step 21's negative test). |
| `try_claim()` on a missing / non-PENDING / future-`run_at` job | Guard released before returning `None`. |
| `try_claim()` while another worker legitimately holds a live lease | Still `None` — via the `status != PENDING` branch (`:615-617`), not the guard. `test_renewed_lease_keeps_a_second_worker_locked_out` proves it. |
| `ChaosContainer.url` accessed after `restart()` | Freshly re-derived from the daemon (`get_exposed_port()` re-queries every call, `.venv/…/core/container.py:247-258`). For Postgres the port **will** typically differ; for the pinned Kafka container it will not. |
| `ChaosContainer` built without a `url_factory`, `.url` accessed | `ValueError` naming the container — fail loudly, same shape as `wait_ready()` with no `ready` predicate. |
| `reserve_host_port()`'s port is taken before `docker run` | Container start fails immediately and loudly, not mid-test. Re-run; if it recurs, the fallback is §RT7b-port's rejected-but-documented `paused()` downgrade for that module. |
| Beanie lock document exists with `expires_at` in the past | Next `acquire()` **wins immediately** — the `$or: [{expires_at: {$lt: now}}]` filter, no TTL monitor involved. Seconds-scale test, not 180 s. |
| Two Beanie migrators race on an **absent** lock document | Exactly one wins; the loser's upsert collides on `_id` and `DuplicateKeyError` is read as "lock lost" (`store.py:121-138`), returning `False` — not an error. |
| A Phase-5 test reveals a **new** `BeanieMigrator` defect | Standing rule applies, not this plan's inversion: `xfail(strict=True)` + BACKLOG row. The licence to patch production code covers only the four named 🔴 rows. |

---

## Verification

```bash
cd /home/edoardo/projects/varco

# Phase 0 — baseline (expect 4 xfail + 1 xpass-free run)
timeout 480 uv run pytest varco_nats/tests/test_nats_semantics_integration.py \
    varco_nats/tests/test_nats_channel_integration.py -m integration -q -rxX
timeout 300 uv run pytest varco_redis/tests/test_redis_job_lease_crash.py -m integration -q -rxX

# Phase 1 — RT2-B
uv run pytest varco_nats/tests/test_nats_bus.py varco_nats/tests/test_nats_config.py -q
timeout 480 uv run pytest varco_nats/tests/ -m integration -q -rxX     # run 2x

# Phase 2 — RT2-C
uv run pytest varco_nats/tests/test_nats_channel.py -q
timeout 480 uv run pytest varco_nats/tests/  -m integration -q -rxX
timeout 480 uv run pytest varco_redis/tests/ -m integration -q -rxX
timeout 900 uv run pytest varco_kafka/tests/ -m integration -q -rxX

# Phase 3 — RT7a
uv run pytest varco_redis/tests/test_redis_job_store.py -q
timeout 480 uv run pytest varco_redis/tests/ -m integration -q -rxX
timeout 480 uv run pytest varco_sa/tests/test_sa_job_lease_crash.py -m integration -q

# Phase 4 — RT7b  (3 consecutive runs, record the flake count)
timeout 900 make chaos-test-clean 2>&1 | tail -40

# Phase 5 — RT9-beanie
timeout 480 uv run pytest varco_beanie/tests/ -m integration -q -rxX

# Phase 6 — close-out
make lint && make type-check
make test PKG=varco_nats && make test PKG=varco_redis \
  && make test PKG=varco_beanie && make test PKG=varco_core
timeout 1800 make integration-test-clean 2>&1 | tail -40
```

| Phase | Command | Pass condition |
|---|---|---|
| 0 | the two baseline runs | exactly 4 `xfail`, 0 `xpass`. An `xpass` invalidates that phase's premise. |
| 1 | `varco_nats -m integration` | `test_at_least_once_redelivers_after_handler_raises` **passes**; `…at_most_once_does_not_redeliver…` still passes; new `…stops_redelivering_after_max_deliver` passes; **0 xfail** in the module |
| 2 | nats/redis/kafka `-m integration` | 4/4 in `test_nats_channel_integration.py`; 3 conformance subclasses green; **0 xfail** anywhere in the three |
| 3 | redis + sa `-m integration` | both twin lease tests green on **both** backends; `test_renewed_lease_keeps_a_second_worker_locked_out` still green; **0 xfail** |
| 4 | `make chaos-test-clean` ×3 | all chaos modules green 3/3; an exploratory run shows Postgres's `chaos.url` **changing** across `restart()` while the tests still pass (proof the fix is what is holding, not luck) |
| 5 | `varco_beanie -m integration` | 5 new tests green, in seconds not minutes (proof the `expires_at` predicate, not a TTL monitor, governs) |
| 6 | full close-out | `make lint`/`make type-check` clean; `-rxX` reports **zero** Plan-018 xfails repo-wide |

---

## Risks

- ⚠️ **ASSUMPTION — reconfiguring an existing durable NATS consumer.** Step 5 passes
  `config=ConsumerConfig(ack_wait=…, max_deliver=…)` to `js.subscribe(subject, durable=…)`. For a
  **new** durable this plainly creates it with those values. For a durable that **already exists**
  on the stream (every existing deployment, and any test that reuses a `durable_name`), nats-py
  may silently ignore the config, may attempt an update, or may raise on a mismatch — research 005
  does **not** cover consumer *reconfiguration*, only creation and the config fields themselves
  (§B). Mitigation: every integration test already namespaces `durable_name` per run
  (`test_nats_semantics_integration.py:61`), so tests exercise the create path only. **The
  upgrade path for existing deployments is genuinely unverified** — if `subscribe()` raises on a
  pre-existing durable with a differing config, that is a 🔴 finding (an upgrade that cannot start)
  and must be handled explicitly (catch, log, and fall back to the existing consumer) rather than
  discovered by a user. **Verify this against a pre-created durable before Step 9's commit.**
- ⚠️ **ASSUMPTION — deleting the claim guard on reap does not weaken the race protection it was
  added for.** The guard (`job_store.py:566-577`) protects an **in-flight** `try_claim` from a
  concurrent second claimer. After a reap there is no in-flight claim: the previous holder is
  gone, the job is `PENDING`, and `lease_epoch` has advanced (which is the *real* fence,
  `job/base.py`'s `expected_epoch` protocol). The residual window is a reap landing exactly
  between another worker's `SET NX` and its `save()` — in which case both could observe `PENDING`
  and both save `RUNNING`, and the loser is fenced at its next `save(expected_epoch=…)`. That
  window exists today too (the GET+SET non-atomicity is already documented at `:574-577`); this
  plan does not widen it for the leased path, but it **does** remove the only protection the
  **`lease_ttl=None` no-lease path** has, since that path never advances `lease_epoch`.
  `reap_expired_leases()` only reaps jobs with a `lease_expires_at` (`:745`), so a no-lease claim
  is never reaped and never reaches the new `delete` — **verify that branch explicitly in Step 20**
  and, if it does not hold, scope the delete to `job.lease_expires_at is not None`.
- ⚠️ **ASSUMPTION — a pinned host port survives `restart()`.** Research 006 §Evidence Gaps 4 is
  candid: *"No explicit moby source or test confirms that `-p 32811:5432` (pinned) survives restart
  unchanged. Assumed safe based on 'only ephemeral ports re-allocated' logic, but not verified
  against moby source."* If it does **not** hold, the Kafka chaos module cannot be fixed by
  pinning at all (its advertised listener is baked in either way) and the fallback is
  §RT7b-port's rejected-but-documented option: convert `test_kafka_chaos.py` to `paused()` and
  accept the weaker assertion, with a BACKLOG row recording the downgrade.
- ⚠️ **ASSUMPTION — `KafkaContainer`'s mapped container port is `9093`.** Step 30 hardcodes it in
  `with_bind_ports`. It must be read from the installed `KafkaContainer.port` attribute rather than
  assumed; a wrong constant produces a container that starts and is simply unreachable.
- ⚠️ **ASSUMPTION — Redis and Kafka `ChannelManager`s already conform.** Step 14 asserts it. Read
  from source for Redis (`varco_redis/channel.py:192-226`, a registry — conformant by construction)
  and Kafka (`varco_kafka/channel.py:302-350`, broker metadata), but **not executed**. If Kafka's
  `list_channels` prefix-filtering makes a declared topic invisible, that is a new finding and
  takes the standing `xfail(strict=True)` + BACKLOG treatment, **not** a contract relaxation.
- **A behaviour change shipped as a bug fix is still a behaviour change.** RT2-B and RT2-C both
  alter observable runtime behaviour for existing `varco_nats` users. The invariant that must hold:
  **both appear in CHANGELOG under an explicit ⚠️ BREAKING(behaviour) marker** (Step 41), even
  though neither changes a signature. A user whose NATS handler is non-idempotent and who relied on
  "a raise means the message is dropped" will now see up to `max_deliver` deliveries.
- **`max_deliver=5` is a chosen default, not a researched one.** Research 005 §B documents the
  field but names no conventional value. 5 mirrors the shape of varco's other bounded-retry
  defaults; it is configurable per-deployment. If it proves wrong in practice, it is a settings
  change, not a design change.
- **Phase 4 cannot be fully proven locally.** The port remap reproduces on this environment
  (Docker 27.5.1/WSL2), and research 006 §F predicts identical behaviour on GHA's native Linux
  dockerd — but the nightly `chaos` job is the only real confirmation, and it is deliberately not
  a required check. Treat the first two nightly runs after this plan as the evidence, and if the
  restart-based modules are still flaky there, the fallback ordering is: (1) pin Postgres too,
  (2) downgrade the affected module to `paused()`, (3) re-open RT7-toxiproxy.
- **The conformance module is new surface in `testkit`.** It is never packaged (same status as the
  other four), but it now gates three backends. A contract test that is subtly wrong fails three
  suites at once — which is the point, and also the risk. Keep it to the four round-trips in
  §RT2-C-contract; resist adding backend-specific assertions to it.
