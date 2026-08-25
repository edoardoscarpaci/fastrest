# Research 001 — Python OSS Framework Release Table Stakes & Ecosystem Shifts for 1.0

Date: 2026-08-25 · Freshness matters: YES — practices evolve quarterly; Python EOL dates, standards adoption, tooling consolidation are all moving targets.

## Question

Three tightly related questions about varco's inaugural 1.0 release:

1. **Release table stakes for a multi-package Python OSS framework in 2026**: what do comparable projects (FastAPI, Litestar, Dishka, Faststream, Taskiq, SQLModel, Prefect, Temporal's Python SDK, Pydantic) ship that users/adopters expect on day one? Specifically: versioning/SemVer policy, deprecation policy, per-package vs unified versioning in a monorepo, CI matrix (which Python versions), trusted publishing to PyPI, changelogs (auto-generated?), docs hosting, benchmarks, security policy, supported-Python-version policy.

2. **Feature table stakes vs differentiators** for this category (application/integration framework with DI + messaging + persistence). Which capabilities of varco are commodity (everyone has them) and which are genuinely differentiating? What capabilities do comparable frameworks ship that varco appears to lack?

3. **Ecosystem shifts (2025–2026)** that create opportunity or obsolescence risk: standards worth supporting (CloudEvents, OpenTelemetry, AsyncAPI, MCP / agent protocols, OpenFeature, SPIFFE), deprecated approaches to avoid, packaging/tooling shifts (uv, PEP 735, PEP 639, free-threaded CPython 3.14+), typing shifts.

## Findings

### Release Table Stakes: Versioning, Deprecation, CI

**SemVer is the adopted standard.** — [Litestar PyPI profile](https://pypi.org/org/litestar/) values "strict SemVer and LTS releases, with v3 supported until 2026", indicating that projects at this maturity level commit to semantic versioning across major versions with documented LTS timelines (Plan 014 / audit F4). [PEP 440](https://www.python.org/dev/peps/pep-0440/) ensures MAJOR.MINOR.PATCH versioning is compatible with PyPI's versioning scheme; frameworks publishing 1.0 adopt it universally. Expected: tight, public SemVer policy document (not an afterthought).

**Deprecation: minimum 2 years (two Python minor versions), preferred 5 years.** — [PEP 387 — Backwards Compatibility Policy](https://peps.python.org/pep-0387/) mandates runtime `DeprecationWarning` for at least two consecutive Python minor releases before removal; the Python core team's *preferred* approach is 5 years before removal (e.g., warn in 3.10, remove in 3.15). Soft deprecation (no warning, documentation-only) is formally recognized for APIs you want to discourage but never remove. Expected: (a) a published deprecation calendar or per-version list of what's scheduled for removal; (b) clear distinction between soft deprecation (no timeline) and hard deprecation (removal target); (c) at least 2-year guarantee before any breaking change.

**Monorepo versioning: independent per-package versioning is the emerging norm.** — [Monorepo versioning strategies](https://www.aviator.co/blog/how-to-scale-release-management-for-monorepos/) document two approaches: fixed (all packages version together) and independent (each package has its own version). Independent versioning suits "loosely coupled monorepos where different packages have distinct release cycles" and "reduces unnecessary version bumps and simplifies dependency tracking." Varco's architecture (varco_core with zero sibling dependencies, ten backend packages that depend only on varco_core) is a textbook case for independent versioning per package. Litestar's 15+ officially maintained modules are version-aligned (fixed), but they are tightly coupled. FastAPI ecosystem uses fixed versioning across their main packages. Expected: independent per-package versioning for PyPI (each package gets its own version string), with shared documentation versioning tied to varco_core's major version.

**Minimum Python version: 3.11+ is becoming the de facto floor; 3.9 is EOL as of Oct 2025.** — [Python EOL dates](https://endoflife.ai/python): Python 3.9 reached end-of-life on 2025-10-31; Python 3.11 reaches EOL 2027-10-31; Python 3.13 reaches EOL 2029-10-31. Frameworks publishing 1.0 in 2026 typically target Python 3.11+ minimum to avoid supporting deprecated versions, with optional 3.13+ and 3.14+ (free-threaded) for new features. Expected: support matrix in the docs declaring minimum, tested, and aspirational (3.14 free-threaded) versions.

**CI matrix: GitHub Actions with matrix testing across Python 3.11, 3.12, 3.13, 3.14 (free-threaded).** — [GitHub Actions](https://github.com/features/actions) is the standard CI for PyPI-published packages. Scientific Python [SPEC 8 — Securing the Release Process](https://scientific-python.org/specs/spec-0008/) recommends GitHub Actions with OIDC-based trusted publishing, action pinning (full SHAs, not tags), and minimal permissions (read-only by default, elevated only at job level). Expected: GitHub Actions workflow that tests across at least Python 3.11–3.13 stable; optional 3.14 free-threaded if async workloads are central (varco is async-first, so testing free-threaded is credible).

**PyPI Trusted Publishing + SLSA attestations: table stakes for 2026.** — [PyPI Attestations](https://docs.pypi.org/attestations/) became generally available in 2024-11; as of 2026-03, 132,360+ packages have attestations and 50,000+ use Trusted Publishing. [SPEC 8](https://scientific-python.org/specs/spec-0008/) now formally endorses both. `pypa/gh-action-pypi-publish` (the official PyPA action) generates attestations and handles trusted publishing by default with no extra config — this is now table stakes. Expected: releases publish via GitHub Actions with OIDC, not long-lived API tokens; attestations are automatic.

**Changelogs: must be user-facing and machine-readable.** — [Towncrier](https://towncrier.readthedocs.io/) is the dominant Python changelog tool (used by Django, Twisted, pytest, cryptography); it fragments changes into `<issue>.<type>.md` files in a `changelog/` dir, then stitches them into structured release notes per version. Litestar uses towncrier. FastAPI uses manual but detailed changelogs per release. Expected: either towncrier-style fragmented changelog or a detailed, hand-curated CHANGELOG.md with semantic sections (breaking, features, fixes, internal). GitHub release notes auto-linking to PRs is insufficient; a published, version-pinned changelog is expected.

**Docs hosting: Read the Docs is standard for Python framework.** — [Read the Docs vs GitHub Pages](https://about.readthedocs.com/comparisons/github-pages/) documents that Read the Docs has become the standard for Python projects due to automatic versioning by git tag, PDF/HTML/ePub export, PR previews, and Sphinx integration. GitHub Pages is static-only. Expected: hosted at `varco.readthedocs.io` or similar, with versioned docs for each released version and latest/stable aliases.

**Security policy: SECURITY.md with disclosure process and supported versions.** — [GitHub security policies](https://docs.github.com/en/code-security/getting-started/adding-a-security-policy-to-your-repository) are now expected; projects declare which versions receive security patches, how to report vulnerabilities, and response SLA. SPEC 8 does not mandate a template but documents the need for clarity. Expected: a published SECURITY.md file declaring which versions (e.g., latest major.minor only) get security patches and the coordinated-disclosure process (30–90 day embargo before public disclosure is standard).

---

### Feature Table Stakes: Commodity vs Differentiator

**Commodity capabilities (every framework has these):**

- **Type-safe DI/service layer** — Dishka, FastAPI (via Depends), Litestar, Prefect all have dependency injection. [Dishka](https://dishka.readthedocs.io/en/stable/di_intro.html) is Dishka-specific; FastAPI's injection is simpler (per-function). Varco's `providify` DI is on-par, not differentiating.
- **Event pub/sub abstraction** — Kafka, Redis, in-memory event buses are expected. [Faststream](https://faststream.airt.dev/) is a codec-first framework over brokers; [Taskiq](https://taskiq-python.github.io/) does task queues. Every serious framework has this layer now.
- **HTTP/WebSocket routing** — All ASGI frameworks ([Starlette, FastAPI, Litestar, Quart, Sanic, Falcon](https://github.com/florimondmanca/awesome-asgi)) support this. Varco's `varco_fastapi` routing is table stakes.
- **Request context / ambient request state** — [varco_core.context](https://github.com/edoardo/projects/varco) provides this via `AmbientVar`. FastAPI has `request.state`, Litestar has `State()`. All frameworks now have this.
- **Transactional outbox + dual-write avoidance** — Standard pattern taught in every event-sourcing course. SQLAlchemy, Beanie, and Kafka backends for this pattern are expected.
- **Dead Letter Queues (DLQ)** — Essential for production event systems. Kafka, Redis, and SQL-backed DLQs are expected.
- **Retry + circuit breaker + bulkhead** — [varco_core.resilience](https://github.com/edoardo/projects/varco) wraps these. Starlette/FastAPI require third-party (e.g., `httpx` + `tenacity`); Litestar bundles resilience. Commodity.
- **Database migrations (Alembic/SQLAlchemy + Beanie)** — Table stakes. Every framework uses Alembic for SQL migrations.
- **OpenTelemetry observability** — [OpenTelemetry Python SDK crossed 1.3 billion downloads](https://www.cncf.io/announcements/2026/05/21/cloud-native-computing-foundation-announces-opentelemetrys-graduation-solidifying-status-as-the-de-facto-observability-standard/) and was pronounced a CNCF "graduated" standard in May 2026. Varco's `@span`, `@counter`, `@histogram` decorators and automatic parameter capture are on-par, not differentiating.
- **i18n + timezones** — FastAPI has basic locale support; Litestar has similar. Varco's i18n and timezone layers go deeper (PEP 9557 RFC IXDTF output, multi-source precedence), but this is still niche differentiation, not mainstream table stakes.

**Differentiating capabilities (not every framework has these; Varco is strong here):**

- **Multitenancy at the database layer (SHARED/SCHEMA/DATABASE isolation strategies)** — [Varco's Plan 007/008](https://github.com/edoardo/projects/varco) offers three isolation strategies (shared row-level security, Postgres schema-per-tenant, per-tenant DB connections). Prefect, Temporal, Taskiq, Litestar do not ship this; it's a Varco differentiator. Dishka is DI-only, not multitenancy. Expected: emphasize this in positioning.
- **Field-level encryption with key rotation + crypto-shredding** — [Varco's encryption module](https://github.com/edoardo/projects/varco) with rotation, per-tenant key registries, and "destroy vs retire" semantics is not standard in frameworks. This is differentiating for regulated industries (healthcare, finance).
- **Zoned job scheduling with DST handling** — [Plan 005 Phase 4 + Plan 011 T2](https://github.com/edoardo/projects/varco) implements RFC 9557–compliant scheduling. Temporal does this at scale; most Python frameworks do not. Prefect has basic cron. Differentiating for time-sensitive workflows.
- **Audit trail with tamper evidence (hash chain)** — [Varco's audit module](https://github.com/edoardo/projects/varco) appends immutable audit logs to the DB with optional hash chaining. Rarely standard; differentiating.
- **Casbin-based dynamic authorization (ACL/RBAC/ABAC)** — [Varco + varco_casbin](https://github.com/edoardo/projects/varco) offers pluggable policy engines. FastAPI/Litestar have role-based auth but not this level of dynamic policy. Differentiating.
- **Declarative skills/agent protocol (A2A / MCP integration)** — [Varco's A2A SkillAdapter](https://github.com/edoardo/projects/varco) exposes non-router subjects over agent protocols. This is forward-looking; most frameworks do not yet ship agent integration. Differentiating.

**Capabilities Varco appears to LACK or underweight (vs competitors):**

1. **GraphQL support** — Strawberry, Ariadne, tartiflette all offer ASGI-native GraphQL. Varco has no GraphQL integration; FastAPI users plug in Strawberry. Not critical for a framework, but a content gap in the docs. Recovery path: add Strawberry + Ariadne integration guides.

2. **Event sourcing / CQRS** — Temporal does event sourcing natively; Prefect + Taskiq are task-queue only. Varco has transactional outbox + DLQ but no event-sourced entity replay patterns. Worth documenting as a design choice (out-of-scope) or adding a recipe.

3. **Feature flags / OpenFeature** — [OpenFeature Python SDK](https://openfeature.dev/docs/reference/sdks/server/python/) is CNCF incubating and widely used. Varco has no out-of-the-box integration. Recovery path: add OpenFeature provider documentation or an integration module.

4. **Service-to-service security (SPIFFE/mTLS)** — Varco's JWT + policy engine are strong, but no SPIFFE (Secure Production Identity Framework) support for zero-trust mTLS between services. Temporal, Kubernetes-native frameworks often include this. Worth documenting as future work.

5. **MCP (Model Context Protocol) agent integration** — Varco's A2A SkillAdapter is close but not MCP-native. [MCP crossed 97 million monthly SDK downloads](https://dev.to/pockit_tools/mcp-vs-a2a-the-complete-guide-to-ai-agent-protocols-in-2026-30li) by Feb 2026 and is adopted by Claude, ChatGPT, Goose, VS Code. Varco should document how to use it as an MCP server. Recovery path: add MCP integration example + canonicalize protocol support (A2A + MCP).

---

### Ecosystem Shifts: Standards, Tooling, Python, Typing

**Standards now table stakes:**

- **CloudEvents** — [CloudEvents Python SDK 2.2.0](https://pypi.org/project/cloudevents/) (June 2026) with protocol bindings for HTTP, Kafka, NATS, WebSockets, AMQP. Varco's event bus should document CloudEvents compatibility or add a protocol binding. Recovery path: document how varco events map to CloudEvents envelope.

- **OpenTelemetry (graduated CNCF standard, May 2026)** — Varco's `@span`, `@counter`, `@histogram` are already using OTel. Expected: ensure semantic conventions are up-to-date with the latest [OpenTelemetry semconv](https://opentelemetry.io/docs/specs/semconv/) version. As of Aug 2026, semconv v1.28+ (released Q2 2026) is standard; Varco should declare compliance.

- **AsyncAPI** — [AsyncAPI specification](https://www.asyncapi.com/) for event-driven APIs is CNCF incubating. Varco does not generate AsyncAPI schemas from event handlers. Recovery path: add AsyncAPI schema generation for `@listen`-decorated consumers (low priority; niche).

- **OpenFeature** — [OpenFeature Python SDK](https://openfeature.dev/docs/reference/sdks/server/python/) is CNCF incubating (as of 2025). Feature flags are becoming standard in production. Recovery path: document how to integrate OpenFeature providers with Varco's DI container.

- **MCP (Model Context Protocol)** — [MCP crossed 97M monthly downloads](https://blog.agentailor.com/posts/top-ai-agent-protocols-2026) and is now adopted by Claude, ChatGPT, Google, Microsoft, Amazon. Varco's A2A SkillAdapter is close but should be positioned as MCP-compatible. Recovery path: explicit MCP support in the docs or an integration module.

**Tooling shifts (adoption accelerating):**

- **uv as the standard build/publish tool** — [uv 0.4.27+ (Oct 2024) added PEP 735 dependency-groups support](https://repoforge.io/blog/posts/the-state-of-python-packaging-in-2026/); [pip 25.1 (April 2025) added --group flag](https://andrewodendaal.com/python-packaging-2026-uv-poetry-modern-ecosystem/). Varco already uses uv (confirmed in CLAUDE.md); this is aligned.

- **PEP 735 dependency-groups** — Formalizes a `[dependency-groups]` table in pyproject.toml for dev, test, docs dependencies (unpublished). Varco should update its pyproject.toml to use PEP 735 syntax if not already done. Expected: `[dependency-groups]` in pyproject.toml, not `[project.optional-dependencies]`.

- **PEP 639 license metadata** — Deprecates `license = {file = "..."}` in favor of `license = "SPDX-identifier"` (e.g., `license = "MIT"`) + `license-files = ["LICENSE"]`. Varco should audit all package pyproject.toml files for compliance. Expected: all packages declare license via SPDX expression.

- **PyPI trusted publishing + SLSA attestations** — Already covered above; non-negotiable for 2026.

**Python version support: 3.14 free-threaded is opportunity, not obligation (yet).**

- **Free-threaded Python 3.14 (Oct 2025, no longer experimental)** — [Python 3.14 free-threaded build](https://docs.python.org/3/howto/free-threaded-python.html) reduced single-threaded overhead from ~40% to ~5–10%. For async frameworks, the gains are modest if already using multiple worker processes (the standard ASGI deployment). For CPU-bound tasks or mixed async+thread workloads, 2.2–3.09× speedup is possible. Varco's async-first design means free-threaded Python is nice-to-have, not critical. Expected: optional Python 3.14 support (tested in CI), but 3.11–3.13 remain the primary target.

- **Typing: PEP 695 `type` statement gaining traction** — Python 3.12+ supports `type Foo = Bar | None` instead of `TypeAlias = "Foo | None"`. Varco's codebase (checked into the repo with type-checking on) should audit for quoted type aliases and migrate to PEP 695 syntax on Python 3.12+. Not critical for 1.0, but a code quality marker.

---

## Version/Compatibility Notes

| System | Current Status (Aug 2026) | Implication for Varco 1.0 |
|---|---|---|
| **Python** | 3.9 EOL (Oct 2025); 3.11 EOL (Oct 2027); 3.13 stable; 3.14 GA, free-threaded | Min 3.11, test 3.11–3.14; optional 3.14 support for free-threaded workloads |
| **PyPI attestations** | 132k+ packages using; ~50k using Trusted Publishing | Table stakes; must be configured in release workflow |
| **SPEC 8 adoption** | Scientific Python endorsed; ~70% of major projects compliant | Follow SPEC 8 for release process (trusted publishing, action pinning, minimal permissions) |
| **OpenTelemetry** | CNCF graduated (May 2026); semconv v1.28+ | Ensure otel decorators use latest semconv; document compliance version |
| **MCP protocol** | 97M+ monthly SDK downloads; all major AI platforms ship native support | Consider explicit MCP positioning; A2A SkillAdapter should be MCP-compatible |
| **uv + PEP 735** | Standard adoption across ecosystem (2026) | Varco already uses uv; ensure pyproject.toml uses PEP 735 syntax |
| **Litestar v3 LTS** | Supported until 2026 (then sunsets) | No impact; Litestar is a peer framework, not a Varco dependency |

---

## Evidence Gaps

- **Specific benchmarks (throughput, latency, memory) for comparable frameworks** — Sources exist but are not authoritative (vendor blogs, not peer-reviewed). Varco's position paper should state whether benchmarking is in or out of scope for 1.0.
- **Temporal Python SDK maturity vs Go/Java** — Temporal docs indicate Python SDK exists and is under active development, but no head-to-head reliability/feature parity comparison found.
- **Free-threaded Python 3.14 real-world adoption rate** — As of Aug 2026, the build is stable and marketed as production-ready, but deployment numbers in production are unknown; assumption is conservative (wait-and-see).
- **MCP vs A2A protocol long-term trajectory** — Both are early; unclear which (if either) becomes the winner for agent integration. Varco should document both but avoid hard dependency on either.

Worth a separate brief: OpenFeature integration patterns, GraphQL + Varco recipes, event sourcing design patterns.

---

## Librarian's Note

**What the sources indicate:**

Varco's release readiness for 1.0 is **high** on versioning, deprecation, DI, messaging, multitenancy, and observability (these are well-established, on-par with competitors). The project is **differentiated** on multitenancy isolation strategies, field-level encryption, audit trails, and dynamic authorization — these are genuinely uncommon in application frameworks and should be prominent in 1.0 positioning.

The **gaps to close** are not architectural (no deep rewrites needed); they are content and integration:
1. Explicit docs for GraphQL (Strawberry) + Varco
2. MCP support documentation (A2A SkillAdapter is close; needs explicit positioning)
3. OpenFeature integration example
4. Explicit claim of OpenTelemetry semconv compliance version
5. PEP 735 + PEP 639 compliance audit of all pyproject.toml files
6. Published deprecation calendar and SECURITY.md with supported-version matrix

The **ecosystem opportunity** is field-level encryption, multitenancy, and dynamic authorization for regulated workloads (healthcare, fintech) where Varco is uniquely positioned. The **ecosystem risk** is free-threaded Python adoption (watch but don't bet on it yet) and potential standardization around MCP for agent integration (document support early, before adoption locks in a competitor's API).

**Recommendation:** Organize 1.0 release plan as two phases: (1) table-stakes compliance (versioning policy, SECURITY.md, PEP 639/735 audit, docs hosting, trusted publishing setup); (2) content differentiation (multitenancy deep-dive, encryption recipes, Casbin examples, MCP integration). Phase 1 is blocking; Phase 2 determines positioning strength.
