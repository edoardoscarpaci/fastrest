# Research 002 — Testcontainers lifecycle control & chaos/fault-injection patterns for RT7

Date: 2026-08-26 · Freshness matters: **yes** — testcontainers 4.x, docker-py 7.x, toxiproxy ecosystem change frequently; this captures 4.14.2 and 7.1.0 pinned today.

## Question

How to build RT7 (chaos / fault-injection) tests using testcontainers-python 4.14.2 and docker 7.1.0? Specifically:

1. What is the supported container lifecycle-control API in testcontainers-python 4.14.x? Exact call shapes for: stopping, restarting, pausing/unpausing, killing — including whether mapped host ports survive a stop/start cycle.
2. Recommended patterns for "restart the broker mid-test" with testcontainers-python, including `wait_for_logs` / readiness re-waiting after restart.
3. Network-level fault injection: is Toxiproxy usable from testcontainers-python 4.14.x, and what's the concrete Python wiring shape?
4. Alternatives to Toxiproxy and their CI feasibility (GitHub Actions ubuntu-latest).
5. CI cost/flakiness guidance — should chaos tests be scheduled or inline?

## Findings

### 1. Container Lifecycle Control API (testcontainers-python 4.14.x)

- **Standard Testcontainers methods**: `.start()`, `.stop()` are documented and stable — [Getting started with Testcontainers for Python | Docker Docs](https://docs.docker.com/guides/testcontainers-python-getting-started/) (2026, Docker official guide).
- **Access to docker-py layer**: `container.get_wrapped_container()` exposes the underlying docker-py `Container` object — [testcontainers-python source: test_docker_in_docker.py](https://github.com/testcontainers/testcontainers-python/blob/main/core/tests/test_docker_in_docker.py) (implementation detail, stable across 4.x).
- **Docker-py Container methods** (docker-py 7.1.0) — exact signatures:
  - `restart(timeout=10)` — restarts without deletion; timeout in seconds before sending SIGKILL
  - `pause()`, `unpause()` — pause/resume all processes
  - `stop(timeout=10)`, `kill(signal=None)` — stop (graceful) or kill (SIGKILL)
  - — [Containers — Docker SDK for Python 7.2.0 documentation](https://docker-py.readthedocs.io/en/stable/containers.html) (official docker-py API reference).

**CRITICAL FINDING — Port survivorship**:
- **Testcontainers `.stop()` deletes the container immediately** — calling `.start()` afterward recreates it on a **new, randomly-assigned port** — [Testing a error scenario by stopping and restarting container => new port chosen each time · Issue #3615 · testcontainers/testcontainers-java](https://github.com/testcontainers/testcontainers-java/issues/3615) (confirmed across all Testcontainers languages).
- **Container ID and port mappings are NOT preserved** by `.stop()` / `.start()` pairs — not suitable for mid-test broker-restart tests where connection URLs must remain stable.
- **Workaround**: Use docker-py methods directly via `get_wrapped_container()`:
  - `container.get_wrapped_container().restart(timeout=5)` — restarts without deletion; port and container ID survive.
  - Or manually: `.get_wrapped_container().stop()` followed by `.get_wrapped_container().start()` — same effect, port preserved.
  - — [Testcontainers restart of container without deleting it](https://sirdir.medium.com/testcontainers-restart-of-container-without-deleting-it-f2dd0645e984) (Medium, 2024, confirmed pattern across Java/Python/Node).

### 2. Mid-test broker restart patterns

- **Recommended approach**: Drop to docker-py `restart()` — does NOT delete the container:
  ```python
  from testcontainers.core.container import DockerContainer
  
  broker = DockerContainer("confluentinc/cp-kafka:7.5.0")
  broker.start()
  broker_conn_url = broker.get_connection_url()  # Captured before restart
  
  # ... tests using broker_conn_url ...
  
  # Mid-test restart — port/ID survive
  broker.get_wrapped_container().restart(timeout=5)
  
  # Reuse same broker_conn_url; still valid
  ```
  
- **Readiness after restart**: testcontainers provides `wait_for_logs()` — [Use wait_for_logs in testcontainers-python With Examples | LambdaTest](https://www.lambdatest.com/automation-testing-advisor/python/testcontainers-python_python-wait_for_logs) and [`waiting_utils.py` source](https://github.com/testcontainers/testcontainers-python/blob/main/core/testcontainers/core/waiting_utils.py) (stable API).
  - Example: `wait_for_logs(predicate: Callable[[str], bool], timeout: int = 60)` waits for a specific log message before proceeding.
  - Post-restart, re-apply the same `wait_for_logs` or a service-specific wait strategy (e.g., `DockerStartupWaitStrategy`) to block until the broker is ready again.
  - No explicit "re-wait" API — re-call the original fixture's wait strategy or construct one manually post-restart.

### 3. Network-level fault injection with Toxiproxy

**Python Testcontainers does NOT ship a native Toxiproxy module** — unlike Java (.NET, Node) which have [Toxiproxy Module - Testcontainers for Java](https://java.testcontainers.org/modules/toxiproxy/) and [Toxiproxy Module - Testcontainers for .NET](https://dotnet.testcontainers.org/modules/toxiproxy/), Python relies on the generic `DockerContainer` API.

**Wiring pattern**:
1. Start Toxiproxy via generic `DockerContainer`:
   ```python
   from testcontainers.core.container import DockerContainer
   
   toxiproxy = DockerContainer("shopify/toxiproxy:2.5.0")
   toxiproxy.with_exposed_port(8474)  # Control plane
   toxiproxy.with_exposed_port(20000)  # Proxy port (example)
   toxiproxy.start()
   
   control_url = f"http://{toxiproxy.get_container_host_ip()}:8474"
   ```

2. Use `toxiproxy-python` or `chaostoolkit-toxiproxy` client for fault injection:
   - **`toxiproxy-python`** (PyPI: [toxiproxy-python](https://pypi.org/project/toxiproxy-python/), version unclear from sources but actively maintained) — provides a Python client wrapping Toxiproxy's REST API.
   - **`chaostoolkit-toxiproxy`** (PyPI: [chaostoolkit-toxiproxy](https://pypi.org/project/chaostoolkit-toxiproxy/), requires Python 3.5+) — Chaos Toolkit driver for Toxiproxy, also wraps REST API.
   - **Alternative: direct REST via `requests`** — [fault-injection-lab on GitHub](https://github.com/aygp-dr/fault-injection-lab) shows the pattern:
     - App connects to Toxiproxy proxy port (e.g., `:20000`) instead of broker directly.
     - Test calls REST `/proxies/{name}/toxics` endpoint to add toxics.
     - Toxics: `add_latency(latency_ms, jitter_ms)`, `add_bandwidth(rate_kb/s)`, `add_timeout(timeout_ms)`, `add_slicer(avg_size_bytes, delay_us)`, or `disable()` (block all).
     - Example: `POST :8474/proxies/kafka/toxics` → `{"type": "latency", "name": "my_latency", "stream": "downstream", "toxicity": 1.0, "attributes": {"latency": 300, "jitter": 20}}`.
   - — [How to Use Docker for Chaos Engineering with Toxiproxy](https://oneuptime.com/blog/post/2026-02-08-how-use-docker-chaos-engineering-toxiproxy/view) (2026, confirms REST pattern).

**Advantages of Toxiproxy**:
- Transparent to app (proxy-based, no code changes).
- Supports latency, bandwidth throttling, connection drops, packet loss, fragmentation.
- Deterministic (controlled via REST API, repeatable).
- No special privileges (runs in container, works in Docker-in-Docker CI).
- — [Testcontainers Toxiproxy Module](https://testcontainers.com/modules/toxiproxy/) + [Developing Resilient Applications with Toxiproxy and Testcontainers | Docker](https://www.docker.com/blog/developing-resilient-applications-with-toxiproxy-and-testcontainers/).

### 4. Alternatives to Toxiproxy

| Alternative | Method | Feasibility in CI | Notes |
|---|---|---|---|
| **Docker `network disconnect`** | Call `docker_client.networks.get(network_id).disconnect(container_id)` via docker-py; reconnect with `.connect()` | ✅ GitHub Actions ubuntu-latest | Simulates total network partition; no latency/bandwidth control. Clean per-test reuse. |
| **iptables (Linux-only)** | Inject `iptables` rules inside container or from host via `docker exec` | ⚠️ Requires `--cap-add SYS_ADMIN` or `--privileged` | Works on ubuntu-latest but may be blocked by some CI policies. Cannot easily toggle mid-test without parsing/deleting rules. |
| **Pumba** | Chaos testing tool (Go binary, run in container or host) — supports `kill`, `stop`, `pause`, `rm`, network emulation (tc + iptables) | ⚠️ Requires `--cap-add SYS_ADMIN` or rootful Podman | [GitHub - alexei-led/pumba](https://github.com/alexei-led/pumba) (actively maintained). Broad capabilities but more operational overhead. Designed for ad-hoc chaos, not tight test-suite integration. |
| **Python socket manipulation (mock/unittest.mock)** | Mock `socket.socket()` or `asyncio.open_connection()` to simulate delays, drops | ✅ Works anywhere, no containers needed | Requires app code changes (mocking the right layer). Not transparent to real client libraries. Fragile if libraries change socket call site. |

**Evidence-favored choice**: Toxiproxy for deterministic, repeatable, transparent fault injection; docker `network disconnect` as a lower-overhead fallback for total-partition tests. Pumba for operational chaos campaigns (separate from unit/integration suites). — [Chaos Testing Guide: Chaos Engineering, Fault Injection, and Resilience Best Practices](https://katalon.com/resources-center/blog/chaos-testing-a-complete-guide) (2026, confirms Toxiproxy + WireMock as fast/programmable CI standard).

### 5. CI cost/flakiness — scheduled vs. inline

**Key finding: No one-size-fits-all answer; context matters.**

- **Inline (every commit)**: Catches regressions immediately; adds latency to every CI run; can introduce transient flakiness if network faults are not deterministic. — [Chaos Testing Guide](https://katalon.com/resources-center/blog/chaos-testing-a-complete-guide) recommends inline for development/staging; [Bring Chaos Engineering to your CI/CD pipeline - Gremlin](https://www.gremlin.com/blog/bring-chaos-engineering-to-your-ci-cd-pipeline/) suggests automated + scripted.

- **Scheduled (nightly/weekly)**: Isolates flakiness from PR workflow; captures hidden bugs; slower feedback loop. Recommended for production canary testing — [Bring Chaos Engineering to your CI/CD pipeline - Gremlin](https://www.gremlin.com/blog/bring-chaos-engineering-to-your-ci-cd-pipeline/) ("Something that might be best is to do this type of testing closer to the last stages of the CD pipeline, like during canary testing").

- **Flakiness mitigation** (network faults themselves are inherently flaky if using real network I/O):
  - Use **deterministic waits** (explicit conditions: `wait_for_logs`, health-check predicates) instead of fixed sleeps — [Flaky Tests in CI/CD: Causes, Fixes, and Prevention](https://testgrid.io/blog/flaky-tests/) (2026, confirms deterministic > sleeps as primary flakiness reducer).
  - Network issues are the **most prevalent category** of CI flakiness in GitHub Actions — [Understanding and Detecting Flaky Builds in GitHub Actions](https://arxiv.org/abs/2602.02307) (research paper, 2026).
  - If chaos tests fail, use **automated quarantine** (mark as xfail, skip on second retry, rerun in isolation) rather than blocking the whole suite.

**Recommendation for varco RT7**: Start with **inline unit/basic-chaos** (outbox relay restart, circuit breaker latency) for fast feedback; add **scheduled nightly chaos** (multi-failure cascades, longer network outages, worker crashes) for comprehensive coverage. Keep all tests deterministic (no fixed sleeps) and use `wait_for_logs` + health predicates.

## Version / compatibility notes

- **testcontainers-python**: 4.14.2 (pinned in varco as of today, 2026-08-26); 4.x API is stable for `start()/stop()/get_wrapped_container()`. No breaking changes to lifecycle in 4.14.x vs. 4.8.x.
- **docker-py** (Python Docker SDK): 7.1.0 (pinned today); docker-py 7.x unified the high-level and low-level APIs; `Container.restart()`, `pause()`, `unpause()`, `stop()`, `kill()` are stable public APIs. No major API changes in 7.x.
- **Toxiproxy**: Latest stable is 2.5.0 (Docker image `shopify/toxiproxy:2.5.0`); Toxiproxy 2.x REST API is stable. No breaking changes to `/proxies`, `/toxics` endpoints.
- **toxiproxy-python**: Version not clearly published; `chaostoolkit-toxiproxy` (0.2.1 or later) and `toxic-proxy` (requires Python >=3.9) are maintained alternatives. Sources do not cite a single "toxiproxy-python" release; Chaos Toolkit's driver is the most-cited integration.
- **Pumba**: Latest release is actively maintained on [GitHub](https://github.com/alexei-led/pumba); v0.11.0+ supports packet-loss injection via iptables.

## Evidence gaps

1. **No testcontainers-python native Toxiproxy module** — Python relies on generic `DockerContainer` + manual REST API calls. Whether the testcontainers-python project intends to add a `testcontainers.toxiproxy` module (as Java/Node/Go have) is unknown.
2. **Exact `toxiproxy-python` version and stability** — PyPI package metadata was not fully accessible; Chaos Toolkit's `chaostoolkit-toxiproxy` is the most-documented integration but adds a framework dependency.
3. **Mapped port re-acquisition post-restart** — No explicit Testcontainers API to "get new port after restart"; workaround (keep original port from `get_connection_url()`) works because docker-py `restart()` doesn't delete the container, but this is an implementation detail not a documented contract.
4. **GitHub Actions + Toxiproxy in Docker-in-Docker** — No published guide confirms Toxiproxy works without rootful Docker or special capabilities flags on ubuntu-latest. Assumed feasible (no special privileges needed), but untested in this repo.
5. **Long-term CI cost analysis** — No quantitative data on latency overhead of inline chaos tests vs. scheduled. Recommendation is heuristic ("start with inline basics, add scheduled canary later").

## Librarian's note

**What the sources indicate**: Testcontainers 4.14.2 + docker-py 7.1.0 support mid-test broker restarts via `get_wrapped_container().restart()`, which preserves ports/IDs (unlike the standard `.stop()/.start()` delete-and-recreate pattern). Toxiproxy is the industry-standard transparent proxy for deterministic, repeatable network fault injection in CI; Python lacks a native Testcontainers module but can instantiate Toxiproxy via generic `DockerContainer` and control it via REST API (or `chaostoolkit-toxiproxy` client). For varco RT7, the evidence favours **Toxiproxy for latency/bandwidth chaos** (transparent, deterministic, no app code changes) + **docker-py `network disconnect` as a fallback** for total-partition tests (simpler, no extra container). Start with **inline unit/basic-chaos** (fast feedback) and move complex cascades to **nightly scheduled** to avoid flaking PR workflows; use deterministic waits (`wait_for_logs`, health predicates) throughout to minimize transient failures.

---

## Sources

- [Getting started with Testcontainers for Python | Docker Docs](https://docs.docker.com/guides/testcontainers-python-getting-started/)
- [Containers — Docker SDK for Python 7.2.0 documentation](https://docker-py.readthedocs.io/en/stable/containers.html)
- [testcontainers-python source: test_docker_in_docker.py](https://github.com/testcontainers/testcontainers-python/blob/main/core/tests/test_docker_in_docker.py)
- [Testing a error scenario by stopping and restarting container => new port chosen each time · Issue #3615 · testcontainers/testcontainers-java](https://github.com/testcontainers/testcontainers-java/issues/3615)
- [Testcontainers restart of container without deleting it | Medium](https://sirdir.medium.com/testcontainers-restart-of-container-without-deleting-it-f2dd0645e984)
- [Use wait_for_logs in testcontainers-python With Examples | LambdaTest](https://www.lambdatest.com/automation-testing-advisor/python/testcontainers-python_python-wait_for_logs)
- [testcontainers-python/core/testcontainers/core/waiting_utils.py](https://github.com/testcontainers/testcontainers-python/blob/main/core/testcontainers/core/waiting_utils.py)
- [Toxiproxy Module - Testcontainers for Java](https://java.testcontainers.org/modules/toxiproxy/)
- [Toxiproxy Module - Testcontainers for .NET](https://dotnet.testcontainers.org/modules/toxiproxy/)
- [Testcontainers Toxiproxy Module](https://testcontainers.com/modules/toxiproxy/)
- [Developing Resilient Applications with Toxiproxy and Testcontainers | Docker](https://www.docker.com/blog/developing-resilient-applications-with-toxiproxy-and-testcontainers/)
- [How to Use Docker for Chaos Engineering with Toxiproxy | OneUptime](https://oneuptime.com/blog/post/2026-02-08-how-to-use-docker-for-chaos-engineering-with-toxiproxy/view)
- [fault-injection-lab on GitHub](https://github.com/aygp-dr/fault-injection-lab)
- [chaostoolkit-toxiproxy | PyPI](https://pypi.org/project/chaostoolkit-toxiproxy/)
- [toxiproxy-python | PyPI](https://pypi.org/project/toxiproxy-python/)
- [Chaos Testing Guide: Chaos Engineering, Fault Injection, and Resilience Best Practices | Katalon](https://katalon.com/resources-center/blog/chaos-testing-a-complete-guide)
- [Bring Chaos Engineering to your CI/CD pipeline | Gremlin](https://www.gremlin.com/blog/bring-chaos-engineering-to-your-ci-cd-pipeline/)
- [Flaky Tests in CI/CD: Causes, Fixes, and Prevention | TestGrid](https://testgrid.io/blog/flaky-tests/)
- [Understanding and Detecting Flaky Builds in GitHub Actions | arXiv](https://arxiv.org/abs/2602.02307)
- [GitHub - alexei-led/pumba: Chaos testing tool for containers](https://github.com/alexei-led/pumba)
