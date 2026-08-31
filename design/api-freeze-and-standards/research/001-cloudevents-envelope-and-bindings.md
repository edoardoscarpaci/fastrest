# Research 001 — CloudEvents Envelope and Protocol Bindings

Date: 2026-08-30 · Freshness matters: **yes** — spec versioning, SDK maintenance, and protocol support evolve

## Question

Can varco add an optional CloudEvents-compliant envelope adapter without violating the "no third-party runtime dependencies in varco_core" rule, and what are the exact wire formats for Kafka, NATS, and Redis protocol bindings?

## Findings

### Current Specification Version and Stability

- **v1.0.2 is the latest ratified core spec** (released February 5, 2022) — [GitHub cloudevents/spec releases](https://github.com/cloudevents/spec/releases/tag/v1.0.2)
- **CNCF Graduated status** as of January 25, 2024 — [CNCF graduation announcement](https://cloudevents.io/blog/2024-01-25/)
- **Versioning guarantee**: CloudEvents uses semantic versioning; new optional properties can be added in MINOR versions without breaking v1.0 consumers — [Primer](https://github.com/cloudevents/spec/blob/main/cloudevents/primer.md)

### Required vs. Optional Context Attributes

**Required (4 attributes, all strings except `id` which can be any type)**:
- **`specversion`** — must be `"1.0"` for v1.0.2 — [spec.md line ~120](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md)
- **`type`** — event category, recommended reverse-DNS prefix (e.g., `com.example.sampletype1`)
- **`source`** — non-empty URI-reference (absolute URI recommended), must be paired with `id` for uniqueness
- **`id`** — string identifier; spec mandates: "Producers MUST ensure that `source` + `id` is unique for each distinct event"

**Optional (5 attributes + `data`)**:
- **`datacontenttype`** — RFC 2046 media type (e.g., `application/json`)
- **`dataschema`** — URI identifying the schema the data adheres to
- **`subject`** — string describing the event subject within producer context
- **`time`** — RFC 3339 timestamp of when the occurrence happened
- **`data`** — any type; the application event payload

Sources: [Spec v1.0.2 §3–§4](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md)

### Structured vs. Binary Content Mode (Protocol-Agnostic Definitions)

**Structured Content Mode**:
- Event metadata attributes and data are combined into a **single serialized body** using an event format (typically JSON)
- The format is identified by the message/frame's content-type header (e.g., `application/cloudevents+json`)
- Single, portable representation across all transports

**Binary Content Mode**:
- Event data is placed in the **transport body as-is**; its content-type header declares the media type
- All other attributes (id, source, type, etc.) are **mapped to transport headers** with a binding-specific prefix
- Attributes are serialized as strings in header values; reduces overhead for large payloads
- Not supported by all protocol bindings (e.g., NATS does not support binary mode)

Source: [Kafka Protocol Binding §4.2, §4.3](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/bindings/kafka-protocol-binding.md)

### Protocol Bindings: Kafka

**Header Naming (Binary Mode)**:
- All CloudEvents attributes are prefixed with **`ce_`** when mapped to Kafka message headers
- Examples: `ce_specversion`, `ce_id`, `ce_type`, `ce_source`, `ce_time`, `ce_subject`, `ce_datacontenttype`, `ce_dataschema`
- Header keys and values MUST be UTF-8 strings
- Content-type is either in a `content-type` header (binary mode) or implicit in the structured format

**Content Mode Selection**:
- **Structured mode**: `content-type` header value MUST start with `application/cloudevents` (e.g., `application/cloudevents+json`); metadata and data in the Kafka message value as JSON
- **Binary mode**: event data in message value as-is; all CloudEvents context attributes in headers with `ce_` prefix
- Kafka 0.11.0.0+ supports both; older versions (0.10.x) can only use structured mode

**Special Extensions in Kafka Binding**:
- **`partitionkey`** — optional extension to control Kafka partition routing; if present, its value SHOULD be used as the Kafka partition key

Sources: [Kafka Protocol Binding v1.0.2](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/bindings/kafka-protocol-binding.md)

### Protocol Bindings: NATS

**Content Mode Support**:
- **Only structured mode is supported** — "NATS will only support _structured_ data mode at this time. Today, the NATS protocol does not support custom message headers, necessary for _binary_ mode" — [NATS Binding v1.0.2](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/bindings/nats-protocol-binding.md)

**Payload Structure (Structured Mode Only)**:
- The NATS message payload MUST be the **JSON event format** (all CloudEvents attributes + data) serialized as UTF-8 JSON
- No header-based attribute transmission; all metadata in the JSON body
- Content is effectively the same as Kafka's structured mode body

Sources: [NATS Protocol Binding v1.0.2](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/bindings/nats-protocol-binding.md)

### Protocol Bindings: Redis

**Finding**: **No official CloudEvents binding for Redis in the v1.0.2 specification**. The spec's bindings directory (`cloudevents/bindings/`) contains only HTTP, Kafka, AMQP, and NATS as of v1.0.2. A Redis binding, if adopted, would need to be hand-rolled by varco based on semantic extension of structured mode (Redis pub/sub payload = JSON; Redis Streams entry data = JSON).

Sources: [CloudEvents spec/bindings directory listing](https://github.com/cloudevents/spec/tree/v1.0.2/cloudevents/bindings)

### Extension Attributes: Naming Rules and Registered Extensions

**Naming Rules**:
- Extension attribute names MUST consist of **lowercase ASCII letters (a–z) and digits (0–9) only**
- MUST begin with a **lowercase letter**
- No separators, underscores, or hyphens
- Recommended: descriptive names to reduce collision risk

Sources: [Spec v1.0.2 §3.2](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md)

**Documented Extensions in v1.0.2**:
- **Dataref** — Claim Check pattern; enables separating event payload from metadata
- **Distributed Tracing** — Correlation across systems (note: spec v1.0.2's documented-extensions.md does not explicitly name `traceparent`/`tracestate` as sub-extensions; research gap below)
- **Partitioning** — Routing and organization hints (includes Kafka's `partitionkey`)
- **Sampling** — Event sampling rate control
- **Sequence** — Event ordering tracking

Sources: [Documented Extensions v1.0.2](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/documented-extensions.md)

**Relevance to varco's tenant + correlation_id**:
- The **Distributed Tracing extension** is the spec-approved path for correlation IDs
- The **Partitioning extension** can carry routing hints (varco could encode `tenant_id` as a custom extension following alphanumeric naming rules, e.g., `tenantid` or `varcotenant`)
- Both are optional; implementations that ignore unknown extensions remain compliant

### Python SDK: CloudEvents Library

**Current Version**: **2.2.0** (released June 11, 2026) — [PyPI cloudevents](https://pypi.org/project/cloudevents/)

**Pydantic v2 Support**:
- ✅ Full support for Pydantic v2
- ✅ Backward compatible with Pydantic v1 (compatibility layer added in v1.10.0)
- The SDK includes a **dedicated Pydantic model class** for CloudEvents integration with FastAPI and other Pydantic-dependent frameworks
- No Pydantic dependency listed as mandatory in the SDK itself; type annotations are present but the core serialization is not Pydantic-dependent

Sources: [SDK CHANGELOG](https://github.com/cloudevents/sdk-python/blob/main/CHANGELOG.md), [PyPI cloudevents page](https://pypi.org/project/cloudevents/)

**Key SDK Capabilities**:
- Protocol bindings: HTTP, Kafka, RabbitMQ, NATS (inferred from features)
- Methods like `to_binary()` and `to_structured()` for content mode conversion
- Type safety with comprehensive annotations
- Python 3.10+ required

**Dependency Footprint**: The SDK itself has minimal external dependencies (the SDK lists only Pydantic as optional/feature-dependent). **This makes hand-rolling a CloudEvents adapter in varco_core feasible without pulling in external packages.**

Sources: [PyPI cloudevents](https://pypi.org/project/cloudevents/), [SDK repository](https://github.com/cloudevents/sdk-python)

### Migration & Coexistence Patterns

**Dual Envelope Approach** (per-endpoint format negotiation):
- Expose both native and CloudEvents envelopes; let subscribers/endpoints opt in via a `event_format` setting
- Example: `event_format: "cloudevents"` vs. `event_format: "legacy"` (varco's native)
- Allows new consumers to adopt CloudEvents while existing ones remain unchanged

**Field Mapping Strategy**:
1. Map varco's native envelope to CloudEvents attributes:
   - `event_id` → CloudEvents `id`
   - `event_type` → CloudEvents `type`
   - `timestamp` → CloudEvents `time` (RFC 3339 format)
   - Existing event payload → CloudEvents `data`
   - `correlation_id` → Custom extension (e.g., `correlationid`) or Distributed Tracing extension
   - `tenant_id` → Custom extension (e.g., `tenantid`) or encoded in `source`/`subject`

**Schema Versioning** (recommended for auditability):
- Add a `schema_version` field to both native and CloudEvents envelopes
- Enables future envelope changes to be tracked; zero cost on startup

**Migration Timeline**:
- Typical production migration: **12–18 months** from announcement to legacy format deprecation
- Phase 1: Announce new optional CloudEvents format; document mapping
- Phase 2: Dual-emit period (both native and CloudEvents sent, opt-in CloudEvents consumption)
- Phase 3: Sunset native format; CloudEvents becomes default

Sources: [SDK MIGRATION.md](https://github.com/cloudevents/sdk-python/blob/main/MIGRATION.md), [5/6 CloudEvents Patterns](https://medium.com/@kaushalsinh73/5-cloudevents-patterns-for-event-driven-platforms-cb7c20290c27)

## Options Compared

| Option | ✅ Strengths | ❌ Weaknesses | Evidence |
|---|---|---|---|
| **Use official cloudevents SDK** | Production-ready, Pydantic v2 support, maintains bindings, reduces hand-rolling | Adds external dependency to varco (violates "no deps in varco_core"); SDK marked "work in progress" | [PyPI cloudevents](https://pypi.org/project/cloudevents/), [CLAUDE.md rule](file://<repo>/CLAUDE.md) |
| **Hand-roll adapter in varco_core** | Complies with "no third-party deps" rule; ~200 lines suffice for Kafka + NATS structured mode; full control | Maintenance burden for spec compliance; no binary mode support for NATS (spec limitation); Redis binding undefined | [Kafka binding spec](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/bindings/kafka-protocol-binding.md), [NATS binding spec](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/bindings/nats-protocol-binding.md) |
| **Plugin-based: varco_cloudevents package (backend)** | Decouples from varco_core; can pull in SDK if desired; opt-in; follows varco's pattern | Adds a new workspace member; SDK dependency still external but isolated; NATS binary mode still impossible (protocol limitation) | Varco's existing backend-package pattern (CLAUDE.md) |

## Version/Compatibility Notes

| System | Version | Status | Notes |
|---|---|---|---|
| **CloudEvents Core Spec** | v1.0.2 | Stable (CNCF Graduated Jan 2024) | Released Feb 5, 2022; no breaking changes expected in v1.x. Optional extensions can be added in future MINOR versions. |
| **Kafka Protocol Binding** | v1.0.2 | Stable | Both binary and structured modes supported; Kafka 0.11.0.0+. Uses `ce_` header prefix. |
| **NATS Protocol Binding** | v1.0.2 | Stable | Structured mode only (binary mode requires message headers, not yet in NATS). |
| **Redis Protocol Binding** | N/A | No official spec | Custom implementation required; varco would define its own convention (recommend structured JSON payload). |
| **CloudEvents SDK (Python)** | 2.2.0 | Production/Stable | Released June 11, 2026. Full Pydantic v2 + v1 support. Python 3.10+ only. |

## Evidence Gaps

1. **Distributed Tracing extension details** — v1.0.2's documented-extensions.md does not explicitly define the shape of `traceparent`/`tracestate` attributes within the Distributed Tracing extension. These may be inherited from W3C Trace Context spec. Worth a separate brief if varco adopts them.

2. **Redis binding specification** — No official CloudEvents protocol binding for Redis pub/sub or Streams exists in the v1.0.2 spec. Varco would need to define its own convention (e.g., structured JSON body for both pub/sub and Streams). Guidance on pub/sub vs. Streams choice would require operational analysis outside this brief.

3. **SDK's Pydantic model class** — The CloudEvents SDK includes a Pydantic model class mentioned in CHANGELOG, but detailed API (validation rules, integration surface) was not examined. Worth a lookup if varco intends to use the SDK in the future.

4. **Tenant encoding best practice** — No guidance found in the spec on how multi-tenant systems should encode tenant identity in CloudEvents. The Partitioning extension is a possibility, but examples in production systems (e.g., Azure EventGrid, AWS EventBridge) are not cited in official docs. Recommend surveying vendor implementations if tenant isolation is critical to varco's envelope design.

## Librarian's Note

**What the sources indicate**: v1.0.2 is production-ready and stable; CloudEvents is a well-governed CNCF spec with clear bindings for Kafka (binary + structured) and NATS (structured only). The Python SDK is maintained and supports Pydantic v2, but adding it as a dependency breaks varco_core's "no third-party deps" rule. **The evidence favours a hand-rolled adapter in varco_core** (~200 lines of spec-compliant JSON serialization + header mapping for Kafka/NATS) or **a separate backend package** (`varco_cloudevents`) that can optionally pull in the SDK for advanced features. **Redis has no official binding; varco must define its own.** The lack of Distributed Tracing and tenant-encoding guidance in the spec is a known limitation, not a blocker — varco can extend with custom attributes (`correlationid`, `tenantid`) following the alphanumeric naming rules.

---

**Sources**:
- [CloudEvents Specification v1.0.2 (GitHub)](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md)
- [Kafka Protocol Binding v1.0.2](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/bindings/kafka-protocol-binding.md)
- [NATS Protocol Binding v1.0.2](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/bindings/nats-protocol-binding.md)
- [Documented Extensions v1.0.2](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/documented-extensions.md)
- [CloudEvents Python SDK (PyPI)](https://pypi.org/project/cloudevents/)
- [CloudEvents SDK CHANGELOG (GitHub)](https://github.com/cloudevents/sdk-python/blob/main/CHANGELOG.md)
- [CloudEvents Graduated Announcement (2024-01-25)](https://cloudevents.io/blog/2024-01-25/)
- [CloudEvents Primer](https://github.com/cloudevents/spec/blob/main/cloudevents/primer.md)
- [CloudEvents Events Patterns (Medium)](https://medium.com/@kaushalsinh73/5-cloudevents-patterns-for-event-driven-platforms-cb7c20290c27)
