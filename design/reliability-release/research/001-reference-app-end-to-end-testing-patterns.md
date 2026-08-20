# Research 001 — Reference Application as Regression Test for Reliability Framework

Date: 2026-08-20 · Freshness matters: yes — testing patterns and framework practices are active, but the evidence here is stable (architectural patterns not moving fast).

## Question

For a reliability-focused framework release (validating outbox pattern, DLQ redrive, circuit breaker fencing, job lease survival, exactly-once delivery), should varco build a "reference application" that exercises the whole stack end-to-end as part of CI, or rely on per-feature integration tests? What patterns do comparable frameworks use?

## Findings

### Testing Patterns in Reliability Frameworks

**Temporal's approach**: Majority of tests should be **integration tests** (not unit), using the test SDK to run real Workflow + Activity + Server setups. The test framework supports time-skipping to accelerate long-running workflow testing. — [Testing - Python SDK | Temporal Platform Documentation](https://docs.temporal.io/develop/python/testing-suite) (official)

**Confluent/Kafka exactly-once validation**: Conducts **distributed chaos tests** — "bring up a full Kafka cluster with multiple transactional clients, produce messages transactionally, read messages concurrently, and hard kill clients and servers during the process to ensure data is neither lost nor duplicated." Wrote 15,000+ lines of test code including distributed tests under real failures, ran them nightly for weeks. — [Exactly-Once Semantics are Possible: Here's How Apache Kafka Does it](https://www.confluent.io/blog/exactly-once-semantics-are-possible-heres-how-apache-kafka-does-it/) (official Confluent, 2026)

**Failure injection as standard**: Chaos engineering (tools: Gremlin, LitmusChaos) deliberately injects broker outages, latency spikes, message corruption, node/container restarts. Network-level failure simulation via Toxiproxy (simulated partitions). — [Chaos Engineering for Kafka | Conduktor](https://www.conduktor.io/glossary/chaos-engineering-for-kafka) (2025); [Chaos Engineering: Build Resilient Systems Through Failure](https://talent500.com/blog/chaos-engineering-resilient-systems/) (2026); [Microsoft Fault Injection Testing](https://microsoft.github.io/code-with-engineering-playbook/automated-testing/fault-injection-testing/) (official)

**Celery DLQ testing**: Best practice is to classify exceptions as transient (retry with exponential backoff: 30s–60m intervals) vs. permanent (dead-letter immediately, alert). Integration tests verify DLQ routing across failure scenarios. Celery signals (task_failure, task_unknown) used to hook test validators. — [Advanced Celery: Monitoring, Priority Queues, Dead Letter Queues, and Scaling Workers](https://medium.com/@sumanb1720/advanced-celery-monitoring-priority-queues-dead-letter-queues-and-scaling-workers-405c94ba33dd) (Mar 2026); [Celery Task Routing & Error Handling](https://usmanasifbutt.github.io/blog/2025/03/13/celery-task-routing-and-retries.html) (2025)

**Kafka idempotent consumer testing**: Unit tests for successful commits + failure modes (rebalances, timeouts). Integration tests verify message redelivery and duplicate handling. Requires mocking or real broker for full validation. — [Achieving Exactly-Once Semantics in Kafka](https://medium.com/@anil.goyal0057/achieving-exactly-once-semantics-in-kafka-producer-consumer-idempotency-abad50cba95c) (2026)

### Reference Applications in Framework CI

**Temporal SDK practice**: Ships separate **sample repositories** (samples-go, samples-java, samples-python) with runnable examples. Also maintains a `features` repository ("behavior and history compatibility testing for Temporal SDKs") that runs cross-SDK feature tests in CI. For PR validation, prior Workflow Event Histories are replayed and CI fails if replay errors occur. — [GitHub: temporalio/samples-go](https://github.com/temporalio/samples-go), [GitHub: temporalio/features](https://github.com/temporalio/features) (official Temporal org); [Testing - Java SDK](https://docs.temporal.io/develop/java/testing-suite) (2025)

**FastAPI framework practice**: Test suite runs against **every code example** in the `docs_src/` directory. Tests live alongside code in the framework repo itself (`tests/` directory), executed via `uv run bash scripts/test-cov.sh` in CI. — [FastAPI Test Framework and Coverage | DeepWiki](https://deepwiki.com/fastapi/fastapi/5.1-test-framework-and-coverage) (2025); [Testing - FastAPI](https://fastapi.tiangolo.com/tutorial/testing/) (official)

**Celery + FastAPI integration**: Multiple standalone example repositories exist on GitHub (fastapi-celery, docker-fastapi-celery, fastapi-celery-template) but are **not** run in the main framework CI as regression tests. They serve educational/reference purposes. — [GitHub: GregaVrbancic/fastapi-celery](https://github.com/GregaVrbancic/fastapi-celery); [GitHub: jitendrasinghiitg/docker-fastapi-celery](https://github.com/jitendrasinghiitg/docker-fastapi-celery)

**Regression testing CI practice**: Standard approach is structured test organization (unit → integration → e2e), run critical-path/smoke tests on every change, schedule complete suites overnight, parallelize to minimize total execution time. No evidence of a single "monolithic reference app" as the primary regression strategy. — [CircleCI: Regression testing and how to automate it](https://circleci.com/blog/regression-testing-and-how-to-automate-it-with-ci/) (2025); [Harness: Regression Testing in CI/CD](https://www.harness.io/blog/regression-testing-in-cicd-deliver-faster-without-the-fear) (2025)

## Options compared

| Option | ✅ Strengths | ❌ Weaknesses | Evidence |
|---|---|---|---|
| **Per-feature integration tests** (default, current varco pattern) | Fast CI (each feature tested in isolation). Easier to debug failures (one test = one feature). Easier to onboard (test lives next to code). Parallelize efficiently. **This is what Temporal, Kafka, Celery do.** | May miss cross-component interactions (e.g., cache + job lease race). Hard to test emergent failure modes. Requires discipline to write chaos tests at the right granularity. | Temporal recommends "majority integration tests"; Confluent used 15,000 LOC distributed tests, not a reference app. FastAPI tests docs_src examples, not a monolithic app. |
| **Dedicated reference application** (separate test harness) | Catches emergent failures (outbox delay + cache staleness + job lease expiry under load). Exercises full request path and all middleware. Good for capturing regression stories ("when X + Y + Z coincide, Z fails"). Can run chaos scenarios on real app. | Slow feedback loop (full app startup, all backends). Harder to isolate failure cause (one test failure = which component?). Harder to maintain (must evolve in lockstep with framework). High cart-before-horse risk (adds burden to a purely reliability-focused release). | No well-known framework ships a monolithic reference app in CI as primary regression strategy. Temporal has separate sample repos. FastAPI tests examples, not a single integrated app. |
| **Hybrid: per-feature tests + selective chaos scenarios** (Confluent/Kafka pattern) | Combines isolation (per-feature) with distributed-failure validation (chaos on key paths). Validates exactly-once guarantees without full app overhead. Matches what Kafka/Confluent actually do. | Still requires writing chaos scenarios explicitly (not automatic). Needs testcontainers + broker restart orchestration. | Confluent: 15,000 LOC tests, distributed chaos, hard kills, nightly runs. Temporal: test SDK with time skipping. Microsoft: fault injection playbook. |

## Version/compatibility notes

- Temporal Test SDK docs: stable (Python 1.2+, Java 1.20+, Go 1.26+)
- Confluent Kafka exactly-once: available since Kafka 0.11 (2016), mature
- Chaos engineering tooling (Gremlin, LitmusChaos): 2023+, still active/evolving
- TestDriven.io FastAPI + Celery course: 2025, current
- Microsoft fault-injection playbook: 2025, current

## Evidence gaps

- No direct evidence of what other Python reliability/async libraries (e.g., AioKafka integration, RabbitMQ client) do for their own regression testing
- No quantitative ROI comparison (e.g., "per-feature tests catch X% of bugs; reference apps catch Y% of additional bugs")
- No specific guidance on whether a reference app should live in the framework repo or a separate examples repo
- Toxiproxy usage is mentioned in theory but not documented as part of a specific framework's CI (it may be internal-only practice at some organizations)

## Librarian's note

**The evidence strongly favors per-feature integration tests over a monolithic reference application for this release.**

Temporal, Kafka/Confluent, and FastAPI all validate reliability guarantees via **integration tests per feature**, not a single reference app. Confluent's approach is the most thorough: distributed chaos tests with real broker failure injection (15,000+ lines of focused test code, not a reference app).

**For varco's reliability release**: Write chaos/failure-injection scenarios **within each feature's test suite** (outbox pattern + broker restart, circuit breaker + connection failure, job lease + worker crash). Use testcontainers to orchestrate real brokers/DB in those tests. This is faster CI feedback, easier debugging, and matches proven practice.

A dedicated reference app adds burden without proportional evidence of bug detection — defer it until there's a specific regression story it solves (e.g., "cache coherence + tenant isolation + job lease interaction broke in 2.x").

