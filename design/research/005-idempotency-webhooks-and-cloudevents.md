# Research 005 — Idempotency-Key HTTP middleware, webhook signing, and CloudEvents envelope

Date: 2026-09-04 · Freshness matters: **yes** — specs evolve, implementation practices solidify; draft expiry and SDK versions matter.

## Question

Three backlog items cite normative specifications. What are their exact current status, MUST/SHOULD requirements, and real-world implementation patterns?

1. **D1 — Idempotency-Key HTTP middleware**: draft-ietf-httpapi-idempotency-key-header-07 (cited in backlog)
2. **D4 — Outbound webhook signing**: RFC 9421 and "HMAC / RFC 9421 signing" (cited in backlog)
3. **N2 — CloudEvents envelope**: structured-mode CloudEvents over varco's event bus

## Findings

### §1 — Idempotency-Key HTTP middleware (backlog D1)

**Draft Status & Expiry**
- **Current document**: [draft-ietf-httpapi-idempotency-key-header-07](https://datatracker.ietf.org/doc/html/draft-ietf-httpapi-idempotency-key-header-07) (October 15, 2025)
- **Status**: Intended Standards Track; **expired April 18, 2026** without RFC publication — still a draft, not an RFC
- The IETF HTTPAPI Working Group has not advanced it further as of 2026-09-04

**Applicable Methods & MUST Requirements**
- Targets non-idempotent methods: **POST, PATCH** (per RFC 9110)
- Server **SHOULD** identify idempotency key, generate fingerprint (if required), and enforce idempotency rules
- Key **MUST be a String** (RFC 8941 Structured Headers); **SHOULD use UUID** or random identifiers ([draft-ietf-httpapi-idempotency-key-header-07](https://datatracker.ietf.org/doc/html/draft-ietf-httpapi-idempotency-key-header-07) §3.2)
- Resources **MUST publish** specifications defining acceptable key formats and uniqueness rules

**Key Fingerprinting & Payload Binding**
- Spec recommends server generate fingerprint from request (method, target URI, body contents)
- **Stripe's behavior** (de facto standard): rejects requests with same key but different payload with error; returns cached response if key reused with *identical* payload within 24h window ([Stripe API Docs](https://docs.stripe.com/api/idempotent_requests))
- Key expiry: Stripe retains for **24 hours**; spec says "MAY require time-based expiration" and "SHOULD publish" retention policy

**Error Responses (Status Codes & Headers)**
- **400 Bad Request**: Missing required Idempotency-Key header; body SHOULD contain link to documentation
- **422 Unprocessable Content**: Key reused with different payload; body SHOULD contain link to documentation  
- **409 Conflict**: Retry received while original request still processing; body SHOULD contain problem description
- Response format: RFC 7807 `application/problem+json` or Link header with `rel="describedby"` ([draft-ietf-httpapi-idempotency-key-header-07](https://datatracker.ietf.org/doc/html/draft-ietf-httpapi-idempotency-key-header-07) §4)

**Concurrency Handling**
- **Recommended**: Respond **409 Conflict** when retry arrives before original request completes
- Stripe's behavior: 10-second timeout on 2xx response; timeout + missing response = retry; no blocking of concurrent requests documented ([Stripe webhooks](https://docs.stripe.com/webhooks))

**Response Replay Storage**
- Store: **status code, body, critical response headers** (Content-Type, Location for 3xx redirects)
- **Do NOT replay**: headers that change with time (Date, Cache-Control, Set-Cookie, server-generated correlation IDs)
- Stream responses: spec silent; Stripe caches full response body

**Scope & Retention Guidance**
- Typically scoped per **API key / tenant / user**
- Retention: spec leaves as SHOULD-publish; Stripe uses **24 hours** globally

---

### §2 — Outbound webhook signing (backlog D4)

**RFC 9421 (HTTP Message Signatures) — Core Spec**
- **Published**: February 2024 (Standards Track)
- **Document**: [RFC 9421](https://datatracker.ietf.org/doc/html/rfc9421/)
- **Minimal Required Components**:
  1. **Signature-Input** header: list of covered components + signature parameters (creation time, key ID, algorithm)
  2. **Signature** header: base64-encoded cryptographic output
  3. Signature base construction: each component serialized as `"component-name": value`, parameters as final `@signature-params` line ([RFC 9421 §3.1](https://datatracker.ietf.org/doc/html/rfc9421/#section-3.1))

**Conventionally Covered Components (no "must", application-defined)**
- **Derived components** (always HTTP request attributes): `@method`, `@target-uri`, `@authority`
- **Content integrity**: `content-digest` header (see §RFC 9530 below)
- **Timing**: `created` (seconds since epoch), `expires` (optional)
- **Identification**: `keyid` (public key identifier), `alg` (algorithm name)
- ([RFC 9421 §2.1, §2.2, §3.2](https://datatracker.ietf.org/doc/html/rfc9421/#section-2.2))

**Registered Algorithms** (RFC 9421 §3.3)
- RSASSA-PKCS1-v1_5 with SHA-256 (`rsa-sha256`)
- RSA-PSS with SHA-512 (`rsa-pss-sha512`, **recommended for new systems**)
- ECDSA with P-256 + SHA-256 (`ecdsa-p256-sha256`)
- ECDSA with P-384 + SHA-384 (`ecdsa-p384-sha384`)
- Ed25519 (`ed25519`)
- HMAC with SHA-256 (`hmac-sha256`)
- No `sha256=` prefix convention; all algorithm names are registry identifiers

**RFC 9530 (Content-Digest) — Companion Spec**
- **Published**: February 2024 (Standards Track)
- **Status**: Required *alongside* RFC 9421 for complete content-integrity protection
- **Header format**: `Content-Digest: <algorithm>=:<base64-value>:` (e.g., `sha-256=:uU0nuZNNPgilLlHj5oN8X+TuTG5WC2qmbLlW7f9c0nQ=:`)
- Covers message content (affected by Content-Encoding and Content-Range, not Transfer-Encoding)
- ([RFC 9530](https://www.rfc-editor.org/rfc/rfc9530.html))

**Practical Contrast: Standard Webhooks, Stripe, GitHub Schemes**

| Scheme | Algorithm | Format | Key Rotation | Timestamp | Replay Protection |
|---|---|---|---|---|---|
| **RFC 9421** | HMAC-SHA256, Ed25519, RSA-PSS, ECDSA | Structured headers (`Signature-Input`, `Signature`) + `Content-Digest` | Not built-in (application choice) | `created` parameter (UNIX seconds) | No timestamp validation requirement |
| **Standard Webhooks** | HMAC-SHA256 (v1), Ed25519 (v1a) | `webhook-id`, `webhook-timestamp`, `webhook-signature` headers; signature = HMAC(`msg_id.timestamp.payload`) | Multiple space-delimited signatures for zero-downtime rotation | `webhook-timestamp` (UNIX seconds) | Recommends tolerance window (unspecified) |
| **Stripe** | HMAC-SHA256 | `X-Stripe-Signature: t=<timestamp>,v1=<hmac>` | Undocumented multi-key support implied | `t=<UNIX>` (UNIX seconds) | No documented tolerance; 300s window observed in practice |
| **GitHub** | HMAC-SHA256 | `X-Hub-Signature-256: sha256=<hex-digest>` | Implicit (secret rotation disables old key) | None | No replay protection; constant-time comparison required |

- **Real-world consensus**: Standard Webhooks and Stripe's simpler scheme are more widely adopted than RFC 9421 as of 2026
- **RFC 9421 interoperability gap**: A consumer expecting Standard Webhooks headers will not understand RFC 9421 `Signature-Input` / `Signature`; shipping RFC 9421 alone leaves naive consumers unable to verify
- ([Standard Webhooks spec](https://github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md), [Stripe API docs](https://docs.stripe.com/api/idempotent_requests), [GitHub webhook docs](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries))

**Replay Protection & Timestamp Tolerance**
- **RFC 9421**: `created` and `expires` parameters are signature metadata; validation is application-defined (spec does NOT require timestamp checks)
- **Standard Webhooks**: Recommends timestamp tolerance window; does not specify exact width (implementations vary: 5 min common)
- **Stripe**: No documented tolerance; observed 300s before rejection
- **Extension attacks**: Prevent by signing the concatenated canonical form (signature base), not a hand-built string; RFC 9421 handles this via structured serialization

**Key Rotation & Multiple Active Secrets**
- **RFC 9421**: No built-in rotation; `keyid` allows multiple active keys; application must track when each key was active
- **Standard Webhooks**: Multiple space-delimited signatures in `webhook-signature` header; consumer verifies against any active signature
- **Stripe**: Undocumented; implies single active key at a time + secret rotation disables old key
- Svix (Standard Webhooks-based): [Endpoint disablement on sustained failure](https://docs.svix.com/retries)

---

### §3 — CloudEvents envelope (backlog N2)

**Specification & Version**
- **Current version**: [CloudEvents v1.0.2](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md)
- **Release date**: v1.0 published 2019; v1.0.2 is latest patch release (as of 2026-09-04)
- **CNCF status**: Graduated project as of January 25, 2024 (sandbox → incubator → graduated)

**REQUIRED Context Attributes** (all four mandatory)
- **`id`**: Unique identifier within producer scope; producers MUST ensure `source + id` is unique per event; non-empty string
- **`source`**: URI-reference identifying context where event occurred; MUST be non-empty; absolute URIs recommended
- **`specversion`**: Specification version; MUST be `"1.0"` (string) for this release
- **`type`**: Describes event type; MUST be non-empty; reverse-DNS naming convention recommended (e.g., `com.github.pull_request.opened`)

**OPTIONAL Context Attributes** (commonly used)
- **`subject`**: Describes subject within producer context; helpful for filtering; string
- **`time`**: Timestamp of occurrence; RFC 3339 format (e.g., `2023-01-01T00:00:00Z`)
- **`datacontenttype`**: Media type of `data` payload; RFC 2046 format (e.g., `application/json`)
- **`dataschema`**: URI identifying schema that `data` adheres to; string
- ([CloudEvents spec §2.0](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md#context-attributes))

**Extension Attributes Naming Rules**
- **Character set**: lowercase letters (a–z) + digits (0–9) only; no hyphens, underscores
- **Case**: lowercase; `TenantID` is invalid; `tenantid` is valid
- **Length**: max 20 characters (recommended; not hard-limited in spec)
- **Reserved extensions**: `partitionkey` (data partitioning), `traceparent` (W3C Distributed Tracing extension, RFC 9110-compatible)
- **Tenant & correlation ID**: No registered extensions exist; varco may invent `tenantid` and `correlationid` (20 chars each, valid)
- ([CloudEvents spec §2.3](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/spec.md#extension-context-attributes))

**JSON Format Binding (Structured Mode)**
- **Media type**: `application/cloudevents+json`
- **Structure**: Each CloudEvent is a single JSON object; all context attributes become JSON object members matching attribute names exactly
- **Type mapping**: Boolean→JSON boolean, String→JSON string, Integer→JSON number, Timestamp→ISO 8601 string, Binary→base64-encoded JSON string
- **Data encoding choice**:
  - **Use `data`** (JSON value directly): when `datacontenttype` ends in `json` or `+json` (e.g., `application/json`, `application/problem+json`)
  - **Use `data_base64`** (base64-encoded JSON string): when data is Binary type OR `datacontenttype` does NOT end in `json`/`+json` (e.g., `application/octet-stream`)
  - **Constraint**: `data` and `data_base64` are **mutually exclusive** in one JSON event
- ([CloudEvents JSON Format spec](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/formats/json-format.md))

**Kafka Protocol Binding**
- **Two content modes**: Structured (event envelope in Kafka value) vs Binary (event attributes in headers, data in value)
- **Structured mode** (recommended for ease):
  - `content-type` header: `application/cloudevents+json; charset=UTF-8`
  - Kafka message value: JSON-serialized CloudEvent (data + all context attributes)
  - No per-attribute headers
- **Binary mode** (for integration with non-CloudEvents Kafka consumers):
  - `content-type` header: maps to CloudEvent's `datacontenttype` attribute
  - **All context attributes as Kafka headers** with `ce_` prefix:
    - `id` → header `ce_id`
    - `source` → header `ce_source`
    - `specversion` → header `ce_specversion`
    - `type` → header `ce_type`
    - Extensions: e.g., `tenantid` → header `ce_tenantid`
  - **Header encoding**: UTF-8 strings required; header keys and values as structured fields
  - Kafka message value: raw `data` (not wrapped)
- **Compatibility**: Structured-mode-first choice is forward-compatible with future binary-mode additions (consumers can ignore unfamiliar headers)
- ([CloudEvents Kafka Protocol Binding v1.0.2](https://github.com/cloudevents/spec/blob/v1.0.2/cloudevents/bindings/kafka-protocol-binding.md))

**Python CloudEvents SDK**
- **Package**: [`cloudevents`](https://pypi.org/project/cloudevents/) on PyPI
- **Current version**: v1.10.1 (as of 2026-09-04)
- **Maintenance status**: **Healthy** — positive version release cadence (≥1 new release in past 12 months); 10+ contributors
- **Governance**: CNCF-hosted; bi-weekly community calls (Slack: `#cloudeventssdk` in CNCF workspace)
- **Stability note**: SDK **work-in-progress disclaimer** — breaking changes possible with every update
- **Dependency advice**: CNCF-official SDK is maintained and actively used in production (AWS EventBridge, Google Eventarc); **worth the dependency** for structured support of JSON/Kafka bindings and extension validation
- ([PyPI cloudevents](https://pypi.org/project/cloudevents/), [MAINTAINERS.md](https://github.com/cloudevents/sdk-python/blob/main/MAINTAINERS.md))

---

### Webhook Delivery Semantics & SSRF Protection (Supporting Context for D4)

**Retry Schedule & Backoff** (across three implementations)
- **Stripe**: 16 attempts over ~3 days; exponential backoff; 10-second response timeout; endpoint disabled on sustained failure ([Stripe webhooks](https://docs.stripe.com/webhooks))
- **Svix** (Standard Webhooks): Immediate, 5s, 5m, 30m, 2h, 5h, 10h, 10h (8 retries); exponential backoff + jitter; → Dead Letter Queue on exhaustion ([Svix docs](https://docs.svix.com/retries))
- **At-least-once delivery**: All three promise "never lose a webhook"; no vendor can ship exactly-once

**Idempotency Expectations on Consumer Side**
- Webhook consumers MUST be idempotent: same event received N times = same final outcome as received once
- Duplicate source: retries (timeout, 5xx, transient network), not sender bugs
- Consumer pattern: unique event ID (Stripe's `id`, Svix's `webhook-id`, GitHub's `X-GitHub-Delivery`) + database unique index or distributed cache to detect replays
- ([Hookdeck: Implement webhook idempotency](https://hookdeck.com/webhooks/guides/implement-webhook-idempotency))

**SSRF Prevention Requirements**
- **Block private IP ranges**: 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, loopback (127.0.0.0/8), link-local (169.254.0.0/16), **169.254.169.254 (AWS metadata endpoint)**
- **Strategy**: Resolve hostname to IP, validate IP against blocklist *before* making HTTP request; re-resolve at connection time (or pin IP) to prevent DNS rebinding attacks
- **Blocklist weakness**: Blacklists are incomplete (encoding bypasses); prefer explicit allowlist when possible (e.g., only HTTPS, not HTTP)
- **Svix implementation**: IP blacklist + symmetric/asymmetric signing + webhook disablement on sustained failure ([Svix docs](https://docs.svix.com/), [Convoy architecture](https://www.getconvoy.io/docs/webhook-guides/tackling-ssrf))

---

## Options Compared

| Aspect | Idempotency-Key (Draft) | RFC 9421 + RFC 9530 | Standard Webhooks | CloudEvents |
|---|---|---|---|---|
| **Status** | Expired draft (Oct 2025) | Published RFC (Feb 2024) | Community de facto (v1.2.2) | CNCF graduated (v1.0.2) |
| **Scope** | Request deduplication (server-side) | Message authentication & integrity | Webhook signing + delivery metadata | Event envelope (transport-agnostic) |
| **Timestamp protection** | Via fingerprint (server choice) | Via `created`/`expires` params | Via `webhook-timestamp` + tolerance | Not built-in (app layer) |
| **Key rotation** | Not specified | Via `keyid` + app tracking | Multiple space-delimited sigs | Not built-in (Kafka headers allow per-key metadata) |
| **Replay protection** | Server must check timestamp window (SHOULD not MUST) | Application-defined | Recommends tolerance window | Not built-in |
| **Extension support** | N/A | `@<component>` syntax (RFC 9421 defined) | Custom headers (non-standard) | Strict naming rules (`[a-z0-9]{1,20}`) |
| **Python SDK** | N/A | None (HTTPS library adapters only) | [`svix-python`](https://github.com/svix/svix-python) (Standard Webhooks) | [`cloudevents`](https://pypi.org/project/cloudevents/) (CNCF official, healthy) |

---

## Version/Compatibility Notes

- **draft-ietf-httpapi-idempotency-key-header-07**: Expired April 18, 2026; no RFC successor announced. Stripe's de facto behavior is the canonical implementation.
- **RFC 9421 (HTTP Message Signatures)**: Stable; published February 2024. RFC 9530 (Content-Digest) is a companion; both published same date.
- **RFC 9530 (Digest Fields)**: Stable; published February 2024; obsoletes RFC 3230.
- **Standard Webhooks spec**: Community-maintained; v1.2.2 (current as of 2026). Multi-language SDK support (Go, Python, Node, Rust).
- **CloudEvents spec**: v1.0.2 stable; v1.0 published 2019. Kafka binding (v1.0.2) supports both structured and binary modes. JSON format binding mandatory for structured mode.
- **CloudEvents Python SDK (`cloudevents`)**: v1.10.1; active maintenance with bi-weekly community calls.

---

## Evidence Gaps

1. **Idempotency-Key**: No RFC published; draft expired. Only Stripe's observed behavior and IETF draft-07 from Oct 2025 are normative sources. No formal error-response problem types registered (spec suggests `type: "https://developer.example.com/idempotency"` pattern).

2. **RFC 9421 adoption in webhook context**: The spec defines the *mechanics* but does NOT define the application profile for webhook signing. Real webhook implementations (Stripe, GitHub, Standard Webhooks) all pre-date RFC 9421 or ignore it in favor of simpler HMAC schemes. No documented case of a webhook platform shipping RFC 9421 as the primary signing method; adoption is emerging but not yet widespread.

3. **CloudEvents extension registry**: CNCF maintains a documented-extensions list, but no formal process for registering new extensions. Custom extensions (`tenantid`, `correlationid`) are valid per spec but not globally standardized. Worth a separate brief: integration of OTEL `traceparent` extension and distributed-tracing baggage.

4. **Kafka protocol binding performance**: No benchmarks comparing structured-mode JSON serialization overhead vs binary-mode header scattering on Kafka consumers. Structured mode is simpler; binary mode is leaner.

---

## Librarian's Note

**What the sources indicate:**

- **§1 (Idempotency-Key, D1)**: Implement per draft-07 + Stripe's 24h retention + 409-on-inflight pattern. The draft has no RFC successor; this is a convention varco must adopt as-is.

- **§2 (Webhook signing, D4)**: RFC 9421 is the published standard for *message authentication*, but **varco should ship both RFC 9421 (for specification compliance) and Standard Webhooks (for real-world adoption)**. Stripe's simpler `t=...,v1=...` scheme is even more common, but varco is not a payment processor; Standard Webhooks + RFC 9421 offer the breadth. Replay protection (timestamp tolerance + monotonic delivery ID) is mandatory; SSRF validation is security-critical.

- **§3 (CloudEvents, N2)**: v1.0.2 structured JSON over Kafka is the interoperable choice. CloudEvents Python SDK is worth the dependency (actively maintained, CNCF-blessed). Custom extensions for tenant/correlation ID are spec-valid; integrating `traceparent` (W3C Distributed Tracing) for OpenTelemetry compatibility is a natural follow-up.

The three specs are orthogonal: idempotency-key is request-response, webhook signing is asynchronous-delivery, CloudEvents is event shape. Varco can implement all three independently.

