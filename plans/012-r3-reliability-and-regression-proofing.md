# Plan 012 — R3: Reliability & regression-proofing (testing only)

## Goal

After this plan, `make integration-test` is a single, reliable, self-contained local
command that (a) installs every dependency it needs from the workspace lock, (b) starts one
container per backend per package run instead of one per test file, (c) runs real-service
tests for **all nine** backend packages plus the full-stack example, and (d) enforces every
`varco_core` ABC's contract identically across every shipped implementation via a shared
conformance suite. No production code changes; no GitHub Actions changes.

## Non-goals

- **No GitHub Actions work of any kind.** `.github/workflows/integration.yml` is verified
  inert — `rg '^[^#]' .github/workflows/integration.yml` returns **0 matches** (all 201 lines
  commented). It stays exactly as-is. Do not uncomment, do not add a new workflow, do not add
  a `workflow_dispatch` trigger. This was declined twice (BACKLOG.md:50-56, "Parked").
- **No new production feature code.** Permitted changes are limited to: files under
  `*/tests/`, `examples/*/tests/`, a new root-level `testkit/` directory (test-only, never
  packaged), `scripts/integration_tests.sh`, `Makefile`, `[dependency-groups]` /
  `[tool.pytest.ini_options]` / `[tool.uv.sources]` blocks in `pyproject.toml` files, and docs.
  Nothing under `varco_*/varco_*/` is edited. If a conformance test fails because a backend
  genuinely violates its ABC, **record it as an `xfail` with a `# BUG:` comment and a backlog
  entry** — do not fix production code inside this plan.
- **No new workspace member / no published test-support package.** Specifically not a
  `varco_testkit` distribution (see Alternatives).
- **No coverage-threshold gate, no pytest-xdist parallelism, no benchmark suite.**
- **No new reference application** (BACKLOG.md:56; [reliability
  brief](../design/reliability-release/research/001-reference-app-end-to-end-testing-patterns.md):61-64
  — "A dedicated reference app adds burden without proportional evidence of bug detection").

---

## Source corrections (verified against the tree — BACKLOG.md and the scout report are stale here)

The implementer must treat these verified facts as authoritative over BACKLOG.md's rationale
column and over the scout summary that seeded this plan:

| # | Stale claim | Verified reality |
|---|---|---|
| C-1 | RT1: "replace manually-provided broker env vars (`REDIS_URL`, `MONGODB_URL`, …)" | **No such env vars exist.** `rg 'os\.environ.*(REDIS_URL\|MONGODB_URL\|KAFKA_\|NATS_URL\|MEMCACHED_\|DATABASE_URL\|POSTGRES_)' varco_*/tests/*.py` → no matches. Every integration test already uses testcontainers. RT1 is therefore **consolidation + correctness**, not migration. |
| C-2 | RT1 (implicit): container startup is already efficient | **38 test files each declare their own container fixture** (`rg -c '(RedisContainer\|PostgresContainer\|MongoDbContainer\|KafkaContainer\|DockerContainer)' varco_*/tests/*.py` → 83 occurrences / 38 files), almost all `scope="module"`. `varco_redis` starts ~9 Redis containers per run; `varco_beanie` ~10 Mongo containers; `varco_sa` ~10 Postgres containers. This is the real reliability/latency problem RT1 must solve. |
| C-3 | RT2: "`varco_nats` — 6 test files, ZERO carry `@pytest.mark.integration`" | **False.** `varco_nats/tests/test_nats_integration.py:32` already has `pytestmark = pytest.mark.integration`. RT2's residual work is (i) the generic-`DockerContainer` NATS fixture at `test_nats_integration.py:54-76` must move to the first-party module, (ii) audit the other 8 nats test files (`test_nats_dlq.py`, `test_nats_health.py`, `test_regression_nats_dlq_ack_durability.py`, …) for real-broker tests hiding behind `fakes.py`. |
| C-4 | RT3: "no Postgres/SQLAlchemy adapter integration test exists" | **False.** `varco_casbin/tests/test_persistence_integration.py` exists, is marked (`:22`), and spins a real `PostgresContainer` (`:25-33`). It contains **exactly one** test (`test_policy_persists_across_engines_postgres`, `:35`). RT3 is *broadening* that file, not creating it. |
| C-5 | RT5: "`varco_kafka` — only `test_kafka_health.py` is marked (1/9)" | **3 of 9 files are marked**: `test_kafka_integration.py:32`, `test_kafka_channel_integration.py:27`, `test_kafka_health.py:264`. The gap is still real: DLQ (`test_kafka_dlq.py`), offsets, and rebalancing (`test_kafka_eos.py`) are mock-only. |
| C-6 | RT8: "~34 integration tests" (BACKLOG.md:45) / scout's "46 total, 13 integration-marked" | **Both wrong.** Verified: `examples/00-full-stack-post-api/example/tests/` has **46 test functions** (`test_api_auth.py` 22, `test_e2e_integration.py` 13, `test_post_service.py` 11), of which **35 carry `@pytest.mark.integration`** (all 22 in `test_api_auth.py` + all 13 in `test_e2e_integration.py`); the remaining 11 in `test_post_service.py` are unit tests. Use **46 total / 35 integration** in any doc text. |
| C-7 | — (new finding) | `varco_casbin/tests/test_beanie_adapter.py:33` assigns `pytestmark_integration = pytest.mark.integration` — a **typo'd name pytest ignores entirely**. The two tests in that file that matter carry their own `@pytest.mark.integration` (`:354`, `:394`), so nothing is currently mis-marked, but the dead line is a trap. Delete it. |
| C-8 | — (new finding) | `varco_sa/pyproject.toml:42-50` declares **neither `testcontainers` nor `asyncpg`** in its dev group (`asyncpg` is only in the `postgresql` *extra*, `:36-38`), yet `varco_sa/tests/conftest.py:90-127` and 10 test files use `PostgresContainer` + `postgresql+asyncpg://` DSNs. Both work today only because sibling packages' dev groups leak them into the shared workspace venv. This is the latent bug called out below as Step 1. |

---

## Design

### Shape of the work

```
Phase 0  dependency & harness hygiene ......... blocks everything (Steps 1-5)
Phase 1  RT1 one container per package ........ every later phase runs on it (Steps 6-11)
Phase 2  RT2 nats ............................. (Steps 12-13)
Phase 3  RT3 casbin + Postgres ................ (Step 14)
Phase 4  RT5 kafka DLQ/offset/rebalance ....... (Steps 15-17)
Phase 5  RT4 varco_ws real server ............. (Steps 18-20)
Phase 6  RT6 conformance suite ................ largest item (Steps 21-27)
Phase 7  RT7 chaos / fault injection .......... depends on RT1+RT5 (Steps 28-32)
Phase 8  RT8 example smoke run ................ 🟡 (Steps 33-34)
Phase 9  RT9 migration lifecycle .............. 🟡 (Steps 35-36)
Phase 10 docs ................................. (Steps 37-38)
```

### Open question 1 — RT1 testcontainers scope: **keep a namespaced env-var override; testcontainers stays the default**

**Decision: containers by default, with a per-service `VARCO_TEST_<SERVICE>_URL` opt-out that
is loudly reported.** Concretely: every shared container fixture starts with

```python
override = os.environ.get("VARCO_TEST_REDIS_URL")
if override:
    request.config.stash[_OVERRIDES].add(("redis", override))  # reported in the summary
    yield override
    return
```

Rationale:

- ✅ The stated goal is *a reliable one-command run*, which is satisfied by the default path
  (a fresh developer with only Docker installed types `make integration-test` and it works).
  The override never fires unless someone deliberately exports a `VARCO_TEST_`-prefixed name.
- ✅ It buys back the inner loop where containers hurt most: a Kafka container is the slowest
  thing in the suite (first-run image pull + broker boot), and a solo maintainer iterating on
  one Kafka DLQ test benefits from pointing at a long-lived local broker.
- ✅ The overrides are **namespaced `VARCO_TEST_*`**, deliberately *not* `REDIS_URL` /
  `MONGODB_URL` / `DATABASE_URL`. Honouring bare names would mean a developer with an
  unrelated `DATABASE_URL` exported in their shell silently runs the whole suite against their
  own dev database — a destructive-write hazard, since RT7 and RT9 create and drop schemas.
  C-1 confirms no bare name is read anywhere today; keep it that way.
- ❌ Two code paths per fixture, and a green run whose provenance is ambiguous. Mitigated by
  the runner printing an explicit `⚠ override active: redis=…` block in the summary
  (Step 10) — a run with any override is visibly not a clean-room run.
- ❌ An override pointing at a dirty broker can produce a false green. Mitigated by Step 8's
  per-test namespacing rule (unique key prefix / stream name / schema per test), which every
  fixture must satisfy anyway for session-scoped container reuse.

Rejected: **unconditional testcontainers**. ✅ one code path, unambiguous results;
❌ makes the single-test inner loop pay full container boot every time, which is exactly the
friction that causes a solo maintainer to stop running integration tests at all — the failure
mode this whole release exists to prevent.

### Open question 2 — RT6 conformance suite shape: **(a) one shared suite package, reached via pytest's `pythonpath` ini option; each backend opts in by subclassing**

**Decision:** a new **test-only, never-packaged** directory at the repo root:

```
testkit/                                  ← on sys.path only during test runs
  varco_conformance/
    __init__.py
    event_bus.py     → class EventBusConformance
    cache.py         → class CacheBackendConformance
    job_store.py     → class JobStoreConformance
    dlq.py           → class DeadLetterQueueConformance
```

reached by adding one line to each participating package's `[tool.pytest.ini_options]`:

```toml
pythonpath = ["../testkit"]        # relative to rootdir = the package dir
```

Each backend opts in with a thin module in its own `tests/`:

```python
# varco_redis/tests/test_redis_conformance.py
from varco_conformance.event_bus import EventBusConformance

pytestmark = pytest.mark.integration


class TestRedisEventBusConformance(EventBusConformance):
    @pytest.fixture
    async def bus(self, redis_url: str):  # ← the package's own shared fixture
        async with RedisEventBus(RedisEventBusSettings(url=redis_url)) as bus:
            yield bus
```

Rationale:

- ✅ **The literal recipe in the brief cannot work here.** The
  [CI-patterns brief](../design/i18n-tz-framework/research/005-github-actions-integration-testing-multi-backend.md):60-83
  shows `@pytest.fixture(params=["in_memory", "kafka", "redis"])` — one fixture constructing
  every implementation in one process. That requires all backends importable and configured in
  a single pytest run, which the monorepo forbids: `scripts/integration_tests.sh:81-87`
  documents that pytest **must** be invoked with `cd $pkg` so it picks up the package's own
  `pyproject.toml` (rootdir → `asyncio_mode`, markers). Class inheritance is the same pattern
  with the parametrization axis moved from *fixture params* to *test-class subclasses*, which
  is exactly what the brief's cited source describes in the general case ("write one test
  suite that takes a constructor, and run it against each implementation",
  brief:88). Indirect parametrization is still used **within** `varco_core/tests/` for the
  in-process implementations (`InMemoryEventBus`, `InMemoryCache`, `NoOpCache`,
  `InMemoryJobStore`, `InMemoryDeadLetterQueue`), where a single process does hold them all.
- ✅ pytest only collects classes matching `Test*`, so the base `EventBusConformance` class is
  **never collected standalone** — an abstract suite cannot accidentally run with an
  unimplemented fixture. This is why the base classes are deliberately *not* named `Test*`.
- ✅ `pythonpath` is a built-in pytest ini key (pytest ≥ 7.0; every package pins
  `pytest>=8.0`), so there is **no `sys.path` manipulation in any conftest** and no new
  dependency.
- ✅ Pointing `pythonpath` at `../testkit` (not `..`) means exactly one package name
  (`varco_conformance`) becomes importable — zero risk of the repo root's outer `varco_core/`
  directory shadowing the installed `varco_core` distribution.
- ❌ A change to a suite file can break nine packages at once. Accepted — that is the entire
  point of a conformance suite, and the blast radius is test-only.
- ❌ `testkit/` is not installed by `uv sync`, so an editor/mypy needs the same path hint.
  Accepted; it is excluded from `make type-check`'s `_SRC_DIRS` by construction.

Rejected: **(b) a fixture registry each backend opts into.** ✅ marginally more explicit about
which impl covers which contract; ❌ the registry module itself has to be imported from a
shared location anyway — so it pays the identical import-path cost while adding a layer of
indirection (register → look up → construct) on top of a mechanism (class inheritance) pytest
already provides for free. Strictly more machinery for the same coupling.

Rejected: **shipping the suites as `varco_core.testing.conformance` inside the wheel.**
✅ trivially importable everywhere, precedent exists (`django.test`, `celery.contrib.testing`);
❌ it is new **public API in a published package** with a permanent back-compat obligation,
which the "testing only, no new feature work" constraint forbids.

---

## Steps

Each step is independently verifiable. Steps marked 🟡 are the "should" items and are
deliberately sequenced last.

### Phase 0 — dependency & harness hygiene (blocks every later phase)

1. [ ] `varco_sa/pyproject.toml` — **fix the latent leak (C-8)**. Add to `[dependency-groups] dev`
       (currently lines 42-50): `"testcontainers[postgres]>=4.0"` and `"asyncpg>=0.29"`, each with
       a comment naming the files that need them (`tests/conftest.py:90-127`,
       `tests/test_rls.py`, `tests/test_migration_lock.py`, …). Today both resolve only because a
       sibling package's dev group leaks into the shared workspace venv — a `uv sync --package
       varco-sa` in isolation, or any reordering of the lock, breaks 10 test files at once.
       *Verify:* `cd varco_sa && uv run python -c "import testcontainers.postgres, asyncpg"`.
2. [ ] `varco_nats/pyproject.toml:48-58` — replace bare `"testcontainers>=4.0"` with
       `"testcontainers[nats]>=4.3"` and delete the now-false comment at `:55-56` ("The generic
       DockerContainer is used (no nats extra) so `uv sync` never fails on a missing
       testcontainers extra"). Grounding: the
       [testcontainers module brief](../design/integration-testing/research/001-testcontainers-nats-memcached-support.md):9-33
       — `testcontainers.nats.NatsContainer` is first-party since **v4.3.0** (2024-03-24), the
       extra pulls **zero** additional dependencies, wait strategy is `wait_for_logs("Server is
       ready", timeout=120)`, and the connection helper is `nats_uri()`.
3. [ ] `varco_memcached/pyproject.toml:40-47` — replace bare `"testcontainers>=4.0"` with
       `"testcontainers[memcached]>=4.4"` (the existing comment at `:44` already *claims* the
       extra). Grounding: same brief, `:35-55` — `testcontainers.memcached.MemcachedContainer`
       is first-party since **v4.4.1** (2024-05-14), port-based wait on 11211, helper
       `get_host_and_port()`. No generic-`DockerContainer` fallback is needed for either
       service (brief:133).
4. [ ] `varco_ws/pyproject.toml:34-38` — add the RT4 harness deps to the dev group:
       `"fastapi>=0.115"`, `"uvicorn>=0.30"`, `"httpx>=0.28"`, `"websockets>=13.0"`. No
       testcontainers entry: `WebSocketEventBus`/`SSEEventBus`
       (`varco_ws/varco_ws/websocket.py:355`, `sse.py:175`) are in-process buses whose "real
       service" is a locally-bound uvicorn server, not a container.
5. [ ] Root — run `uv sync` and commit the resulting `uv.lock` delta as its own commit, so a
       later test failure is never confused with a resolution change.
       *Verify:* `uv sync && git diff --stat uv.lock`.

### Phase 1 — RT1: one container per package per run

6. [ ] `varco_redis/tests/conftest.py` — introduce a **session-scoped** `redis_url` fixture
       (start `RedisContainer` once, honour `VARCO_TEST_REDIS_URL` per Open Question 1, always
       stop in `finally`). Migrate the 9 files that currently each declare their own
       (`test_redis_health.py`, `test_redis_cache_integration.py`, `test_redis_bulkhead.py`,
       `test_redis_integration.py`, `test_redis_encryption_store.py`,
       `test_redis_lock_integration.py`, `test_stream_dlq.py`, `test_redis_dlq_integration.py`,
       `test_redis_rate_limit_integration.py`) to consume it.
       *Verify:* `cd varco_redis && VARCO_RUN_INTEGRATION=1 uv run pytest tests/ -m integration`
       and confirm with `docker events` (or wall-clock) that exactly **one** Redis container starts.
7. [ ] `varco_beanie/tests/conftest.py`, `varco_sa/tests/conftest.py`,
       `varco_kafka/tests/conftest.py`, `varco_memcached/tests/conftest.py`,
       `varco_casbin/tests/conftest.py`, `varco_nats/tests/conftest.py` — same treatment
       (`mongo_url`, `postgres_url`, `kafka_bootstrap`, `memcached_host_port`,
       `nats_url`). `varco_sa/tests/conftest.py` already owns the *helpers*
       (`asyncpg_url()` `:90`, `provision_rls_app_url()` `:171`,
       `create_isolated_database_url()` `:235`) but **no container fixture** — add it and route
       the helpers through it. Standardise every Postgres DSN on
       `container.get_connection_url(driver="asyncpg")` (the reason is documented at
       `varco_sa/tests/conftest.py:94-105`: the older
       `.replace("postgresql://", "postgresql+asyncpg://")` silently no-ops), including
       `varco_casbin/tests/test_persistence_integration.py:31-32` which still string-replaces.
8. [ ] Same six conftests — **write the isolation rule into the fixture docstrings**: a
       session-scoped container is shared, so every test must namespace its own state (unique
       key prefix, stream/subject/topic name, Mongo database name, or Postgres schema; a
       `uuid4().hex[:8]` run id, as `varco_nats/tests/test_nats_integration.py:92-98` already
       does). Any test that needs a pristine server declares a function-scoped
       `*_container_fresh` fixture instead — explicitly, and paying the cost visibly.
9. [ ] `varco_sa/tests/conftest.py` — additionally expose `postgres_container` (the raw object)
       alongside `postgres_url`, because `create_isolated_database_url()` and RT7/RT9 need the
       container handle, not just a DSN.
10. [ ] `scripts/integration_tests.sh` — after the Docker check (`:34-45`), add an **override
        report**: scan the environment for `VARCO_TEST_*_URL` names and, if any are set, print a
        `⚠ Override active: <NAME>=<value>` block before the run and repeat it in the summary
        (`:119-129`). A run with any override present is visibly not a clean-room run
        (Open Question 1's mitigation). Do not change the exit-code semantics, including the
        exit-5 = "no integration tests" branch (`:110-112`).
11. [ ] `Makefile:108-115` — leave `integration-test` semantics unchanged, add
        `integration-test-clean` which runs the script with every `VARCO_TEST_*` name unset
        (`env -u`), i.e. the guaranteed clean-room entry point. Update `make help` text.

### Phase 2 — RT2: `varco_nats`

12. [ ] `varco_nats/tests/test_nats_integration.py` — delete the local generic-`DockerContainer`
        fixture (`:54-76`) and consume the new session-scoped `nats_url` from Step 7, which uses
        first-party `NatsContainer` (brief:12-22). Keep JetStream enabled — the existing fixture
        passes `--command "-js"` (`:66`) and the tests depend on it; carry that over via
        `NatsContainer(...).with_command("-js")`.
13. [ ] `varco_nats/tests/` — audit the other 8 files (`test_nats_bus.py`, `test_nats_channel.py`,
        `test_nats_health.py`, `test_nats_config.py`, `test_nats_connection.py`,
        `test_nats_dlq.py`, `test_nats_di.py`, `test_regression_nats_dlq_ack_durability.py`) for
        tests that touch a real broker rather than `tests/fakes.py`. Mark each such test
        `@pytest.mark.integration` and point it at `nats_url`. If a file turns out to be fully
        fake-backed, add a one-line module docstring saying so — so the next audit is free.
        C-3: this is a smaller job than BACKLOG.md implies, because
        `test_nats_integration.py:32` is already marked.

### Phase 3 — RT3: `varco_casbin` + Postgres

14. [ ] `varco_casbin/tests/test_persistence_integration.py` — broaden from its single test
        (`:35`) to cover the SQLAlchemy adapter's real contract against the shared
        `postgres_url`: (i) `remove_policy` / `remove_filtered_policy` round-trip; (ii) RBAC
        role-inheritance enforcement after a cold reload (new `CasbinPolicyEngine` on the same
        DSN); (iii) ABAC enforcement with `subject_attrs`/`object_attrs` mirroring
        `tests/test_abac_e2e.py`, but persisted; (iv) two engines sharing one database — writer
        adds a policy, reader `reload()`s and sees it (the documented "shared singleton"
        requirement in CLAUDE.md's pitfall table, verified against a real DB); (v) domain/tenant
        `RequestMapper.domain_for` keying if the preset supports it. Also delete the dead
        `pytestmark_integration` line at `varco_casbin/tests/test_beanie_adapter.py:33` (C-7).
        *Verify:* `cd varco_casbin && VARCO_RUN_INTEGRATION=1 uv run pytest tests/ -m integration -v`.

### Phase 4 — RT5: `varco_kafka` real-broker coverage

15. [ ] `varco_kafka/tests/test_kafka_dlq_integration.py` (new) — `KafkaDLQ` against a real
        broker: `push()` lands on the dedicated DLQ topic; a `@listen(..., retry_policy=..., dlq=)`
        handler that fails N times routes to the DLQ and the entry round-trips
        (`DeadLetterEntry` fields survive serialization); **`push()` never raises** when the DLQ
        topic is unwritable (the hard contract in CLAUDE.md / `varco_core/event/dlq.py:261`) —
        assert it logs and swallows.
16. [ ] `varco_kafka/tests/test_kafka_offsets_integration.py` (new) — consumer offset management:
        messages published while a consumer is stopped are delivered after restart with the same
        `group_id`; a fresh `group_id` re-reads from the configured earliest/latest position;
        redelivery after a failed handler does not silently advance the offset.
17. [ ] `varco_kafka/tests/test_kafka_rebalance_integration.py` (new) — partition rebalancing:
        create a multi-partition topic, run two consumers in one `group_id`, assert every
        published event is received exactly once across the pair, then stop one consumer and
        assert the survivor takes over the orphaned partitions and no message is lost. Give
        rebalance settle-time generous sleeps (CLAUDE.md test convention: widen the margin, never
        `xfail`); a rebalance is inherently seconds-scale.

### Phase 5 — RT4: `varco_ws` real WebSocket/SSE server

18. [ ] `varco_ws/tests/conftest.py` (new) — a `running_server` fixture: a small FastAPI app
        defined **in the test tree** (never in `varco_ws/varco_ws/`) exposing `GET /ws`
        (accepts a real Starlette `WebSocket`, registers it with a module-level
        `WebSocketEventBus`) and `GET /sse` (returns the `SSEEventBus` stream), served by
        `uvicorn.Server` on an ephemeral port in a background task. Model it on the working
        precedent at `examples/00-full-stack-post-api/example/tests/conftest.py:16-49`,
        including its session-scoped-event-loop requirement — that file documents exactly the
        failure this fixture would otherwise hit ("uvicorn's background task runs in one loop
        while `httpx.AsyncClient` runs in another — requests never complete", `:36-38`). That
        means `varco_ws/pyproject.toml` also needs
        `asyncio_default_fixture_loop_scope = "session"` /
        `asyncio_default_test_loop_scope = "session"` (mirroring
        `examples/00-full-stack-post-api/pyproject.toml:27-28`).
19. [ ] `varco_ws/tests/test_ws_integration.py` (new, `pytestmark = pytest.mark.integration`) —
        against the real server, using the `websockets` client: (i) connect → publish → receive,
        with a real wire frame rather than `MockWebSocket` (`tests/test_ws_bus.py:44`);
        (ii) **connection pooling** — N concurrent clients each receive every broadcast and
        `connection_count` tracks connect/disconnect accurately; (iii) **message ordering** —
        100 sequenced events arrive in publish order on a single connection; (iv) **reconnect** —
        client drops mid-stream, the server evicts it (no leaked task; assert
        `connection_count` returns to 0), the client reconnects and resumes receiving;
        (v) **backpressure** — a deliberately non-reading client under each
        `BackpressurePolicy` (`varco_ws/varco_ws/websocket.py:101`) behaves as the mocked unit
        tests claim (`test_ws_bus.py:233-322`), i.e. `DROP_NEWEST`/`DROP_OLDEST` keep the other
        clients healthy and `DISCONNECT` removes only the offender.
20. [ ] `varco_ws/tests/test_sse_integration.py` (new, marked) — the SSE half over real HTTP with
        `httpx.AsyncClient(...).stream()`: event framing (`data:` lines / event ids) is
        wire-correct, multiple subscribers each receive every event, `stop()` terminates every
        open stream (the sentinel path at `sse.py` exercised end-to-end, not via
        `test_ws_bus.py:491-527`'s in-process stand-in).

### Phase 6 — RT6: conformance suite

21. [ ] `testkit/varco_conformance/__init__.py` (new) — empty package marker plus a module
        docstring stating the contract: *these classes are never named `Test*`, are never
        collected standalone, and every abstract fixture raises `NotImplementedError` so a
        backend that forgets to override one fails loudly.*
22. [ ] `testkit/varco_conformance/event_bus.py` (new) — `class EventBusConformance` over
        `AbstractEventBus` (`varco_core/varco_core/event/base.py`): publish→subscribe round-trip;
        multiple subscribers on one channel; unsubscribe stops delivery; channel isolation (an
        event on channel A never reaches a channel-B subscriber); `start()`/`stop()` idempotency;
        async-context-manager entry/exit; publish with no subscriber does not raise; event
        payload/type fidelity after serialization round-trip.
23. [ ] `testkit/varco_conformance/cache.py` (new) — `class CacheBackendConformance` over
        `CacheBackend` (`varco_core/varco_core/cache/base.py`): get-miss returns `None`;
        set→get round-trip; TTL expiry; `delete()`; `clear()`; overwrite; the **portable bulk
        defaults** `get_many`/`set_many`/`delete_many` (CLAUDE.md: `CacheBackend` gives every
        backend these as loop-over-single defaults, so **every** backend must satisfy
        `BulkCache`) — including partial-hit `get_many`; and `isinstance(backend, AsyncCache)`
        stays `True` (the `runtime_checkable`-Protocol invariant CLAUDE.md's D-11 pitfall row is
        about).
24. [ ] `testkit/varco_conformance/job_store.py` (new) — `class JobStoreConformance` over
        `AbstractJobStore` (`varco_core/varco_core/job/base.py`): save→get round-trip;
        `list_by_status`; `try_claim` succeeds once and returns `None`/`False` for a second
        claimant; `run_at` in the future is not claimable; `save(expected_epoch=<stale>)` raises
        `StaleLeaseError`; `renew()` extends and `reap_expired_leases()` reclaims — each guarded
        by `pytest.skip` on `NotImplementedError` (the ABC's documented
        concrete-but-raising contract for stores without lease support);
        `delete_where()` with **no predicate raises `ValueError`** (the U-18 retention
        guard); and `supports_zoned_schedules` is honoured consistently (a store declaring
        `True` must persist `run_at_wall`/`run_at_tz`/`run_at_fold`).
25. [ ] `testkit/varco_conformance/dlq.py` (new) — `class DeadLetterQueueConformance` over
        `AbstractDeadLetterQueue` (`varco_core/varco_core/event/dlq.py:261`): **`push()` never
        raises**, including against a broken/unreachable sink; `push` then `ack`; the
        `supports_random_access` flag matches reality (`get()`/`list_entries()` either work or
        raise `DeadLetterNotAddressable` — never silently return empty); `delete()`'s portable
        default falls back to `ack()`; `delete_where()`/`count_by_channel()` with no predicate
        refuse (`ValueError`); and the tenancy rule — an entry stamped `tenant_id=None` is
        **never** matched by an explicit `tenant_id="acme"` filter (CLAUDE.md Plan 009 RD-4,
        currently guarded nowhere across backends).
26. [ ] `varco_core/tests/test_conformance_inmemory.py` (new, **unmarked** — no Docker) +
        `varco_core/pyproject.toml` `pythonpath = ["../testkit"]` — run all four suites against
        the in-process implementations (`InMemoryEventBus`, `InMemoryCache`, `NoOpCache`,
        `InMemoryJobStore`, `InMemoryDeadLetterQueue`). This is the fast feedback loop and is
        also where the brief's **indirect parametrization** recipe is used literally
        ([brief](../design/i18n-tz-framework/research/005-github-actions-integration-testing-multi-backend.md):74-83),
        since one process holds every impl. ⚠️ `NoOpCache` legitimately cannot satisfy
        set→get; give it its own subclass that overrides the storage-semantics tests with the
        no-op expectation rather than weakening the shared suite.
27. [ ] Per-backend opt-in modules + one `pythonpath = ["../testkit"]` line each in
        `varco_redis`, `varco_kafka`, `varco_nats`, `varco_sa`, `varco_beanie`,
        `varco_memcached`, `varco_ws` `[tool.pytest.ini_options]`. New files, all
        `pytestmark = pytest.mark.integration`, each consuming its package's Phase-1 fixture:
        - `varco_redis/tests/test_redis_conformance.py` → `RedisEventBus`
          (`varco_redis/varco_redis/bus.py:99`), `RedisStreamEventBus` (`streams.py:133`),
          `RedisCache` (`cache.py:133`), `RedisJobStore`, `RedisDLQ`
        - `varco_kafka/tests/test_kafka_conformance.py` → `KafkaEventBus`
          (`varco_kafka/varco_kafka/bus.py:98`), `KafkaDLQ`
        - `varco_nats/tests/test_nats_conformance.py` → `NatsEventBus`
          (`varco_nats/varco_nats/bus.py:131`), `NatsDLQ`
        - `varco_sa/tests/test_sa_conformance.py` → `SAJobStore`
          (`varco_sa/varco_sa/job_store.py:288`), `SADeadLetterQueue` (`dlq.py:141`)
        - `varco_beanie/tests/test_beanie_conformance.py` → `BeanieJobStore`
          (`varco_beanie/varco_beanie/job_store.py:385`), `BeanieDeadLetterQueue` (`dlq.py:122`)
        - `varco_memcached/tests/test_memcached_conformance.py` → `MemcachedCache`
          (`varco_memcached/varco_memcached/cache.py:144`)
        - `varco_ws/tests/test_ws_conformance.py` → `WebSocketEventBus`, `SSEEventBus` against
          the Step-18 server
        - `LayeredCache` conformance belongs in `varco_redis` (needs a real L2) —
          add it to that package's module.
        Any contract violation found here is recorded as `@pytest.mark.xfail(reason="BUG: …",
        strict=True)` plus a BACKLOG entry — **not** fixed in this plan (Non-goals).

### Phase 7 — RT7: chaos / fault injection

Grounding for the approach:
[reliability brief](../design/reliability-release/research/001-reference-app-end-to-end-testing-patterns.md):15
(Confluent "bring up a full cluster … and hard kill clients and servers during the process to
ensure data is neither lost nor duplicated") and `:62` ("Write chaos/failure-injection
scenarios **within each feature's test suite** … Use testcontainers to orchestrate real
brokers/DB in those tests"). Per that recommendation, each scenario lives in the owning
backend package, not in a central chaos suite.

28. [ ] `varco_sa/tests/test_outbox_chaos_integration.py` (new, marked) — outbox durability under
        broker restart, on real Postgres + real Redis or Kafka: write N entries through
        `SAOutboxRepository` inside the domain transaction, start `OutboxRelay`, **stop the
        broker container mid-drain**, assert the relay neither loses nor deletes undelivered
        entries and does not crash, restart the broker, assert every entry is ultimately
        published exactly once and removed. Container stop/start via the testcontainers handle
        from Step 9 (`container.get_wrapped_container().stop()/start()` — see ⚠️ ASSUMPTION A-2).
29. [ ] Same file — **poison-entry containment**: an entry that can never be delivered, with
        `OutboxRelay(retry_policy=…, max_attempts=…, dlq=…)`, must end up in the DLQ and be
        deleted so that entries queued *behind* it drain (the "poison outbox row silently stops
        a stream" pitfall in CLAUDE.md, verified against real infrastructure for the first
        time). Also assert `OutboxRelay(max_attempts=…)` **without** `dlq=` raises `ValueError`
        at construction.
30. [ ] `varco_redis/tests/test_breaker_chaos_integration.py` (new, marked) — `CircuitBreaker`
        against a **real** network failure rather than a raised mock: point a client at the Redis
        container, stop the container, assert the shared breaker transitions CLOSED → OPEN after
        `failure_threshold` real failures, that calls then fail fast (measurably faster than the
        connect timeout), then restart the container and assert HALF_OPEN → CLOSED recovery after
        `recovery_timeout`. Use a **shared** breaker instance (CLAUDE.md's per-call-breaker
        pitfall) and generous timing margins.
31. [ ] `varco_sa/tests/test_job_lease_chaos_integration.py` (new, marked) — simulated worker
        crash against real Postgres: worker A `try_claim(owner_id="a", lease_ttl=…)`, then A is
        killed (task cancelled / its session closed) without renewing; after
        `reap_expired_leases()` worker B claims the same job; when A "resumes" and calls
        `save(expected_epoch=<A's old epoch>)` it must raise `StaleLeaseError` and must **not**
        clobber B's result (CLAUDE.md's "stalled worker resumes and overwrites a completed
        result" pitfall).
32. [ ] `varco_beanie/tests/test_job_lease_chaos_integration.py` (new, marked) — the identical
        scenario against `BeanieJobStore`, because the whole point of RT6+RT7 together is that a
        guarantee proven on one backend is not assumed on another.

### Phase 8 — 🟡 RT8: full-stack example smoke run

33. [ ] `scripts/integration_tests.sh` — the current package loop assumes `$ROOT/$pkg/tests`
        (`:62-67`) and `cd $ROOT/$pkg && pytest tests/` (`:92-96`), which does not fit the example
        (rootdir `examples/00-full-stack-post-api`, testpath `example/tests`). Add a second,
        explicitly-declared list of extra suites as `"<dir>:<testpath>"` entries — first entry
        `"examples/00-full-stack-post-api:example/tests"` — validated and run by the same loop,
        reported in the same summary, honouring the same exit-5 rule. Keep
        `ALL_INTEGRATION_PACKAGES` (`:52`) untouched so a plain
        `scripts/integration_tests.sh varco_redis` still works.
34. [ ] `examples/00-full-stack-post-api/example/tests/` — no new test logic (the suite already
        works: 46 test functions, **35** integration-marked — C-6, correcting BACKLOG.md:45's
        "~34"). Only verify it still passes when invoked by the runner, and confirm the deps it
        needs come from the `examples` workspace member's dev group
        (`examples/pyproject.toml:8-15`: `testcontainers[postgres,redis,kafka,mongodb]`,
        `uvicorn`, `httpx`, `asyncpg`) rather than from
        `examples/00-full-stack-post-api/pyproject.toml`, which is **not** a workspace member
        (root `pyproject.toml:10-22` lists `"examples"`, not the sub-directory) and whose dev
        group therefore never installs. If anything is missing at runtime, add it to
        `examples/pyproject.toml`, not to the non-member file.

### Phase 9 — 🟡 RT9: migration lifecycle against a real database

35. [ ] `varco_fastapi/pyproject.toml` — add to the **dev group only** (`:82-94`):
        `"varco-sa"`, `"alembic>=1.13"`, `"asyncpg>=0.29"`, `"testcontainers[postgres]>=4.0"`,
        plus `varco-sa = { workspace = true }` under `[tool.uv.sources]`. This does **not**
        violate the "`varco_fastapi` imports only `varco_core.migration`" layer rule: that rule
        constrains `varco_fastapi/varco_fastapi/**` runtime imports and the published
        `[project] dependencies` (`:21-37`), neither of which changes. A dev-group entry is not
        a package dependency and never reaches the wheel.
36. [ ] `varco_fastapi/tests/test_app_migrations_integration.py` (new, marked) — the real-DB
        counterpart to today's `InMemoryMigrator`-only unit tests
        (`tests/test_app_migrations.py:24-79`): against a `PostgresContainer` with a real
        `AlembicMigrator`, (i) `mode="check"` on a behind schema fails startup and **serves no
        request** and writes **no DDL** (assert the table is still absent); (ii) `mode="upgrade"`
        applies revisions before the first request is served, and a second boot is a no-op;
        (iii) `on_failure="warn"` keeps serving on a failed migration while `"fail"` aborts;
        (iv) `mode="off"` (the default) registers nothing and touches nothing; (v) two
        `create_varco_app` lifespans started concurrently against one database — exactly one
        migrates, the other blocks on the held-open advisory-lock transaction and then proceeds
        (or raises `MigrationLockTimeout`), and the schema is not corrupted. Import
        `MigrationError`/`MigrationPlan` from `varco_core.migration`, never from `varco_core`
        (CLAUDE.md's name-collision pitfall).

### Phase 10 — docs

37. [ ] `CLAUDE.md` — extend the "Test Conventions" section with: the session-scoped-container +
        per-test-namespacing rule (Step 8), the `VARCO_TEST_*_URL` override contract and why
        bare `REDIS_URL`/`DATABASE_URL` are deliberately **not** honoured, the
        `testkit/varco_conformance` opt-in recipe (one `pythonpath` line + one subclass), and
        the rule that a conformance failure becomes a `strict=True` xfail + BACKLOG entry rather
        than an in-plan production fix.
38. [ ] `scripts/integration_tests.sh` header + `make help` — document `make integration-test`,
        `make integration-test PKG=varco_redis`, `make integration-test-clean`, and the extra
        suites list. State explicitly that **nothing here runs in CI by design**
        (BACKLOG.md:50-56).

---

## Edge cases

- **Docker absent** → `scripts/integration_tests.sh:37-44` already exits 1 with a legible
  message. Unchanged; the new example suite must not be reached before that check.
- **A package has zero integration tests** → pytest exit code 5, reported as `○ skipped`, not a
  failure (`:110-112`). The extra-suites loop (Step 33) must reuse this branch verbatim.
- **`VARCO_TEST_REDIS_URL` set to a dead endpoint** → the fixture must fail with a message
  naming the env var, never silently fall back to starting a container (a silent fallback would
  make the override untrustworthy in the opposite direction).
- **Session-scoped container + a test that mutates global broker state** (e.g. Redis `FLUSHALL`,
  dropping a Mongo database) → forbidden by Step 8's rule; such a test declares the explicit
  function-scoped fresh-container fixture instead.
- **`NoOpCache` under `CacheBackendConformance`** → set→get is legitimately a miss; handled by a
  dedicated subclass overriding those cases (Step 26), never by loosening the shared suite.
- **A stream-backed DLQ (`KafkaDLQ`, `NatsDLQ`, `RedisDLQ`) under `DeadLetterQueueConformance`**
  → `supports_random_access = False`, so `get()`/`list_entries()` must raise
  `DeadLetterNotAddressable`; the suite asserts the raise, and skips only the random-access
  tests — it never skips `push()`-never-raises.
- **A job store without lease support** → `renew()`/`reap_expired_leases()` are
  concrete-but-raising `NotImplementedError` on the ABC; the suite `pytest.skip`s exactly those
  tests and still runs claim/save/`delete_where` coverage.
- **Kafka rebalance timing** → seconds-scale and inherently jittery; widen sleeps per CLAUDE.md's
  convention ("increase its sleep margin rather than marking it xfail"), never `xfail`.
- **Two workspace packages' session containers alive at once** → they are not: the runner runs
  packages sequentially (`:79`), so peak container count is bounded by the widest single
  package (`varco_casbin`: Postgres + Mongo).
- **`testkit/` picked up by lint/type-check/build** → `Makefile`'s `_SRC_DIRS`/`_TARGETS` are
  package-derived and must not gain `testkit`; verify `make lint`/`make build` are unaffected.

## Verification

```bash
# Phase 0 — the latent-leak fix, proven in isolation
cd varco_sa && uv run python -c "import testcontainers.postgres, asyncpg; print('ok')"
cd varco_nats && uv run python -c "from testcontainers.nats import NatsContainer; print('ok')"
cd varco_memcached && uv run python -c "from testcontainers.memcached import MemcachedContainer; print('ok')"

# Unit suites must stay green throughout (no marker/fixture regressions)
make test

# Per-package integration loop while iterating
make integration-test PKG=varco_redis
make integration-test PKG=varco_ws
make integration-test PKG=varco_kafka

# The one-command goal, clean room (no VARCO_TEST_* overrides honoured)
make integration-test-clean

# Conformance suites, fast path (no Docker)
cd varco_core && uv run pytest tests/test_conformance_inmemory.py -v

# Static gates unchanged
make lint && make type-check

# Guard: the workflow file is still inert (must print 0)
rg -c '^[^#]' .github/workflows/integration.yml || echo 0
```

Acceptance: `make integration-test-clean` exits 0 with **nine** `✔` package lines plus the
example suite, **zero** `○ (no integration tests)` lines for `varco_ws` and `varco_nats`
(they have real coverage after Phases 2 and 5), and the summary contains no override warning.

## Risks

- **Session-scoped container reuse changes test isolation.** An existing test that implicitly
  relied on a pristine module-scoped server can start failing intermittently. Invariant that
  must hold: every test namespaces its own state (Step 8). Mitigation: migrate one package per
  commit (Steps 6-7) and run each package's suite three times consecutively before moving on.
- **Conformance suites will find real bugs.** That is the point, and it collides with the
  "testing only" constraint. Invariant: a violation becomes `xfail(strict=True)` + a BACKLOG
  entry; production code is not touched inside this plan.
- **RT7's broker-restart tests are the most likely source of flakiness** in the whole suite
  (container restart + reconnect backoff + relay poll interval all interact). Mitigation:
  generous, explicitly-commented timing margins; if a scenario cannot be made stable in a
  reasonable number of attempts, reduce its scope (e.g. assert "no entry lost" without
  asserting a bound on recovery latency) rather than deleting it or `xfail`ing it.
- **`varco_fastapi` dev-depending on `varco_sa` (Step 35) could be misread as a layer
  violation** by a future reader. Invariant: `[project] dependencies` and every runtime import
  under `varco_fastapi/varco_fastapi/` stay unchanged. Mitigation: a comment on the dev-group
  entry naming the rule and why a dev-group entry does not break it.
- ⚠️ **ASSUMPTION A-1**: adding the `[nats]` / `[memcached]` / `[postgres]` extras resolves
  cleanly against the existing `uv.lock`. The [testcontainers
  brief](../design/integration-testing/research/001-testcontainers-nats-memcached-support.md):11,37
  states both extras pull **zero** additional dependencies, but no `uv sync` was executed while
  writing this plan. If resolution conflicts, Step 5 is where it surfaces.
- ⚠️ **ASSUMPTION A-2**: testcontainers-python exposes a usable stop/restart handle for RT7
  (`container.get_wrapped_container()` → docker-py object). The reliability brief documents the
  *practice* of hard-killing brokers mid-test (`:15`) but not this Python API. If the handle is
  unavailable, fall back to the docker-py client directly (already a transitive testcontainers
  dependency) or to a Toxiproxy sidecar (brief:17 names Toxiproxy for simulated partitions;
  brief:54 flags that its framework-CI usage is undocumented).
- ⚠️ **ASSUMPTION A-3**: pytest's `pythonpath` ini key behaves as documented under each
  package's rootdir (relative to rootdir, applied before collection). It is built-in since
  pytest 7.0 and every package pins `pytest>=8.0`, but **no package in this repo uses it today**
  (`rg pythonpath */pyproject.toml` → no matches), so this is unexercised here. Step 26 is the
  cheapest place to prove it; if it misbehaves, the fallback is a `conftest.py`-level
  `sys.path.insert` in each package (uglier, but equivalent) — not a redesign of the suite shape.
- ⚠️ **ASSUMPTION A-4**: `varco_ws`'s buses can be driven by a real Starlette `WebSocket`
  without an adapter. The unit tests use a `MockWebSocket` (`tests/test_ws_bus.py:44`) and the
  bus classes (`websocket.py:355`, `sse.py:175`) were read only at the class-signature level for
  this plan. If a shim is required, it belongs in `varco_ws/tests/`, never in the package.
- ⚠️ **ASSUMPTION A-5**: the per-file breakdown of which `varco_nats`, `varco_kafka` and
  `varco_casbin` tests genuinely touch a real broker (vs. `fakes.py`/mocks) is based on
  file-level marker greps, not a read of every test body. Steps 13 and 14 begin with that audit;
  the item counts there may move.
- ⚠️ **ASSUMPTION A-6**: `examples/00-full-stack-post-api`'s suite currently passes. It was
  counted (46 functions / 35 marked) but not executed while writing this plan.
