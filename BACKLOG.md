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
| KI-1 | `KafkaEventBus`'s default settings (`enable_auto_commit=True`) rely on aiokafka's own periodic auto-commit, which advances the committed offset on a timer independent of handler success/failure — **not** "committed after successful dispatch" as `varco_kafka/varco_kafka/config.py:76-77` documents for the default `AT_LEAST_ONCE` guarantee. A message whose `@listen` handler raises is not redelivered to a fresh consumer in the same `group_id`; the offset silently advances past it. | `varco_kafka/tests/test_kafka_offsets_integration.py::test_redelivery_after_failed_handler_does_not_silently_advance_offset` (`xfail(strict=True)`), verified against a real Kafka broker (Plan 012 Step 16) |
| KI-2 | `KafkaDLQ.delete_where()` (`varco_kafka/varco_kafka/dlq.py:519-531`) always raises `NotImplementedError`, even when called with **no predicate at all** — it never reaches the "no predicate -> `ValueError`" check the `AbstractDeadLetterQueue` ABC documents (`varco_core/varco_core/event/dlq.py:440-489`). Every other backend's `delete_where()` refuses a no-predicate call with `ValueError` before checking whether it supports the operation at all. | `varco_kafka/tests/test_kafka_conformance.py::TestKafkaDLQConformance::test_delete_where_no_predicate_raises` (`xfail(strict=True)`), found by the shared `varco_conformance.dlq.DeadLetterQueueConformance` suite (Plan 012 Step 27) against a real Kafka broker |
| KI-3 | `RedisCache.set(ttl=)` (`varco_redis/varco_redis/cache.py:283-290`) truncates a sub-second float `ttl` to `int()` before calling `SETEX` — `ttl=0.05` becomes `0`, and Redis's `SETEX` rejects a `0`/negative expire time with `ResponseError: invalid expire time in 'setex' command`, raising instead of storing a very-short-lived entry. `CacheBackend.set()`'s `ttl: float \| None` contract implies sub-second precision is valid. | `varco_redis/tests/test_redis_conformance.py::TestRedisCacheConformance::test_ttl_expiry` (`xfail(strict=True)`), found by the shared `varco_conformance.cache.CacheBackendConformance` suite against a real Redis instance |
| KI-4 | `RedisJobStore.try_claim()` grants a non-zero `lease_epoch` (advertising lease support), but `RedisJobStore.save()` has no `expected_epoch=` parameter at all — calling it raises `TypeError: unexpected keyword argument 'expected_epoch'` instead of fencing a stale write with `StaleLeaseError`, as `AbstractJobStore.save()` documents. | `varco_redis/tests/test_redis_conformance.py::TestRedisJobStoreConformance::test_save_with_stale_expected_epoch_raises` (`xfail(strict=True)`), found by the shared `varco_conformance.job_store.JobStoreConformance` suite against a real Redis instance |
| KI-5 | `MemcachedCache.set(ttl=)` (`varco_memcached/varco_memcached/cache.py:343`) truncates a sub-second float `ttl` to `int()` before passing it as `exptime` — `ttl=0.05` becomes `exptime=0`, which the Memcached protocol treats as "no expiry" rather than "expire almost immediately"; the entry is never evicted. Same root cause as KI-3 (`RedisCache`), different failure mode (silent no-expiry instead of a raised error). | `varco_memcached/tests/test_memcached_conformance.py::TestMemcachedCacheConformance::test_ttl_expiry` (`xfail(strict=True)`), found by the shared `varco_conformance.cache.CacheBackendConformance` suite against a real Memcached instance |
| KI-6 | `BeanieDeadLetterQueue.count_by_channel()` (`varco_beanie/varco_beanie/dlq.py:316`) does `await DeadLetterDocument.aggregate(pipeline).to_list()`, which under the installed beanie/motor version combination raises `TypeError: object AsyncIOMotorLatentCommandCursor can't be used in 'await' expression` — beanie's `to_list()` now returns the pymongo `aggregate()` cursor directly rather than a coroutine. | `varco_beanie/tests/test_beanie_conformance.py::TestBeanieDeadLetterQueueConformance::test_count_by_channel_no_predicate_refuses_or_raises` (`xfail(strict=True)`), found by the shared `varco_conformance.dlq.DeadLetterQueueConformance` suite against a real MongoDB instance |
| KI-7 | `NatsDLQ.delete_where()` (`varco_nats/varco_nats/dlq.py:504-516`) always raises `NotImplementedError`, even when called with **no predicate at all** — same class of deviation as KI-2 (`KafkaDLQ`) from the `AbstractDeadLetterQueue` ABC's documented "no predicate -> `ValueError`" contract (`varco_core/varco_core/event/dlq.py:440-489`). | `varco_nats/tests/test_nats_conformance.py::TestNatsDLQConformance::test_delete_where_no_predicate_raises` (`xfail(strict=True)`), found by the shared `varco_conformance.dlq.DeadLetterQueueConformance` suite against a real NATS/JetStream broker |
| KI-8 | `CasbinPolicyEngine.enforce()` (`varco_casbin/varco_casbin/engine.py`) always wraps subject/object in `_AttrStr` (a `str` subclass with a custom `__new__(cls, value, attrs)`), even for the plain RBAC preset. `_AttrStr` has no `__deepcopy__`/`__reduce__`; once one has been threaded into Casbin's internal role-manager/model state via an `enforce()` call, a later `CasbinPolicyEngine.reload()` (-> Casbin's `load_policy()` -> `copy.deepcopy(self.model)`) raises `TypeError: _AttrStr.__new__() missing 1 required positional argument: 'attrs'`. | `varco_casbin/tests/test_persistence_integration.py::test_two_engines_share_database_writer_reader` (`xfail(strict=True)`), verified against a real Postgres-backed `CasbinPolicyEngine` |

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
