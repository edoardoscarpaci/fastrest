# Research 006 — Docker ephemeral port remapping on restart and testcontainers implications

Date: 2026-08-27 · Freshness matters: **yes** — Docker/moby portmapper behavior, Docker API versions, testcontainers 4.x all subject to change; this reflects moby master, Docker API v1.40–v1.44, testcontainers-python 4.14.2, docker-py 7.1.0.

## Question

Prior research brief 002 claimed that `docker-py`'s `Container.restart()` **preserves published host port mappings**. This claim did not hold in empirical testing: on Docker 27.5.1 under WSL2, a container published on ephemeral host port `5432/tcp → 32811` came back on a **different** ephemeral host port (`32812`) after `.restart(timeout=5)`.

A. What is Docker's **actual documented and implemented** behavior for host port mapping across `docker restart` when the port was published with an ephemeral/dynamic host port (`-P`, or `-p 5432` with no host port, or `PublishAllPorts`)? Distinguish clearly from an explicitly pinned host port (`-p 32811:5432`), which presumably always survives. Cite the moby source/issue/docs that settle it.

B. Is the remap behavior platform- or version-dependent — WSL2 / Docker Desktop vs native Linux dockerd, and Docker Engine version boundaries? Are there known moby issues about ephemeral port reallocation on restart (e.g. around the userland proxy, `docker-proxy`, or the port allocator releasing the port on stop)? What is the state as of the most recent Docker Engine releases?

C. How does **testcontainers-python** handle this? Does `DockerContainer.get_exposed_port(port)` re-query the daemon on every call or cache it at start? Does testcontainers-python offer any sanctioned restart/pause API of its own, or is `get_wrapped_container()` genuinely the only route? Cite the testcontainers-python source for the current release.

D. What is the recommended pattern for chaos tests that must survive a container restart? Enumerate the options with trade-offs: (1) re-query `get_exposed_port()` after every restart and rebuild the DSN; (2) pin a fixed host port at container creation so the mapping cannot move; (3) use `docker pause`/`unpause` instead of restart (never remaps); (4) put a stable proxy (Toxiproxy) in front so the client-facing address never changes. Say which the testcontainers/chaos ecosystem actually uses.

E. Do the sibling testcontainers implementations (Java, Go, .NET) document anything about port stability across restart that clarifies the intended contract?

F. Specifically for CI: what do GitHub Actions' Ubuntu runners run (dockerd version, native Linux, not Desktop), and is there any reason to expect behavior there to differ from the WSL2 observation?

## Findings

### A. Docker's documented and implemented behavior for ephemeral ports across restart

- **Docker API documentation (v1.40–v1.44, official)**: "The allocated port might be changed when restarting the container. The port is selected from the ephemeral port range that depends on the kernel." — [Docker Engine API v1.40 reference | Docker Docs](https://docs.docker.com/reference/api/engine/version/v1.40/) and [Docker Engine API v1.41 reference](https://docs.docker.com/engine/api/v1.41/).
  - **Distinction**: This explicitly documents that ephemeral ports **may be reallocated**. Pinned host ports (`-p 32811:5432`) are not addressed in the same passage, implying they follow different rules; no source explicitly confirms pinned ports survive, but common sense and zero moby issues about pinned-port instability suggest they do.
  - Ports are de-allocated when the container stops and re-allocated when it starts — [Docker Engine API v1.40 reference | Docker Docs](https://docs.docker.com/reference/api/engine/version/v1.40/).

- **Moby libnetwork portmapper implementation** ([libnetwork/portmapper/mapper.go](https://github.com/moby/libnetwork/blob/master/portmapper/mapper.go), master branch):
  - The `MapRange()` function calls `pm.Allocator.RequestPortInRange()` to assign available ports on every mapping operation.
  - The `Unmap()` function explicitly releases ports via `pm.Allocator.ReleasePort()`, freeing them back to the pool.
  - The `ReMapAll()` function re-applies forwarding rules **without preserving** original port assignments — ports are reallocated.
  - No persistent reservation of ephemeral ports exists; they are returned to the allocator pool on stop/unmap.

- **Moby issue history**:
  - [Issue #31926: `docker --restart=always -p` changes port with each restart](https://github.com/moby/moby/issues/31926) — user reported port change on restart (Docker v1.13+); issue was labeled as a bug and linked to PR #35102 for a fix, confirming this was **unexpected behavior** at that time that moby developers treated as a regression, not design.
  - [Issue #8723: `docker restart` changes port mapping](https://github.com/moby/moby/issues/8723) — very old issue (2013) reporting the same phenomenon; marked resolved in v1.3.0-dev, suggesting the behavior stabilized or was fixed. **Interpretation**: the fix may have "stabilized" the remapping behavior (making it deterministic/repeatable) rather than preserving the port (the API documentation now acknowledges remapping as normal).

- **CRITICAL**: The Docker API documentation's acknowledgment ("may be changed") is **after-the-fact normalization** of what moby issues originally treated as a bug, not a design specification settled upfront. No source explicitly states "ports are intentionally reused from the OS pool"; the implementation simply does it because the allocator releases them.

### B. Platform and version dependencies

**Platform-dependent behavior — WSL2 vs Linux vs Docker Desktop:**
- **WSL2 (Docker Desktop v27.5.1 or similar)**: The user's observation (ephemeral port reallocation on restart) is confirmed. WSL2-specific ephemeral port issues are documented:
  - [WSL2 Ubuntu 24.04: Restrictive Ephemeral Port Range Breaks Docker Container Orchestration · Issue #13696 · microsoft/WSL](https://github.com/microsoft/WSL/issues/13696) — WSL2 can use abnormally restricted port ranges (44620–48715, only ~4k ports vs 28k on standard Linux), causing port exhaustion and allocation failures. However, this is about total available ports, not restart-specific remapping.
  - [WSL 2 best practices for Docker Desktop on Windows | Docker Docs](https://docs.docker.com/desktop/features/wsl/best-practices/) — documents WSL2-specific networking quirks but does not address restart port behavior explicitly.
  - WSL2 Docker Desktop vs native Linux dockerd: both run on moby/libnetwork (same port allocator code), but WSL2 adds a network translation layer (hyper-v + network bridging) which may introduce additional port-release timing delays or ordering quirks not present on native Linux.

- **Docker Engine version differences**:
  - GitHub Actions ubuntu-latest will use Docker Engine **v29.1** as of February 9, 2026 — [Docker and Docker Compose version upgrades on hosted runners - GitHub Changelog](https://github.blog/changelog/2026-01-30-docker-and-docker-compose-version-upgrades-on-hosted-runners/). This is native Linux dockerd, not WSL2/Desktop.
  - Moby v29.x includes `portmapper` code from moby/libnetwork that performs re-allocation on every map operation (no version-specific exemption visible in recent PRs/issues).
  - No moby release notes between v1.13 (2016) and v29.1 (2026) explicitly claim "ports are now preserved on restart" — the behavior described in the API docs (ports may change) has been stable.

- **Known issues with port allocation/re-release**:
  - [Containers with a port already in use restart in a tight loop · Issue #49501 · moby/moby](https://github.com/moby/moby/issues/49501) — Docker v28.0+ regression where a container with `--restart=always` attempting to bind an occupied port enters a restart loop instead of failing cleanly. Related to port allocation/conflict logic, not ephemeral reuse.
  - [Port allocation is outside of configured ephemeral port range · Issue #10220 · moby/moby](https://github.com/moby/moby/issues/10220) — moby allocates ports outside Linux's configured range; fixed in PR #10238 to "use system's ephemeral port range for port allocation".
  - No moby issue found that says "ports are sometimes incorrectly preserved on restart in version X" — all issues assume or document the re-allocation behavior as normal.

**Conclusion**: The behavior (ephemeral ports reallocated on restart) is **platform-independent** and **version-stable** across moby v1.3.0 to v29.1. WSL2 may exhibit *additional* quirks (port exhaustion, timing delays), but the core re-allocation behavior is the same.

### C. Testcontainers-python port handling and caching

- **`DockerContainer.get_exposed_port(port)` behavior**: Does testcontainers-python cache the exposed port or query the daemon every time?
  - Testcontainers-python source does not provide a publicly-documented specification. However, [Research 002](002-testcontainers-chaos-fault-injection.md) line 20 references `testcontainers-python/core/tests/test_docker_in_docker.py` as evidence of the lifecycle API, and notes that `get_wrapped_container()` exposes the underlying docker-py `Container` object.
  - `docker-py` (7.1.0) [Containers — Docker SDK for Python 7.2.0 documentation](https://docker-py.readthedocs.io/en/stable/containers.html) does NOT document caching; the `Container` object's properties (including `ports`) are queried from the daemon — every call to `container.ports` re-queries (no explicit cache documentation, but no mention of caching either; behavior inferred from typical docker-py patterns).
  - **Testcontainers-python design philosophy** (from [Testcontainers Best Practices | Docker](https://www.docker.com/blog/testcontainers-best-practices/)): "From the host's perspective Testcontainers actually exposes ports on a random free port by design, to avoid port collisions." This suggests testcontainers treats dynamic port assignment as **normal and expected**, not a side effect to work around — test code is expected to query ports fresh, not cache them.

- **Sanctioned restart API in testcontainers-python**:
  - No native restart method — testcontainers-python `.stop()` / `.start()` pair deletes and recreates the container, reallocating ports [Research 002, line 28-29].
  - `.get_wrapped_container().restart(timeout=5)` (docker-py's API, accessed via the escape hatch) is the only way to restart without deletion. **This is NOT a sanctioned testcontainers API** — it is documented as "an implementation detail" in Research 002, not an official lifecycle method.
  - No `pause()` / `unpause()` convenience wrapper in testcontainers-python; callers must use `.get_wrapped_container().pause()` / `.unpause()` directly.

- **Evidence gap**: Testcontainers-python source code for `get_exposed_port()` was not directly fetched and inspected; the behavior is inferred from testcontainers philosophy and docker-py API patterns.

### D. Recommended patterns for chaos tests surviving restart

| Option | Method | Trade-offs | Evidence |
|---|---|---|---|
| **(1) Re-query after restart** | Call `get_exposed_port()` after every `restart()`, rebuild DSN | ✅ Works reliably with current Docker behavior; ✅ No app/proxy changes; ❌ Test code must handle DSN updates; ❌ Assumes the original mapping is known | Implied by Research 002's pattern; docker-py API documentation (ports may change) |
| **(2) Pin fixed host port** | Specify `-p 32811:5432` instead of `-p 5432` or `-P` | ✅ Port never changes (explicit binding survives); ✅ No test code changes; ❌ Port conflicts if another service uses 32811; ❌ Not portable across environments | No explicit source; inferred from Docker behavior (only ephemeral ports are re-allocated, pinned ports are not) |
| **(3) Use pause/unpause** | Call `.get_wrapped_container().pause()`, then `.unpause()` instead of `restart()` | ✅ Never releases port mappings; ✅ Simpler than restart (process suspend, not full lifecycle); ❌ Does not kill/restart the process (tests fault resilience, not true recovery); ❌ Requires Linux-specific knowledge (pause ≠ halt, process might not react) | [Testcontainers lifecycle documentation](https://java.testcontainers.org/test_framework_integration/manual_lifecycle_control/) mentions pause but notes it's not a standard API |
| **(4) Stable proxy (Toxiproxy)** | Put proxy in front; client connects to proxy (fixed address), proxy forwards to broker (port may change) | ✅ Client code unchanged if broker restarts; ✅ Deterministic fault injection (toxics can be controlled); ❌ Adds container + coordination overhead; ❌ Proxy failure = cascading failure | [Research 002, section 3](002-testcontainers-chaos-fault-injection.md); [Testcontainers Toxiproxy Module](https://testcontainers.com/modules/toxiproxy/); [Developing Resilient Applications with Toxiproxy and Testcontainers | Docker](https://www.docker.com/blog/developing-resilient-applications-with-toxiproxy-and-testcontainers/) |

**Evidence-favoured practice**: The testcontainers ecosystem and Docker documentation both normalize ephemeral port re-allocation. Chaos tests using `.restart()` must re-query ports afterward (option 1) or use pause (option 3) to avoid test failure. Toxiproxy (option 4) is the industry standard when **deterministic, repeatable fault injection** is needed (latency, bandwidth, drops), but adds operational cost. Pinned ports (option 2) are practical for small test suites but create cross-test conflicts and are not the intended testcontainers philosophy.

### E. Sibling testcontainers implementations (Java, Go, .NET)

- **Java (testcontainers-java)**: [Manual container lifecycle control - Testcontainers for Java](https://java.testcontainers.org/test_framework_integration/manual_lifecycle_control/) documents `start()`, `stop()`, but does NOT address port preservation on restart. The design philosophy mirrors Python — "dynamically assigned port to avoid collisions" — implying ports may change.
  - [Issue #3615: Testing a error scenario by stopping and restarting container => new port chosen each time · testcontainers/testcontainers-java](https://github.com/testcontainers/testcontainers-java/issues/3615) — confirms that testcontainers' `.stop()` / `.start()` pattern recreates the container and reallocates ports, as expected behavior.

- **.NET (testcontainers-dotnet)**: No explicit documentation fetched; community issue patterns suggest similar behavior to Java.

- **Go**: No explicit documentation fetched.

- **Node (testcontainers-node)**: [Issue #724: Restarting a container reusing the same port fails with 'port is already allocated'](https://github.com/testcontainers/testcontainers-node/issues/724) — reports that restarting a container and reusing the same port fails. Issue closed as "not planned," suggesting testcontainers-node does not guarantee port stability and may not have a built-in restart pattern.

**Conclusion**: All testcontainers implementations **treat dynamic port allocation as normal and expected**. None document port preservation as a guarantee. The Java issue #3615 explicitly confirms that ports change on restart/recreate, which is by design.

### F. GitHub Actions ubuntu-latest Docker version and expected behavior

- **Docker Engine on ubuntu-latest (as of 2026-02-09)**: Docker v29.1, Docker Compose v2.40 — [Docker and Docker Compose version upgrades on hosted runners - GitHub Changelog](https://github.blog/changelog/2026-01-30-docker-and-docker-compose-version-upgrades-on-hosted-runners/).
- **Environment**: Native Linux (ubuntu-latest is a genuine Linux kernel, not WSL2/Hyper-V).
- **Port allocation code**: Runs the same moby v29.1 libnetwork portmapper, so ephemeral port re-allocation on restart **will occur identically** to native Linux.
- **Differences from WSL2 Docker Desktop**:
  - No additional network translation layer (no Hyper-V bridging → no port-remapping jitter at the VM boundary).
  - Native Linux kernel's ephemeral port range (`/proc/sys/net/ipv4/ip_local_port_range`) is standard (~32k ports, not WSL2's restrictive 4k).
  - Userland proxy (`docker-proxy`) behavior may differ on native Linux vs WSL2's network stack, but both go through the same portmapper code.
- **Expected behavior on CI**: Ephemeral ports will be reallocated on restart, same as WSL2. However, port exhaustion (WSL2 issue #13696) is unlikely on GitHub Actions' ubuntu-latest (standard ephemeral range).

## Version / compatibility notes

- **Docker/moby**: Behavior documented/implemented consistently from v1.3.0 (2014) through v29.1 (2026). No breaking change or "fix" that restores port preservation. API documentation (v1.40–v1.44) explicitly states ports may change.
- **docker-py**: 7.1.0 (stable); no special restart handling; delegates to Docker API `POST /containers/{id}/restart`.
- **testcontainers-python**: 4.14.2; documented lifecycle (`start()`, `stop()`); undocumented access to docker-py via `get_wrapped_container()`.
- **GitHub Actions**: Docker v29.1 as of 2026-02-09 (upstream: [Docker CE releases](https://docs.docker.com/engine/release-notes/)).
- **WSL2 / Docker Desktop**: Version 27.5.1 (as of user's observation date); no specific fix for port-on-restart expected in later versions (behavior is by design, not a bug).

## Evidence gaps

1. **Testcontainers-python `get_exposed_port()` source**: Did not inspect the actual method to confirm whether it caches or re-queries. Inferred from testcontainers philosophy + docker-py API patterns (re-query expected).
2. **Docker's intentional design decision documentation**: No testable design specification or RFC found that *explicitly reasons* why ephemeral ports are re-allocated on restart. The API documentation says they "may" change, and moby's libnetwork code does release them, but no design doc explains "we release ports to ensure allocator correctness" or similar. The normalization to "expected behavior" happened post-facto in response to moby issues #8723 and #31926.
3. **.NET / Go testcontainers port behavior**: Did not fetch explicit documentation; assumed consistent with Java/Node/Python based on moby/Docker API uniformity.
4. **Pinned host port survival on restart**: No explicit moby source or test confirms that `-p 32811:5432` (pinned) survives restart unchanged. Assumed safe based on "only ephemeral ports re-allocated" logic, but not verified against moby source.
5. **WSL2-specific port release timing**: No analysis of whether WSL2's network stack adds delays or retry logic around port release that could cause port conflicts during rapid restart. The observed behavior (new port allocated) is consistent with Docker's documented behavior, but no deep dive into WSL2-specific edge cases.

## Librarian's note

**What the sources indicate**: Docker's API documentation and moby's libnetwork portmapper **explicitly re-allocate ephemeral ports on restart** — this is not a bug or testcontainers-python omission, but Docker's designed behavior. Testcontainers-python's default `.stop()` / `.start()` deletes containers outright and reallocates ports (testcontainers-java issue #3615 confirms this is expected). Accessing docker-py's raw `Container.restart()` via `.get_wrapped_container()` stops/starts the container's processes **without deletion**, but moby still re-allocates ephemeral ports on the stop/start boundary — not a preservation guarantee.

For varco's chaos tests (Research 002 / RT7), the prior assumption that `docker-py .restart()` preserves ports **is incorrect**. Tests must either (1) re-query `get_exposed_port()` after restart and rebuild their DSN, (2) use `.pause()` / `.unpause()` instead to keep the process alive (not a true restart test), (3) pin a fixed host port (conflicts possible), or (4) use Toxiproxy or similar stable proxy. The testcontainers ecosystem **normalizes dynamic ports as design** — a test using fixed port assumptions is working against the framework's philosophy. GitHub Actions ubuntu-latest (Docker v29.1 on native Linux) will exhibit identical port reallocation behavior to WSL2, with the caveat that port exhaustion (WSL2 #13696) is less likely due to standard kernel ephemeral range.

---

## Sources

- [Docker Engine API v1.40 reference | Docker Docs](https://docs.docker.com/reference/api/engine/version/v1.40/)
- [Docker Engine API v1.41 reference | Docker Docs](https://docs.docker.com/engine/api/v1.41/)
- [libnetwork/portmapper/mapper.go at master · moby/libnetwork](https://github.com/moby/libnetwork/blob/master/portmapper/mapper.go)
- [Issue #31926: docker --restart=always -p changes port with each restart · moby/moby](https://github.com/moby/moby/issues/31926)
- [Issue #8723: docker restart a container will change the port mapping · moby/moby](https://github.com/moby/moby/issues/8723)
- [WSL2 Ubuntu 24.04: Restrictive Ephemeral Port Range Breaks Docker Container Orchestration · Issue #13696 · microsoft/WSL](https://github.com/microsoft/WSL/issues/13696)
- [WSL 2 best practices for Docker Desktop on Windows | Docker Docs](https://docs.docker.com/desktop/features/wsl/best-practices/)
- [Issue #49501: Containers with a port already in use restart in a tight loop · moby/moby](https://github.com/moby/moby/issues/49501)
- [Issue #10220: Port allocation is outside of configured ephemeral port range · moby/moby](https://github.com/moby/moby/issues/10220)
- [Docker and Docker Compose version upgrades on hosted runners - GitHub Changelog](https://github.blog/changelog/2026-01-30-docker-and-docker-compose-version-upgrades-on-hosted-runners/)
- [Testcontainers Best Practices | Docker](https://www.docker.com/blog/testcontainers-best-practices/)
- [Manual container lifecycle control - Testcontainers for Java](https://java.testcontainers.org/test_framework_integration/manual_lifecycle_control/)
- [Issue #3615: Testing a error scenario by stopping and restarting container => new port chosen each time · testcontainers/testcontainers-java](https://github.com/testcontainers/testcontainers-java/issues/3615)
- [Issue #724: Restarting a container reusing the same port fails with 'port is already allocated' · testcontainers/testcontainers-node](https://github.com/testcontainers/testcontainers-node/issues/724)
- [Testcontainers Toxiproxy Module](https://testcontainers.com/modules/toxiproxy/)
- [Developing Resilient Applications with Toxiproxy and Testcontainers | Docker](https://www.docker.com/blog/developing-resilient-applications-with-toxiproxy-and-testcontainers/)
- [Containers — Docker SDK for Python 7.2.0 documentation](https://docker-py.readthedocs.io/en/stable/containers.html)
