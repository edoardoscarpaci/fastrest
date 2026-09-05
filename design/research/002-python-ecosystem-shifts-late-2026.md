# Research 002 — Python ecosystem shifts as of late 2026: opportunities and obligations

Date: 2026-09-04 · Freshness matters: **yes**

---

## Question

Which ecosystem shifts as of late 2026 create new opportunities or new obligations for varco (a Python 3.12/3.13 async backend platform)? Focus on feature-level opportunities, not implementation design.

---

## Findings

### 1. Python Runtime

#### Free-Threading (No-GIL) Status
- **Python 3.14 ships free-threaded as stable** (not experimental) — [Python 3.14.7 documentation on free-threading](https://docs.python.org/3/howto/free-threading-python.html) (Oct 2025); overhead reduced to 5–10% on single-threaded workloads vs 40% in early 3.13 — **OBLIGATION**: varco's lazy `asyncio.Lock` pattern is already correct for free-threaded Python; document that process-global singletons and per-request ambient values continue to work unchanged, but a **new async-native thread pool** becomes viable if varco wants to support sync-in-async workloads (e.g., CPU-bound business logic in a background task). This is not today's varco posture but is now defensible.

#### Subinterpreters (PEP 734)
- **`concurrent.interpreters` module added to Python 3.14** — [PEP 734 final](https://peps.python.org/pep-0734/) (June 2025), [CPython documentation](https://docs.python.org/3/library/concurrent.interpreters.html) (Sept 2026); enables intra-process isolation and reduces shared-state bugs in worker pools — **NOISE**: not a primary concern for async-first design; meaningful only if varco wants to expose sub-interpreter isolation as an opt-in deployment mode (e.g., for multi-tenant isolation without process overhead). Not actionable now.

#### Asyncio Improvements
- **Eager task creation stabilized** — `asyncio.Task(..., eager_start=True)` available since 3.12, [documented in 3.14](https://docs.python.org/3.14/library/asyncio-task.html); immediate task startup until first blocking call — **NOISE**: already in 3.12 (before varco's 3.12 floor); does not change api surface or design.
- **TaskGroup for structured concurrency** — [Python 3.14 asyncio docs](https://docs.python.org/3.14/library/asyncio-task.html); built into stdlib — **NOISE**: varco's event consumers already use this pattern implicitly; no library-level action needed.

**Overall assessment**: Free-threaded Python creates a **low-priority opportunity** (sync-in-async bridge) but does not alter today's async-first surface.

---

### 2. Typing Ecosystem

#### PEP 695 Type Parameters
- **PEP 695 `class Container[T]:` syntax widely adopted** — [PEP 695](https://peps.python.org/pep-0695/) (Python 3.12+), mypy/Pyright full support confirmed as of July 2026 — [pydevtools summary](https://pydevtools.com/handbook/explanation/what-is-pep-695/) (July 2026) — **OPPORTUNITY**: varco's current generic abstractions (e.g., `AsyncService[D, PK, C, R, U]`) can now be declared inline as a `class` without `Generic(...)` boilerplate. Cleaner surface does not break existing code but makes new APIs more readable. Varco 3.1.0+ can migrate internal type parameters at will; shipping PEP 695 syntax in public ABCs (e.g. `class AsyncRepository[D]:` instead of `class AsyncRepository(Generic[D])`) is low-risk ergonomic gain. This is a **nice-to-have polish**, not a breaking obligation.

#### Typing Tool Consolidation
- **mypy 2.3.1 + Pyright in parity** — [mypy releases](https://github.com/python/mypy/releases), [Pyright stable](https://github.com/microsoft/pyright/releases) (2026); both track PEP 696 TypeVar defaults and PEP 698 @override — **NOISE**: varco already pinned mypy in dev deps; no change needed.

**Overall assessment**: **Low-priority opportunity** for ergonomic refresh using PEP 695; not a blocker for any feature.

---

### 3. Packaging & Supply Chain

#### uv Dominance & PEP 735
- **uv is the de-facto standard** — [uv 0.11.7 production](https://medium.com/@diwasb54/the-2026-golden-path-building-and-publishing-python-packages-with-a-single-tool-uv-b19675e02670) (2026), >60% adoption by developers using it, 100x faster than pip in dependency resolution — [uv adoption article](https://medium.com/@diwasb54/the-2026-golden-path-building-and-publishing-python-packages-with-a-single-tool-uv-b19675e02670) — **OBSERVATION**: varco already uses uv; no action required. Continue documenting uv as the reference toolchain.
- **PEP 735 dependency groups** — [PEP 735](https://pydevtools.com/handbook/explanation/what-is-pep-735/); implemented in uv 0.4.27 (Oct 2024), pip 25.1 (Apr 2025) — **OBSERVATION**: varco already uses `[dependency-groups]` in `pyproject.toml`; no action required.

#### Lockfile Standard: PEP 751
- **`pylock.toml` standardized** — [PEP 751 accepted March 2025](https://peps.python.org/pep-0751/); replaces tool-specific formats (Poetry, PDM, pip-tools each had their own) — [InfoWorld explainer](https://www.infoworld.com/article/3951671/understand-pythons-new-lock-file-format.html) (2026) — **OBLIGATION**: by 2027, distribute a `pylock.toml` alongside `uv.lock` for reproducibility. Varco's current `uv.lock` is sufficient for development but packaging a `pylock.toml` in the repo signals maturity and compliance with the emerging Python packaging baseline. Action: add `pylock.toml` generation to the build pipeline when uv ships native support (likely 0.12+).

#### External Dependencies: PEP 725
- **System dependency specification** — [PEP 725 accepted Sept 2025](https://peps.python.org/pep-0725/); `[external]` table in `pyproject.toml` for build/runtime system deps — [discussion](https://discuss.python.org/t/pep-725-specifying-external-dependencies-in-pyproject-toml-round-2/103890) (2025) — **NOISE**: varco has no external C/system dependencies; not applicable.

#### SBOM & Supply Chain Hardening: PEP 770 & CRA
- **SBOMs embedded in wheels** — [PEP 770](https://pypi.org/project/sbom4python/) (draft 2026); `.dist-info/sboms/` directory holds SBOM files — **OBLIGATION**: EU CRA (Cyber Resilience Act) requires SBOM submission for high-criticality products. Varco, if used in critical infrastructure, must ship a CycloneDX SBOM in `dist-info`. Action: integrate CycloneDX generation into release CI (`make build` → embeds SBOM) by end of 2026. See section 8 (Security/Compliance) for CRA timeline.
- **SLSA/Attestations** — PEP 740 provenance attestations available via `pypa/gh-action-pypi-publish` (already in varco's release workflow) — **OBSERVATION**: varco ships attestations today; no change needed.

**Overall assessment**: `pylock.toml` is a **table-stakes obligation** by 2027; SBOM embedding is driven by CRA compliance (see section 8).

---

### 4. Observability

#### OpenTelemetry Semantic Conventions Stability
- **Semantic conventions v1.44.0 stable** — [OpenTelemetry spec reference](https://opentelemetry.io/docs/specs/semconv/) (2026); messaging, database, HTTP, RPC domains finalized — [2026 blog](https://openobserve.ai/blog/opentelemetry-semantic-conventions/) — **OBSERVATION**: varco's OTel integration already follows stable semantic conventions; no breaking changes expected.

#### GenAI Semantic Conventions Extraction
- **`gen_ai.*` attributes moved to dedicated repository** — [OpenTelemetry GenAI specs](https://github.com/open-telemetry/semantic-conventions-genai) (2026); LLM model, tokens, cost attributes standardized — [blog post](https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions) — **OPPORTUNITY**: if varco adopts agent/LLM integration (e.g., OpenAI client wrapping, MCP tool invocation), shipping `gen_ai.*` spans would enable observability of model calls. Low-priority unless varco's roadmap includes agent-facing APIs.

#### Logs Functionality: Still Experimental
- **OpenTelemetry Python logs remain experimental** — [OpenTelemetry Python docs](https://opentelemetry-python.readthedocs.io/en/stable/examples/logs/README.html) (2026); **not GA**; `opentelemetry.sdk._logs` subject to breaking changes in minor releases — **OBSTACLE**: varco cannot ship logs as a stable feature until OTel Python publishes a GA release (expected 2027). Continue using Python's `logging` module with a bridge handler; do not commit to OTLP-native logging yet.

#### Profiling: No Native OTLP Support Yet
- **OTLP profiling still in development** — no official OpenTelemetry Python profiling SDK released as of Sept 2026 — **NOISE**: varco's standalone profiling backend (`cProfile`, `tracemalloc`) is the right approach for now; native OTel profiling adoption deferred to a future version.

**Overall assessment**: Semantic conventions stable (no action); gen-ai is a **low-priority future opportunity**; logs remain a **blocker for GA observability** (defer); profiling stays standalone (correct choice).

---

### 5. AI & Agent Integration

#### Model Context Protocol (MCP) v2 Major Redesign
- **MCP Python SDK v2 released (2026-07-28 spec)** — [MCP blog: v2 release](https://blog.modelcontextprotocol.io/posts/sdk-betas-2026-07-28/) (July 2026), [SDK repo releases](https://github.com/modelcontextprotocol/python-sdk/releases); stateless protocol, no more session/handshake layer, 83% smaller package, 25% faster — [SDK what's new](https://py.sdk.modelcontextprotocol.io/whats-new/) — **BREAKING CHANGE**: varco's unmigrated MCP surface (mentioned in CLAUDE.md) must migrate to v2 API before v4.0.0 (recommend doing this in 3.2 or 3.3 after A2A stabilizes). The v1→v2 breaking changes are non-trivial: handlers change from decorator-based to `async (ctx, params) -> result` signature; class names shift (FastMCP → MCPServer); imports move. Action: plan a dedicated sprint to migrate varco's MCP adapter to v2, or document the v1 migration as a known issue. Both MCP v1 and v2 can coexist in production (backward compatibility via protocol negotiation) but shipping a `varco_mcp` backend (or public MCP utilities) must target v2.

#### Google A2A Protocol Production Adoption
- **A2A protocol >150 orgs in first year, major clouds integrated** — [Linux Foundation announcement](https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year/) (April 2026); v1.0.1 released May 2026 with extension mechanism — [Google A2A blog](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/) (2026) — **OPPORTUNITY**: A2A is more stable/enterprise than MCP at present. Varco already ships an A2A SkillAdapter; this is a **differentiator to lean into**. Consider publishing A2A examples prominently and ensure A2A surface is fully tested in CI. A2A is the **safer bet** than MCP for 2026 agent workloads; promote it in README/docs.

#### Agent API Conventions Converging
- **OpenAPI 3.2 + Arazzo for workflow specs** — [OpenAPI 3.2 released Sept 2025](https://thenewstack.io/openapi-initiative-new-standards-and-a-peek-at-the-roadmap/) (with streaming, OAuth2 device flow), [Arazzo 1.1.0 for multi-step workflows](https://spec.openapis.org/arazzo/latest.html) (2026) — **OPPORTUNITY**: if varco's documentation or agent-exposure features could be described in Arazzo (multi-step workflows like "create entity → publish event → send notification"), publishing an Arazzo doc alongside OpenAPI 3.2 would be a **high-visibility differentiator for agentic platforms**. Low-priority but table-stakes for agent-first platforms by 2027.

**Overall assessment**: **MCP migration is an obligation** (v1 → v2, Plan TBD); **A2A is a current strength** (lean into it); **Arazzo support is a future opportunity** (2027+).

---

### 6. Standards Adoption Worth Tracking

#### CloudEvents v1.0 (CNCF Graduated)
- **CloudEvents v1.0 official and graduated** — [CNCF graduation Jan 2024](https://cloudevents.io/), [used by Azure, Google Cloud](https://learn.microsoft.com/en-us/azure/event-grid/cloud-event-schema) — **OBSERVATION**: varco's event system is internal; shipping a `CloudEvents` envelope option for external integrations (e.g., webhook delivery, Kafka headers) would be a **nice-to-have integration layer** but not a core obligation. Defer to Phase 3+ roadmap.

#### RFC 9457 Problem Details for HTTP APIs
- **RFC 9457 published July 2023; adoption still limited** — [RFC 9457 IETF](https://datatracker.ietf.org/doc/html/rfc9457/) (2023); most APIs still return `{"error": "..."}` despite the standard existing — [Swagger blog](https://swagger.io/blog/problem-details-rfc9457-doing-api-errors-well/) (2026) — **OPPORTUNITY**: varco's error handling could emit RFC 9457 `application/problem+json` responses from HTTP 4xx/5xx errors. Varco ships `ServiceException` with stable `code` and `message_key`; wrapping these in `application/problem+json` (type URI, status, detail, instance) is a **low-effort, high-leverage improvement** to API developer experience. Action: add an opt-in `RFC9457ErrorMiddleware` to `varco_fastapi` for 3.2+. Not breaking; ships as a new middleware.

#### RFC 9421 HTTP Message Signatures
- **RFC 9421 published; implementation emerging** — [RFC 9421 IETF](https://datatracker.ietf.org/doc/html/rfc9421/) (2023); [Python implementation released Jan 2026](https://github.com/pyauth/http-message-signatures) — **NOISE**: relevant only if varco ships APIs that require server-to-server message verification (e.g., webhook signature validation beyond HMAC). Not a primary concern unless varco's roadmap includes strict A2A mTLS or webhook security hardening.

#### Idempotency-Key Header (De-Facto Standard)
- **IETF draft expired but industry standard** — [IETF draft-07](https://datatracker.ietf.org/doc/html/draft-ietf-httpapi-idempotency-key-header-07/) (expires April 2026); Stripe popularized it, most payment platforms use identical semantics — **OPPORTUNITY**: varco's `AsyncService` could optionally store and replay idempotent requests (POST/PATCH only) based on `Idempotency-Key` header. This is a **high-value feature** for fintech/payment integrations. Action: design an optional `IdempotencyKeyMiddleware` + `IdempotencyKeyStore` (abstract, impl: in-memory + Redis/SQL backends) for 3.2 or 3.3. Do not ship as default (opt-in only) but make it prominent in docs.

#### OpenFeature Specification (CNCF Incubating, v0.9)
- **OpenFeature v0.9 stable enough for production** — [OpenFeature spec](https://openfeature.dev/docs/reference/intro/) (July 2026); Incubating maturity (CNCF); core API stable — **NOISE**: varco has no built-in feature-flagging surface; if operators want feature flags, they integrate a vendor (Unleash, LaunchDarkly, etc.) independently. Not a library concern.

**Overall assessment**: **RFC 9457 error responses are a quick win** (3.2); **Idempotency-Key is a valuable addition** (3.2–3.3); **CloudEvents wrapper is future-proofing** (Phase 3+); others are noise or deferred.

---

### 7. Data, Messaging & Database Ecosystem

#### Kafka vs NATS Positioning Clarified
- **Kafka for durable, multi-consumer event streaming; NATS for low-latency microservice messaging** — [Svix comparison](https://www.svix.com/resources/faq/kafka-vs-nats/) (2026), [DEV Community 2026 post](https://dev.to/young_gao/real-time-event-streaming-kafka-vs-redis-streams-vs-nats-in-2026-34o1) — **OBSERVATION**: varco already ships both; no change needed. Documentation already distinguishes use cases correctly (Kafka for event sourcing / CDC, NATS for light-weight pub/sub). This is validated guidance; no action.

#### Redis → Valkey Fork Settled
- **Valkey now default in AWS ElastiCache, major Linux distributions** — [DEV Community fork article](https://dev.to/synsun/redis-vs-valkey-in-2026-what-the-license-fork-actually-changed-1kni) (March 2026); Valkey BSS-licensed, Redis 8 tri-licensed (SSPL/RSALv2/AGPLv3) — **OBLIGATION**: varco's Redis backend should document Valkey compatibility (it is; Valkey is wire-compatible with Redis 7.2). For users on Redis 8+ licensing, document the license change. No code changes needed; Valkey is a drop-in replacement. Action: add a "Redis 8 licensing notes / Valkey migration" section to README/docs by end of 2026.

#### PostgreSQL as a Message Queue (pgmq, PgQue)
- **pgmq (AWS-backed) and PgQue (independent) viable alternatives to Kafka for <1K msgs/sec workloads** — [DEV Community article](https://dev.to/density_tech/do-you-really-need-kafka-a-practical-alternative-with-postgres-2de8) (2026), [pgmq repo](https://github.com/pgmq/pgmq), [PgQue](https://pgque.dev/) — **OPPORTUNITY**: varco could ship an optional `PostgresEventBus` (or `PgmqEventBus`) for deployments that prefer "one database, no extra broker." This is a **small opportunity** for single-database deployments (SMBs, startups) where Kafka is overkill. Not a priority unless varco targets resource-constrained environments. Action: file a BACKLOG item; defer to Phase 3+.

#### Beanie & MongoDB Async Maturity
- **Beanie 1.18+ production-ready, built on Motor (official async driver)** — [Beanie GitHub](https://github.com/BeanieODM/beanie) (2026), [course content](https://training.talkpython.fm/courses/mongodb-with-async-python-beanie-and-pydantic) (2026) — **OBSERVATION**: varco's `varco_beanie` backend is already production-grade; no action needed. Continue to use Beanie as the Pydantic-based ODM for MongoDB.

#### SQLAlchemy 2.x Async Instrumentation Gap
- **SQLAlchemy 2.0+ async support mature; OpenTelemetry instrumentation still catching up** — [SQLAlchemy 2.1 docs](https://docs.sqlalchemy.org/en/21/orm/extensions/asyncio.html) (2026), [OneUptime blog](https://oneuptime.com/blog/post/2026-02-06-fix-sqlalchemy-async-engine-spans-view) (Feb 2026 — OTel SQLAlchemy instrumentation does not capture async engine spans by default) — **OBSTACLE**: varco's OTel tracing for SQLAlchemy may not capture database spans if using async engines. Action: audit varco's SA tracing setup (`varco_sa/observability/`); verify OTel instrumentation covers async engines. If not, file an upstream issue with SQLAlchemy/OTel maintainers or work around it with manual `@span` decorators on repository methods.

**Overall assessment**: Valkey compatibility is a **documentation obligation**; Postgres-as-queue is a **low-priority future opportunity**; SA async instrumentation is a **known blocker for full OTel coverage** (investigate and document workaround).

---

### 8. Security, Compliance & Cryptography

#### EU Cyber Resilience Act (CRA)
- **CRA in force Dec 10, 2024; key 2026 milestones** — [EU Digital Strategy](https://digital-strategy.ec.europa.eu/en/policies/cyber-resilience-act), [Mend.io compliance guide](https://www.mend.io/blog/eu-cyber-resilience-act-compliance-guide/) (2026):
  - **June 11, 2026**: Conformity assessment body notification rules apply
  - **Sept 11, 2026**: Start of active vulnerability reporting (24-hour early warning, 72-hour full notification)
  - **Dec 11, 2027**: Full compliance deadline (CE-marking, conformity assessment)
  - **SBOM requirement**: Machine-readable Software Bill of Materials (CycloneDX or SPDX) must be submitted for high-criticality products — [CRA full details](https://securityboulevard.com/2026/05/the-eu-cyber-resilience-act-a-complete-compliance-guide-for-2026-and-beyond/) (May 2026)

- **Non-commercial OSS exemption**: Projects that do not commercialize (no SSPL, dual licensing, support contracts) are exempt from fines; "OSS stewards" (orgs that support OSS systematically) face lighter duties — **OBSERVATION**: varco is non-commercial OSS (Apache 2.0, no commercial entity behind it); no CRA fines apply. However, **downstream users** of varco in regulated industries (fintech, healthcare, critical infrastructure) must comply with CRA for their products. Action: add a "Compliance & Attestations" section to README documenting varco's security posture and stating that varco operators are responsible for their own CRA/NIS2 compliance. Optionally, generate and ship CycloneDX SBOM in `dist-info/sboms/` (via PEP 770) to help downstream compliance; this is a **table-stakes feature for enterprise adoption** by 2027.

#### NIS2 Directive
- **NIS2 applies to operators of essential services; OSS maintainers not directly regulated but users are** — [NIS2 compliance guide](https://www.legiscope.com/blog/nis2-compliance-guide.html) (2026); June 30, 2026 audit deadline for first compliance cycle — **OBSERVATION**: Same as CRA — varco is not regulated but users are. Action: document that varco's design supports NIS2 compliance by providing audit trail (DLQ, audit logs), encryption (TLS, field-level encryption), multitenancy (tenant isolation), and observability (OTel). Do not claim compliance, but help operators understand how varco features map to NIS2 requirements.

#### Post-Quantum Cryptography Readiness
- **NIST standardized ML-KEM, ML-DSA, SLH-DSA (2022–2024); practical deployment underway** — [NIST post-quantum specs](https://www.safelogic.com/compliance/pqc-standards) (2026), [White House executive order enforcement](https://blog.cloudflare.com/post-quantum-eo-2026/) (2026):
  - **Dec 31, 2030**: Federal agencies must transition most-sensitive systems to PQ crypto
  - **Dec 31, 2031**: PQ authentication deadline
  - **2030–2035**: Critical infrastructure migration expected
  - **2035+**: RSA-2048 and ECC-256 deprecated/disallowed

- **TLS 1.3 adopting hybrid PQ ciphers in 2026–2027** — [Measurement study](https://arxiv.org/pdf/2606.16473) (2026); >50% of web traffic already encrypted with PQ crypto; JDK 27 (Sept 2026) includes PQ TLS support — **OPPORTUNITY for varco's TLS layer**: By 2027, applications using varco in regulated sectors (finance, healthcare, critical infrastructure) will need post-quantum TLS support. Varco's `TrustStore` and certificate handling should be positioned to support PQ ciphers when OpenSSL/Python's ssl module adds native PQ TLS support. Action: **monitor Python's ssl module and OpenSSL for PQ TLS support**; when it arrives (expected late 2026 or 2027), add guidance to README on "Post-Quantum TLS Configuration." This is a **2027 obligation** for enterprise deployments; not urgent now but flag for 3.2+ roadmap. No code changes needed until Python/OpenSSL ship PQ TLS.

**Overall assessment**: **CRA/NIS2 compliance documentation is a 2026 obligation** (Sept); **SBOM generation is table-stakes for enterprise** (Oct–Nov 2026); **PQ cryptography is a 2027+ obligation** (monitor and prepare).

---

## Options Compared

N/A — this brief is a landscape survey, not a decision between alternative approaches.

---

## Version & Compatibility Notes

| Technology | Current Version (Sept 2026) | Status | Impact on Varco |
|---|---|---|---|
| Python (free-threading) | 3.14.x | Stable | No breaking changes; new opportunity (sync-in-async bridge) |
| PEP 695 (type params) | Adopted in 3.12+ | Stable | Nice-to-have ergonomic refresh |
| PEP 735 (dependency groups) | Implemented in uv/pip | Stable | Already in use; no action needed |
| PEP 751 (lockfile standard) | Accepted; early adoption | Emerging standard | Obligation by 2027; integrate pylock.toml generation |
| PEP 725 (external deps) | Draft; low adoption | Pre-standard | Not applicable to varco |
| PEP 770 (wheel SBOMs) | Draft; PyPI support starting | Emerging | Obligation for CRA compliance; embed CycloneDX SBOM in dist-info |
| OpenTelemetry semantic conventions | v1.44.0 | Stable | No breaking changes |
| OTel Python logs | Experimental | **Not GA** | Defer until GA (expected 2027) |
| MCP Python SDK | v2 (2026-07-28 spec) | **Breaking change** | Obligation: migrate v1 → v2 by 3.3 or 3.4 |
| Google A2A protocol | v1.0.1 (May 2026) | Stable, production-ready | Varco's SkillAdapter ready; lean into this |
| CloudEvents | v1.0 (CNCF graduated) | Stable | Optional integration layer (defer to Phase 3+) |
| AsyncAPI | v3.1.0 (Jan 31, 2026) | Stable | Not directly applicable; documentation tool only |
| RFC 9457 (problem details) | Published July 2023 | Stable | Opportunity: opt-in middleware for 3.2+ |
| Idempotency-Key | Draft expired; de-facto standard | Stable | Opportunity: new middleware + store for 3.2–3.3 |
| OpenFeature | v0.9 (CNCF Incubating) | Stable but pre-GA | Not applicable; user responsibility |
| Arazzo (workflows) | v1.1.0 (2026) | Emerging | Opportunity: document A2A workflows in Arazzo (2027+) |
| SQLAlchemy 2.x async | v2.0.52 (Aug 2026) | Mature | Known OTel instrumentation gap; audit & document |
| Beanie + MongoDB | v1.18+ (2026) | Mature | No action needed; production-ready |
| Redis vs Valkey | Valkey in AWS/major distros | Settled | Documentation obligation: license notes + migration guide |
| Postgres as queue | pgmq, PgQue stable | Emerging | Low-priority opportunity (Phase 3+) |
| EU CRA | In force; key dates Sept 2026 | **Obligation** | SBOM generation + compliance docs (Sept 2026) |
| NIS2 Directive | Audit deadline June 30, 2026 | **Obligation** | Compliance mapping documentation (Sept 2026) |
| Post-quantum crypto | NIST standards finalized; >50% web traffic PQ-encrypted | 2027+ obligation | Monitor Python/OpenSSL for PQ TLS support; prepare guidance |
| SBOM/SLSA | PEP 770 emerging; attestations stable | Emerging standard | Attestations in place; embed SBOMs in wheels (2026) |

---

## Evidence Gaps

1. **OTel Python logs GA timeline**: Search found experimental status; no official roadmap for 1.0 release date — worth a dedicated watch on the [OpenTelemetry Python GitHub](https://github.com/open-telemetry/opentelemetry-python).

2. **Python ssl module + post-quantum TLS integration**: NIST standards released; practical TLS 1.3 adoption timeline unclear — worth monitoring [Python ssl module issues](https://github.com/python/cpython/issues) and [OpenSSL releases](https://www.openssl.org/news/).

3. **SQLAlchemy OTel instrumentation async engines**: Documented as a gap in Feb 2026; unclear if fixed in 2.0.52+ — worth testing against latest OTel SQLAlchemy instrumentor.

4. **Arazzo adoption by API-first frameworks**: Few practical examples of OpenAPI 3.2 + Arazzo in production — wait for more maturity before designing varco's workflow surface.

---

## Librarian's Note

**What the sources indicate:**

varco is well-positioned for late 2026, with most ecosystem shifts either validated (uv, PEP 735, Beanie, SQLAlchemy 2.x) or **non-blocking for today** (free-threading, PEP 695 ergonomics, experimental OTel logs).

**Two immediate obligations exist:**

1. **Compliance & SBOMs (September 2026)**: Generate CycloneDX SBOMs, document CRA/NIS2 compliance mapping, and add Valkey migration notes to README. These are table-stakes for enterprise adoption and should ship in 3.1.1 or 3.2.

2. **MCP v2 migration (2026–2027)**: Varco's unmigrated MCP surface must move to v2 API. This is a breaking change that deserves a dedicated sprint; do not bundle with other work. Plan for 3.2 or 3.3.

**Three high-value opportunities** (defer to 3.2–3.3 roadmap):

- **RFC 9457 error responses**: Opt-in middleware for `application/problem+json` responses — quick win for API developer experience.
- **Idempotency-Key support**: New middleware + store (in-memory, Redis, SQL backends) for replaying idempotent requests — high value for fintech.
- **A2A documentation**: Lean into varco's existing SkillAdapter; publish A2A examples and best practices prominently.

**Lowest-priority items** (Phase 3+ / watch list):

- PQ cryptography readiness (2027+), Postgres-as-queue backend, CloudEvents wrapper, Arazzo workflow specs.

**No breaking changes from the ecosystem** — async-first design remains sound; free-threading does not disrupt the model; typing improvements are opt-in ergonomics. The roadmap has breathing room to absorb these shifts incrementally without a reactive redesign.

