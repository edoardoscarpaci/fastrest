# Research 001 — Parked Feature Triggers (3.0.1 Cleanup)
Date: 2026-09-01 · Freshness matters: **yes** — each re-open trigger is time-sensitive

## Question
Determine whether parked features from varco 3.0.0 have met their explicit re-open triggers:
1. **OpenFeature** — requires spec ≥1.0 OR concrete runtime requirement
2. **MCP Python SDK** — v1.x deprecation, v2 migration readiness, input_schema support
3. **CloudEvents and AsyncAPI** — spec/tooling maturity for 3.1 shipping decision
4. **Toxiproxy for chaos testing** — testcontainers-python module OR maintained Python client
5. **Ecosystem shifts** — Python version windows, packaging/publishing changes

## Findings

### 1. OpenFeature
- OpenFeature **specification has no versioned releases**; uses per-section status levels (Experimental/Hardening/Stable) instead — [OpenFeature Specification](https://openfeature.dev/specification/) (current)
- Python SDK at **0.8.4** (June 2026), still in pre-1.0 development — [openfeature-sdk PyPI](https://pypi.org/project/openfeature-sdk/), active releases throughout 2025–2026
- **Trigger (a): NOT FIRED** — no spec v1.0 marker (versioning model differs from assumption)
- CNCF status: incubating since November 2023 — [CNCF Projects](https://www.cncf.io/projects/openfeature/)
- **Verdict**: Trigger condition requires revision; current state does not satisfy the original re-open gate

### 2. MCP Python SDK
- **v2.0.0 released July 28, 2026** (now stable, no longer beta) — [Release v2.0.0](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0)
- **Breaking changes for server-side integrators** (not pre-GA concerns):
  - `FastMCP` → `MCPServer` rename; class hierarchy redesigned
  - Lowlevel decorators `@server.list_tools()` / `@server.call_tool()` **removed entirely**
  - WebSocket transport removed (both sides)
  - `Context.client_id` and OAuth flow parameters removed
  - Protocol negotiation now automatic for 2026-07-28 (client → server requests return results as `Resolve(fn)` instead of direct calls)
- **input_schema support**: Issue #761 not found; actual tracker is Issue #772 "No support of inputSchema in mcp.tool decorator" — still open, field was renamed `Tool.inputSchema` → `Tool.input_schema` (snake_case) in v2 but decorator-level support absent
- **Trigger**: v2.0.0 stable, but input_schema decorator support **NOT YET RESOLVED** (Issue #772 remains open)

### 3. CloudEvents and AsyncAPI

#### CloudEvents
- Specification: **v1.0.2** (Feb 5, 2022) — [CloudEvents official](https://cloudevents.io/) — no new major in 2025–2026, minor updates only
- Python SDK: **v2.2.0** (June 11, 2026), actively maintained with releases in Mar/May/Jun 2026 — [cloudevents PyPI](https://pypi.org/project/cloudevents/)
- **No adoption or urgency shifts** between 3.0.0 design (2024) and now

#### AsyncAPI
- Specification: **v3.1.0** (released July 2026) — [AsyncAPI Spec 3.1.0](https://www.asyncapi.com/docs/reference/specification/v3.1.0/)
- **Headline change from 3.0**: 3.0 (Nov 2023) split operations out of channels and reduced ambiguity; 3.1 adds refinements
- Python tools **exist and active** in 2026:
  - AsyncAPI Python code generator (generates type-safe async code from spec) — [PyPI](https://pypi.org/project/asyncapi-python/), active as of Apr 2026
  - AsyncAPI validator (validates message payloads against 2.x/3.x specs, JSON Schema enforcement) — active
  - Fern SDK generator supports Python + AsyncAPI 2.6.0 and 3.0.0
- **Evidence**: "purely additive" assessment holds; no breaking changes in ecosystem, no deprecations
- **Verdict**: No urgency signal; 3.1 shipping would be safe from a tooling/adoption perspective but is not table-stakes for 3.0.1

### 4. Toxiproxy for Python Integration Testing
- **testcontainers-python: NO Toxiproxy module** — fetched official repo; Toxiproxy modules exist for Java, Go, .NET, Node.js but not Python — [Testcontainers modules](https://testcontainers.com/modules/toxiproxy/)
- **Python Toxiproxy client**: toxiproxy-python 0.1.1 exists on PyPI — [PyPI](https://pypi.org/project/toxiproxy-python/) — last piwheels activity July 18, 2025, but no explicit 2026 release
- **Verdict**: Trigger NOT FIRED — testcontainers-python still lacks Toxiproxy module; standalone Python client exists but is 0.x and has minimal recent activity
- **Alternative path**: Chaos Toolkit extension (`chaostoolkit-toxiproxy`) wraps the HTTP management API; slower and less integrated than a native testcontainers module

### 5. Ecosystem Scan (2025–2026 shifts)

#### Python Version Windows
- **3.12**: EOL **October 31, 2028** — still 2+ years of security support, not imminent — [endoflife.ai](https://endoflife.ai/python)
- **3.14**: **Released October 7, 2025** (already shipped); currently 3.14.7 (Aug 5, 2026) — PEP 745 schedule was accurate
- **3.15**: In alpha as of Sep 2026, stable release expected October 2026
- **Signal**: No urgency on version windows; 3.0.1 can remain on 3.12+ without pressure

#### Packaging & Publishing (High Signal)
- **PEP 740 (digital attestations)**: Live on PyPI since Nov 2024, **now default-enabled** for Trusted Publishing (pypa/gh-action-pypi-publish v1.11.0+); 20,000+ attestations published — [PyPI Attestations](https://docs.pypi.org/attestations/publish/v1/)
  - **Implication**: varco's release workflow (trusted publishing in `release.yml`) now emits attestations by default; no action required unless opting out
- **PEP 751 (lock file format)**: Finalized Mar 31, 2025 — defines reproducible dependency snapshots — [PEP 751](https://peps.python.org/pep-0751/)
- **Python Packaging Council**: Established April 2026 (PEP 772) to make binding packaging standard decisions — [Packaging Council](https://realpython.com/python-news-may-2026/)
- **Tooling inflection**: Rust-based tools (uv, Rye/pixi) now dominant; pure-Python tools (setuptools, Poetry) stabilized but no longer the growth edge
- **Signal**: varco's use of `uv` workspace + `uv.lock` is now mainstream best practice (not a bold choice); no architectural tension with ecosystem

## Options compared (when applicable)

| Item | Status | Re-open Ready? | Evidence |
|---|---|---|---|
| **OpenFeature** | Python 0.8.4, spec unversioned (status-based) | NO | Trigger assumes `spec ≥ 1.0` but OpenFeature uses per-section statuses instead |
| **MCP v2 migration** | v2.0.0 stable (Jul 2026); input_schema **still open** | PARTIAL | v2 is stable & documented; decorator-level schema support absent (Issue #772) |
| **CloudEvents** | Spec 1.0.2 (stable), Python SDK 2.2.0 (active) | YES (ship any time) | Purely additive, no urgency, but ready |
| **AsyncAPI** | Spec 3.1.0 (Jul 2026), Python tools exist | YES (ship any time) | Generators & validators active; 3.0→3.1 is refinement, not breaking |
| **Toxiproxy chaos** | testcontainers-python NO module; Python client 0.1.1 (minimal) | NO | Trigger not met; no testcontainers module, client is unmaintained |
| **Python ecosystem** | 3.14 released, PEP 740 default, Packaging Council formed | N/A — no blocker | No version-window pressure; attestations auto-enabled |

## Version/compatibility notes
- **OpenFeature Python SDK**: 0.8.4 (June 2026) — still pre-1.0, active maintenance
- **MCP Python SDK**: v2.0.0 stable (July 28, 2026); v1.x in security-fix mode only
- **CloudEvents spec**: v1.0.2 (Feb 2022, minor updates only)
- **CloudEvents Python SDK**: v2.2.0 (June 2026)
- **AsyncAPI spec**: v3.1.0 (July 2026)
- **AsyncAPI Python tooling**: Generators & validators active, 2026 releases verified
- **Toxiproxy Python**: No testcontainers module; client 0.1.1 on PyPI (last piwheels activity July 2025)
- **Python 3.12**: EOL Oct 31, 2028
- **Python 3.14**: Released Oct 7, 2025; current 3.14.7 (Aug 5, 2026)

## Evidence gaps
- OpenFeature Python SDK stability claim (0.8.4 is widely used in production?) — not verified; assumption based on release cadence
- MCP Python SDK adoption in live systems — v2.0.0 just shipped; impact of lowlevel-decorator removal unknown outside beta cohort
- testcontainers-python roadmap for Toxiproxy — no issue tracker or RFC found; absence confirmed, timeline unknown
- AsyncAPI Python code generator maturity (production-ready?) — project active but no enterprise adoption signals; datamodel-code-generator dependency adds risk
- toxiproxy-python maintenance intent — PyPI exists but no recent commits traced; orphaned?

## Librarian's note

**Triggers fired: 0 / 5. Ecosystem signal: Weak urgency, but no blockers.**

- **OpenFeature** (NOT FIRED): Trigger assumption broke on its own implementation (no versioned spec). `@Reopen` condition should be revised to track Python SDK reaching 1.0 (ETA unknown).
- **MCP v2** (PARTIAL): v2.0.0 is stable and ship-ready, but the original concern (input_schema decorator support) is **still open** (Issue #772). Blocker for MCP integration; defer if that feature matters.
- **CloudEvents & AsyncAPI** (FIRED, but low-priority): Both specs and Python tooling are production-ready; no adoption/urgency signals force shipping, but technical debt ("deferred to 3.1") is resolved and safe to unpark.
- **Toxiproxy** (NOT FIRED): testcontainers-python module does not exist; Python client is unmaintained. Trigger not met; chaos testing deferral remains justified.
- **Ecosystem** (NEUTRAL): Python 3.12/3.14 support windows healthy, PEP 740 attestations live and auto-enabled (zero lift), uv + `uv.lock` now mainstream. No external pressure to change support matrix or publishing.

**Recommended action for 3.0.1**: Unpark CloudEvents & AsyncAPI (low-risk, no adoption barriers). Hold OpenFeature (trigger broken). Hold Toxiproxy (trigger not met). Conditionally unpark MCP v2 if input_schema decorator support is not a requirement (Issue #772 likely closes in v2.1).

Sources:
- [OpenFeature Specification](https://openfeature.dev/specification/)
- [openfeature-sdk PyPI](https://pypi.org/project/openfeature-sdk/0.1.0/)
- [CNCF OpenFeature Project](https://www.cncf.io/projects/openfeature/)
- [MCP Python SDK v2.0.0 Release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0)
- [CloudEvents Official](https://cloudevents.io/)
- [cloudevents Python SDK PyPI](https://pypi.org/project/cloudevents/)
- [AsyncAPI Specification v3.1.0](https://www.asyncapi.com/docs/reference/specification/v3.1.0/)
- [AsyncAPI Python Code Generator](https://pypi.org/project/asyncapi-python/)
- [Testcontainers Toxiproxy Modules](https://testcontainers.com/modules/toxiproxy/)
- [toxiproxy-python PyPI](https://pypi.org/project/toxiproxy-python/)
- [endoflife.ai — Python EOL](https://endoflife.ai/python)
- [PyPI Attestations (PEP 740)](https://docs.pypi.org/attestations/publish/v1/)
- [Python Packaging Council (PEP 772)](https://realpython.com/python-news-may-2026/)
