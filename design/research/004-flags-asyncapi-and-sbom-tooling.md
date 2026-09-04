# Research 004 — OpenFeature SDK stability, AsyncAPI tooling, CycloneDX SBOM, and EU compliance

Date: 2026-09-04 · Freshness matters: **yes** — SDK/spec versions, regulatory enforcement dates, PyPI tooling adoption, and CycloneDX releases all change; re-check in 12 months.

## Question

Three independent dependency/version assessments that varco's D7, N3, and D5 backlog items hinge on:

1. **§1 — OpenFeature Python SDK**: Is the SDK at version 1.0 yet, and what is the minimal provider API surface?
2. **§2 — AsyncAPI export tooling**: What Python tooling exists for producing AsyncAPI documents, and is `datamodel-code-generator` actually needed?
3. **§3 — CycloneDX SBOM + EU regulations**: What is the current SBOM tooling, PEP 770 status, and do EU CRA/NIS2 bind varco as a non-commercial FOSS upstream?

## Findings

### §1 — OpenFeature Python SDK

- **Current SDK version**: 0.10.0, released 2026-06-01 — **has NOT reached 1.0** — [openfeature-sdk PyPI](https://pypi.org/project/openfeature-sdk/) (fetched 2026-09-04)
- **Current specification version**: 0.9.0, released 2026-07-29, still **pre-1.0 allowing breaking changes** — [OpenFeature spec GitHub](https://github.com/open-feature/spec), [OpenFeature specification page](https://openfeature.dev/specification/) (fetched 2026-09-04)
- **Python support**: SDK requires Python ≥3.10, explicitly tested on 3.10–3.14 — [PyPI classifiers](https://pypi.org/project/openfeature-sdk/)
- **Stability pre-1.0**: Spec explicitly warns "breaking changes will be introduced" while version < 1.0 — [openfeature-sdk CHANGELOG](https://github.com/open-feature/python-sdk/blob/main/CHANGELOG.md) — v0.10.0 itself introduced a breaking change: `set_provider()` no longer blocks; use `set_provider_and_wait()` if initialization completion is required
- **Provider API surface** (minimal contract):
  - Abstract class: `AbstractProvider` (implement and register via SDK)
  - Typed resolution methods: `resolve_boolean_details()`, `resolve_numeric_details()`, `resolve_string_details()`, `resolve_object_details()` — each takes `(flag_key: str, default_value: T, evaluation_context: EvaluationContext | None) → FlagResolutionDetails[T]`
  - Metadata: `get_metadata() → Metadata` with name field identifying the provider
  - Hooks: `get_provider_hooks() → list[Hook]` for integration into flag evaluation lifecycle
  - Lifecycle hooks (optional): `initialize(global_eval_context)`, `on_context_changed(eval_context)`, `shutdown()` — emit status events `PROVIDER_READY`, `PROVIDER_ERROR`, `PROVIDER_CONTEXT_CHANGED`
  - [OpenFeature Provider specification](https://openfeature.dev/specification/sections/providers/), [EvaluationContext docs](https://openfeature.dev/docs/reference/concepts/evaluation-context/)
- **Test provider**: No official in-memory/no-op provider documented; SDK ships no test double — **evidence gap**: varco will need to author its own simple in-memory provider for testing

### §2 — AsyncAPI export tooling

- **Current AsyncAPI spec version**: 3.1.0 — [AsyncAPI spec v3.1.0](https://www.asyncapi.com/docs/reference/specification/v3.1.0), with v3.0.0 baseline from late 2023 — [AsyncAPI spec v3.0.0](https://www.asyncapi.com/docs/reference/specification/v3.0.0)
- **Major structural change 2.6→3.0**: **Channels/operations split** — Channels define communication pathways (the "where"); operations describe application behavior ("what" and "action"), explicitly reference channels, declare `send` or `receive` action — [AsyncAPI 3.0 channels spec](https://www.asyncapi.com/docs/reference/specification/v3.0.0), fetched 2026-09-04
- **Kafka binding**:
  - Binding version field required: `bindingVersion` (specifies the Kafka binding specification version used)
  - Channel binding fields: `partitions` (topic partition count), `replicas` (replica count), `topicConfiguration` (cleanup policy, retention, message size limits)
  - Operation binding fields: `groupId` (consumer group ID), `clientId` (individual consumer ID within the group)
  - [AsyncAPI Kafka bindings tutorial](https://www.asyncapi.com/docs/tutorials/kafka/bindings-with-kafka), fetched 2026-09-04
- **Python tooling for producing AsyncAPI**:
  - **No heavyweight mandatory dependency**: AsyncAPI is JSON/YAML — emitting a dict and serializing it is the sane baseline (no "must use a generator")
  - **`datamodel-code-generator`**: **Does NOT produce AsyncAPI** — it is a *code* generator from JSON Schema, OpenAPI, GraphQL, Avro, Protobuf, and raw data *into* Python models — [GitHub datamodel-code-generator](https://github.com/koxudaxi/datamodel-code-generator), [PyPI datamodel-code-generator](https://pypi.org/project/datamodel-code-generator/) (fetched 2026-09-04) — **verdict: not needed for producing AsyncAPI documents**
  - **`asyncapi-python`** (PyPI): A Python AsyncAPI generator; experimentally generates type-safe Python apps from AsyncAPI 3 specs, using `datamodel-code-generator` *internally* for message type generation (not for producing the AsyncAPI itself) — [PyPI asyncapi-python](https://pypi.org/project/asyncapi-python/), active as of 2026-04
  - **Official AsyncAPI generator** (Node.js): Language-agnostic, template-based, primarily a code-generation tool *from* AsyncAPI, not a producer — [AsyncAPI generator](https://www.asyncapi.com/tools/generator) — **low relevance to varco's use case**
  - **Recommended approach for varco**: Hand-author or runtime-introspect (`@listen` decorators) → emit a plain `dict` following the AsyncAPI 3.0/3.1 schema → serialize to JSON/YAML with `json`/`pyyaml` — no third-party AsyncAPI library required
- **JSON Schema for message payloads**:
  - Pydantic v2 emits JSON Schema Draft 2020-12 via `model_json_schema()` — [Pydantic JSON Schema docs](https://pydantic.dev/docs/validation/dev/concepts/json_schema/)
  - AsyncAPI 3.0 accepts JSON Schema; compatibility is not a concern (Draft 2020-12 is the modern standard) — [AsyncAPI spec reference](https://www.asyncapi.com/docs/reference/specification/v3.0.0), fetched 2026-09-04
  - **Workflow**: `pydantic_model.model_json_schema() → asyncapi_message["payload"] = {schema: json_schema_dict}` — no extra tool needed

### §3 — CycloneDX SBOM + EU CRA/NIS2

- **CycloneDX Python tooling**:
  - **Current package**: `cyclonedx-bom` (formerly `cyclonedx-py`), version 7.3.1, released 2026-07-23 — [PyPI cyclonedx-bom](https://pypi.org/project/cyclonedx-bom/), [GitHub CycloneDX/cyclonedx-python](https://github.com/CycloneDX/cyclonedx-python), fetched 2026-09-04
  - **CycloneDX spec support**: Supports CycloneDX 1.7 (current stable) with namespace taxonomies for Python, Pipenv, Poetry — described as "probably the most accurate, complete SBOM generator for any python-related projects" — [CycloneDX Python docs](https://cyclonedx-bom-tool.readthedocs.io/)
  - **No direct `uv` integration**: `cyclonedx-bom` consumes virtual environments, requirements.txt, Poetry/Pipenv manifests — **workaround for uv**: `uv export --format requirements-txt` → pipe to `cyclonedx-bom` or use `cyclonedx-py --format json` on the installed environment
  - **CLI invocation**: `cyclonedx-bom` or `python -m cyclonedx_bom`
- **PEP 770 status** (Python SBOM attachment standard):
  - **Status**: Finalized April 2025 (canonical spec now on PyPA specs page) — [PEP 770 peps.python.org](https://peps.python.org/pep-0770/), fetched 2026-09-04
  - **SBOM placement in wheels**: `.dist-info/sboms/` directory — install tools must copy files from this directory
  - **Format**: UTF-8-encoded JSON recommended; documents **should** use CycloneDX or SPDX (not mandated, both can coexist in one wheel)
  - **Current adoption**: 332 projects on PyPI shipping SBOM data as of late 2026 — [Seth Larson's PEP 770 writeup](https://sethmlarson.com/pep-770-sbom-data-from-pypi-fedora-and-red-hat)
  - **PyPI serving**: PEP 770 compliant SBOMs are placed in wheels; **PyPI does not yet serve SBOMs as a separate artifact or metadata endpoint** — attach to GitHub Release separately for now
  - **Recommended artifact strategy**: Generate SBOM during `release.yml` build, attach to GitHub Release as a downloadable artifact, AND include in the wheel via `.dist-info/sboms/` for consumers who `pip install` — covers both discovery pathways
- **EU Cyber Resilience Act (CRA)**:
  - **Regulation**: EU 2024/2847, entered into force 2024-12-10
  - **Enforcement timeline**:
    - Some requirements mandatory 2026-09-11 (8 days after research date)
    - **Full enforcement: 2027-12-11** (three years after entry into force)
    - [EU CRA summary](https://digital-strategy.ec.europa.eu/en/policies/cra-summary), [Regulation (EU) 2024/2847](https://www.greenbone.net/en/blog/cra-open-source-software/), fetched 2026-09-04
  - **Non-commercial FOSS exemption**:
    - Free and open-source software **not monetized** by developers/creators is exempt
    - Threshold: Accepting donations **exceeding costs** of design, development, and provision constitutes commercial activity
    - Providing paid support, SLA hosting, or professional services around FOSS **triggers commercial activity classification**
    - [Cycode CRA guide](https://cycode.com/blog/cyber-resilience-act/), [Greenbone CRA for FOSS](https://www.greenbone.net/en/blog/cra-open-source-software/), fetched 2026-09-04
  - **"Open-source software steward" category**:
    - Entities supporting FOSS development for **commercial purposes** (e.g., vendor-backed foundations, corporate open-source programs)
    - Subject to lighter obligations than product manufacturers: establish cybersecurity policies, encourage responsible disclosure, work with authorities on security risks
    - [OpenSSF on CRA](https://openssf.org/public-policy/eu-cyber-resilience-act/), [European Commission guidance](https://www.centerforcybersecurity.org/insights-and-research/european-commission-publishes-final-cyber-resilience-act-implementation-guidance-addresses-concerns-raised-by-cybersecurity-coalition/), fetched 2026-09-04
  - **Verdict for varco**: As a free, non-commercial FOSS project (no funding model that exceeds costs, no paid support), **varco is exempt from CRA** — no burden unless the project monetizes in the future
- **EU NIS2 Directive** (separate from CRA):
  - Does not regulate FOSS maintainers or projects directly
  - Places responsibility on **organizations using FOSS** (not on FOSS authors) to assess and manage risks
  - [NLS Labs on NIS2 vs. FOSS](https://blog.nlnetlabs.nl/supply-chain-security-obligations-for-nis2-regulated-entities-vs-developers-of-open-source-software/), [Uptime on NIS2 compliance](https://www.uptime.eu/blog/navigating-nis2-compliance-can-companies-still-use-open-source-components/), fetched 2026-09-04
  - **Verdict for varco**: NIS2 does not bind varco; it binds downstream consumers of varco who are NIS2-regulated entities

## Options compared

| Aspect | Finding | Impact on backlog |
|---|---|---|
| **§1: OpenFeature SDK un-park trigger** | SDK is v0.10.0 (pre-1.0), spec is v0.9.0 (pre-1.0). Re-check target may be "when SDK reaches 1.0 AND spec stable". Threshold not yet met. | D7: **blocked** — maintain un-park trigger; SDK/spec are actively developed |
| **§2: AsyncAPI Python tooling** | No mandatory heavyweight tool. Emit plain dict + serialize; `datamodel-code-generator` is a red herring (it is a code *consumer*, not producer). `pydantic_model.model_json_schema()` is sufficient for message payloads. | N3: **unblocked** — no new dependency risk; standard JSON + Pydantic suffice |
| **§3: CycloneDX + EU compliance** | `cyclonedx-bom` v7.3.1 is current. PEP 770 finalized; 332 projects adopt it. CRA exempts non-commercial FOSS (varco qualifies). NIS2 does not bind FOSS authors. | D5: **unblocked** — CRA is a courtesy for non-commercial projects; NIS2 is not a varco concern. Implement PEP 770 + SBOM generation; both are low-friction. |

## Version/compatibility notes

| Item | Current version | Release date | Support matrix | EOL / next major |
|---|---|---|---|---|
| `openfeature-sdk` (Python) | 0.10.0 | 2026-06-01 | Python 3.10–3.14 | v1.0 date unknown (spec v0.9.0 as of 2026-07-29, pre-1.0) |
| OpenFeature specification | 0.9.0 | 2026-07-29 | Server-side, client-side, streaming | Stable at 1.0 (timeline TBD) |
| AsyncAPI specification | 3.1.0 (latest), 3.0.0 (stable baseline) | 2023 (v3.0.0), ~mid-2026 (v3.1.0) | Channels/operations, Kafka + 10+ protocol bindings | No EOL indicated |
| `cyclonedx-bom` | 7.3.1 | 2026-07-23 | CycloneDX 1.7, Python venv/Poetry/Pipenv/requirements.txt | Active, no EOL announced |
| PEP 770 | Finalized (April 2025) | Canonical spec on PyPA | `.dist-info/sboms/`, CycloneDX or SPDX | No changes expected (stable standard) |
| EU CRA | Regulation (EU) 2024/2847 | Entered force 2024-12-10 | Full enforcement 2027-12-11 | Binding |
| EU NIS2 | Directive 2022/2555 | Entered force 2024-10-17 | Organizations (not FOSS authors) | Binding |

## Evidence gaps

- **OpenFeature SDK**: No official in-memory test provider documented; varco will author one if proceeding with D7 implementation. Research only confirmed the SDK ships none.
- **AsyncAPI spec version 3.1.0 details**: The `reply` and `replyAddresses` enhancements in 3.1.0 are mentioned in spec reference but detailed semantics not fetched (low priority — varco's initial AsyncAPI export likely targets basic pub/sub, not reply-reply patterns).
- **CycloneDX + uv integration**: No native CycloneDX scanner for uv.lock files exists; workaround via `uv export → cyclonedx-bom` is manual. Worth a separate brief if varco chooses to automate SBOM generation in CI.
- **PyPI SBOM metadata endpoint**: PEP 770 specifies the wheel storage format; whether PyPI will expose SBOMs via JSON API (discoverable without downloading the wheel) remains unconfirmed from these sources — check PyPI JSON API docs separately if needed.
- **NIS2 applicability for varco's downstream consumers**: The research covers NIS2 at a high level; organizations integrating varco in NIS2-regulated contexts should consult legal counsel on their own obligations.

## Librarian's note

**What the sources indicate:**

- **D7 (OpenFeature): Stay parked.** The SDK is actively developed but not at v1.0; the specification explicitly allows breaking changes. Both backlog warnings are confirmed: the SDK and spec are distinct from each other, and neither is stable. Re-check in 12 months or when OpenFeature upstream announces an RC for v1.0.
- **N3 (AsyncAPI export): Proceed.** No risky dependency required; Pydantic v2 + plain dict serialization is the standard path. `datamodel-code-generator` was a misdirected risk (it consumes schemas, not produces them). The evidence is clear and the lift is low.
- **D5 (CycloneDX + EU compliance): Implement opportunistically.** Varco qualifies for CRA exemption (non-commercial); CRA and NIS2 are not binding on the project itself. PEP 770 adoption is already at 332 projects — it is the Python standard. The barrier to entry is low: one `cyclonedx-bom` invocation in CI + SBOM storage in `.dist-info/sboms/` per wheel. Regulatory posture becomes a courtesy, not an obligation, for a non-commercial upstream.

Sources (all fetched or verified 2026-09-04 unless noted):
- [OpenFeature Python SDK on PyPI](https://pypi.org/project/openfeature-sdk/)
- [OpenFeature specification GitHub](https://github.com/open-feature/spec)
- [OpenFeature spec page](https://openfeature.dev/specification/)
- [OpenFeature Provider spec](https://openfeature.dev/specification/sections/providers/)
- [OpenFeature EvaluationContext reference](https://openfeature.dev/docs/reference/concepts/evaluation-context/)
- [AsyncAPI spec v3.1.0](https://www.asyncapi.com/docs/reference/specification/v3.1.0)
- [AsyncAPI spec v3.0.0](https://www.asyncapi.com/docs/reference/specification/v3.0.0)
- [AsyncAPI Kafka bindings tutorial](https://www.asyncapi.com/docs/tutorials/kafka/bindings-with-kafka)
- [CycloneDX Python GitHub](https://github.com/CycloneDX/cyclonedx-python)
- [CycloneDX Python PyPI](https://pypi.org/project/cyclonedx-bom/)
- [CycloneDX Python documentation](https://cyclonedx-bom-tool.readthedocs.io/)
- [datamodel-code-generator GitHub](https://github.com/koxudaxi/datamodel-code-generator)
- [Pydantic JSON Schema docs](https://pydantic.dev/docs/validation/dev/concepts/json_schema/)
- [PEP 770 (finalized April 2025)](https://peps.python.org/pep-0770/)
- [Seth Larson on PEP 770 adoption](https://sethmlarson.dev/pep-770-sbom-data-from-pypi-fedora-and-red-hat)
- [EU CRA summary (European Commission)](https://digital-strategy.ec.europa.eu/en/policies/cra-summary)
- [Cycode: CRA guide](https://cycode.com/blog/cyber-resilience-act/)
- [Greenbone: CRA for open source](https://www.greenbone.net/en/blog/cra-open-source-software/)
- [OpenSSF on CRA](https://openssf.org/public-policy/eu-cyber-resilience-act/)
- [EU Commission CRA implementation guidance](https://www.centerforcybersecurity.org/insights-and-research/european-commission-publishes-final-cyber-resilience-act-implementation-guidance-addresses-concerns-raised-by-cybersecurity-coalition/)
- [NLS Labs: NIS2 vs. FOSS developers](https://blog.nlnetlabs.nl/supply-chain-security-obligations-for-nis2-regulated-entities-vs-developers-of-open-source-software/)
- [Uptime: NIS2 and open source](https://www.uptime.eu/blog/navigating-nis2-compliance-can-companies-still-use-open-source-components/)
