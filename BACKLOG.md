# BACKLOG

Feature backlog produced by `/discover` (focus: reliability & regression-proofing via testing).

**Stated priority** (user, this session): make varco more reliable and regression-proof purely
through testing — more integration tests against real platforms, closing coverage gaps. No new
feature work.

**Research briefs backing this backlog:**

- `design/i18n-tz-framework/research/005-github-actions-integration-testing-multi-backend.md`
  — testcontainers-python vs GitHub Actions `services:` vs docker-compose for multi-broker CI;
  conformance/contract testing pattern precedent (SQLAlchemy, Celery, Motor).
- `design/reliability-release/research/001-reference-app-end-to-end-testing-patterns.md` —
  how reliability-focused frameworks (Temporal, Confluent, Celery) validate delivery/DLQ/
  circuit-breaker/lease-fencing guarantees; ROI of a dedicated reference app vs per-feature
  chaos tests.

---

## R3 — Reliability & regression-proofing (testing only, no new features)

Focus for this release (user, this session): make varco more reliable and regression-proof
purely through testing — more integration tests against real platforms, closing coverage
gaps, no feature work. **Local-only**: this is a one-developer open-source project with no
CI budget concerns to manage around, but the user runs integration tests on their own
schedule rather than gating every PR — so this release deliberately makes **no GitHub
Actions changes**. The goal is a reliable one-command local run (`make integration-test`),
not a CI gate.

Scout finding this release is built on: `.github/workflows/integration.yml` is already
entirely commented out and 28 `@pytest.mark.integration` tests exist locally but never run
automatically today — the previous CI-gating angle was explicitly declined in favor of
local-only tooling (see Parked).

| ID | Feature | Severity | Complexity | Rationale | Evidence |
|----|---------|----------|------------|-----------|----------|
| RT1 | Testcontainers-backed local integration runner — replace manually-provided broker env vars (`REDIS_URL`, `MONGODB_URL`, …) with `testcontainers-python` fixtures across Kafka/NATS/Redis/Postgres/Mongo/Memcached, driven by one `make integration-test` command | 🔴 must | M | The single biggest usability gap: a solo maintainer needs one reliable command with guaranteed cleanup, not per-backend manual Docker wrangling. Foundation every other RT item's tests run on top of. | [CI-patterns brief](design/i18n-tz-framework/research/005-github-actions-integration-testing-multi-backend.md) — testcontainers-python is the 2025–2026 consensus for multi-broker Python integration testing |
| RT2 | Mark & wire up `varco_nats` real-broker tests — add missing `@pytest.mark.integration` markers so existing real-NATS tests are picked up by `integration_tests.sh` | 🔴 must | S | Real NATS tests already exist and run against a broker, but carry no marker — invisible to the local integration script and the package matrix entirely. Pure gap-closing, no new test logic needed for the marking itself. | scout scan of `varco_nats/tests/` (9 files, 0 markers) |
| RT3 | `varco_casbin` + Postgres/SQLAlchemy adapter integration tests | 🔴 must | S/M | Only the Mongo adapter is integration-tested today; Postgres is CLAUDE.md's recommended durable adapter for production and is completely unverified against a real database. | scout scan (`2/8` casbin test files marked, both Beanie-only) |
| RT4 | `varco_ws` real integration tests — real WebSocket/SSE server: connection pooling, backpressure, reconnect, message ordering | 🔴 must | M | Zero integration coverage today on a shipped event bus backend; only mocked unit tests exist. | scout scan (`varco_ws/tests/` — 0 `@pytest.mark.integration`) |
| RT5 | Expand `varco_kafka` integration coverage — DLQ, consumer offset management, partition rebalancing against a real broker | 🔴 must | M | Kafka is a core event bus backend but only its health check is integration-tested; DLQ/offset/rebalancing behavior is verified against mocked `aiokafka` only. | scout scan (`1/9` kafka test files marked) |
| RT6 | Conformance/contract test suite — one parametrized suite per ABC (`AbstractEventBus`, `CacheBackend`, `AbstractJobStore`, `AbstractDeadLetterQueue`) run against every backend implementation | 🔴 must | L | Structurally guarantees every backend honors its contract, catching "works on Redis, breaks on Beanie" bugs by construction instead of by luck of which backend happened to get a test written. | [CI-patterns brief](design/i18n-tz-framework/research/005-github-actions-integration-testing-multi-backend.md) — named pattern (`pytest.mark.parametrize(indirect=True)` over backend fixtures) used by SQLAlchemy, Celery, Motor for exactly this problem shape |
| RT7 | Chaos / fault-injection tests for reliability primitives — outbox relay + broker restart, circuit breaker + real network failure, job lease + simulated worker crash | 🔴 must | L | Validates the guarantees varco actually sells (durable delivery, breaker opening under real failure, lease fencing surviving a crash) against real failure conditions, not mocks standing in for them. | [reliability brief](design/reliability-release/research/001-reference-app-end-to-end-testing-patterns.md) — Temporal/Confluent precedent for chaos/fault-injection over broker restarts and hard kills |
| RT8 | Local one-command smoke run of `examples/00-full-stack-post-api` — wire the existing full-stack example's 46 test functions (35 `@pytest.mark.integration`; event bus + auth + DB together) into the same local runner as a cross-feature smoke check | 🟡 should | S | Reuses the one example that already exercises multiple features together instead of building a new dedicated reference app — the reliability brief found no ROI advantage to a new monolithic app over per-feature tests. | [reliability brief](design/reliability-release/research/001-reference-app-end-to-end-testing-patterns.md) — explicit anti-recommendation for a new reference app; user decision this session to reuse the existing example |
| RT9 | Migration lifecycle integration tests — `create_varco_app(migrations=...)` startup path (Alembic/`BeanieMigrator`) against a real database | 🟡 should | S | Startup migration is a safety-critical path (locking, `check`/`upgrade` modes) currently only unit-tested; a real-DB regression here breaks every app at boot. | scout scan (`test_app_migrations.py` — unit-only) |

---

## Known issues found while implementing Plan 012 (xfail'd, not fixed — Non-goals)

| ID | Finding | Evidence |
|----|---------|----------|
| KI-3 | ✅ **Fixed** — `RedisCache.set(ttl=)` (`varco_redis/varco_redis/cache.py`) truncated a sub-second float `ttl` to `int()` before calling `SETEX` — `ttl=0.05` became `0`, and Redis's `SETEX` rejects a `0`/negative expire time with `ResponseError: invalid expire time in 'setex' command`, raising instead of storing a very-short-lived entry. `CacheBackend.set()`'s `ttl: float \| None` contract implies sub-second precision is valid. Fixed by switching `set()`/`set_many()` to millisecond-precision `PSETEX` (`round(ttl * 1000)`) instead of second-precision `SETEX`/`int(ttl)`; a ttl that still rounds to `<=0`ms now raises a clear `ValueError` instead of Redis's cryptic `ResponseError`. `TestLayeredCacheConformance::test_ttl_expiry` (L1 `InMemoryCache` + real Redis L2) inherited the same symptom but for an unrelated second reason — its fixture built L1 with no `InvalidationStrategy`, and per `InMemoryCache`'s own documented contract a strategy-less L1 never expires a ttl-bearing entry on its own; fixed by giving the fixture's L1 a `TTLStrategy()`. | `varco_redis/tests/test_redis_conformance.py::TestRedisCacheConformance::test_ttl_expiry` and `::TestLayeredCacheConformance::test_ttl_expiry` (previously `xfail(strict=True)`, now pass), found by the shared `varco_conformance.cache.CacheBackendConformance` suite against a real Redis instance |
| KI-5 | ✅ **Fixed** — `MemcachedCache.set(ttl=)` (`varco_memcached/varco_memcached/cache.py`) truncated a sub-second float `ttl` to `int()` before passing it as `exptime` — `ttl=0.05` became `exptime=0`, which the Memcached protocol treats as "no expiry" rather than "expire almost immediately"; the entry was never evicted. Same root cause as KI-3 (`RedisCache`), different failure mode (silent no-expiry instead of a raised error). Unlike KI-3, Memcached's `exptime` is genuinely whole-seconds-only at the wire-protocol level — there is no millisecond-precision command to switch to (Redis's `PSETEX` fix does not apply here). Fixed by rounding a positive sub-second `ttl` UP to the smallest expressible non-zero `exptime` (`1`) via `math.ceil()`, instead of truncating DOWN to `0` — an explicit `ttl<=0`/`ttl=None` still means no-expiry, unchanged. The shared conformance suite's `ttl=0.05`/`sleep(0.3)` timing cannot observe a real 1-second-granularity expiry, so `TestMemcachedCacheConformance.test_ttl_expiry` overrides the shared test with timing compatible with that real granularity (`sleep(1.3)`) rather than loosening the shared suite for every other backend. | `varco_memcached/tests/test_memcached_conformance.py::TestMemcachedCacheConformance::test_ttl_expiry` (previously `xfail(strict=True)`, now passes with an overridden timing window), found by the shared `varco_conformance.cache.CacheBackendConformance` suite against a real Memcached instance; hardened with 3 new unit tests in `varco_memcached/tests/test_cache.py` (sub-second round-up, fractional-above-1s round-up, explicit `ttl=0` still no-expiry) |
| KI-6 | ✅ **Fixed** — `BeanieDeadLetterQueue.count_by_channel()` (`varco_beanie/varco_beanie/dlq.py`) did `await DeadLetterDocument.aggregate(pipeline).to_list()`. Root cause: beanie's `AggregationQuery.get_cursor()` unconditionally `await`s the collection's `aggregate()` call, but the installed motor version's `AsyncIOMotorCollection.aggregate()` returns its cursor synchronously (not a coroutine) — `TypeError: object AsyncIOMotorLatentCommandCursor can't be used in 'await' expression`. Fixed by driving `DeadLetterDocument.get_pymongo_collection().aggregate(pipeline)` directly (bypassing beanie's broken cursor plumbing) and iterating with `async for`, tolerating both a sync-cursor and a coroutine-returning `aggregate()`. | `varco_beanie/tests/test_beanie_conformance.py::TestBeanieDeadLetterQueueConformance::test_count_by_channel_no_predicate_refuses_or_raises` (previously `xfail(strict=True)`, now passes), found by the shared `varco_conformance.dlq.DeadLetterQueueConformance` suite against a real MongoDB instance |
| KI-7 | ✅ **Fixed** — `NatsDLQ.delete_where()` (`varco_nats/varco_nats/dlq.py`) always raised `NotImplementedError`, even when called with **no predicate at all** — same class of deviation as KI-2 (`KafkaDLQ`) from the `AbstractDeadLetterQueue` ABC's documented "no predicate -> `ValueError`" contract (`varco_core/varco_core/event/dlq.py:440-489`). Root cause: `delete_where()` jumped straight to the backend-support `NotImplementedError`, never reaching the ABC's own "was any predicate given at all?" guard. Fixed by adding that guard as the first check in `NatsDLQ.delete_where()`, matching its own full keyword-only signature (`older_than`/`source`/`channel`/`tenant_id`/`limit`) instead of a catch-all `**_kwargs`, so an unbounded call raises `ValueError` before the backend-support `NotImplementedError`. | `varco_nats/tests/test_nats_conformance.py::TestNatsDLQConformance::test_delete_where_no_predicate_raises` (previously `xfail(strict=True)`, now passes), found by the shared `varco_conformance.dlq.DeadLetterQueueConformance` suite against a real NATS/JetStream broker |
| KI-8 | ✅ **Fixed** — `CasbinPolicyEngine.enforce()` (`varco_casbin/varco_casbin/engine.py`) always wraps subject/object in `_AttrStr` (a `str` subclass with a custom `__new__(cls, value, attrs)`), even for the plain RBAC preset. `_AttrStr` had no `__deepcopy__`/`__reduce__`; once one had been threaded into Casbin's internal role-manager/model state via an `enforce()` call, a later `CasbinPolicyEngine.reload()` (-> Casbin's `load_policy()` -> `copy.deepcopy(self.model)`) raised `TypeError: _AttrStr.__new__() missing 1 required positional argument: 'attrs'` — `copy.deepcopy`'s default reconstruction for a `str` subclass calls `cls(value)` only, never the extra `attrs` kwarg the custom `__new__` requires. Fixed by adding `_AttrStr.__reduce__`, which stashes the original `attrs` mapping in `__new__` and returns `(cls, (str(self), self._attrs))` so `deepcopy`/`pickle` reconstruct through the real constructor instead of the broken default path. Chosen over the alternative (only wrap in `_AttrStr` for ABAC-configured engines) because it fixes the actual `str`-subclass/`deepcopy` incompatibility at its root without adding preset-conditional branching to `enforce()`, and preserves the existing "one engine serves ACL/RBAC/ABAC uniformly" design the module docstring describes. | `varco_casbin/tests/test_persistence_integration.py::test_two_engines_share_database_writer_reader` (previously `xfail(strict=True)`, now passes), verified against a real Postgres-backed `CasbinPolicyEngine`; full `varco_casbin/tests/` suite (67 tests, unit + `-m integration`, including ABAC tests in `test_abac_e2e.py`) green |

### Example suite findings (Plan 012 / RT8, Step 34 — corrected in test files, no production code touched)

Running `examples/00-full-stack-post-api`'s real integration suite for the first time (C-6/A-6
had never actually executed it) surfaced one missing-config issue and two stale test
expectations, all fixed inside `examples/00-full-stack-post-api/example/tests/` (a permitted
path):

- `example/app.py` constructs `JwtBearerAuth(registry=registry, required=False)` with no
  `audience=` — since Plan 005 Phase 2, `JwtBearerAuth()` refuses to construct without an
  audience configured (`ValueError`). Fixed by setting `VARCO_JWT_ALLOW_ANY_AUDIENCE=true` in
  `example/tests/conftest.py`'s `running_server` fixture (a demo app has no single-audience
  concept to enforce).
- `test_me_with_garbage_token_is_anonymous` asserted a *present* malformed Bearer token falls
  back to anonymous — `JwtBearerAuth.__call__`'s own docstring documents `required=False` only
  covers an *absent* Authorization header; a present-but-invalid token always raises 401.
  Renamed to `test_me_with_garbage_token_returns_401` and corrected the assertion.
- `test_anonymous_cannot_create_post_returns_403` asserted anonymous `POST /v1/posts` is
  rejected — `example/authorizer.py`'s own docstring documents anonymous CREATE as allowed
  (`author_id=None`); only anonymous UPDATE/DELETE are rejected. Renamed to
  `test_anonymous_can_create_post_with_null_author` and corrected the assertion.

---

## Deferred follow-ups (Plan 014 / audit 001 Batch B)

- **`weakref.WeakSet[FastAPI]` upgrade for the double-mount guards** — both `varco_fastapi.tenancy.mount._MOUNTED_APPS` and `varco_fastapi.admin.mount._MOUNTED_APPS` are `set[int]` keyed by `id(app)`, which can produce a spurious `ValueError` if a collected `FastAPI` instance's id is reused by a new, unrelated app; deliberately not fixed in Plan 014 to keep `mount_reliability_admin()`'s guard shape-identical to the `mount_tenant_admin()` reference it was ported from — should change *both* modules together in one follow-up.
- **`varco_redis.di.async_bootstrap()` is missing the `container is None` guard `varco_memcached.di.async_bootstrap()` has** — when providify is absent, `bootstrap()` returns `None` and the subsequent `await container.ainstall(RedisCacheConfiguration)` (when `setup_cache=True`) raises `AttributeError: 'NoneType' object has no attribute 'ainstall'` instead of returning `None` like every other varco `async_bootstrap()`.

---

## Deferred follow-ups (Plan 015 / audit 002)

- **F12 — `## Test Conventions` prose density (RT1/RT6 paragraphs)** — the audit flagged this as
  "a judgment call, not a clear misplacement," and Plan 015 explicitly left it untouched
  (`## Test Conventions` in `CLAUDE.md` is byte-identical to before the refactor). Revisit in a
  future pass if the section keeps growing.

---

## Parked

| Feature | Why parked |
|---------|------------|
| **Re-enable `integration.yml` in GitHub Actions** (as a PR gate) | Declined this session: no CI budget concern, but the user runs integration tests on their own schedule locally rather than gating PRs. |
| **Re-enable `integration.yml` as manual/nightly trigger** | Also declined: user chose local-only tooling over any GitHub Actions involvement, even non-blocking. |
| **New dedicated e2e reference application** | Research found no comparable reliability-focused framework uses a monolithic reference app as its primary regression strategy — per-feature chaos tests (RT7) score better, and an existing example already covers the cross-feature case (RT8). |

---

## Open questions for `/plan`

- **RT1 testcontainers scope**: whether to keep an env-var override path for developers who
  already have brokers running locally (faster inner loop) alongside the testcontainers default,
  or require testcontainers unconditionally for consistency.
- **RT6 conformance suite shape**: exact fixture parametrization strategy — one shared
  `tests/conformance/` package imported by each backend's test suite, vs. a fixture registry
  each backend opts into.
