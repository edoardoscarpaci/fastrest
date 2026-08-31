# Research 002 — Generating AsyncAPI documents from varco event metadata

Date: 2026-08-30 · Freshness matters: **yes** — AsyncAPI is active; spec versions, binding versions, and tooling are current as of early 2026.

## Question

How should varco export AsyncAPI specifications describing its event-driven surface, given the event metadata already stored in decorators (`@listen` channel, retry policy, DLQ), EventConsumer wiring, ChannelManager abstractions, and Pydantic Event subclasses? What are the current AsyncAPI spec version, document structure, protocol bindings, Python tooling, and CI practices for generated documents?

## Findings

### 1. Current AsyncAPI Specification Version

- **Current stable version: 3.1.0** (released January 31, 2026) — [AsyncAPI Spec 3.1.0 Release Notes](https://www.asyncapi.com/blog/release-notes-3.1.0)
  - Minor release; no breaking changes from 3.0.0; primarily added ROS 2 binding support
  - **3.0.0** (released November 2023) was the major restructure from 2.x — [3.0.0 Release Notes](https://www.asyncapi.com/blog/release-notes-3.0.0)
  - **2.6.0** is end-of-life but still documented; tools exist for both 2.x and 3.x
  - Recommended migration path: 3.x only; 2.x support exists but 3.x is current — [Migrating to v3](https://www.asyncapi.com/docs/migration/migrating-to-v3)

### 2. AsyncAPI 3.x Document Model — Major Changes

**The 3.0 Restructure:**
- **Channels decoupled from operations** — channels define message topics/addresses and their schemas; operations separately declare what the application does (send/receive messages to those channels) — [AsyncAPI 3.0.0 Release Notes](https://www.asyncapi.com/blog/release-notes-3.0.0)
- **Terminology shift:** `publish`/`subscribe` replaced with `send`/`receive` (application's perspective, not abstract broker direction)
- **Explicit references:** All references use `$ref` syntax; no implicit name-based resolution
- **Restructured servers:** Root `url` split into `host`, `path`, `protocol` fields; metadata relocations (tags, externalDocs moved into `info` object)

**Minimal Valid 3.x YAML Skeleton:**

```yaml
asyncapi: '3.1.0'
info:
  title: Event Service
  version: '1.0.0'

channels:
  userSignup:
    address: users.signup
    messages:
      userSignedUp:
        payload:
          schemaFormat: 'application/vnd.apache.avro;version=1.9.0'
          # or 'application/json' for JSON Schema (see §3 for schemaFormat guidance)
          type: object
          properties:
            userId:
              type: string
            email:
              type: string

operations:
  onUserSignup:
    action: receive
    channel:
      $ref: '#/channels/userSignup'
    messages:
      - $ref: '#/channels/userSignup/messages/userSignedUp'
```

**Channels now optional:** Use cases like definition collections omit channels entirely — [3.1.0 Spec](https://www.asyncapi.com/docs/reference/specification/v3.1.0)

### 3. JSON Schema Integration with Pydantic v2

- **Pydantic v2's `model_json_schema()`** emits **JSON Schema Draft 2020-12** with `$defs` for model references — [Pydantic JSON Schema Docs](https://docs.pydantic.dev/latest/concepts/json_schema/)
- **Can embed directly** — The output is a valid JSON Schema; `$defs`/`$ref` resolution works inside an AsyncAPI document as long as `$ref` paths are valid (e.g., `#/components/schemas/MyEvent/$defs/NestedType`) — [AsyncAPI Payload Schema Docs](https://www.asyncapi.com/docs/concepts/asyncapi-document/define-payload)
- **`schemaFormat` values for AsyncAPI 3.x:**
  - **AsyncAPI Schema** (default — implicit if omitted) — built-in superset of JSON Schema Draft 07; used if no schemaFormat specified
  - **Avro:** `application/vnd.apache.avro;version=1.9.0` (or other versions) — [Kafka Bindings with AsyncAPI](https://www.asyncapi.com/docs/tutorials/kafka/bindings-with-kafka)
  - **JSON Schema:** No universally documented schemaFormat string; AsyncAPI 3.x defaults to AsyncAPI Schema (superset of JSON Schema Draft 07). **Evidence gap:** the spec does not explicitly name the schemaFormat value for "bare" JSON Schema Draft 2020-12 when you want to opt out of AsyncAPI's extensions. Industry practice suggests omitting schemaFormat (uses default) or using `application/json` informally, but no official RFC/binding defines this — see §5 gap.
- **No official Pydantic-specific binding** exists; treat Pydantic `model_json_schema()` output as generic JSON Schema — [asyncapi-schema-pydantic](https://github.com/albertnadal/asyncapi-schema-pydantic) provides Pydantic models that *represent* the AsyncAPI spec itself, not Pydantic-emitted schemas.
- **Known gotcha:** AsyncAPI historically supported JSON Schema Draft 07; Draft 2020-12 support was proposed but **as of 3.1.0, the spec itself still aligns with Draft 07 as the default schema format** — [Support JSON Schema Draft 2020-12 · Issue #596](https://github.com/asyncapi/spec/issues/596). Embedding Draft 2020-12 output directly works (tools are permissive) but is not officially blessed.

### 4. Protocol Bindings — Current Versions and Field Definitions

| Protocol | Binding Version | Channel Binding Fields | Operation Binding Fields | Status | Notes |
|----------|-----------------|------------------------|--------------------------|--------|-------|
| **Kafka** | **0.5.0** | `topic`, `partitions`, `replicas`, `topicConfiguration` | `groupId`, `clientId`, `bindingVersion` | Stable | Server binding: `schemaRegistryUrl`, `schemaRegistryVendor`; Message binding: `key`, `schemaIdLocation`, `schemaIdPayloadEncoding` — [Kafka Binding README](https://github.com/asyncapi/bindings/blob/master/kafka/README.md) |
| **NATS** | **0.1.0** | (none — reserved for future use) | `queue` (string, max 255 chars) | Draft/Reserved | Queue defines durable consumer queue name; all other binding objects (server, channel, message) reserved — [NATS Binding README](https://github.com/asyncapi/bindings/blob/master/nats/README.md) |
| **Redis** | **0.1.0** | (none — reserved for future use) | (none — reserved for future use) | Draft/Reserved | **All four binding levels (server, channel, operation, message) explicitly defined but contain zero properties** — [Redis Binding Tree](https://github.com/asyncapi/bindings/tree/master/redis) |

**Binding repo versioning:** Bindings version independently of core spec — check [asyncapi/bindings GitHub releases](https://github.com/asyncapi/bindings/releases).

### 5. Python Tooling for Building & Validating AsyncAPI

**Building/Generating:**

- **FastStream** (most comparable prior art) — async Python framework that auto-generates AsyncAPI from decorators (`@broker.subscriber`, `@broker.publisher`), emitting **both 2.6.0 and 3.0.0 specs**; ships `faststream docs gen` CLI to output JSON/YAML — [faststream.ag2.ai](https://faststream.ag2.ai/latest/faststream/) and [Streamlining Asynchronous Services with FastStream](https://nats.io/blog/nats-supported-by-faststream/)
  - Handler decorator syntax: `@app.subscriber(channel, description="...")` captures message types from function parameter annotations
  - CLI: `faststream docs gen <module>:<app>` generates AsyncAPI; `faststream docs serve <asyncapi.json>` launches web UI
  - **Approach:** Traverses decorated handlers at runtime, reflects on type hints, builds operations and channel metadata
  
- **asyncapi-schema-pydantic** — provides Pydantic models representing the AsyncAPI specification structure itself (not message payloads) — useful for type-safe spec construction in Python but **does not generate specs from decorators** — [GitHub](https://github.com/albertnadal/asyncapi-schema-pydantic), [PyPI](https://pypi.org/project/asyncapi-schema-pydantic/)
  
- **No other maintained Python library** explicitly focuses on building AsyncAPI specs from decorators. **No pure-Python generator** (equivalent to FastStream) exists for framework-agnostic event metadata — [Tools Index](https://www.asyncapi.com/tools)

**Validating/Parsing:**

- **@asyncapi/parser** (JavaScript/Node.js) — official parser for YAML/JSON AsyncAPI documents, works for 2.x and 3.x — [`@asyncapi/parser` npm](https://www.npmjs.com/package/@asyncapi/parser) and [AsyncAPI Parser Docs](https://www.asyncapi.com/docs/tools/generator/parser)
  - CLI: `asyncapi validate` (requires Node.js)
  - No official Python equivalent; validation requires Node.js or a workaround
  
- **Python validator library** — A Python library exists for validating message payloads against AsyncAPI 2.x/3.x specs (JSON Schema validation, type checking, required fields, constraints) — [Nordic APIs: 6 AsyncAPI Validation Tools](https://nordicapis.com/6-asyncapi-validation-tools/) references it, but the exact package name was not pinned in search results
  
- **AsyncAPI CLI** (`@asyncapi/cli`) — Node.js-based; **requires Node.js 24+ and NPM 11+** as of 2026 — [asyncapi/cli releases](https://github.com/asyncapi/cli/releases), [`@asyncapi/cli` npm](https://www.npmjs.com/package/@asyncapi/cli/v/0.21.7)
  - Commands: `validate`, `generate`, `bundle`, `split`
  - Validation-only means: **no pure-Python validator exists in the official toolchain**

### 6. Recommended Practice for Keeping Generated Docs Honest in CI

- **Snapshot in Git + diff gate** — the established pattern (mirrors OpenAPI snapshot practice):
  - Commit generated AsyncAPI YAML/JSON to repo
  - CI job: regenerate spec, diff against committed version, **fail the build if they diverge**
  - Prevents silent documentation rot after code changes
  - [AsyncAPI Documentation Generator Guide](https://buildwithfern.com/post/asyncapi-documentation-generator) and [Validate AsyncAPI Documents](https://www.asyncapi.com/docs/guides/validate)
  
- **Diff tooling:**
  - **AsyncAPI Diff** — compares two AsyncAPI documents, identifies breaking changes — [asyncapi/diff](https://github.com/asyncapi/diff)
  - Can be used in CI to flag breaking changes or auto-version the spec (semantic versioning on schema changes)
  
- **Generator + validation loop:**
  - Post-merge, regenerate spec
  - Optionally publish to AsyncAPI Studio or documentation server
  - Use `asyncapi validate` (Node CLI) in CI; a Python fallback requires a Node container or vendored Node binary
  
- **No "generate-on-demand" alternative** is recommended for critical surfaces — synchronous source-of-truth (Git snapshot) prevents race conditions and ensures every deployed service knows what AsyncAPI version it shipped with.

## Version/Compatibility Notes

- **AsyncAPI 2.6.0:** Older; spec still documented but no longer current. Tools support both 2.x and 3.x.
- **AsyncAPI 3.0.0:** Major restructure; breaking changes from 2.x (channels / operations split, send/receive terminology, $ref unification).
- **AsyncAPI 3.1.0:** Current stable (Jan 2026); no breaking changes from 3.0; added ROS 2 binding.
- **FastStream:** Latest version supports AsyncAPI 3.0.0+ generation; recommend 3.x over 2.x for new projects.
- **Node CLI:** `asyncapi` CLI requires Node.js 24+ as of 2026; previous versions accepted Node 16+.
- **Kafka binding 0.5.0:** Stable, comprehensive fields for topic, partitions, consumer groups.
- **NATS binding 0.1.0:** Only operation-level `queue` field implemented; others reserved.
- **Redis binding 0.1.0:** Fully reserved; no fields defined yet.

## Evidence Gaps

1. **JSON Schema Draft 2020-12 official schemaFormat value** — AsyncAPI 3.x spec aligns with Draft 07 as default; no explicit schemaFormat string is documented for declaring "this is Draft 2020-12". Embedding Pydantic v2's Draft 2020-12 output works in practice but is not officially sanctioned. *Recommendation: Verify with AsyncAPI maintainers or test validator acceptance; interim: omit schemaFormat or document choice in schema comments.*

2. **Python-native AsyncAPI validator** — No official Python library mirrors @asyncapi/parser; validation in CI still requires Node.js. *Worth a separate brief: evaluate feasibility of a varco-internal lightweight validator or JSON Schema validator that accepts AsyncAPI documents.*

3. **FastStream's exact handler→operation mapping** — FastStream documentation describes the decorators and CLI but does not detail the precise algorithm for translating handler parameter types + docstrings into AsyncAPI operations/messages. *Low priority: copy its approach (introspect type hints, extract docstrings) or inspect its source code.*

4. **AsyncAPI Diff semantic-versioning integration** — AsyncAPI Diff identifies breaking changes; unclear whether a standard GitHub Action or CI recipe exists to auto-bump the AsyncAPI document version on spec changes. *Worth tracking: would enable automatic versioning in CI.*

## Librarian's Note

**What the sources indicate**: AsyncAPI 3.1.0 is current and stable. The 3.x document model (channels/operations separation) maps cleanly onto varco's existing abstractions: `@listen` channel declarations become channels, `EventConsumer.register_to()` calls become operations, Pydantic Event subclasses map to message payloads via `model_json_schema()`. FastStream is the closest prior art — it auto-generates AsyncAPI from decorators; copying its handler-inspection approach is feasible. Protocol bindings are mature (Kafka 0.5.0), partially mature (NATS 0.1.0 with only queue field), or not yet ready (Redis 0.1.0, all reserved). Python tooling is sparse; validation and generation both lean on Node.js; embedding Pydantic v2 Draft 2020-12 schemas works in practice but is not explicitly blessed by spec. CI practice is snapshot + diff, proven by OpenAPI experience. No blocking evidence gaps prevent implementation; the JSON Schema version mismatch is a minor documentation/validator-acceptance risk, not a structural blocker.

