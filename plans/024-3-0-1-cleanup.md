# Plan 024 — varco 3.0.1 cleanup release

## Goal

Ship **varco 3.0.1**, a strict patch: the backlog is reconciled against source, the
`P22-PROVIDER-PREDESTROY` resource leak is closed **inside varco** with `@Disposes`, the
`BeanieMigrator` index-mode defect is fixed, the API-surface snapshot becomes a real CI gate,
the conformance coverage matrix is audited and written down, and both `strict=True` xfails are
deleted in favour of passing assertions. After this plan, `uv run python scripts/bump.py --set
3.0.1` writes the ten versions and the `v3.0.1` tag drives `release.yml`.

**3.0.1 is no longer gated on anything external** (Decision 1, below). Everything in this plan is
executable today.

## Non-goals

- **No new public API surface.** `CONTRIBUTING.md` §"Versioning and deprecation policy" binds
  3.0.1 to bug fixes, xfail closures, CI gates, tests, docs and internal refactors. No new
  exported symbol, no new `__all__` entry, no signature change. This constraint is **locked** and
  may not be relaxed by the implementer. `scripts/api_surface.py --check` becomes the mechanical
  proof of it in Phase 3.
- **No 3.1 rows.** N1 (MCP v2), N2 (CloudEvents), N3 (AsyncAPI), N4 (NATS→DLQ bridge), N5
  (`BeanieConfig`/`BeanieSettings` collapse) are all additive or breaking and cannot ride a
  patch. See §"What did not make the cut".
- **No parked-table relitigation.** OpenFeature, Toxiproxy, RL-16 integration-gating, WD-1,
  RT4-ws-scale, GraphQL/event-sourcing/per-package-versioning/umbrella-package/RRULE stay parked;
  research brief 001 confirms none of their triggers fired except CloudEvents/AsyncAPI, which are
  3.1 rows.
- **No upstream providify change is requested or awaited.** Brief 002 settles this.
- **No new backend, no new conformance base class.** C7 fills gaps in the existing five suites and
  records legitimate absences; it does not invent a sixth suite.

---

## Design

### Phase order

Backlog default (severity, then complexity ascending), with **C1 first** because it absorbs the
C4 and C6 closures and therefore prevents the implementer chasing work that no longer exists:

```
P0  C1  🔴 S  reconcile backlog + stale operator docs   (absorbs C4, C6, KI-12)
P1  C2  🔴 S  @Disposes teardown + providify>=2.0.1     (un-gated, reshaped)
P2  C3  🔴 S  BeanieMigrator index_mode='create'        (integration-only)
P3  C5  🟡 S  api_surface.py --check as a CI gate
P4  C7  🟡 M  conformance coverage audit + fill
P5  C8  🟡 M  Kafka chaos flake — ONE attempt, then downgrade
P6  C9  🟢 M  RedisJobStore atomic CAS claim           (DROPPABLE)
P7  ——  ——   release: bump.py --set 3.0.1, tag v3.0.1
```

Every phase is independently shippable. P6 may be dropped entirely without affecting the release
(it replaces a fix that already works — BACKLOG C9's own rationale).

### §D-C2 — the `@Disposes` adoption (Decision 1, reshaped)

Research brief 002 (`design/3-0-1-cleanup/research/002-providify-201-status.md`) establishes:
providify 2.0.1 shipped 2026-09-01 and **deliberately does not fix** the gap. Its changelog states
the release carries no lifecycle or disposal changes; it adds `IssueKind.UNREACHABLE_PRE_DESTROY`
(a **WARNING**) that *detects* the pattern, plus docstring corrections declaring the behaviour
intentional per the Jakarta CDI producer-method rule. The "Unreleased" section is empty; no public
signal a fix is planned.

Verified in providify source this session (spot-check of brief 002):

- `providify/CHANGELOG.md:12-20` — 2.0.1 adds `IssueKind.UNREACHABLE_PRE_DESTROY` as a `WARNING`.
- `providify/README.md:945-949`, `SKILL.md:287`, `PROVIDERS.md:133-138` — `@PreDestroy` is
  *never* invoked on `@Provider`-produced instances; `@Disposes` is the only teardown path.
- `providify/container.py:496-508` (`_unreachable_pre_destroy`) — the detector: `ProviderBinding`
  + `Scope.SINGLETON` + `disposer is None` + produced type carries `@PreDestroy`.
- `providify/container.py:4540-4545` (`_adispose`) — an **async disposer is awaited**
  (`inspect.iscoroutinefunction(binding.disposer)`), so `async def close_x(...)` is legal.
- `providify/decorator/lifecycle.py:246-273` — `@Disposes(disposed_type)`; the method takes the
  instance as its single argument, exactly like providify's own `close_conn` example.
- `providify/container.py:6201-6214` — disposer wiring runs inside the same install path used by
  both `container.install()` and the scanner's `_autoregister_configurator`
  (`providify/scanner.py:261-288`), so a scan-discovered `@Configuration` gets its disposers wired
  too.

**Therefore varco adopts `@Disposes`** — exactly what varco's own report already proposed
(`design/upstream-gaps/providify-provider-predestroy.md` §5). This reverses BACKLOG.md's "do not
implement the workaround" instruction, which was written on the now-falsified premise that the fix
was coming upstream.

#### §D-C2-audit — the audit found more than two sites

The report's §6 table names two orphans (`RedisCache`, `MemcachedCache`). Auditing every
`@Provider(singleton=True)` in the ten packages found **nine** provider bindings that hand back a
started/connected resource with no teardown path, of which three are visible to
`UNREACHABLE_PRE_DESTROY` and six are invisible to it (the produced class has no `@PreDestroy`, so
providify's new detector cannot see them — they leak just as hard):

| # | Provider | Anchor | Produces / binds | Provider starts it? | Class has `@PreDestroy`? | `validate()` flags? |
|---|---|---|---|---|---|---|
| 1 | `RedisCacheConfiguration.redis_cache` | `varco_redis/varco_redis/cache.py:573` | `RedisCache` → `CacheBackend` | ✅ `await cache.start()` | ✅ `cache.py:213` | ✅ |
| 2 | `RedisLayeredCacheConfiguration.layered_cache` | `varco_redis/varco_redis/cache.py:764` | `LayeredCache` → `CacheBackend` | ✅ `start()` | ❌ | ❌ |
| 3 | `RedisEventBusSelectorConfiguration.bus` | `varco_redis/varco_redis/bus.py:526` | `RedisEventBus` / `RedisStreamEventBus` → `AbstractEventBus` | ❌ (constructed cold) | ✅ `bus.py:217`, `streams.py:318` | ✅ |
| 4 | `RedisDLQConfiguration.redis_dlq` | `varco_redis/varco_redis/dlq.py:725` | `RedisDLQ` → `AbstractDeadLetterQueue` | ✅ `await dlq.connect()` | ❌ | ❌ |
| 5 | `RedisStreamDLQConfiguration.redis_stream_dlq` | `varco_redis/varco_redis/stream_dlq.py:765` | `RedisStreamDLQ` → `AbstractDeadLetterQueue` | ✅ (confirm at implementation time) | ❌ | ❌ |
| 6 | `RedisBulkheadConfiguration.redis_bulkhead` | `varco_redis/varco_redis/bulkhead.py:479` | `RedisBulkhead` | ✅ `await instance.connect()` | ❌ | ❌ |
| 7 | `KafkaDLQConfiguration.kafka_dlq` | `varco_kafka/varco_kafka/dlq.py:765` | `KafkaDLQ` → `AbstractDeadLetterQueue` | ✅ `start()` | ❌ | ❌ |
| 8 | `NatsDLQConfiguration.nats_dlq` | `varco_nats/varco_nats/dlq.py:754` | `NatsDLQ` → `AbstractDeadLetterQueue` | ✅ `await dlq.start()` | ❌ | ❌ |
| 9 | `MemcachedCacheConfiguration` cache provider | `varco_memcached/varco_memcached/cache.py:643` | `MemcachedCache` → `CacheBackend` | ✅ `start()` | ✅ `cache.py:248` | ✅ |

Three docstrings actively **lie** about this today and must be corrected in the same commit:
`varco_redis/varco_redis/cache.py:530-533` and `:709-712`, `varco_redis/varco_redis/bulkhead.py:457-459`
("disconnected automatically by `await container.ashutdown()`"), `varco_memcached/varco_memcached/cache.py:600-602`,
and `varco_redis/varco_redis/di.py:129-130` ("`container.ashutdown()` must be awaited … to call the
bus `stop()` via its `@PreDestroy` hook").

**Tiering, to bound the phase**: Tier A = #1, #3, #9 (the `UNREACHABLE_PRE_DESTROY`-visible set —
these are what the two xfails and the report name). Tier B = #2, #4, #5, #6, #7, #8 (identical
defect class, invisible to validation). **Both tiers ship in 3.0.1**; Tier B may land as a second
commit but must not be deferred to a backlog row — leaving six known leaks in a release whose
whole point is closing this one would be indefensible.

#### §D-C2-test — the test shape (no Docker, public API only)

Every site gets the same fast unit test, using only public providify API:

```python
# monkeypatch the concrete class's start/connect + stop/disconnect to no-ops,
# so no broker is needed and the assertion is on the hook actually firing.
monkeypatch.setattr(RedisDLQ, "connect", _noop)
monkeypatch.setattr(RedisDLQ, "disconnect", _record)

container = DIContainer()
await container.ainstall(RedisDLQConfiguration)
await container.aget(AbstractDeadLetterQueue)
await container.ashutdown()
assert _record.called
```

For the three Tier-A sites, add a second, mechanism-level assertion using providify's own
detector — `container.validate(raise_on_error=False)` must report **zero**
`IssueKind.UNREACHABLE_PRE_DESTROY` issues after install. ⚠️ That kind is `WARNING`-severity, so
it never reaches `report.errors`; the assertion must inspect `report.issues`
(`providify/README.md:1535-1538`). This is also why the existing
`assert_no_structural_di_issues()` (`testkit/varco_conformance/providify_health.py:79-81`, which
filters `report.errors`) **does not become noisy** when the lock moves to 2.0.1 — verified by
reading the helper and providify's severity contract, and re-verified empirically in Step 12.

#### §D-C2-firstmatch — a sharp edge to test, not to work around

`providify/container.py:6202-6214` attaches a `@Disposes` to the **first** matching
`ProviderBinding` in `self._bindings` and `break`s — it iterates *all* container bindings, not
only the ones the configuration being installed just registered. varco has two configurations
producing `CacheBackend` (#1 and #2), and a recursive `scan("varco_redis")` installs **both**
(`providify/scanner.py:161-162`). So the second install's disposer can overwrite the first
binding's, leaving the second binding with none.

In effect this is benign here — both disposers do `await backend.stop()` on a `CacheBackend`, and
equal-priority resolution returns the first-registered binding anyway — but it must be **proven**,
not assumed. Step 16 installs both configurations and asserts both instances are stopped. If that
test shows a leak, file `design/upstream-gaps/providify-disposes-first-match.md` + an
`UPSTREAM-GAPS.md` row + a `strict=True` xfail (repo norm), and do **not** hand-roll a workaround.

### §D-C3 — `BeanieMigrator.upgrade()` restructure

Confirmed defect at `varco_beanie/varco_beanie/migration/migrator.py:170-171`:

```python
if not pending_migrations:
    return MigrationReport(applied=(), duration_s=time.monotonic() - start)
```

returns before the index block at `:228-231`, which lives inside the lock-held `try/finally`
(lock acquired `:179-182`, released in `finally` `:232-238`). `plan()` already reports index drift
independently via `_index_pending()` (`:138-148`, `:156`), so `plan()` and `upgrade()` disagree —
that disagreement is the bug.

**Chosen shape** — the zero-pending index path **does** take the migration lock, and only when
there is genuine index drift:

1. `dry_run` (`:166-168`) — additionally include `await self._index_pending()` revisions when
   `index_mode == "create"`, so `upgrade(dry_run=True)` agrees with `plan()`. Report-only path, no
   DDL. ⚠️ Step 22 must first grep existing tests/CLI output for dry-run content assertions.
2. Early return (`:170-171`) becomes: return only if `not pending_migrations` **and** there is no
   index work — where "index work" is `index_mode == "create" and index_guard is not None and
   await self._index_pending()`. The extra `listIndexes` round-trip is paid only when the
   migration registry is empty, and it is what keeps the common no-drift startup lock-free.
3. Lock-timeout branch (`:184-193`) — unchanged. `revision_pending` already excludes
   `branch == "index"`, so "another instance holds the lock and there are no revisions" still
   returns `skipped_locked=True`; that other instance will do the index work.
4. Post-lock re-check (`:202-207`) — must no longer `return` unconditionally. Set
   `skipped_locked = True` and **fall through** into the heartbeat/`try` block; the migration loop
   over an empty list is a no-op, the index block at `:228-231` then runs under the lock, the
   `finally` releases as before, and the final report carries `skipped_locked=skipped_locked`.

Why the lock: index reconciliation is a schema change, and the lock exists to serialize schema
changes across a rolling deploy. `createIndex` is idempotent (MongoDB 4.4+, cited in the existing
test at `varco_beanie/tests/test_beanie_migration_integration.py:117-118`), so an unlocked variant
would also "work" — but it would make the lock's meaning conditional on which kind of schema
change is pending, which is exactly the kind of split-brain rule that produced this bug.

Guard: `varco_beanie/tests/test_beanie_migration_integration.py:91-92` is `strict=True` against
real MongoDB — the marker is **deleted**, so the fix is only provable with `-m integration`.

### §D-C5 — the API-surface gate and its honest scope

`uv run python scripts/api_surface.py --check` was executed this session: *"API surface matches
api-surface.json (0 non-breaking note(s))"*, exit 0. The snapshot is in sync; wiring is the whole
job.

- CI: a fourth step in `test.yml`'s `lint` job (after `mypy`, `.github/workflows/test.yml:57-63`).
  The job already runs `uv sync --locked --all-packages --all-extras` (`:52`), which
  `api_surface.py` needs since it imports every package live.
- `make lint`: added **only on the no-`PKG` path** (`Makefile:151-154`), via an
  `ifeq ($(strip $(PKG)),)` guard — `make lint PKG=varco_redis` is a narrow, fast local loop and
  must not start importing the whole workspace.
- `make help` (`Makefile:89-98`) gains a line; a `make api-check` target is added so the check is
  runnable on its own.

**Gate scope, stated honestly** in CLAUDE.md: `--check` catches **removals and *function*
signature changes only**. Class signatures are deliberately not recorded (pydantic/dataclass
`__init__` rendering is not stable across the 3.12/3.13 matrix), so a narrowed class `__init__`
stays invisible. Additions and module moves are notes and never fail. CLAUDE.md's "⚠️ **This is
not a gate today.**" paragraph is rewritten, not merely amended.

### §D-C7 — conformance coverage: audit outcome and what it produces

The audit is **done** (verified against source this session) and its outcome answers BACKLOG open
question 2: the real gaps are a **minority**, so C7 stays at M and is not split.

| Suite | Implementations | Subclassed | Gap |
|---|---|---|---|
| `channel_manager` | `KafkaChannelManager`, `NatsStreamManager`, `RedisChannelManager` | `varco_kafka/tests/test_kafka_channel_integration.py:111`, `varco_nats/tests/test_nats_channel_integration.py:122`, `varco_redis/tests/test_redis_channel.py:228` | none (no in-process impl exists — CLAUDE.md already says so) |
| `cache` | `InMemoryCache`, `NoOpCache`, `RedisCache`, `LayeredCache`, `MemcachedCache` | `varco_core/tests/test_conformance_inmemory.py:59,70`; `varco_redis/tests/test_redis_conformance.py:49,73`; `varco_memcached/tests/test_memcached_conformance.py:26` | none |
| `job_store` | `InMemoryJobStore`, `SAJobStore`, `RedisJobStore`, `BeanieJobStore` | `varco_core/tests/test_conformance_inmemory.py:130` (cross-package import of `varco_fastapi/varco_fastapi/job/store.py:40`, documented); `varco_sa/tests/test_sa_conformance.py:26`; `varco_redis/tests/test_redis_conformance.py:56`; `varco_beanie/tests/test_beanie_conformance.py:34` | none |
| `event_bus` | `InMemoryEventBus`, `NoopEventBus`, `RedisEventBus`, `RedisStreamEventBus`, `KafkaEventBus`, `NatsEventBus` | all but `NoopEventBus` | **`NoopEventBus`** (`varco_core/varco_core/event/memory.py:639`) |
| `dlq` | `InMemoryDLQ`, `RedisDLQ`, `RedisStreamDLQ`, `KafkaDLQ`, `NatsDLQ`, `SADeadLetterQueue`, `BeanieDeadLetterQueue` | all but `RedisStreamDLQ` (`varco_redis/tests/test_redis_conformance.py:66` covers `RedisDLQ` only) | **`RedisStreamDLQ`** (`varco_redis/varco_redis/stream_dlq.py:168`) |

Two decisions:

- **`RedisStreamDLQ` → subclass.** It is a real, durable DLQ implementation with a real transport;
  there is no reason it should be less proven than `RedisDLQ`.
- **`NoopEventBus` → stated reason, not a subclass.** ✅ It is a Null Object: `publish()` discards
  and `subscribe()` returns a **pre-cancelled** `Subscription` (`memory.py:665-691`), so it
  deliberately violates `EventBusConformance`'s deliver-what-you-publish contract by design.
  Subclassing would mean xfail-ing most of the suite, which teaches nothing and rots.
  ❌ It stays uncovered by the shared suite. Mitigation: the reason is written down where the next
  auditor looks, following the `varco_ws/tests/test_ws_conformance.py:1-27` landing-page model
  that already resolved the `varco_ws` "hole" the same way.

`varco_ws` is **already resolved**, not a hole (`test_ws_conformance.py` explains that
`WebSocketEventBus`/`SSEEventBus` are push adapters wrapping an `AbstractEventBus`, covered by
bespoke real-server tests). `varco_memcached` implements only `CacheBackend`; `varco_casbin`
implements none of the five and therefore does **not** need `pythonpath = ["../testkit"]` (it is
the only package without one — nine confirmed present).

The durable artifact is `testkit/varco_conformance/COVERAGE.md` — the matrix above plus the stated
reasons, referenced from CLAUDE.md's conformance paragraph so a future absence has to be argued
against a written record rather than rediscovered.

### §D-C8 — one attempt, then a pre-authorised downgrade (Decision 2)

`varco_kafka/tests/test_kafka_chaos.py` — two tests across a real `docker restart`
(`:185` `test_outbox_entries_survive_a_broker_restart_and_are_republished`, `:263`
`test_relay_does_not_dead_letter_on_a_transient_broker_outage`). The host-port remap is **already
fixed** (`kafka_container_chaos` pins via `reserve_host_port()` + `with_bind_ports`, `:96-123`),
and KRaft mode is already required and documented (`:45-58`), so neither is the cause.

**One focused root-cause pass**, budget ≤ 3 chaos runs of `make chaos-test PKG=varco_kafka` plus
whatever reading they justify. If it does not resolve, **downgrade — this is a pre-authorised
step, not a failure**: widen the timing margin per CLAUDE.md's "increase the sleep margin rather
than marking it xfail" rule if the evidence points at timing; otherwise add a BACKLOG row + a
`⚠️ known flake` note in the module docstring naming the observed failure mode and the runs
counted. Chaos never gates a merge and is never a required check, so the cost ceiling matters more
than the fix.

### §D-C9 — atomic claim (droppable)

`varco_redis/varco_redis/job_store.py:612` uses `SET claim_key "1" NX EX ttl` plus a
`claimed`-flag/`finally` guard release (`:621-645`, Plan 019 / RT7a-guard). The documented drawback
is at `:576-579`: the claim key and the job JSON are two unlinked keys.

**Chosen replacement: a byte-exact compare-and-set Lua on the job key**, modelled on
`varco_redis/varco_redis/lock.py:114` (`_RELEASE_SCRIPT` + `await self._redis.eval(...)`, `:345-346`):

```
-- KEYS[1] = job key, ARGV[1] = expected JSON (as read), ARGV[2] = RUNNING JSON
if redis.call('GET', KEYS[1]) == ARGV[1] then redis.call('SET', KEYS[1], ARGV[2]); return 1 end
return 0
```

Read → decide in Python → CAS. A losing claimer sees `0` and returns `None`. The separate claim
key and its whole `finally`-release apparatus disappear.

**Alternative rejected — decode/mutate/encode the job inside Lua with `cjson`:** ✅ truly
single-round-trip. ❌ duplicates `Job` serialization semantics in Lua, where `as_running()`,
`lease_epoch`, and the datetime encoding would silently drift from the Python side; the CAS gets
the same atomicity with zero duplicated domain logic.

C9 is 🟢 nice, sorts last, and **replaces a fix that already works**. Nothing may wait on it; drop
it if anything above runs long.

### Alternatives considered

- **Wait for providify to fix `@PreDestroy` for provider bindings (BACKLOG's original C2).**
  ❌ Rejected: brief 002 §Findings — 2.0.1 explicitly ships no lifecycle change, documents the
  behaviour as intentional (Jakarta CDI), and the "Unreleased" section is empty. Waiting means
  shipping a release with nine live resource leaks against an event with no date. ✅ `@Disposes`
  is upstream's *supported* mechanism, not a workaround.
- **Ship the eight, let C2 follow in 3.0.2** (the fallback floated in BACKLOG open question 1).
  ❌ Rejected by Decision 1: with the upstream dependency removed, C2 is a same-day fix that varco
  fully controls; deferring it would leave the two strict xfails and the open `UPSTREAM-GAPS.md`
  row in a release whose stated purpose is closing them.
- **Fix only the two orphans the report names (#1, #9).** ❌ Rejected: the audit that C2 mandates
  found seven more sites of the identical defect class, six of them invisible to providify's new
  detector. Fixing two and knowingly leaving six is worse than not auditing.
- **Assert `UNREACHABLE_PRE_DESTROY` inside `assert_no_structural_di_issues()`.** ❌ Rejected:
  that helper's contract is "fail on structural `ERROR`s, tolerate app-supplied
  `MISSING_BINDING`", and the new kind is a `WARNING` that never reaches `report.errors`. Widening
  it would change the meaning of seventeen existing call sites for one phase's benefit. ✅ A
  separate, explicitly-named assertion at the three Tier-A sites instead.
- **Run index reconciliation without the migration lock (C3).** ✅ Cheaper, and `createIndex` is
  idempotent. ❌ Rejected: it makes the lock's meaning depend on which kind of schema change is
  pending — the same split-brain that produced the bug.
- **Make `api_surface.py --check` unconditional in `make lint`.** ❌ Rejected: it would break the
  `make lint PKG=<one package>` fast local loop by importing the whole workspace. ✅ Guarded to
  the no-`PKG` path, plus a standalone `make api-check`.

---

## Steps

### Phase 0 — C1: reconcile the backlog and the stale operator docs (🔴 must, S)

1. [x] `varco_fastapi/varco_fastapi/tenancy/mount.py:53` + `varco_fastapi/varco_fastapi/admin/mount.py:43`
       — **verify only**, no code change: both are already `weakref.WeakSet[Any]`
       (usages at `tenancy/mount.py:112,128`, `admin/mount.py:107,155`). C4(a) is landed.
2. [x] `varco_redis/varco_redis/di.py:223-230` — **verify only**: the `container is None` guard
       with its RIDER-1 comment is present. C4(b) is landed.
3. [x] `scripts/unit_tests.sh:53-71` + `.github/workflows/test.yml:82-83` — **verify only**:
       `EXTRA_SUITES=("examples/00-full-stack-post-api:example/tests")` is appended to `SUITES` on
       the no-argument path, and CI runs `bash scripts/unit_tests.sh`. C6 is landed.
4. [x] `varco_beanie/varco_beanie/uow.py:86` — **verify** `start_session()` is called un-awaited
       with its motor-vs-pymongo docstring table, then **run** the test BACKLOG.md:229 claims is
       failing: `uv run pytest varco_beanie/tests/test_beanie_bootstrap.py -m integration -k
       round_trip` (needs Docker/MongoDB). Record the result verbatim in the BACKLOG row.
5. [x] `BACKLOG.md` — rewrite the C4, C6 rows to ✅ **DONE (Plan 022 Phase 3)** / ✅ **DONE (Plan
       020)** with the anchors from Steps 1-3 as evidence, in the same "stale row corrected, not
       deleted" style RL-4/RL-20 established. Rewrite the KI-12 row (`BACKLOG.md:229`) per Step 4.
6. [x] `BACKLOG.md:23` — replace the "**C2 / providify 2.0.1 — Gates the release**" locked-decision
       row with the Decision 1 outcome: providify 2.0.1 shipped **without** the fix and declares
       the behaviour intentional (brief 002); varco adopts `@Disposes`; **3.0.1 is gated on
       nothing external**. Keep the old text struck through or quoted, per this repo's
       "reversals are visible, never silent" convention (see the 3.0.0 cycle's reversal note at
       `BACKLOG.md:113-116`).
7. [x] `BACKLOG.md:41` — rewrite the C2 row to the reshaped scope of §D-C2 (adopt `@Disposes`,
       nine sites, close the gap as *upstream declared intentional; varco adapted*), and delete
       the "⚠️ BLOCKED" / "do not implement the workaround" language.
8. [x] `BACKLOG.md:81-89` — replace the three open questions with their answers (see §"Answers to
       BACKLOG's three open questions"), pointing at this plan.
9. [x] `CLAUDE.md:185-191` — rewrite the branch-protection paragraph: the ruleset **is applied**
       (branch ruleset `main-branch-protection` + tag ruleset `release-tags`, per Plan 023 Phase 9
       / Appendix A). Keep the invariant sentence unchanged: the required check is and remains
       only `Tests / All tests passed`; `release`, `docs`, `scorecard`, `chaos` must never be
       selected.
10. [x] `CLAUDE.md:193-198` — rewrite the "Manual, out-of-repo operator steps" paragraph from
        *pending instructions* to a *completed record*: the ten GitHub Environments, the ten PyPI
        trusted publishers, and the Pages source are **done**; the runbook remains the reference
        for re-doing them (e.g. an eleventh package), not a to-do list.
11. [x] `design/varco-1-0-release/release-runbook.md` — add a dated "✅ Applied" banner under the
        title and to §1 (Environments), §2 (trusted publishers), §3 (Pages source), §4 (`main`
        ruleset — its heading still reads "after the release tag — not now", `:56`), and §6
        (`v3.0.0` released). Do not delete the procedures; they are the re-run instructions.

**Verification (P0):** `make lint` (docs-only changes still pass `ruff format --check` on
Markdown-adjacent files); Step 4's integration run. **DoD:** no open BACKLOG row and no CLAUDE.md
sentence asserts something contradicted by source or by the operator's report. **No CHANGELOG
entry** — Phase 0 changes no user-visible behaviour.

### Phase 1 — C2: `@Disposes` teardown + providify ≥ 2.0.1 (🔴 must, S)

12. [x] Bump the ten pins `providify>=2.0.0` → `>=2.0.1`:
        `varco_core/pyproject.toml:28`, `varco_kafka:30`, `varco_nats:32`, `varco_redis:28`,
        `varco_sa:25`, `varco_beanie:34`, `varco_memcached:26`, `varco_ws:25`, `varco_fastapi:31`,
        `varco_casbin:24`. Then `uv lock` + `uv sync --all-packages --all-extras`; confirm
        `uv.lock` resolves `providify==2.0.1`. Run `make test` **before touching anything else** —
        this establishes empirically that the new `UNREACHABLE_PRE_DESTROY` WARNING does not turn
        the seventeen existing `assert_no_structural_di_issues()` call sites red (predicted green
        by §D-C2-test; record the observed result either way).
13. [x] `varco_redis/tests/test_redis_cache_disposes.py` (new) — **failing first**: install
        `RedisCacheConfiguration`, assert `container.validate(raise_on_error=False)` reports zero
        `IssueKind.UNREACHABLE_PRE_DESTROY` issues in `report.issues`; plus the §D-C2-test
        monkeypatched start/stop round-trip asserting `RedisCache.stop()` runs on `ashutdown()`.
14. [x] `varco_redis/varco_redis/cache.py` — add `@Disposes(CacheBackend) async def
        close_cache(self, cache: CacheBackend) -> None: await cache.stop()` to
        `RedisCacheConfiguration` (after `:597`) and the same to `RedisLayeredCacheConfiguration`
        (after `:830`). Import `Disposes` from `providify` at `:48`. Correct the two Lifecycle
        docstrings at `:530-533` and `:709-712` to name `@Disposes`, not `@PreDestroy`.
15. [x] `varco_memcached/tests/test_memcached_cache_disposes.py` (new, same two-assertion shape) →
        `varco_memcached/varco_memcached/cache.py` — `@Disposes(CacheBackend)` on
        `MemcachedCacheConfiguration`; correct `:600-602`; correct `varco_memcached/varco_memcached/di.py:194-196`.
16. [x] `varco_redis/tests/test_redis_cache_disposes.py` — add the §D-C2-firstmatch test: install
        **both** cache configurations into one container, resolve, `ashutdown()`, assert **both**
        instances were stopped. If it fails, file
        `design/upstream-gaps/providify-disposes-first-match.md` + an `UPSTREAM-GAPS.md` row +
        `xfail(strict=True)`; do **not** hand-roll a workaround.
17. [x] `varco_redis/tests/test_redis_bus_disposes.py` (new) → `varco_redis/varco_redis/bus.py` —
        `@Disposes(AbstractEventBus)` on `RedisEventBusSelectorConfiguration` (after `:558`)
        calling `await bus.stop()`. Assert **double-stop safety**: the varco_fastapi lifespan stops
        the bus *and* then calls `ashutdown()`; `RedisEventBus.stop()` is documented idempotent
        (`bus.py:217-219`) and `varco_fastapi/varco_fastapi/lifespan.py:212-213` already records
        that all ten `@PreDestroy` components were measured idempotent. Correct
        `varco_redis/varco_redis/di.py:129-130`.
18. [x] Tier B, one test + one `@Disposes` each, same shape:
        `varco_redis/varco_redis/dlq.py` (`RedisDLQConfiguration`, `:748`),
        `varco_redis/varco_redis/stream_dlq.py` (`RedisStreamDLQConfiguration`, after `:769` —
        confirm the provider connects before adding), `varco_redis/varco_redis/bulkhead.py`
        (`RedisBulkheadConfiguration`, `:496`; correct the false docstring at `:457-459`),
        `varco_kafka/varco_kafka/dlq.py` (`KafkaDLQConfiguration`, `:765`),
        `varco_nats/varco_nats/dlq.py` (`NatsDLQConfiguration`, `:754`).
19. [x] Delete the strict xfail at `varco_core/tests/test_providify_provider_predestroy.py:79-88`.
        The file must **not** be deleted: rewrite it as a *characterization* of the settled
        upstream contract — `@PreDestroy` on a `@Provider`-produced instance does **not** run
        (assert `resource.closed is False`), a `@Disposes` on the same configuration **does**, and
        the `ClassBinding` control at `:61-76` still passes. Rewrite the module docstring: the gap
        is closed as *intentional upstream, adapted in varco*, citing brief 002 and
        `providify/SKILL.md:287`.
20. [x] Delete the strict xfail at
        `varco_redis/tests/test_redis_cache_lifespan_shutdown_integration.py:61-69` and update the
        module docstring (`:1-49`, particularly the "⛔ And the RL-8a adoption does not fix it"
        block at `:18-36`) to describe the `@Disposes` fix. The body's assertions (`:120-121`)
        become real, passing assertions against a real Redis container.
21. [x] Docs, same commit:
        - `design/upstream-gaps/providify-provider-predestroy.md` — **append** a "§8. Resolution
          (2026-09-02)" section (never delete the file, per its own filing note at `:204-214`):
          upstream declared the behaviour intentional and documented `@Disposes` as the supported
          path (brief 002; `providify/CHANGELOG.md:12-32`); varco adopted it at nine sites;
          §5's own proposal is what shipped. Update the `**Status**` line at `:7`.
        - `UPSTREAM-GAPS.md:59` — move the `P22-PROVIDER-PREDESTROY` row from "Open gaps" to
          "Recently closed" (`:61-66`), with the resolution one-liner.
        - `CHANGELOG.md` `## [Unreleased]` → `### Fixed`: the leak, the nine sites, the
          `providify>=2.0.1` floor. Keep the existing docs-workflow entry.
        - `CLAUDE.md` — no new section; if the DI verb taxonomy or the providify-limitation section
          implies `@PreDestroy` works for `@Provider` output anywhere, correct it.

**Verification (P1):** `make test` (all eleven suites — Steps 13-19 are Docker-free);
`make lint`; `make type-check`; `make integration-test PKG=varco_redis` for Step 20.
**DoD:** zero `UNREACHABLE_PRE_DESTROY` issues across every package's DI-health container; both
strict xfails gone and their files green; `UPSTREAM-GAPS.md` "Open gaps" is empty.

### Phase 2 — C3: `BeanieMigrator` index-mode (🔴 must, S — integration-only)

22. [x] `rg -n "dry_run" varco_beanie/tests varco_core/varco_core/cli varco_fastapi` — establish
        whether any test or CLI output asserts on `upgrade(dry_run=True)`'s exact `applied`
        tuple before changing it (§D-C3 item 1).
23. [x] `varco_beanie/tests/test_beanie_migration_integration.py` — delete the `strict=True` xfail
        at `:91-92` and the `_INDEX_CREATE_SKIPPED_WITHOUT_PENDING_MIGRATIONS_REASON` constant at
        `:72-88`. Run `-m integration` to see it **fail** (red-first). Add two more cases in the
        same file: (a) `index_mode="create"` with a *non-empty* registry still creates indexes
        (regression guard on the restructure); (b) a second `upgrade()` with no drift acquires **no**
        lock (assert via a store spy or by asserting `skipped_locked is False` and no lock
        document is written).
24. [x] `varco_beanie/varco_beanie/migration/migrator.py:160-242` — apply the §D-C3 restructure:
        dry-run includes index revisions; the `:170-171` early return is conditioned on index work
        too; the `:203-207` post-lock re-check sets a `skipped_locked` flag and falls through
        instead of returning; the final `MigrationReport` (`:240-242`) carries that flag. Add a
        `DESIGN:` block naming why the zero-pending index path takes the lock.
25. [x] Docs: `technical_docs/features/schema-migrations.md` (the Mongo index-mode section) —
        state that `index_mode="create"` reconciles indexes **whether or not** revisions are
        pending, and that reconciliation happens under the migration lock. `CHANGELOG.md`
        `### Fixed`.

**Verification (P2):** `uv run pytest varco_beanie/tests/test_beanie_migration_integration.py -m
integration` (needs Docker + MongoDB); then `make integration-test PKG=varco_beanie`; then
`make test` for the non-integration regression surface. **DoD:** the previously-xfailed test
passes for the right reason, and the two new cases pass.

### Phase 3 — C5: make the API-surface snapshot a gate (🟡 should, S)

26. [x] `uv run python scripts/api_surface.py --check` — re-run and confirm exit 0 (it was green
        this session; Phases 1-2 have since touched source, so re-confirm. If Phase 1's
        `@Disposes` methods moved the needle, that is a **finding**, not a rubber stamp: the
        snapshot records only top-level `__all__` names + function signatures, so a new
        `@Configuration` method must be invisible. If it is not, stop and re-read the
        strict-patch constraint.)
27. [x] `Makefile` — add `.PHONY: api-check` / `api-check:` running
        `$(UVRUN) python scripts/api_surface.py --check`; add it to `lint:` (`:151-154`) under
        `ifeq ($(strip $(PKG)),)`; add a `make help` line (`:89-98`).
28. [x] `.github/workflows/test.yml` — add an `api surface --check` step to the `lint` job after
        `mypy` (`:57-63`). No new job, no change to `all-green`'s `needs`.
29. [x] `varco_core/tests/test_repo_tooling_pins.py` (or a sibling repo-guard test) — assert the
        `lint` job's step list contains the api-surface step, in the same spirit as the existing
        pin-parity guards, so the gate cannot be silently removed.
30. [x] Docs: `CLAUDE.md` §"Public API surface snapshot" — replace the "⚠️ **This is not a gate
        today.**" paragraph with the gate's real status **and its honest scope** (removals +
        *function* signature changes only; a narrowed class `__init__` stays invisible; additions
        and module moves are notes). Keep the "regenerate and commit the snapshot alongside the
        change" instruction — it is now a *hard requirement*, not a courtesy.
        `CONTRIBUTING.md` — one line in the PR checklist. `CHANGELOG.md` `### Changed`.

**Verification (P3):** `make lint`; `make lint PKG=varco_redis` (must stay fast and **not** run
the api check); `make api-check`; `make test PKG=varco_core`. **DoD:** removing a name from any
`__all__` locally turns `make lint` red.

### Phase 4 — C7: conformance coverage audit + fill (🟡 should, M)

31. [x] `testkit/varco_conformance/COVERAGE.md` (new, never packaged) — the §D-C7 matrix verbatim,
        with a "stated absences" section for `NoopEventBus` (Null Object; pre-cancelled
        `Subscription` at `varco_core/varco_core/event/memory.py:665-691`), `varco_ws` (push
        adapters — points at `varco_ws/tests/test_ws_conformance.py`), `varco_memcached`
        (`CacheBackend` only), `varco_casbin` (implements none of the five; therefore needs no
        `pythonpath = ["../testkit"]` — the only package without one, deliberately), and
        `channel_manager` (no in-process implementation).
32. [x] `varco_redis/tests/test_redis_conformance.py` — add `class
        TestRedisStreamDLQConformance(DeadLetterQueueConformance)` after `:71`, mirroring
        `TestRedisDLQConformance` (`:66-70`) with
        `async with RedisStreamDLQ(RedisEventBusSettings(url=redis_url)) as dlq: yield dlq`
        (`stream_dlq.py:211-218` documents the context-manager shape). Import at `:28`-adjacent.
        Per-test namespacing: the shared session container requires a `uuid4().hex[:8]`-scoped
        prefix if the suite does not already provide isolation.
33. [x] **Pre-authorised**: if the new suite reveals a genuine ABC-contract violation (the
        `delete_where` "no predicate → `ValueError`" hole already recorded for `KafkaDLQ`/`NatsDLQ`
        is the likely candidate), mark it `@pytest.mark.xfail(reason="BUG: …", strict=True)` and
        add a one-line BACKLOG row — **never** an in-place production fix. That is the repo norm
        and it is not a phase failure.
34. [x] `varco_core/tests/test_conformance_inmemory.py` — extend the module docstring with the
        `NoopEventBus` stated reason and a pointer to `COVERAGE.md`. No new test class.
35. [x] Docs: `CLAUDE.md` §Test Conventions' conformance paragraph — link `COVERAGE.md` as the
        authoritative matrix and state the rule: *a new implementation of one of the five ABCs
        either subclasses its suite or gets a row in `COVERAGE.md` explaining why not*.
        `CHANGELOG.md` `### Changed` (test-surface only).

**Verification (P4):** `make integration-test PKG=varco_redis` (the new suite is
`@pytest.mark.integration` via the module's `pytestmark` at `:32`); `make test` for the docstring
change. **DoD:** every implementation of the five ABCs is either subclassed or has a written
reason in `COVERAGE.md`.

### Phase 5 — C8: Kafka chaos flake, one attempt (🟡 should, M)

36. [x] Reproduce: `make chaos-test PKG=varco_kafka` up to 3 times, recording pass/fail and the
        failure mode of each run. If 3/3 green, record that and close the row as unreproducible on
        this machine — do **not** chase it further.
37. [x] One root-cause pass on `varco_kafka/tests/test_kafka_chaos.py:185` /
        `:263`, focused on multi-client reconnection timing after `chaos.restart()` (`:230`,
        `:304`). Explicitly out of scope as a cause: the host-port remap (already fixed, `:96-123`)
        and non-KRaft mode (already required, `:45-58`).
38. [x] **Pre-authorised downgrade** if Step 37 does not resolve it: widen the timing margin
        (`:86-87` already documents "widen a flaky timing margin, never xfail it" as the house
        rule) if the evidence is timing; otherwise add a `⚠️ Known flake` note to the module
        docstring naming the observed failure mode and the run count, plus a BACKLOG row. Ship.
39. [x] Docs: `BACKLOG.md` C8 row — record the outcome either way. No `CHANGELOG.md` entry unless
        production code changed.

**Verification (P5):** `make chaos-test PKG=varco_kafka` (chaos + Docker; never on `push: main`,
never a required check). **DoD:** either 3/3 green after a fix, or a documented known-flake with
its evidence.

### Phase 6 — C9: `RedisJobStore` atomic CAS claim (🟢 nice, M — DROPPABLE)

40. [ ] `varco_redis/tests/test_redis_job_store_claim.py` — a red-first N-concurrent-claimers test
        (`@pytest.mark.integration`, real Redis): N tasks call `try_claim(job_id)`, exactly one
        wins, and no claim key survives. Include the crash-between-guard-and-save case the current
        design documents as a drawback (`job_store.py:576-579`).
41. [ ] `varco_redis/varco_redis/job_store.py` — add a module-level `_CLAIM_SCRIPT` per §D-C9,
        modelled on `varco_redis/varco_redis/lock.py:114` + `:345-346`; rewrite `try_claim`
        (`:608-655`) to read → build the RUNNING JSON → `eval` the CAS; delete the claim key, the
        `claimed` flag and the `finally` guard release (`:612-645`). Replace the `DESIGN:` block at
        `:568-590` with the CAS reasoning, keeping the RT7a-guard history as a superseded note.
42. [ ] Check for other `_claim_key` readers/writers before deleting it —
        `reap_expired_leases()` releases this claim's guard today (`:580-590` documents it).
43. [ ] Docs: `technical_docs/features/job-scheduling-and-leases.md`; `CHANGELOG.md`
        `### Changed` (internal behaviour, no API change); close `BACKLOG.md`'s C9 row.

**Verification (P6):** `make integration-test PKG=varco_redis`; `make test PKG=varco_redis`.
**DoD:** the concurrency test passes and no `claim:` key remains in Redis after a claim.
**If any earlier phase ran long, drop this phase and leave the BACKLOG row open.**

**DROPPED (2026-09-02)**, per this phase's own pre-authorization: 🟢 nice, "replaces a fix that
already works," "nothing may wait on it." Step 40's pre-written test
(`varco_redis/tests/test_redis_job_store_claim.py`) surfaced that its
`test_no_claim_key_survives_after_a_claim_round` assertion does not hold for the
*winning* claimer under the current guard-key design: `try_claim()`'s `finally`
block only deletes the claim key on a **non-success** path
(`job_store.py:645-647`, `if not claimed: await self._client.delete(claim_key)`)
— the winner's guard key is left in place (with its TTL) by design, not deleted.
This is not a newly-discovered production bug; it is exactly the class of
two-separate-keys drawback the module's own `DESIGN:` block already documents
(`job_store.py:576-579`) and exactly what C9's CAS rewrite (Steps 41-42, not
executed) would have eliminated by construction. BACKLOG's C9 row is left open,
not closed — Steps 40-43 are unchecked above. No production code changed in this
phase.

### Phase 7 — release

44. [ ] `uv run python scripts/bump.py --set 3.0.1 --dry-run`, inspect the diff, then
        `uv run python scripts/bump.py --set 3.0.1` (writes the ten versions + `uv lock`). This is
        the **only** mechanism permitted to write a version number. Sibling requirement strings
        stay `~=3.0` (compatible release) — never `==`; `bump.py` handles this, do not hand-edit.
45. [ ] `CHANGELOG.md` — promote `## [Unreleased]` to `## [3.0.1] — <date>`, leaving a fresh empty
        `## [Unreleased]`.
46. [ ] Full gate sweep: `make lint`, `make type-check`, `make test`,
        `uv run python scripts/api_surface.py --check`,
        `uv run pytest varco_core/tests/test_bump_script.py::test_workspace_versions_are_coherent`,
        `make integration-test-clean`.
47. [ ] Tag `v3.0.1` and push — `.github/workflows/release.yml` builds ten wheels and publishes via
        the ten `pypi-<name>` environments with PEP 740 attestations. `docs.yml` publishes the
        versioned docs via `mike`.
48. [ ] `BACKLOG.md` — mark the 3.0.1 cycle closed; carry any downgraded C8 row and an unshipped
        C9 forward.

---

## Edge cases

- **`ashutdown()` runs a disposer for a component the lifespan already stopped** → benign;
  every varco `stop()`/`disconnect()` is documented idempotent, and
  `varco_fastapi/varco_fastapi/lifespan.py:212-213` records that all ten `@PreDestroy` components
  were measured idempotent. Asserted in Step 17.
- **Two `CacheBackend` provider bindings in one container** (recursive scan installs both cache
  configurations) → the `@Disposes` first-match loop may attach one configuration's disposer to
  the other's binding (`providify/container.py:6202-6214`). Both disposers do `await
  backend.stop()` on a `CacheBackend`, so the effect is identical. Proven, not assumed — Step 16.
- **`@Disposes` where the produced class has no `@PreDestroy`** (Tier B, six sites) → providify's
  `UNREACHABLE_PRE_DESTROY` never fires for these, so **only** the Step-18 behavioural tests can
  catch a regression. Do not rely on `validate()` for Tier B.
- **`index_mode="off"` or `index_guard is None`** → C3's new condition must short-circuit before
  `_index_pending()`'s `listIndexes` round-trip; `_index_pending()` already returns `[]` for
  `"off"` (`migrator.py:139-140`), but the guard must be explicit so no extra I/O is paid.
- **`index_mode="create"` with drift, but another instance holds the lock** → returns
  `skipped_locked=True` with no index work; the holder does it. Unchanged from today.
- **Second `upgrade()` with no drift** → `_index_pending()` returns `[]`, so the early return
  fires and **no lock is taken**. This is what keeps the common startup path cheap, and it is what
  the existing idempotency assertion at
  `varco_beanie/tests/test_beanie_migration_integration.py:119-121` exercises.
- **`make lint PKG=varco_redis`** → must **not** run the api-surface check (§D-C5).
- **`api_surface.py --check` in CI without `--all-extras`** → would fail on import; the `lint` job
  already syncs `--locked --all-packages --all-extras` (`test.yml:52`). Do not add a second sync.
- **`NoopEventBus` in a future conformance run** → its pre-cancelled `Subscription` means most of
  `EventBusConformance` cannot pass; the `COVERAGE.md` row is the answer, not an xfail sweep.
- **A conformance failure in Step 32** → `xfail(strict=True)` + a BACKLOG row (Step 33), never an
  in-place production fix.
- **C8 unreproducible (3/3 green)** → record and close; three green runs are not proof of absence
  (RL-21's own lesson), but the time-box is the point.

---

## Verification

Per phase, in order. Everything is run from the workspace root.

```bash
# P0
make lint
uv run pytest varco_beanie/tests/test_beanie_bootstrap.py -m integration -k round_trip  # Docker+Mongo

# P1
uv lock && uv sync --all-packages --all-extras
make test                                   # eleven suites; Steps 13-19 need no Docker
make lint && make type-check
make integration-test PKG=varco_redis       # Step 20's real-container proof

# P2  (integration-ONLY — the fix is unprovable without MongoDB)
uv run pytest varco_beanie/tests/test_beanie_migration_integration.py -m integration
make integration-test PKG=varco_beanie

# P3
uv run python scripts/api_surface.py --check
make api-check && make lint && make lint PKG=varco_redis
make test PKG=varco_core

# P4
make integration-test PKG=varco_redis
make test

# P5  (chaos-ONLY)
make chaos-test PKG=varco_kafka             # up to 3 runs, per the time-box

# P6  (droppable)
make integration-test PKG=varco_redis && make test PKG=varco_redis

# P7
uv run python scripts/bump.py --set 3.0.1 --dry-run
uv run python scripts/bump.py --set 3.0.1
make lint && make type-check && make test
uv run python scripts/api_surface.py --check
uv run pytest varco_core/tests/test_bump_script.py::test_workspace_versions_are_coherent
make integration-test-clean
```

Test requirements per repo norms: **every code change carries a unit test** (Steps 13, 15, 16, 17,
18, 23, 29, 40); **anything touching a real broker/DB carries an integration test** (Steps 20, 23,
32, 40). Phases 2 and 4 are integration-gated; Phase 5 is chaos-gated. Docs ship **in the same
commit** as the code that makes them true (`CONTRIBUTING.md:40-43`).

---

## Risks

- **⚠️ ASSUMPTION — providify 2.0.1 resolves from PyPI in CI.** Brief 002 records the 2026-09-01
  upload and a local checkout at `/home/edoardo/projects/providify` already at 2.0.1; this plan
  did not re-query PyPI. *Invariant:* `uv lock` must produce `providify==2.0.1` in `uv.lock`; if
  it resolves lower, stop — the pin bump is meaningless without it.
- **⚠️ ASSUMPTION — `UNREACHABLE_PRE_DESTROY` is `WARNING`-severity and therefore invisible to
  `assert_no_structural_di_issues()`.** Read from `providify/README.md:1535-1538`,
  `CHANGELOG.md:16-20` and `container.py:496-508`; **not executed**. Step 12 runs `make test`
  immediately after the lock bump precisely to falsify this cheaply. *Invariant:* if seventeen
  DI-health tests turn red, the fix is to add a WARNING-aware assertion, never to widen
  `assert_no_structural_di_issues()`'s tolerated set.
- **⚠️ ASSUMPTION — the `@Disposes` first-match/overwrite behaviour is as read** at
  `providify/container.py:6202-6214`; not executed. Step 16 is the falsification.
- **⚠️ ASSUMPTION — `RedisStreamDLQConfiguration.redis_stream_dlq` connects inside the provider**
  (inferred from its siblings' shape); confirm before adding its `@Disposes` (Step 18).
- **⚠️ ASSUMPTION — Docker with MongoDB, Redis, Kafka is available.** Phases 2, 4, 5 and the P7
  clean-room sweep are unprovable without it. *Invariant:* never mark a `-m integration` step
  "done" from a unit-test run.
- **⚠️ ASSUMPTION — the operator steps really are applied** (PyPI, ten Environments, ten trusted
  publishers, Pages source, branch + tag rulesets). This is the user's report; nothing in the tree
  can verify it. Phase 0 writes it down as fact — if it is wrong, `release.yml` fails at publish
  time on the `v3.0.1` tag, which is a loud, recoverable failure.
- **⚠️ ASSUMPTION — C8's root cause is reconnection timing, not a real bug.** Unknown by
  construction; that is why Decision 2 time-boxes it.
- **Scope creep into 3.1.** C2's audit found nine sites; the temptation is to "tidy" adjacent DI
  wiring. *Invariant:* no new public symbol, no new `__all__` entry, no signature change —
  mechanically enforced by Step 26 and by P3's gate thereafter.
- **A `@Disposes` double-stop breaks a non-idempotent `stop()`.** *Invariant:* every hook added in
  Phase 1 must target a method whose docstring already claims idempotence; if one does not, make
  it idempotent **and test it** rather than skipping the disposer.
- **C3's dry-run change alters output someone parses.** *Invariant:* Step 22 runs before Step 24;
  if a CLI consumer asserts on the tuple, keep dry-run unchanged and note the `plan()`/`upgrade()`
  divergence in the feature doc instead.
- **P6 (C9) rewrites a working claim path.** *Invariant:* it is droppable and sorts last; if
  Step 40's concurrency test is not unambiguously green, revert the phase rather than ship a
  weaker claim.

---

## Answers to BACKLOG's three open questions

1. **"C2's start signal — does providify 2.0.1 have a date?"** — It shipped **2026-09-01** and
   **does not contain the fix**; the behaviour is declared intentional (Jakarta CDI producer-method
   rule), with only a detection warning and docstring corrections added (brief 002 §Findings;
   `providify/CHANGELOG.md:12-32`, `SKILL.md:287`, `README.md:945-949`). The gate is therefore
   **removed**, not rescheduled, and the 3.0.2-fallback is moot: varco adopts `@Disposes` itself
   (Decision 1, §D-C2). **3.0.1 is gated on nothing external.**
2. **"C7's audit outcome — how many absences are real?"** — **Two**, out of a five-suite ×
   ~24-implementation matrix: `NoopEventBus` (resolved as a *stated reason*, not a subclass) and
   `RedisStreamDLQ` (resolved by subclassing). `varco_ws` was already resolved as a documented
   push-adapter absence; `varco_memcached` and `varco_casbin` absences are legitimate. C7 stays
   **M (arguably S–M)** and is **not split** (§D-C7).
3. **"C8's time-box?"** — **One focused root-cause pass**, ≤ 3 `make chaos-test PKG=varco_kafka`
   runs plus the reading they justify. Then a **pre-authorised downgrade** to a documented
   known-flake (BACKLOG row + module-docstring note) — an explicit step, not a failure
   (Decision 2, §D-C8).

---

## What did not make the cut, and why

- **N1 MCP v2 migration** — MCP Python SDK v2.0.0 removes the `@server.list_tools()` /
  `@server.call_tool()` lowlevel decorators that Plan 020 rebuilt `to_mcp_server()` on. That is a
  rewrite of a public adapter, not a patch. The `mcp>=1.28.1,<2` pin holds, so nothing is broken
  today. **3.1.**
- **N2 CloudEvents envelope / N3 AsyncAPI export** — both triggers **FIRED** (brief 001 §3) and
  both designs are complete (Plan 022 §D-CE1-4, §D-AA1-4) — and both are **purely additive**, so
  neither is patch-legal. N3 additionally carries an unassessed `datamodel-code-generator`
  dependency risk to resolve at plan time. **3.1.**
- **N4 NATS max-deliveries → DLQ bridge** — a new advisory-consumer wiring; additive. **3.1.**
- **N5 `BeanieConfig`/`BeanieSettings` collapse** — **breaking**, and needs the deprecation cycle
  `CONTRIBUTING.md` mandates. Minor at minimum; cheapest at the *start* of 3.1.
- **The whole parked table** — OpenFeature (trigger NOT FIRED and mis-specified; Python SDK at
  0.8.4), Toxiproxy (NOT FIRED; no `testcontainers.toxiproxy` module, client 0.x and possibly
  orphaned), RL-16 integration-gating (needs ≥30 consecutive nightly runs with ≤1 non-code
  failure — below 30 there is no measurement, only anecdote), WD-1, RT4-ws-scale, and the
  carried-forward 3.0.0 rows. Brief 001 confirms no trigger fired for any of them.
- **Operator/release debt (Plan 023 Phases 8-9)** — reported **done**; the only work left was the
  in-tree docs still claiming otherwise, which is C1 (Steps 9-11), not a parked item.
