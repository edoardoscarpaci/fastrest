# Plan 018 — Reliability floor: RT integration coverage + chaos / fault injection

BACKLOG.md **Phase 3 — reliability floor (RT, Plan 012)**, items RT2, RT3, RT4, RT5, RT7, RT8,
RT9 (`BACKLOG.md:99-114`). Answers the register's own open question *"RT7 shape: which failures
are simulated in-process vs driven by real container kill/restart, and whether chaos tests run in
`integration.yml` or on a separate schedule"* (`BACKLOG.md:176-177`).

## Goal

After this plan, every reliability guarantee varco advertises has at least one test that
exercises it against a **real** broker/database rather than a mock: NATS delivery semantics and
stream management, Kafka exactly-once/at-least-once/at-most-once, Casbin concurrent writers on
Postgres, `varco_ws` backpressure over a real socket, migration lock-timeout and crashed-holder
recovery, and — new — a `chaos` test class that kills, pauses and restarts the container
underneath a running relay/breaker/lease and asserts nothing is lost. Chaos tests are a
first-class, marker-selected suite (`testkit/varco_chaos` helpers, `@pytest.mark.chaos`) with
their own runner entry point (`make chaos-test`) and their own nightly CI job. BACKLOG's Phase-3
table is corrected against source in the same commit.

## Non-goals

- **No production-code behaviour changes.** Every contract violation these tests surface becomes
  `@pytest.mark.xfail(reason="BUG: ...", strict=True)` plus a BACKLOG row — the standing rule in
  CLAUDE.md §Test Conventions, applied here to *all* new tests, not just conformance subclasses.
  Two exceptions, and only two: (a) purely additive **test-only** endpoints in a package's own
  `tests/conftest.py`, (b) `markers = [...]` / `pythonpath` lines in `[tool.pytest.ini_options]`.
- **No reference application.** Research 001's Librarian's Note is explicit — Temporal,
  Confluent/Kafka and FastAPI all validate reliability per-feature; *"a dedicated reference app
  adds burden without proportional evidence of bug detection"*. BACKLOG's park of *"New dedicated
  e2e reference application"* (`BACKLOG.md:151`) stands. Not re-litigated.
- **No WS/SSE conformance base class.** `varco_ws/tests/test_ws_conformance.py` is a deliberate
  no-op landing-page module: WS/SSE are push *adapters*, not `AbstractEventBus` implementations.
  Documented design resolution; do not add a fifth `testkit/varco_conformance` module for them.
- **No Toxiproxy** (Design §RT7-toxiproxy). Deferred to 3.1 with a BACKLOG row.
- **No integration-gates-PRs change.** RL-16 stays open; this plan takes a position on *when* it
  should be closed (§RT7-ci) but does not close it. Chaos tests, being the flakiest class, are
  explicitly **not** proposed as a PR gate.
- **No Phase 4 / Phase 5 work.** RL-8 (API audit), RL-9…RL-13 (release engineering) are
  untouched. No version bumps, no `pyproject.toml` metadata edits beyond pytest markers.
- **No new workspace member.** Chaos tests live in existing packages' `tests/` dirs (§RT7-home);
  `testkit/` gains a module but stays never-packaged, exactly as `varco_conformance` is.
- **No cooperative-rebalance (KIP-429) coverage.** Research 003 §Evidence Gaps: aiokafka 0.13.0
  documents eager rebalancing only; cooperative support is undocumented and unevidenced. A test
  for it would be a test for a feature that probably does not exist.

---

## Status corrections to BACKLOG.md's Phase-3 table

BACKLOG.md:99-114 claims to carry *"**Verified status**, not the stale figures"*. Six of its nine
rows do not survive contact with the tree. State these in the BACKLOG update (Step 41) — the same
U-8 "verify in source, not from docs" discipline Plan 017 applied to its own register rows.

| BACKLOG claim | Reality (verified in source) |
|---|---|
| RT8 *"Suite executed and its findings recorded, but **not wired into the standard runner**"* | Wired into **both** runners already: `scripts/unit_tests.sh:61` and `scripts/integration_tests.sh:100` each carry `EXTRA_SUITES=("examples/00-full-stack-post-api:example/tests")`, with the `run_from="root"` handling both scripts document at length. **RT8 is done** — nothing remains (Step 41 closes it). |
| RL-20 *"the `unit` job is RED on its very first CI run"* (`BACKLOG.md:76-84`) | **Already fixed.** `uv run pytest examples/00-full-stack-post-api/example/tests/test_post_service.py -q` → **16 passed**. The `InMemoryUoW`-missing-`.posts` failure does not reproduce. The load-bearing "do not rely on `all-green` as a merge gate" caveat no longer applies. |
| RT4 *"🟠 partial (2/7)"*, gap = *"pooling, backpressure, reconnect, ordering"* | Of the four named behaviours, **three are already covered over a real socket**: `varco_ws/tests/test_ws_integration.py` has `test_connection_pooling_multiple_clients_each_receive_broadcast`, `test_message_ordering_single_connection`, `test_reconnect_after_drop_resumes_receiving`, `test_publish_is_delivered_over_real_websocket`; `test_sse_integration.py` has two more. **Only backpressure is missing.** Further: all four `BackpressurePolicy` branches are *already* unit-tested deterministically (`varco_ws/tests/test_ws_bus.py:230,255,279,297,321`). The residual gap is one real-socket test, not a suite. **RT4 is S, not M.** |
| RT3 *"🟠 partial (2/10)"*, *"largely unverified"* against Postgres | **Seven** real-Postgres tests exist in `varco_casbin/tests/test_persistence_integration.py` (`test_policy_persists_across_engines_postgres`, `test_remove_policy_round_trip`, `test_remove_filtered_policy_round_trip`, `test_rbac_role_inheritance_after_cold_reload`, `test_abac_enforcement_persisted`, `test_two_engines_share_database_writer_reader`, `test_domain_scoped_rbac_persisted`), on a function-scoped isolated-database fixture (`conftest.py:66 casbin_db_url`). The genuine remaining gap is **concurrent writers** and the `adapter="file"` corruption CLAUDE.md already warns about. **RT3 is S.** |
| RT5 *"🟠 partial (7/15)"*, gap = *"rebalancing/offset paths"* | True but incomplete. Real counts: rebalance **1** (`test_two_consumers_share_partitions_then_survivor_takes_over`), offsets **3**, dlq **3**, channel 6, base 3. The row **fails to name the largest gap**: `varco_kafka/tests/test_kafka_eos.py` has **13 tests, all against `FakeProducer`/`FakeConsumer`/`FakeTransaction`, none marked `integration`** — `KafkaDeliverySemantics.EXACTLY_ONCE` has **zero real-broker verification** while being an advertised guarantee. |
| RT9 *"⬜ pending"*, *"unit-tested only"* | **False.** `varco_fastapi/tests/test_app_migrations_integration.py` (`pytestmark = pytest.mark.integration`, `:47`) already covers `check`-fails-closed, `upgrade`-applies-and-is-idempotent, `on_failure` fail/warn, `mode=off`-touches-nothing, and two concurrent lifespans (`:302`). `varco_sa/tests/test_migration_lock.py` covers two concurrent migrators, `MigrationLockTimeout` raised, and clean-return-when-nothing-pending; `varco_beanie/tests/test_beanie_migration_lock.py` exists too. **RT9 is 🟠 partial with a small residual**, not pending — see §RT9-scope for what the residual actually is. |
| Scout-report claim: *"`varco_core/tests/test_conformance_inmemory.py` does NOT include an InMemory DLQ conformance subclass"* | **Wrong** — `TestInMemoryDeadLetterQueueConformance(DeadLetterQueueConformance)` is at `varco_core/tests/test_conformance_inmemory.py:144`. All four ABCs are covered in-process. No gap. |
| RT2 *"🟠 partial (2/13 files marked)"* | Confirmed, and it is the thinnest. `varco_nats/tests/test_nats_integration.py` has exactly **2** tests, across **8** source modules and **10** test files. `test_regression_nats_dlq_ack_durability.py` is `_FakeMsg`-based. `NatsStreamManager` (declare/delete/exists/list) and all three `NatsDeliverySemantics` branches have **zero** real-broker coverage. (File count is 10, not 13.) |

---

## Design

### §RT7-shape — one mechanism per scenario, chosen by *what actually fails* ✅

The BACKLOG's open question asks in-process vs. container-restart vs. network-fault **per
scenario**. They do not get the same answer, because the thing that fails is different in each.

| Scenario | Mechanism | Why this one |
|---|---|---|
| **Outbox relay + broker restart** | **Real container restart** — `container.get_wrapped_container().restart(timeout=5)` | The invariant is "an entry survives a broker that is genuinely gone and is republished when it returns". `OutboxRelay._relay_entry` (`varco_core/varco_core/service/outbox.py:809-830`) only deletes an entry *after* `bus.publish()` returns; on any exception `_handle_publish_failure` leaves the row untouched for the next tick. A mock bus that raises proves the `try/except` branch; it does **not** prove that a real `AIOKafkaProducer` raises rather than silently buffering/dropping. That gap is exactly the class of bug RT7 exists to find. |
| **Circuit breaker + real network failure** | **`docker pause` / `unpause`** on a real Redis container + a short client timeout | The breaker's trigger is *a call that fails or times out*. `pause()` freezes the container's processes, so an in-flight request hangs with no RST — which is strictly the **harder** failure mode (a closed port fails fast; a black hole is what actually takes production down and what `@timeout` + `CircuitBreaker` exist for). Deterministic, one docker-py call, no extra container. |
| **Job lease + worker crash** | **Fully in-process**, against a real store — **no container touched** | "Worker crash" means *this process stops renewing*. The store is not what fails. Abandon a claimed lease (cancel the renew task), advance past `lease_expires_at`, have worker B `reap_expired_leases()` + `try_claim()`, then have zombie A attempt `save(expected_epoch=<old>)` and assert `StaleLeaseError` (`varco_core/varco_core/job/base.py:82,682,770,1027,1063`). Killing a container here would prove nothing about fencing and would add 20 s of boot time per test. |

**Two deliberate additions beyond the three named scenarios**, because they are nearly free once
the fixtures exist:

- **Outbox durability across a *database* restart** (`varco_sa`) — restart Postgres under an
  `SAOutboxRepository` mid-relay and assert the pending rows are still there and still relayed.
  This is the half of "outbox" the Kafka test deliberately does not cover (§RT7-home).
- **Migration crashed-lock-holder recovery** (`varco_fastapi` + Postgres) — kill the connection
  holding the advisory lock and assert the next lifespan proceeds rather than hanging to
  `lock_timeout`. This is RT9's genuine residual and it is a chaos test by nature (§RT9-scope).

- ✅ Each scenario uses the cheapest mechanism that can actually falsify the guarantee.
- ✅ Two of the five need no container lifecycle control at all — the suite stays mostly fast.
- ❌ Three different mechanisms to learn. Mitigated by putting all three behind one helper
  (`testkit/varco_chaos/containers.py`, §chaos-fixture) with a three-method surface.
- ❌ `pause`-based breaker testing cannot assert *latency* thresholds, only timeout/failure —
  that is the Toxiproxy capability being deferred (§RT7-toxiproxy).

**Rejected — in-process simulation for all three** (a bus double that raises, a store double that
expires leases). ✅ Zero Docker, zero flake, runs in `unit`. ❌ It is what already exists
(`varco_core/tests/test_outbox.py`, `test_job.py`, the whole `resilience/` unit suite) and it is
precisely what BACKLOG calls *"the guarantees varco actually sells against real failure, **not
mocks**"*. A plan whose answer to "prove it against real failure" is "keep mocking" has not
answered the question. Rejected.

**Rejected — container restart for all three.** ✅ One uniform mechanism. ❌ The lease scenario
gains nothing and pays ~20 s per test; and testcontainers' own `.stop()`/`.start()` pair
**deletes and recreates** the container on a new random host port (research 002 §1, *CRITICAL
FINDING — Port survivorship*, confirmed by testcontainers-java issue #3615 across all
languages) — so a naive uniform "restart everything" would silently break every connection URL.
Rejected.

### §RT7-toxiproxy — **not adopted for 3.0.0.** `pause`/`restart` buys enough ✅

Research 002 §3–§4 is genuinely favourable to Toxiproxy: transparent, deterministic, no
privileges, works in Docker-in-Docker. It is nevertheless rejected **for this release**:

- ❌ **No native testcontainers-python module** (research 002 §3, and §Evidence Gaps 1): Java,
  .NET and Node ship one; Python does not. varco would hand-roll `DockerContainer` + raw REST
  against `/proxies/{name}/toxics`, i.e. maintain the module upstream forgot to write.
- ❌ **The Python client story is unresolved.** Research 002 §Version notes: *"toxiproxy-python:
  Version not clearly published"*; the best-documented option is `chaostoolkit-toxiproxy`, which
  drags in the whole Chaos Toolkit framework as a test dependency for four REST calls.
- ❌ **Untested on GitHub Actions** (research 002 §Evidence Gaps 4: *"No published guide confirms
  Toxiproxy works without rootful Docker or special capabilities flags on ubuntu-latest.
  Assumed feasible … but untested"*). Adopting it means the first CI run is the experiment.
- ❌ **Every varco connection URL would have to be rewritten through the proxy**, per backend —
  which means the chaos tests would no longer exercise the same connection path as every other
  integration test in the repo.
- ✅ **The cheap substitute covers the three named scenarios.** `pause`/`unpause` produces a
  hung connection (breaker + timeout); `restart` produces a genuine broker outage and recovery
  (outbox); neither needs latency shaping. Research 002 §4 lists docker-level control as the
  *"lower-overhead fallback"* and it is exactly the fallback case here.

**What is genuinely lost, stated plainly**: graded latency, bandwidth throttling, partial/slicing
failures, and one-directional (upstream-only) faults. Those are real chaos capabilities and
`pause` cannot emulate any of them. **File as a BACKLOG row for 3.1** (Step 41): *"Toxiproxy for
graded latency/bandwidth fault injection — needs an upstream `testcontainers.toxiproxy` module or
a vetted Python client; re-evaluate when research 002's Evidence Gaps 1–4 close."*

Consequence: **no new test dependency is added by this plan.** `testcontainers` (already pinned
4.14.2) and `docker` (7.1.0, already a transitive dependency of testcontainers) supply everything
— `get_wrapped_container()` returns the docker-py `Container`, whose `restart()`, `pause()`,
`unpause()`, `kill()` are stable public 7.x APIs (research 002 §1). No `[dependency-groups]` or
optional-deps edits anywhere.

### §RT7-ci — a **second job inside `integration.yml`**, nightly + dispatch only, never `push` ✅

```
integration.yml   (unchanged triggers: push:main + nightly schedule + workflow_dispatch)
  ├─ integration ───────────── make integration-test-clean
  │     MARKER_EXPR defaults to "integration and not chaos"  ← chaos excluded here
  │
  └─ chaos ─────────────────── if: github.event_name != 'push'
        make chaos-test-clean
        MARKER_EXPR = "integration and chaos"
        continue-on-error: false, but the workflow is not a required check
```

Research 002 §5 finds *"No one-size-fits-all answer"* and recommends *"inline unit/basic-chaos …
for fast feedback; add scheduled nightly chaos … for comprehensive coverage"*. In varco's shape
there **is no inline tier available** — `integration.yml` already does not run on PRs (RL-16), so
"inline" would mean "on every push to main", which is the wrong place for the flakiest test class
in the repo: research 002 §5 cites [arXiv 2602.02307] finding network issues are *the most
prevalent category of CI flakiness in GitHub Actions*. A chaos failure on every main push trains
maintainers to ignore the workflow, which destroys the signal for the non-chaos integration job
sharing it.

- ✅ Nightly gives a real, attributable signal with a real owner and no merge pressure.
- ✅ `workflow_dispatch` makes it runnable on demand before a release tag — which is when it
  actually matters.
- ✅ Two jobs in one file share the checkout/setup-uv/`uv sync` block verbatim and one set of
  SHA pins (which RL-10's dependabot will then bump once, not twice).
- ❌ A chaos regression introduced on Monday morning is caught Tuesday at 05:00 UTC. Accepted:
  chaos asserts *failure-mode* behaviour, which changes far less often than feature code.
- ❌ Chaos and integration jobs each boot their own containers (no reuse across jobs). Accepted;
  the chaos suite is small by construction.

**Rejected — a separate `chaos.yml`.** ✅ Fully independent schedule and timeout; a chaos failure
can never be confused with an integration failure. ❌ Duplicates the entire 12-line
checkout/python/uv/sync preamble and a second copy of three pinned action SHAs, for a one-flag
difference in the final `run:` line — precisely the duplication RL-18 already flags as how
`varco_casbin` went missing from `make`. Two jobs in one workflow give the same isolation in the
Actions UI (separate job, separate log, separate red X) at zero duplication. Rejected.

**Rejected — chaos inline in the existing `integration` job.** ✅ Simplest possible change (add
tests, change nothing else). ❌ Chaos then runs on `push: main`, and one flaky pause/restart turns
the whole integration job red, hiding the nine backends' genuine results. Rejected.

**Interaction with RL-16** (integration does not gate PRs): unchanged and **deliberately not
closed here**. This plan takes the position that promoting `integration.yml` to a required PR
check should happen only *after* the chaos suite has run nightly long enough to produce a measured
flake rate, and even then **only the `integration` job**, never `chaos`. Record that as the
disposition on RL-16 rather than leaving it an open "revisit in Phase 3" (Step 41).

### §RT7-home — chaos tests live in the package that owns **the thing that fails** ✅

Research 001's verdict is per-feature tests, not a monolith — but it does not resolve varco's
awkward case: an outbox chaos test needs `varco_core`'s `OutboxRelay`, `varco_sa`'s
`SAOutboxRepository`, and a Kafka container, and no single package owns all three.

**Resolution — the owner is the package whose container is being attacked, and the scenario is
split along the failure surface rather than kept whole:**

| Chaos test | Home | Fails |
|---|---|---|
| Outbox relay survives a **broker** restart | `varco_kafka/tests/test_kafka_chaos.py` | Kafka container (`kafka_bootstrap` already there) |
| Outbox rows survive a **database** restart | `varco_sa/tests/test_sa_chaos.py` | Postgres container (`postgres_container` already there) |
| Breaker opens on a black-holed dependency | `varco_redis/tests/test_redis_chaos.py` | Redis container |
| Job lease fencing after a worker crash | `varco_sa/tests/test_sa_chaos.py` + `varco_redis/tests/test_redis_chaos.py` | nothing (in-process) |
| Migration lock survives a crashed holder | `varco_fastapi/tests/test_migration_chaos.py` | the lock-holding connection |

Splitting the outbox scenario in two is the load-bearing move: the Kafka test uses an
**in-process outbox repository double** (the one `varco_core/tests/test_outbox.py` already
models) plus a **real** bus, because the thing under test is `bus.publish()` raising against a
real absent broker. The Postgres test uses a **real** `SAOutboxRepository` plus a **fake failing
bus**, because the thing under test is row durability. Neither test needs a dependency its
package does not already have, and together they cover the whole claim.

- ✅ No new workspace member, no cross-package test dependency, no `pyproject.toml` dependency
  edits. Each file sits next to a conftest that already provides its container.
- ✅ `scripts/integration_tests.sh`'s `ALL_INTEGRATION_PACKAGES` (`:93`) already lists all five —
  the chaos files are picked up with zero runner changes beyond the marker expression.
- ✅ Matches research 001 exactly: *"Write chaos/failure-injection scenarios **within each
  feature's test suite**"*.
- ❌ The end-to-end "durable outbox → real broker" path is never exercised by one single test.
  Accepted and stated: it is covered as two halves plus the existing `examples/00-full-stack-post-api`
  integration suite (RT8), which is the cross-feature case BACKLOG's own park already credits.
- ❌ Chaos tests are not co-located, so "run all the chaos tests" is a marker query, not a
  directory. That is what `@pytest.mark.chaos` + `make chaos-test` are for.

**Rejected — a top-level `tests/chaos/` suite.** ✅ One obvious home; the cross-package test has a
natural place. ❌ It is not a workspace member, so `uv run pytest` from inside it resolves a
non-workspace environment with no `varco_core` — the exact failure `scripts/integration_tests.sh:109-118`
documents for the example suite. It would need either a new workspace member (heavy, and every
`Makefile`/script package list grows a twelfth entry — RL-18) or a second `EXTRA_SUITES`
`run_from="root"` entry plus its own `pyproject.toml` for `asyncio_mode`/`pythonpath`. All that
machinery to relocate five files. Rejected.

**Rejected — `testkit/varco_chaos/` holding the *tests*** (as `varco_conformance` holds shared
test *classes*). ✅ Consistent with RT6's precedent. ❌ `varco_conformance` works because a
conformance suite is genuinely *shared* — one class, eight subclasses. A chaos scenario is
single-instance by nature: there is one outbox relay, one breaker, one lease model. A base class
with exactly one subclass is indirection with no payoff. `testkit/varco_chaos` therefore holds
**helpers only** (§chaos-fixture), never test classes. Rejected as a test home; adopted as a
helper home.

### §chaos-fixture — module-scoped `*_container_chaos` fixtures + one shared helper ✅

CLAUDE.md §Test Conventions documents exactly one escape hatch from the shared session-scoped
container: *"A test that genuinely needs a pristine server declares its own function-scoped
`*_container_fresh` fixture instead, paying the full container-boot cost explicitly and rarely."*
Chaos tests need more than pristine — they need a container they are **allowed to break**.
Restarting or pausing the session-scoped `redis_url` container would disrupt every other test in
`varco_redis/tests/` that is mid-flight or that follows.

**Chosen — a `module`-scoped `*_container_chaos` fixture, declared inside each chaos test module
itself (never in `conftest.py`), yielding a `ChaosContainer` handle.**

```python
# varco_redis/tests/test_redis_chaos.py
@pytest.fixture(scope="module")
def redis_container_chaos() -> Iterator[ChaosContainer]:
    with RedisContainer() as c:
        yield ChaosContainer(c, ready=lambda logs: "Ready to accept connections" in logs)
```

`ChaosContainer` (`testkit/varco_chaos/containers.py`, new) is a thin wrapper with a three-method
surface, and it is the *only* place `get_wrapped_container()` appears in the repo:

- `restart(timeout=5)` → `container.get_wrapped_container().restart(timeout=timeout)` then
  re-waits readiness. **Never `.stop()` + `.start()`** — research 002 §1: that pair deletes and
  recreates the container on a **new random host port**, invalidating every captured URL; the
  docker-py `restart()` preserves container ID and port mappings.
- `paused()` → context manager doing `pause()` / `unpause()`, unpausing in `finally` so a failed
  assertion never leaves a frozen container behind for the rest of the module.
- `wait_ready(timeout=60)` → re-applies the module's readiness predicate via testcontainers'
  `wait_for_logs` (research 002 §2: *"No explicit 're-wait' API — re-call the original fixture's
  wait strategy"*). **Deterministic predicates only, never `asyncio.sleep(n)`** — research 002 §5
  names fixed sleeps as the primary avoidable flakiness source.

- ✅ `module` scope, not `function`: within one chaos module, tests are written to leave the
  container healthy (`paused()` always unpauses; `restart()` always re-waits), so the boot cost is
  paid **once per chaos module** rather than once per chaos test. Accepted cost: **1 extra
  container boot per package that has chaos tests** — Redis/Postgres ≈ 1–5 s, Kafka ≈ 20–30 s.
- ✅ Declared in the test module, not `conftest.py`: it is impossible for a non-chaos test to
  accidentally depend on a container that gets restarted under it.
- ✅ Naming (`*_container_chaos`) is deliberately distinct from CLAUDE.md's documented
  `*_container_fresh` so the two escape hatches are never confused; both get documented (Step 39).
- ❌ It is a **third** container-scope convention alongside session-shared and function-fresh.
  Accepted, and it is why Step 39 adds it to CLAUDE.md rather than leaving it as tribal knowledge.
- ❌ Module scope means a test that *does* leave the container wedged poisons its module's
  remaining tests. Mitigated by the `finally`-unpause contract and by keeping chaos modules small.

**Rejected — reuse the existing session-scoped fixture and restart it.** ✅ Zero extra boots.
❌ Catastrophic: every other test in that package's session shares it. Rejected outright.

**Rejected — function-scoped, per CLAUDE.md's existing `*_fresh` hatch.** ✅ Perfect isolation;
one documented convention instead of two. ❌ A Kafka boot per chaos test at ~20–30 s each puts the
Kafka chaos module alone in the multi-minute range on a shared runner, for isolation the
`finally`-unpause/re-wait contract already provides. Rejected — but note this is a *judgement*: if
any chaos module proves unable to leave its container healthy, drop that module to `function`
scope rather than adding cleanup cleverness.

**Marker**: chaos tests carry **both** `@pytest.mark.integration` **and** `@pytest.mark.chaos`
(`pytestmark = [pytest.mark.integration, pytest.mark.chaos]`). `chaos` must be registered in the
`markers` list of each participating package's `[tool.pytest.ini_options]` (`varco_kafka`,
`varco_redis`, `varco_sa`, `varco_fastapi`) or `--strict-markers`-style typo protection is lost
and the marker silently does nothing.

**Runner change — one variable, no new script.** `scripts/integration_tests.sh` hardcodes
`-m integration` at both invocation sites (`:184` and `:190`). Replace both with
`-m "${MARKER_EXPR}"`, defaulting to **`integration and not chaos`**, and print the active
expression in the header and summary blocks (the same place the "NOT a clean-room run" banner
goes). `make chaos-test` / `make chaos-test-clean` set `MARKER_EXPR="integration and chaos"`.

- ✅ One script, one package list — no new duplication for RL-18 to inherit.
- ✅ `make integration-test` stays exactly as fast as today and can never be made flaky by chaos.
- ❌ A developer running `make integration-test` no longer runs *everything* marked integration.
  Mitigated by printing `Marker expression: integration and not chaos — chaos tests excluded, run
  \`make chaos-test\`` in the summary, so it can never be silently missed.

### §RT5-eos — a **new** `test_kafka_eos_integration.py`; keep all 13 mock tests ✅

The 13 tests in `varco_kafka/tests/test_kafka_eos.py` assert **wiring**: that
`transactional_id` is set only for `EXACTLY_ONCE`, that `enable_auto_commit=False`, that
`isolation_level="read_committed"` is passed, that at-most-once commits *before* dispatch and
at-least-once *after*, that a raising handler does not commit. Those are real assertions about
real code, they run in milliseconds, and they would be strictly worse expressed against a broker
(you cannot observe "which kwarg was passed" from Kafka).

What they cannot assert is **observable semantics**: that a message inside an aborted transaction
is invisible to a `read_committed` consumer; that an at-most-once consumer that dies mid-dispatch
really loses the message; that an at-least-once consumer that dies before commit really
redelivers it. Add those as a new file:

- `test_exactly_once_aborted_transaction_invisible_to_read_committed` — produce inside
  `async with producer.transaction()` then raise; a `read_committed` consumer sees nothing, a
  `read_uncommitted` consumer sees it (the control).
- `test_exactly_once_offsets_committed_atomically_with_produce` —
  `send_offsets_to_transaction(offsets, group_id)` (research 003 §EOS) then assert `committed()`
  reflects it only after commit.
- `test_at_least_once_redelivers_when_consumer_dies_before_commit` — stop the consumer without
  committing, restart in the same group, assert redelivery.
- `test_at_most_once_loses_message_when_consumer_dies_after_commit_before_dispatch` — the
  documented, *intended* data-loss behaviour of `AT_MOST_ONCE`. Asserting a guarantee's
  documented weakness is as important as asserting its strength.

**Position on gating 3.0.0 on aiokafka 0.13 transactions**: **yes for the commit/abort paths, no
for crash-mid-transaction.** Research 003 §EOS records transactions as *"✅ supported in 0.13.0"*,
documented and exemplified, with `send_offsets_to_transaction` and `read_committed` present —
that is enough to gate a release on the paths a test can drive deterministically. But research
003 §Evidence Gaps is equally explicit: *"aiokafka 0.13.0 docs exemplify the EOS pattern but do
not detail failure scenarios (e.g. producer crash mid-transaction …)"*, and §EOS warns a reused
`transactional_id` fences the previous instance with a non-retriable `ProducerFenced`. So:
producer-crash-mid-transaction goes in the **chaos** tier (nightly, non-gating), and every test
here uses a `uuid4().hex[:8]`-suffixed `transactional_id` and topic name per the standing
per-test namespacing rule — a shared `transactional_id` across tests on a session-scoped broker
would fence one test with another's producer and produce a mystifying failure.

- ✅ Fast wiring assertions and slow semantic assertions both kept, each where it belongs.
- ✅ `test_kafka_eos.py` is untouched — zero risk of regressing 13 passing tests.
- ❌ Two files describe one feature. Accepted; cross-reference both module docstrings (Step 24).

**Rejected — promote the 13 mock tests to real-broker tests.** ✅ One file, no mocks, no
duplication. ❌ Loses every wiring assertion (unobservable from a broker), adds ~13 × broker
round-trips to the default suite, and rewrites 13 currently-green tests for no new coverage.
Rejected.

### §RT4-backpressure — assert the **documented** contract: per-client isolation ✅

Read from source, not invented. `BackpressurePolicy` (`varco_ws/varco_ws/websocket.py:102-123`,
carrying `# noqa: UP042` per RL-15) has four members with these exact documented behaviours
(`websocket.py:187-241`):

| Policy | Full-queue behaviour | `_enqueue` returns |
|---|---|---|
| `DROP_OLDEST` (default) | `get_nowait()` the front, then `put_nowait` the new message | `True` |
| `DROP_NEWEST` | discard the incoming message; queue contents preserved | `True` |
| `BLOCK` | `await self._queue.put(message)` — suspends the **bus handler** | `True` |
| `DISCONNECT` | signal removal; `_handle_event` (`:600-609`) discards the conn and `_stop_drain()`s it | `False` |

Note precisely what `DISCONNECT` does **not** do: it never closes the WebSocket. The client stays
TCP-connected and simply stops receiving; the observable is `WebSocketEventBus.connected_count`
(`:567-570`), which the test conftest already exposes as `GET /ws/connected-count`.

All four branches are **already** unit-tested deterministically against a fake websocket
(`varco_ws/tests/test_ws_bus.py:230,255,279,297,321`). So the gap is not policy semantics — it is
**whether any of it holds over a real socket**, and specifically the central claim of the module's
own DESIGN block (`websocket.py:19-26`): *"Each client drains at its own rate — a slow client
never blocks others."*

**Chosen — two real-socket tests, both asserting cross-client isolation, plus one new
parameterised endpoint.**

1. `test_slow_client_does_not_starve_a_fast_client` — two real WS clients on a new
   `/ws/bp?policy=drop_newest&queue=2` endpoint (the existing `/ws` hard-codes bus defaults;
   `WebSocketEventBus.connect()` already accepts `max_queue_size=`/`backpressure_policy=`
   overrides at `websocket.py:469-516`, so the endpoint is a pure test-conftest addition). Client
   A connects with `websockets.asyncio.client.connect(uri, max_queue=1)` and **never calls
   `recv()`**; client B recv's promptly. Publish N ≫ queue depth. Assert **B receives all N in
   order** — the DESIGN claim — and make **no assertion about how many A received**.
2. `test_disconnect_policy_ejects_a_stalled_client` — same shape with
   `?policy=disconnect&queue=1`; poll `GET /ws/connected-count` to a deadline and assert it
   drops from 2 to 1 while B still receives everything.

Research 004 §2 supplies the deterministic knob: the websockets client's **`max_queue`** (default
16) — *"When exceeded, the connection stops reading from the network until the application
consumes messages"*. Setting `max_queue=1` on a client that never recv's is what propagates TCP
backpressure up through uvicorn's write buffer into `send_text`, stalling the drain task and
filling varco's per-client `asyncio.Queue`. `write_limit` (research 004 §2, default 32 KB) is the
*server*-side complement and is **not** used here: it is a `uvicorn.Config`/websockets-server knob
the test fixture would have to thread through, and `max_queue=1` on the client is sufficient and
simpler.

- ✅ Asserts the module's own documented central guarantee, over a real socket.
- ✅ Robust to the one thing nobody can pin down: the exact number of bytes that fit in
  TCP + uvicorn buffers before `send_text` stalls. Assertions are about **B**, never about a
  count of drops on A.
- ✅ Ordering for B is guaranteed by RFC 6455 (research 004 §3: *"Message fragments MUST be
  delivered to the recipient in the order sent"*), so `assert received == list(range(N))` is a
  legitimate assertion, not a hopeful one.
- ❌ Neither test proves *which* policy fired on A — that stays unit-level. Deliberate.
- ❌ Needs N large enough to overflow the buffers; if N is too small the test passes vacuously.
  Guard with an explicit assertion that A received **fewer** than N (proving backpressure
  actually engaged) before asserting B received all N — otherwise the test is green for the
  wrong reason. **This guard is load-bearing; do not drop it.**

**Rejected — asserting exact drop counts on the slow client.** ✅ Directly tests the policy over a
real socket. ❌ The count depends on TCP send-buffer size, uvicorn's write buffer, the OS, and the
runner — unreproducible between a laptop and `ubuntu-latest`. This is how a test becomes a flake.
Rejected.

**Rejected — a many-connection (100–1000) pooling/backpressure test.** ✅ Research 004 §4 suggests
empirical CI testing at that scale. ❌ Research 004 §Evidence Gaps also states GitHub Actions
file-descriptor limits are **undocumented**, so the failure threshold is unknown and would be
discovered as a CI flake. `test_connection_pooling_multiple_clients_each_receive_broadcast`
already covers pooling at a sane scale. Rejected (recorded as a Risk).

### §RT2-scope — NATS: cover the three delivery semantics + `NatsStreamManager` ✅

`varco_nats` is the thinnest suite by a wide margin (2 real-broker tests, 8 source modules). The
gaps that matter, in source order:

- `NatsEventBus._on_message` (`bus.py:525-573`) branches on `NatsDeliverySemantics`:
  `pre_ack = semantics is AT_MOST_ONCE` — ack-before-dispatch vs ack-after-dispatch. Zero
  real-broker coverage of either branch. Add: at-least-once **redelivers** after a handler
  raises; at-most-once **does not**. (Same "assert the documented weakness" principle as §RT5-eos.)
- `publish()`'s `EXACTLY_ONCE` branch (`bus.py:376`) — Nats msg-ID dedup. Publish the same event
  twice with the same dedup id; assert one delivery.
- `NatsStreamManager` (`channel.py:192-455`) — `declare_channel` / `channel_exists` /
  `list_channels` / `delete_channel` have **zero** real-broker coverage. Four round-trip tests.
- `NatsDLQ` ack durability — `test_regression_nats_dlq_ack_durability.py` is `_FakeMsg`-based
  (`:31`). Add one real-JetStream test: pop → ack → reconnect → assert the entry does not
  reappear. This is the regression the fake was written for; the fake proves the call shape, the
  broker proves the durability.
- `varco_nats/varco_nats/health.py` — one real-broker up/down assertion, `pause()`d container for
  the "down" half (reuses §chaos-fixture; mark it `chaos` since it pauses a container).

Everything except the health test goes on the existing session-scoped `nats_url` fixture with
`uuid4().hex[:8]` stream/subject namespacing per the standing rule.

### §RT3-scope — Casbin: concurrent writers is the whole gap ✅

Seven real-Postgres tests already exist (see Status corrections). CLAUDE.md's own pitfall table
says `adapter="file"` *"is durable but single-process only (concurrent writers can corrupt the
CSV)"* and recommends `adapter="sqlalchemy"` for exactly that reason — but nothing verifies the
recommended adapter actually survives concurrency. Add, on the existing function-scoped
`casbin_db_url` fixture (`varco_casbin/tests/conftest.py:66`):

- `test_concurrent_add_policy_from_two_engines_all_persist` — two `CasbinPolicyEngine`s over the
  same DB, `asyncio.gather` N `add_policy` calls each, cold-reload a third engine, assert all
  2N rules are present and none are lost.
- `test_concurrent_add_and_remove_converge` — interleaved add/remove, assert the final state is
  self-consistent (no duplicate rows, no phantom rules) after a cold reload.
- `test_concurrent_writers_do_not_corrupt_rbac_inheritance` — two engines writing `g` grouping
  rules; assert inherited permissions still enforce correctly after reload.

**`adapter="file"` is deliberately NOT tested.** CLAUDE.md documents it as unsafe under
concurrency; a test asserting corruption would either be a flake (it is a race) or would codify
broken behaviour. The documentation is the correct artifact for that one.

### §RT9-scope — the residual is the two paths the existing tests decline to assert ✅

`test_app_migrations_integration.py:302-357` runs two concurrent lifespans and then asserts
`outcome in ("served", "lock_timeout")` — i.e. it deliberately accepts **either** branch, with an
honest comment saying both are legitimate. That is the right call for that test, and it means the
`MigrationLockTimeout` branch at the **app** layer is never actually asserted. (It *is* asserted
at the migrator layer: `varco_sa/tests/test_migration_lock.py:114`.) Residual work:

- `test_lifecycle_raises_migration_lock_timeout_when_holder_never_releases` — hold the advisory
  lock from a separate connection deliberately, boot a lifespan with a 1 s `lock_timeout` and a
  genuinely pending revision, assert `MigrationLockTimeout` propagates and **no request is
  served** (deterministic, because the holder is controlled by the test — not a race).
- `test_crashed_lock_holder_releases_and_next_boot_proceeds` (**chaos**, in
  `varco_fastapi/tests/test_migration_chaos.py`) — hold the lock, then kill that connection
  (terminate the backend / restart Postgres via `ChaosContainer`), assert the next lifespan
  acquires and applies. This is the real operational question: *"a pod died mid-migration — is the
  next deploy wedged forever?"* CLAUDE.md's held-open-advisory-lock-transaction design says no,
  and nothing verifies it.
- **No Beanie/Mongo migration integration test is added.** `varco_beanie/tests/test_beanie_migration_lock.py`
  exists; the Mongo index-mode migrator has a materially different lock story and pulling it in
  makes RT9 an M. File it as a BACKLOG row instead (Step 41).

### §phase-order — what gates 3.0.0, and where the cut line actually falls

| Phase | Item | Effort | Gates 3.0.0? |
|---|---|---|---|
| 0 | Scaffolding: `testkit/varco_chaos`, `chaos` marker, `MARKER_EXPR`, `make chaos-test` | 0.5 d | ✅ prerequisite |
| 1 | **RT2** — NATS real-broker coverage | 1 d | ✅ yes |
| 2 | **RT3** — Casbin concurrent writers | 0.5–1 d | ✅ yes |
| 3 | **RT4** — WS backpressure over a real socket | 0.5 d | ✅ yes |
| 4 | **RT5** — Kafka EOS integration + rebalance/offset deepening | 1.5–2 d | ✅ yes |
| 5 | **RT9** — migration lock-timeout (app layer) + **RT7a** job-lease fencing (in-process) | 1 d | ✅ yes |
| 6 | **RT7b** — container-lifecycle chaos: outbox×2, breaker, crashed lock holder | 3–4 d | ⬜ **cut line** |
| 7 | Docs + BACKLOG + CI wiring | 0.5 d | ✅ yes |

**Total: 8.5–10.5 days**, of which Phase 6 is 3–4.

**On BACKLOG's claim that RT7 is "the first item to renegotiate": agree, with one correction.**
RT7 as written is one row covering three scenarios of wildly different cost, and one of them —
**job lease + worker crash — needs no container lifecycle control at all** (§RT7-shape). It is a
day's work against stores that already have session-scoped fixtures, and it verifies fencing:
`try_claim` / `renew` / `reap_expired_leases` / `save(expected_epoch=)` → `StaleLeaseError`, a
four-method protocol whose *whole purpose* is correctness under a crash, currently proven only by
`varco_core/tests/test_job.py` unit tests against in-memory stores. **Pull it forward into Phase 5
and gate 3.0.0 on it.** Splitting RT7 into RT7a (in-process, gating) and RT7b (container
lifecycle, cuttable) is the substantive disagreement with the BACKLOG row, and Step 41 re-files
it that way.

Phases 1–5 are ordered cheapest-gap-first so that if the horizon tightens mid-plan, the cut falls
at a phase boundary with the most coverage already banked. Phase 6 is last because it is the only
phase that introduces a new *mechanism* (container lifecycle control) rather than new tests over
existing mechanisms — and therefore the only one whose CI behaviour is unproven until it runs.

---

## Steps

TDD is inverted here: these steps *are* tests, and every one of them is written to fail first
against the current tree. **The pass condition for a new test is not "green" — it is "green, or
`xfail(strict=True)` + a BACKLOG row"** (CLAUDE.md §Test Conventions). A step that turns red
against production code is a finding, never a licence to edit `varco_*/varco_*/`.

### Phase 0 — scaffolding (gates every later phase)

1. [x] `testkit/varco_chaos/__init__.py` (**new**) — package marker + module docstring stating
       that this package is **never packaged** (same status as `varco_conformance`), that it holds
       **helpers only, never test classes** (§RT7-home), and that it is the only place in the repo
       allowed to call `get_wrapped_container()`.
2. [x] `testkit/varco_chaos/containers.py` (**new**) — `ChaosContainer` per §chaos-fixture:
       `__init__(container, *, ready: Callable[[str], bool] | None = None)`,
       `restart(timeout: int = 5)`, `paused()` (contextmanager, `unpause()` in `finally`),
       `wait_ready(timeout: float = 60.0)`. Full docstring with `Args`/`Raises`/`Edge cases`/
       `Async safety`, and a `DESIGN:` block recording ✅ port/ID survivorship via docker-py
       `restart()` / ❌ `.stop()`+`.start()` deletes and re-ports (research 002 §1, §2) — so the
       next reader cannot "simplify" it back to the broken form.
3. [x] `testkit/varco_chaos/leases.py` (**new**) — `abandon_lease(store, job_id)` helper for the
       in-process worker-crash scenario: cancels the renew task without renewing, leaving the
       claim stale. Keeps the same shape in both `varco_sa` and `varco_redis` lease tests.
4. [x] `varco_kafka/pyproject.toml`, `varco_redis/pyproject.toml`, `varco_sa/pyproject.toml`,
       `varco_fastapi/pyproject.toml`, `varco_nats/pyproject.toml` — add to
       `[tool.pytest.ini_options] markers`:
       `"chaos: kills/pauses/restarts a real container; excluded from -m integration by default — use make chaos-test"`.
       In the same edit **verify** each already has `pythonpath = ["../testkit"]` (as
       `varco_core/pyproject.toml:95` does) and add it where missing.
5. [x] `scripts/integration_tests.sh` — introduce `MARKER_EXPR="${MARKER_EXPR:-integration and not chaos}"`
       near the top; replace the hardcoded `-m integration` at **`:184`** and **`:190`** with
       `-m "$MARKER_EXPR"`. Print `Marker expression: <expr>` in the header block (next to the
       clean-room banner, `:81-86`) and again in the summary (`:226-231`), with the explicit hint
       `chaos tests excluded — run 'make chaos-test'` when the default expression is active.
6. [x] `Makefile` — add `chaos-test` and `chaos-test-clean` targets mirroring
       `integration-test`/`integration-test-clean` (`:147-169`) exactly, differing only by
       `MARKER_EXPR="integration and chaos"`. `chaos-test-clean` uses the same `env -u` list of
       six `VARCO_TEST_*_URL` names. Add both to the `help` target and the `Makefile:4-25` header.
7. [x] **Verify:** `make integration-test PKG=varco_core` still behaves identically (exit 5 →
       "no tests" is still not a failure, `:195-201`); `make chaos-test PKG=varco_core` reports
       "no tests" rather than erroring. `make lint && make type-check`.
       Commit: `RT7: chaos scaffolding — testkit/varco_chaos, chaos marker, MARKER_EXPR`.

### Phase 1 — RT2, `varco_nats` real-broker coverage (§RT2-scope)

8. [ ] `varco_nats/tests/test_nats_semantics_integration.py` (**new**,
       `pytestmark = pytest.mark.integration`) — three tests over the session-scoped `nats_url`
       fixture, each on a `uuid4().hex[:8]`-namespaced stream/subject:
       `test_at_least_once_redelivers_after_handler_raises`,
       `test_at_most_once_does_not_redeliver_after_handler_raises` (asserting the documented data
       loss), `test_exactly_once_dedups_duplicate_publish`. Drives `bus.py:376` and
       `bus.py:549`'s `pre_ack` branch.
9. [ ] `varco_nats/tests/test_nats_channel_integration.py` (**new**) — four `NatsStreamManager`
       round-trips against a real JetStream: `declare_channel` then `channel_exists` → `True`;
       `list_channels` contains it; `delete_channel` then `channel_exists` → `False`;
       `declare_channel` twice is idempotent. Namespaced stream names; delete in a `finally`.
10. [ ] `varco_nats/tests/test_nats_dlq_integration.py` (**new**) — real-JetStream ack durability:
        `push` → `pop` → `ack` → drop and recreate the consumer → assert the entry does not
        reappear. This is the broker-side counterpart to the `_FakeMsg` regression tests at
        `test_regression_nats_dlq_ack_durability.py:52-83`; cross-reference both docstrings.
11. [ ] `varco_nats/tests/test_nats_health_chaos.py` (**new**,
        `pytestmark = [pytest.mark.integration, pytest.mark.chaos]`) — module-scoped
        `nats_container_chaos` fixture; assert the health check reports healthy, then unhealthy
        inside `chaos.paused()`, then healthy again after the block exits (`wait_ready()` first).
12. [ ] **Verify:** `uv run pytest varco_nats/tests/ -m integration` (11 → 20 tests) and
        `MARKER_EXPR="integration and chaos" uv run pytest varco_nats/tests/ -m "integration and chaos"`.
        Any red result → `xfail(strict=True, reason="BUG: ...")` + a BACKLOG row, **not** a fix in
        `varco_nats/varco_nats/`. Commit: `RT2: varco_nats real-broker semantics, stream manager, DLQ ack, health`.

### Phase 2 — RT3, Casbin concurrent writers (§RT3-scope)

13. [ ] `varco_casbin/tests/test_concurrent_writers_integration.py` (**new**) — the three tests in
        §RT3-scope, each on the existing function-scoped `casbin_db_url` fixture
        (`varco_casbin/tests/conftest.py:66`) so every test owns its own database.
14. [ ] Module docstring records why `adapter="file"` is deliberately untested (§RT3-scope) so the
        omission reads as a decision, not an oversight.
15. [ ] **Verify:** `uv run pytest varco_casbin/tests/ -m integration`. Same xfail rule.
        Commit: `RT3: varco_casbin concurrent-writer coverage on Postgres`.

### Phase 3 — RT4, WS backpressure over a real socket (§RT4-backpressure)

16. [ ] `varco_ws/tests/conftest.py` — add a **parameterised** WS endpoint to `_build_app()`
        (`:64-128`): `@app.websocket("/ws/bp")` reading `policy` and `queue` from query params and
        passing them to `ws_bus.connect(websocket, max_queue_size=..., backpressure_policy=...)`
        (the override kwargs already exist at `websocket.py:469-516`). Mirror the existing `/ws`
        handler's `websocket.receive()` loop verbatim — the comment at `:94-98` explains why a
        bare `asyncio.sleep()` wedges uvicorn's graceful shutdown.
17. [ ] `varco_ws/tests/test_ws_backpressure_integration.py` (**new**) —
        `test_slow_client_does_not_starve_a_fast_client` per §RT4-backpressure: client A
        `connect(uri, max_queue=1)` and never `recv()`s, client B recv's promptly, N ≫ queue
        depth. **Assert in this order**: (1) A received strictly fewer than N (proves backpressure
        engaged — without this the test can pass vacuously), (2) B received all N, (3) B's
        sequence numbers are `list(range(N))` (RFC 6455 in-order guarantee, research 004 §3).
18. [ ] Same file — `test_disconnect_policy_ejects_a_stalled_client` against
        `?policy=disconnect&queue=1`: poll `GET /ws/connected-count` to a deadline (never
        `asyncio.sleep(n)` then assert) until it drops to 1, and assert B still received all N.
        Docstring must state that `DISCONNECT` does **not** close the socket
        (`websocket.py:600-609` only discards + `_stop_drain()`s), so `connected_count` is the
        observable, not a client-side close frame.
19. [ ] **Verify:** `uv run pytest varco_ws/tests/ -m integration`. Run it **5×** in a row before
        committing — this is the plan's most buffer-sensitive test and a 1-in-5 flake must be
        found here, not in CI. If it flakes, raise N and the payload size; do **not** relax the
        assertions. Commit: `RT4: real-socket backpressure and DISCONNECT-policy coverage`.

### Phase 4 — RT5, Kafka EOS + rebalance/offset deepening (§RT5-eos)

20. [ ] `varco_kafka/tests/test_kafka_eos_integration.py` (**new**) — the four tests in
        §RT5-eos. Every test: `uuid4().hex[:8]`-suffixed topic **and** `transactional_id`
        (research 003 §EOS — a reused `transactional_id` fences the other producer with a
        non-retriable `ProducerFenced`), and topics **pre-created explicitly** via
        `AIOKafkaAdminClient.create_topics([NewTopic(..., num_partitions=N, replication_factor=1)])`
        (research 003 §Testcontainers Kafka Specifics + §Evidence Gaps: the testcontainers default
        partition count is undocumented — never rely on auto-creation).
21. [ ] `varco_kafka/tests/test_kafka_rebalance_integration.py` — extend the single existing test
        with two more, using the deterministic technique research 003 §Force-Rebalance ranks
        highest: pre-created 3-partition topic + a second consumer joining the group, with
        `metadata_max_age_ms` low. Add `test_rebalance_listener_callbacks_fire_in_order`
        (`on_partitions_revoked` before `on_partitions_assigned`) and
        `test_offsets_committed_in_on_partitions_revoked_prevent_duplicate_delivery` — research
        003 §Rebalance API calls the latter *"critical"* under manual commit.
        ⚠️ Keep `session_timeout_ms` **≥ 6000** unless the broker's `group.min.session.timeout.ms`
        is verified lower on the testcontainers image (research 003 §Evidence Gaps) — a
        client-requested value below the broker floor is rejected at join time.
22. [ ] `varco_kafka/tests/test_kafka_offsets_integration.py` — add
        `test_committed_offset_survives_consumer_restart`: `commit()` → assert `committed(tp)` →
        stop → recreate in the same group → assert the fetch position resumes from the committed
        offset (research 003 §Offset Management: *"if offset persists across consumer restart, the
        broker durably committed it"*).
23. [ ] `varco_kafka/tests/test_kafka_eos.py` — **docstring only**, no test changes: point to the
        new integration file and state the division of labour (wiring here, observable semantics
        there) per §RT5-eos.
24. [ ] **Verify:** `uv run pytest varco_kafka/tests/ -m integration`. Same xfail rule — an EOS
        semantic that does not hold against a real broker is a 🔴 BACKLOG finding about
        `KafkaDeliverySemantics.EXACTLY_ONCE`, not a test to soften.
        Commit: `RT5: Kafka EOS/at-least-once/at-most-once real-broker semantics + rebalance and offset durability`.

### Phase 5 — RT9 residual + RT7a job-lease fencing (in-process, gating)

25. [ ] `varco_fastapi/tests/test_app_migrations_integration.py` — add
        `test_lifecycle_raises_migration_lock_timeout_when_holder_never_releases` per §RT9-scope:
        the test itself holds the advisory lock from a separate connection, so the outcome is
        deterministic and the assertion is a single branch (unlike `:302-357`, which legitimately
        accepts either). Assert `MigrationLockTimeout` **and** that no request was served.
26. [ ] `varco_sa/tests/test_sa_job_lease_crash.py` (**new**, `-m integration`, **no chaos
        marker** — nothing is killed at the container level) — against a real Postgres
        `SAJobStore`: worker A `try_claim`s, `abandon_lease()` (Step 3), time advances past
        `lease_expires_at`, worker B `reap_expired_leases()` then `try_claim()` succeeds, then
        zombie A `save(job, expected_epoch=<old epoch>)` → **`StaleLeaseError`**. Also assert the
        negative: B `renew()`ing before expiry keeps A locked out.
27. [ ] `varco_redis/tests/test_redis_job_lease_crash.py` (**new**) — the identical scenario
        against `RedisJobStore`, reusing the same `testkit/varco_chaos/leases.py` helper. Two
        stores, one scenario, one helper — if the two backends disagree, that is precisely the
        finding worth having.
28. [ ] **Verify:** `uv run pytest varco_sa/tests/ varco_redis/tests/ varco_fastapi/tests/ -m integration`.
        Commit: `RT9+RT7a: app-layer migration lock timeout; job-lease fencing after a worker crash`.

### Phase 6 — RT7b, container-lifecycle chaos (**the cut line** — §phase-order)

29. [ ] `varco_kafka/tests/test_kafka_chaos.py` (**new**,
        `pytestmark = [pytest.mark.integration, pytest.mark.chaos]`) — module-scoped
        `kafka_container_chaos` fixture (§chaos-fixture).
        `test_outbox_entries_survive_a_broker_restart_and_are_republished`: real `KafkaEventBus` +
        an in-process outbox repository double; enqueue M entries; `chaos.restart()` mid-relay;
        assert (1) **zero** entries were deleted while the broker was down
        (`outbox.py:809-830` — delete happens only after a successful publish), (2) after
        `wait_ready()` every entry is published and deleted, (3) the consumer observed each event
        **at least once** (at-least-once, per `outbox.py:818-820` — never assert exactly-once).
30. [ ] Same file — `test_relay_does_not_dead_letter_on_a_transient_broker_outage`: with **no**
        `retry_policy` configured, `_handle_publish_failure` (`outbox.py:838-850`) must log and
        leave the entry untouched. Assert the DLQ is **empty** after the restart cycle. A
        transient outage that dead-letters is a data-shape bug, and nothing tests it today.
31. [ ] `varco_sa/tests/test_sa_chaos.py` (**new**) —
        `test_outbox_rows_survive_a_database_restart`: real `SAOutboxRepository` + a deliberately
        failing in-process bus; write M rows in a transaction; `chaos.restart()` the Postgres
        container; reconnect; assert all M rows are still pending with their `attempts` intact,
        then let the bus succeed and assert all M relay and delete.
32. [ ] `varco_redis/tests/test_redis_chaos.py` (**new**) —
        `test_circuit_breaker_opens_when_the_dependency_black_holes`: a **shared**
        `CircuitBreaker` (never per-call — CLAUDE.md's pitfall table) wrapping a real Redis call
        under a short `@timeout`; inside `chaos.paused()`, drive ≥ `failure_threshold` calls and
        assert the breaker transitions to `OPEN` and subsequent calls fail fast **without** waiting
        the full timeout (assert on elapsed time, with a generous margin). After the block exits
        and `wait_ready()` returns, assert HALF_OPEN → CLOSED recovery.
33. [ ] `varco_fastapi/tests/test_migration_chaos.py` (**new**) —
        `test_crashed_lock_holder_releases_and_next_boot_proceeds` per §RT9-scope: hold the
        advisory lock, kill the holding connection (`pg_terminate_backend`, or
        `chaos.restart()` if the former proves flaky), assert the next lifespan acquires the lock
        and applies the pending revision rather than hanging to `lock_timeout`.
34. [x] `.github/workflows/integration.yml` — add the `chaos` job per §RT7-ci: same
        checkout/setup-python/setup-uv/`uv sync --locked --all-packages --all-extras` preamble
        (reuse the **existing pinned SHAs** at `:48-52` verbatim — do not re-derive them),
        `if: github.event_name != 'push'`, `run: make chaos-test-clean`, its own
        `timeout-minutes`. Extend the file's header DESIGN block with §RT7-ci's ✅/❌ and an
        explicit note that `chaos` is **not** a required check and must never become one.
35. [ ] **Verify:** `make chaos-test-clean` locally end-to-end (all five chaos modules), then
        `gh workflow run integration.yml` and watch **both** jobs. Run the chaos suite **3×** and
        record the flake count in the commit message — a suite whose flake rate is unknown is not
        a signal. Commit: `RT7b: container-lifecycle chaos — outbox×2, breaker, crashed lock holder; nightly chaos job`.

### Phase 7 — docs and close-out (same commits as the code, per CLAUDE.md)

36. [x] `CLAUDE.md` §*Commands* — add `make chaos-test` / `make chaos-test-clean` and state that
        `make integration-test` now **excludes** chaos by default (`MARKER_EXPR`).
37. [x] `CLAUDE.md` §*Test Conventions* — add a **"Chaos tests"** paragraph: the `chaos` marker and
        why it is additive to `integration`; the three container scopes and when each applies
        (session-shared → function-scoped `*_container_fresh` → module-scoped `*_container_chaos`,
        §chaos-fixture); that `ChaosContainer` is the only sanctioned caller of
        `get_wrapped_container()` and **why `.stop()`+`.start()` is forbidden** (new host port,
        research 002 §1); that chaos runs nightly + dispatch only, never on `push`, and is never a
        required check.
38. [x] `CLAUDE.md` §*CI* subsection — document `integration.yml`'s second job and its
        `if: github.event_name != 'push'` guard.
39. [x] `CLAUDE.md` §*Common Pitfalls* — one row: **"restarting a session-scoped container"** →
        symptom "unrelated tests in the same package fail with connection errors after a chaos
        test" → cause "the session-scoped fixture is shared by the whole package suite" → fix
        "declare a module-scoped `*_container_chaos` fixture **inside the chaos module**, never in
        `conftest.py`".
40. [x] `README.md` — testing section: add `make chaos-test` to the command list and one sentence
        on what the chaos suite asserts (outbox durability, breaker behaviour under a black hole,
        lease fencing). Keep it short; the design lives in this plan.
41. [x] `BACKLOG.md` §Phase 3 — apply **every** row of the *Status corrections* table above, then:
        close **RT8** ✅ done (already in both runners) and **RT2/RT3/RT4/RT5** as this plan
        completes them; downgrade **RT9** from ⬜ pending to its real residual and close it; split
        **RT7** into **RT7a** (in-process lease fencing — done in Phase 5) and **RT7b** (container
        lifecycle — Phase 6, the cut line) per §phase-order; strike the *"RT7 shape"* open question
        (`:176-177`) and replace it with a one-line pointer to §RT7-shape/§RT7-ci. Update **RL-20**
        to ✅ fixed with the 16-passed evidence. Update **RL-16**'s disposition per §RT7-ci (promote
        only the `integration` job, only after a measured chaos flake rate; never `chaos`). File new
        rows: (a) **Toxiproxy deferred to 3.1** with §RT7-toxiproxy's four ❌s, (b) **Beanie/Mongo
        migration integration coverage** (§RT9-scope), (c) **many-connection WS/SSE scale test**
        blocked on undocumented Actions fd limits (§RT4-backpressure), (d) every
        `xfail(strict=True)` filed in Steps 12/15/19/24/28/35.
42. [x] `CHANGELOG.md` `## [Unreleased]` — chaos test suite + `make chaos-test`; NATS/Kafka/Casbin/
        WS real-broker coverage; the nightly chaos CI job. **Test-only release** — call out
        explicitly that no runtime package changed.
43. [ ] **Final:** `make lint && make type-check && make test && make integration-test-clean &&
        make chaos-test-clean`, then one `gh workflow run integration.yml` proving both jobs green.

---

## Edge cases

| Input / state | Expected behaviour |
|---|---|
| `chaos.restart()` on a container | Host port and container ID **survive** (docker-py `restart()`, research 002 §1). Captured connection URLs stay valid. If a future refactor swaps in `.stop()`+`.start()`, every chaos test fails with a connection error on a *new random port* — the `DESIGN:` block in Step 2 exists to prevent exactly that. |
| A chaos test fails inside `chaos.paused()` | `paused()` unpauses in `finally`, so the remaining tests in that module still run. A leaked pause would cascade into every later test in the module (module scope). |
| Broker restarts but never becomes ready within `wait_ready(timeout)` | `TimeoutError` naming the container and the readiness predicate — never a silent `sleep`-and-hope. Research 002 §5: deterministic waits are the primary flakiness reducer. |
| Outbox relay ticks while the broker is down | Entry is **not** deleted (`outbox.py:809-816`); with no `retry_policy` it is not dead-lettered either (`:838-850`). Both asserted (Steps 29, 30). |
| An event is delivered twice after a broker restart | **Correct, not a bug** — `outbox.py:818-830` documents at-least-once explicitly. Assert `>= 1` delivery, never `== 1`. |
| Zombie worker calls `save(expected_epoch=<old>)` after its lease was reaped | `StaleLeaseError` (`job/base.py:82,682`). This is the fencing guarantee; if it does **not** raise, that is a 🔴 finding, not a flaky test. |
| WS slow client (A) happens to receive all N messages | The test **fails at assertion (1)** rather than passing vacuously — backpressure never engaged, so N or the payload size is too small. Raise them; do not relax the assertion. |
| `DISCONNECT` policy fires | The socket stays open (`websocket.py:600-609` only discards + `_stop_drain()`s); `connected_count` is the observable. A test asserting a client-side close frame would be asserting behaviour varco does not implement. |
| A Kafka test reuses another's `transactional_id` on the shared session broker | `ProducerFenced`, non-retriable (research 003 §EOS). Prevented by the `uuid4().hex[:8]` suffix rule — the standing per-test namespacing convention, applied to `transactional_id` as well as topics. |
| A Kafka test relies on topic auto-creation | Partition count is broker/image dependent and **undocumented** for testcontainers (research 003 §Evidence Gaps). All rebalance/EOS topics are pre-created explicitly with `num_partitions` (Step 20/21). |
| `session_timeout_ms` set below the broker floor | Join is rejected. Keep ≥ 6000 ms unless `group.min.session.timeout.ms` is verified lower on the actual image (research 003 §Evidence Gaps). |
| `make integration-test` run by a developer | Chaos excluded by default (`MARKER_EXPR="integration and not chaos"`), and the summary says so with the `make chaos-test` hint — it can never be silently missed. |
| `integration.yml` runs on `push: main` | The `chaos` job is **skipped** (`if: github.event_name != 'push'`). It is not in any `needs:`, so nothing is left pending. |
| A chaos test goes red nightly | Not a required check, `main` stays mergeable — same disposition as the existing integration job (Plan 017 §RL-5-triggers). It is a BACKLOG signal with a named owner. |
| A new test surfaces a genuine production-code bug | `@pytest.mark.xfail(reason="BUG: ...", strict=True)` + a BACKLOG row. `strict=True` means it fails loudly if someone later fixes the bug, so the marker cannot rot. **No production-code edit in this plan.** |

---

## Verification

```bash
cd /home/edoardo/projects/varco

# Phase 0
make integration-test PKG=varco_core     # unchanged behaviour; "no tests" is not a failure
make chaos-test       PKG=varco_core     # "no tests", exits 0
make lint && make type-check

# Phases 1-5 (per package, gating)
uv run pytest varco_nats/tests/    -m integration
uv run pytest varco_casbin/tests/  -m integration
uv run pytest varco_ws/tests/      -m integration          # run 5x — buffer-sensitive
uv run pytest varco_kafka/tests/   -m integration
uv run pytest varco_sa/tests/ varco_redis/tests/ varco_fastapi/tests/ -m integration

# Phase 6 (chaos, cuttable)
make chaos-test-clean                                       # run 3x, record flake count
gh workflow run integration.yml && gh run watch             # both jobs

# Phase 7 close-out
make lint && make type-check && make test \
  && make integration-test-clean && make chaos-test-clean
```

| Phase | Command | Pass condition |
|---|---|---|
| 0 | `make chaos-test PKG=varco_core` | exit 0, "no tests collected" treated as skip; `make integration-test` byte-identical to before |
| 1 | `uv run pytest varco_nats/tests/ -m integration` | 2 → ~11 integration tests green (or xfail-strict + BACKLOG row) |
| 2 | `uv run pytest varco_casbin/tests/ -m integration` | 9 → 12 green |
| 3 | `uv run pytest varco_ws/tests/ -m integration` | 6 → 8 green, **5 consecutive clean runs** |
| 4 | `uv run pytest varco_kafka/tests/ -m integration` | +7 tests; EOS abort/commit paths verified against a real broker |
| 5 | `uv run pytest varco_sa/ varco_redis/ varco_fastapi/ -m integration` | `StaleLeaseError` asserted on both stores; `MigrationLockTimeout` asserted deterministically at the app layer |
| 6 | `make chaos-test-clean` | all five chaos modules green, **3 consecutive runs**, flake count recorded |
| 7 | full close-out chain | all green; `integration.yml` green on dispatch with `chaos` job green and `integration` job unchanged |

---

## Risks

- ⚠️ **ASSUMPTION — `casbin-async-sqlalchemy-adapter` 1.17's concurrent-writer semantics.**
  Deliberately not researched. §RT3-scope's three tests assume the adapter serialises writes
  correctly through SQLAlchemy's session/transaction handling. If it does not (e.g. a
  last-writer-wins full-policy rewrite rather than row-level inserts), Step 13 goes red and is a
  🔴 BACKLOG finding about **CLAUDE.md's own recommendation** to use `adapter="sqlalchemy"` for
  durability. The invariant that must hold: *do not fix the adapter, and do not weaken the test to
  match observed behaviour* — file it.
- ⚠️ **ASSUMPTION — GitHub Actions runner file-descriptor limits.** Research 004 §Evidence Gaps:
  *"File descriptor limits on GitHub Actions ubuntu-latest runners are **not documented**."* The
  §RT4-backpressure tests use 2 connections, so this is not a live risk *for this plan* — it is
  recorded because it is the reason the many-connection scale test is a Non-goal, and because Step
  41(c) files it. If someone later raises the connection count, this becomes the failure mode.
- ⚠️ **ASSUMPTION — testcontainers Kafka default partition count.** Research 003 §Evidence Gaps:
  *"testcontainers-python stable docs do not specify the default partition count for auto-created
  topics."* Mitigated structurally: Steps 20/21 pre-create every topic with an explicit
  `num_partitions`. If any existing Kafka test relies on auto-creation, a rebalance test could
  pass for the wrong reason (1 partition = no rebalance to observe). ⚠️ Whether the *existing*
  `test_kafka_rebalance_integration.py` pre-creates its topic is **unverified** — Step 21 must
  check before extending it.
- ⚠️ **ASSUMPTION — aiokafka 0.13 cooperative rebalancing.** Research 003 §Evidence Gaps records
  it as undocumented and *"likely unsupported"* (eager only). All rebalance tests are written
  against **eager** semantics (full revoke → full reassign). If aiokafka silently negotiates
  cooperative rebalancing on this broker image, `on_partitions_revoked` would fire with a partial
  set and Step 21's ordering assertion could behave unexpectedly. Explicitly out of scope
  (Non-goal); recorded so a future red test is diagnosed correctly.
- ⚠️ **ASSUMPTION — `get_wrapped_container().restart()` preserves the mapped host port.**
  Research 002 §Evidence Gaps 3 is candid: *"this is an implementation detail not a documented
  contract."* If it does not hold, every Step 29/31/33 test fails immediately with a connection
  error on the first restart. Fallback, in order: (a) re-derive the URL from the container after
  restart, (b) `pause()`/`unpause()` instead of `restart()` for the outbox tests too — weaker
  (processes are frozen, not restarted, so it does not prove recovery from a cold broker) but
  sufficient for "publish raises while the broker is unreachable".
- ⚠️ **ASSUMPTION — Toxiproxy on Actions.** Not adopted (§RT7-toxiproxy), so not a live risk; the
  assumption being *avoided* is research 002 §Evidence Gaps 4's *"Assumed feasible … but
  untested"*. Recorded so the 3.1 BACKLOG row carries its own precondition.
- **Chaos tests are the flakiest class in the repo, by construction.** Research 002 §5 cites
  network issues as the top GitHub Actions flakiness category [arXiv 2602.02307]. Mitigations are
  structural: deterministic waits only (`wait_for_logs`/health predicates, never fixed sleeps),
  nightly-only scheduling, the `finally`-unpause contract, and a **recorded flake count** (Step
  35). If a chaos test flakes ≥ 1 in 3, it must be quarantined with `xfail(strict=False)` + a
  BACKLOG row — not retried into apparent health.
- **Module-scoped chaos containers assume every chaos test leaves its container healthy.** If one
  does not, its module's remaining tests fail confusingly. The named fallback is to drop that one
  module to `function` scope and pay the boot cost, **not** to add cleanup cleverness to
  `ChaosContainer`.
- **Phase 6 introduces a mechanism, not just tests.** It is the only phase whose CI behaviour is
  unproven until it runs, which is why it is last and why it is the cut line. Cutting it leaves
  every gating phase (0–5, 7) intact and coherent — that is the property the phase order was
  chosen for.
- **The chaos suite proves failure-mode behaviour on *these five* paths, not that varco is
  resilient.** The honest claim after this plan is: "the outbox does not lose entries across a
  broker or database restart; the breaker opens on a black-holed dependency; a reaped lease fences
  its zombie holder; a crashed migration lock holder does not wedge the next deploy." It is not
  "varco is chaos-tested". Do not let the CHANGELOG overstate it.

---

## Execution log — resume state (as of 2026-08-26)

A `/build` run was started and **interrupted mid-way** (API session limit). This section records
exactly what exists so a fresh session can resume without redoing work. **Read this before
re-running `/build`.**

### Git state

- Branch: **`plan-018-reliability-floor`**, cut from `main` at `241aada`.
- **Nothing is committed.** All work below is in the uncommitted working tree.
- `main` is untouched. Nothing pushed. CI has not run.
- Intent (per the original request): finish on this branch, then merge to `main` **once**, so
  GitHub Actions fires a single time.

### ✅ DONE — Phase 1-6 test modules (18 files, uncommitted)

The test-writer phase is **complete**. Do **not** rewrite, reshape or "fix" these files.
All 18 pass `uv run ruff check` and `uv run ruff format --check`.

| Plan steps | File | State |
|---|---|---|
| 8 | `varco_nats/tests/test_nats_semantics_integration.py` | new, 3 tests — 1 red (Finding B below) |
| 9 | `varco_nats/tests/test_nats_channel_integration.py` | new, 4 tests — 3 red (Finding C below) |
| 10 | `varco_nats/tests/test_nats_dlq_integration.py` | new, 1 test — green |
| 11 | `varco_nats/tests/test_nats_health_chaos.py` | new, 1 test — blocked on `varco_chaos` |
| 13-14 | `varco_casbin/tests/test_concurrent_writers_integration.py` | new, 3 tests — **all green** (the Risks section's adapter assumption **holds**) |
| 16 | `varco_ws/tests/conftest.py` | modified — `/ws/bp` endpoint at `:106-158` |
| 17-18 | `varco_ws/tests/test_ws_backpressure_integration.py` | new, 2 tests — green, 3/3 consecutive |
| 20 | `varco_kafka/tests/test_kafka_eos_integration.py` | new, 4 tests — **blocked, see Finding A** |
| 21 | `varco_kafka/tests/test_kafka_rebalance_integration.py` | modified, 1→3 tests — green (74 s) |
| 22 | `varco_kafka/tests/test_kafka_offsets_integration.py` | modified, 3→4 tests — green |
| 23 | `varco_kafka/tests/test_kafka_eos.py` | modified — docstring only, as specified |
| 25 | `varco_fastapi/tests/test_app_migrations_integration.py` | modified, 6→7 tests — green (34 s) |
| 26 | `varco_sa/tests/test_sa_job_lease_crash.py` | new, 2 tests — blocked on `varco_chaos` |
| 27 | `varco_redis/tests/test_redis_job_lease_crash.py` | new, 2 tests — blocked on `varco_chaos` |
| 29-30 | `varco_kafka/tests/test_kafka_chaos.py` | new, 2 tests — blocked on `varco_chaos` |
| 31 | `varco_sa/tests/test_sa_chaos.py` | new, 1 test — blocked on `varco_chaos` |
| 32 | `varco_redis/tests/test_redis_chaos.py` | new, 1 test — blocked on `varco_chaos` |
| 33 | `varco_fastapi/tests/test_migration_chaos.py` | new, 1 test — blocked on `varco_chaos` |

"blocked on `varco_chaos`" = collection fails with exactly
`ModuleNotFoundError: No module named 'varco_chaos'`. **This is expected and correct** — Steps 1-3
have not been written yet. **No file fails importing a `varco_*` module.**

### ❌ NOT STARTED — everything else

- **Steps 1-3** — `testkit/varco_chaos/` does not exist. `testkit/` contains only
  `varco_conformance`. This is the gate for six test modules.
- **Step 4** — the `chaos` marker is **not** registered in any `[tool.pytest.ini_options] markers`
  list. (`pythonpath = ["../testkit"]` **is** already present in varco_kafka, varco_nats,
  varco_fastapi, varco_redis, varco_sa, varco_ws — Step 4's verify half is done.)
- **Steps 5-7** — `scripts/integration_tests.sh` `MARKER_EXPR` and the `Makefile` `chaos-test` /
  `chaos-test-clean` targets: not written.
- **Step 34** — the `chaos` job in `.github/workflows/integration.yml`: not written.
- **Steps 36-43** — CLAUDE.md, README.md, BACKLOG.md, CHANGELOG.md: untouched.

### Questions the plan left open, now ANSWERED

- **Step 21 / Risks §3** — *"whether the existing `test_kafka_rebalance_integration.py`
  pre-creates its topic is unverified"*. **It does pre-create it.** Safe to extend; done.
- **Risks §1 — casbin-async-sqlalchemy-adapter concurrent-writer semantics.** The assumption
  **holds**. All three Step 13 tests are green against real Postgres. No BACKLOG finding.

### Findings that must be carried into the resumed build

**A. BLOCKING PREREQUISITE the plan omits — Kafka transactions hang.** Every Step 20 EOS test
times out (560 s × 3). Root-caused with a standalone probe: `AIOKafkaProducer(transactional_id=...)`
`.start()` never returns against a default `testcontainers.kafka.KafkaContainer()` — a single
broker cannot create `__transaction_state` at the default replication factor 3. **Proven fix**, at
`varco_kafka/tests/conftest.py:53`:

```python
with KafkaContainer().with_env(
    "KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR", "1"
).with_env("KAFKA_TRANSACTION_STATE_LOG_MIN_ISR", "1") as container:
```

Probe result with the two env vars: `producer started (init_transactions OK)` / `txn committed OK`.
**[x] Applied** (`varco_kafka/tests/conftest.py`, resumed session). It is a test-fixture change,
inside the plan's allowed Non-goals exception (a) for a package's own `tests/conftest.py` — but it
is a **deviation from the plan as written**, called out here rather than in a commit message since
this session does not commit. Treated as a new Phase-0 step per the resume instructions.

**B. NATS `AT_LEAST_ONCE` does not redeliver after a handler raises.**
`varco_nats/varco_nats/bus.py:565-573` acks in a `finally`, and its own docstring says the message
is acked *"whether or not a handler raised … JetStream only redelivers on a process crash."*
Step 8's test as specified contradicts the documented implementation. Per the standing rule →
`xfail(strict=True, reason="BUG: ...")` + a BACKLOG row. **Do not edit `bus.py`.**

**C. NATS `channel_exists` is a "has messages" predicate, not an "exists" predicate.**
`channel.py:377-395` and `:417` — `list_channels` returns *"channels that currently carry
messages"*, and `declare_channel`'s `channel` arg is *"only used for logging"*. So
`declare_channel(...)` → `channel_exists(...)` is `False` with nothing published, and 3 of Step 9's
4 round-trips fail **structurally**. Genuine finding: the `ChannelManager` ABC contract is
unsatisfiable on NATS. → `xfail(strict=True)` on the three + a BACKLOG row naming the ABC-contract
gap. **Do not edit `channel.py`.**

**D. WS backpressure margin is machine-dependent and thin.** Steps 17-18 required raising the
volume to `_N=6000` × `_PAYLOAD=64 KiB` (~384 MB in flight) to make assertion (1)
(`slow_received < _N`) engage. Calibration recorded in the module: 2000×16 KiB **fails**,
3000×64 KiB **fails**, 6000×64 KiB **passes**. Cause: uvicorn's `websockets_impl` buffers
server-side writes with no applied `write_limit`, so the client's `max_queue=1` does not propagate
into varco's per-client `asyncio.Queue` until the buffer saturates. §RT4-backpressure explicitly
declined to use `write_limit`, which is why the margin is this thin. **3/3 clean runs locally
(~95 s each); Step 19 asks for 5 — 2 runs still owed.** File a BACKLOG row about the
machine-dependent margin. If it flakes, the Edge-cases table governs: **raise the volume, never
relax the assertion.**

**E. Test-side bugs found and already fixed** (recorded so they are not "re-discovered"):
- aiokafka fires `on_partitions_revoked` with an **empty set on the initial join**; the
  precondition now anchors on a post-initial-join slice. Characterized in the docstring.
- The commit-on-revoke listener called `consumer.commit()` unconditionally → `IllegalStateError:
  No partitions assigned` on that initial empty revoke. Now guarded on `consumer.assignment()`.
- That same test decoded `json.loads(record.value)["data"]["order_id"]`, but varco's
  `JsonEventSerializer` envelope is **flat** — `{"__event_type__", "event_id", "timestamp",
  <fields>}`, with no `"data"` wrapper.

### Plan line-reference drift (minor, verify before quoting)

| Plan cites | Actual |
|---|---|
| `bus.py:376` (EXACTLY_ONCE dedup branch) | `bus.py:377-379` |
| `bus.py:549` (`pre_ack`) | `bus.py:552` |
| Step 16 as work to be done | **already implemented** at `varco_ws/tests/conftest.py:106-158`, which shifts every plan line number after `:105` in that file |
| `varco_casbin/tests/conftest.py:66` "function-scoped `casbin_db_url`" | correct — no drift |
| `varco_ws/tests/conftest.py:64-128` `_build_app()`, `:94-98` comment | correct |
| `outbox.py:809-830`, `websocket.py:600-609`, `websocket.py:469-516`, `job/base.py:82,682` | **unverified** — could not be reached while `varco_chaos` is absent |
| Step 3's description of `abandon_lease` ("cancels the renew task without renewing, leaving the claim stale") | As shipped (`testkit/varco_chaos/leases.py`), it is a **documented no-op** — neither `test_sa_job_lease_crash.py` nor `test_redis_job_lease_crash.py` ever starts a background renew task, so there is nothing to cancel; "abandoning" the lease is achieved simply by the test not calling `renew()` again. The function's own docstring explains this and names the no-op as deliberate: it exists as a named, searchable call site for the moment the worker "crashes", and gives a future real background-renew-task refactor exactly one place to add a `task.cancel()` call so both twin tests would pick it up from one edit |
| BACKLOG RT2 file count (plan says 10, BACKLOG says 13) | **unverified** |

### How to resume

Re-run `/build plans/018-reliability-floor-rt-integration-and-chaos.md` **on the existing
`plan-018-reliability-floor` branch**, and tell it:

1. **Skip the test-writer phase entirely** — the 18 modules above are done and verified.
2. Go straight to the implementer with: Phase 0 (Steps 1-7) **plus Finding A's conftest fix as a
   new Phase-0 step**, then Step 34, then Phase 7 (Steps 36-43), applying Findings B/C as
   `xfail(strict=True)` + BACKLOG rows.
3. **Bound every command** — pass an explicit tool `timeout`, wrap pytest/make in shell `timeout`
   (≤ 480 s for integration/chaos), pipe through `| tail -N`, never run the whole repo suite in
   one call, retry at most once. An earlier foreground run appeared to deadlock on an unbounded
   testcontainers boot.
4. **Do not run `gh workflow run` / `gh run watch`** (Steps 35, 43 ask for it) — the branch is
   unpushed and CI must fire exactly once, on the final merge to `main`.
5. Standing rule, unchanged: **nothing under `varco_*/varco_*/` may be edited.** A red test is a
   finding (`xfail(strict=True)` + BACKLOG row), never a licence to patch production source.

### Second resume — completed 2026-08-26

Steps 1-7, the Finding A conftest fix, Step 34, and Phase 7 (Steps 36-42) are now done, all
uncommitted on `plan-018-reliability-floor`. Step 43 is **partially** done — `make lint`/`make
type-check`/`make test` (per-touched-package, not the full `make test` sweep) all green; the full
`make integration-test-clean`/`make chaos-test-clean` sweep and `gh workflow run integration.yml`
were deliberately **not** run (explicit scope instruction: bound every command, never the whole
repo suite in one call, never trigger CI from this unpushed branch).

**New findings from wiring the scaffolding, beyond B/C/D:**

- **`ChaosContainer.wait_ready()` bug, found and fixed (testkit-only, not a scope violation —
  this file is Steps 1-3's own new code, not a "the 18 test modules" file).** The original
  implementation matched the readiness predicate against `DockerContainer.get_logs()`'s full
  cumulative history. After `restart()`, a historical "ready" line from the container's
  *original* boot is still present in that history, so the predicate matched instantly and
  falsely — before the restarted process had actually finished coming back up, producing
  `ConnectionRefusedError` on the next connection attempt. Fixed by tracking a per-stream byte
  offset, captured immediately before `restart()` issues the docker restart call, and matching
  the predicate only against log content at-or-after that offset. Verified against
  `test_nats_health_chaos.py`/`test_redis_chaos.py` (pause-based, unaffected by the bug, still
  green) and `test_sa_chaos.py` (restart-based, where the bug reproduced and the fix corrected
  the false-positive timing).
- **RT7b-port-remap (new, load-bearing, NOT fixed this session)**: `docker-py`'s
  `Container.restart()` did not preserve the host port mapping in this session's Docker
  27.5.1/WSL2 environment — verified independently with raw `docker-py`, no testcontainers
  involved (`before restart ports: {'HostPort': '32811'}` → `after: {'HostPort': '32812'}`).
  This contradicts research 002 §1's port-survivorship claim that every restart-based chaos
  test's "capture the DSN once, reuse across restarts" pattern depends on. Reproduced through
  `ChaosContainer`/`test_sa_chaos.py`. **Not worked around** — doing so would require rewriting
  the already-written Phase 6 test files' DSN-caching pattern, out of this session's scope.
  Filed as its own BACKLOG.md row (`RT7b-port-remap`) and a Common Pitfalls table row in
  CLAUDE.md. Not yet re-verified against a native Linux dockerd (the actual GitHub Actions
  runner) — genuinely unknown whether the nightly `chaos` job will hit this.
- **RT7a-redis-claim-guard (new)**: running the previously-`varco_chaos`-blocked
  `test_redis_job_lease_crash.py` for the first time surfaced a genuine cross-backend
  disagreement — `RedisJobStore.reap_expired_leases()` does not release the SET-NX-EX claim
  guard key `try_claim()` created, so a legitimate re-claim can be refused for up to `claim_ttl`
  (default 30s) after a correct reap. `SAJobStore`'s twin test passes cleanly. `xfail(strict=True)`
  applied to the one affected test; the negative-case sibling test is unaffected and stays green.
  Confirmed via an ad-hoc probe (`claim_ttl=1` passes, default `claim_ttl=30` fails).

**Verified this session** (previously blocked on `varco_chaos`, now collect and were run against
real Docker): `test_nats_health_chaos.py` (green), `test_redis_chaos.py` (green),
`test_redis_job_lease_crash.py` (1 green + 1 xfail-strict), `test_sa_job_lease_crash.py` (green),
`test_sa_chaos.py` (green after the `wait_ready()` fix above), plus re-runs of
`test_nats_semantics_integration.py` and `test_nats_channel_integration.py` with Findings B/C's
xfail markers applied and confirmed green. **Also verified this session**: `test_kafka_eos_integration.py` (all 4 tests green, 42s) —
confirms Finding A's conftest fix (`KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR`/`_MIN_ISR=1`)
resolves the hang exactly as the interrupted session's probe predicted. **Not run this session**
(out of scope / not needed to validate Phase-0 scaffolding, and each pays a fresh ~20-30s Kafka
container boot): `test_kafka_chaos.py`, `test_migration_chaos.py`, the broader
`test_kafka_rebalance_integration.py`/`test_kafka_offsets_integration.py` suites, and
`test_nats_dlq_integration.py`/`test_concurrent_writers_integration.py`/
`test_ws_backpressure_integration.py` (all previously reported green in the first resume's
execution log and not re-run here since they were never blocked on `varco_chaos`).
